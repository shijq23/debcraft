"""Default storage engine implementation.

Implements the StorageEngine contract, coordinating filesystem layout
initialization, temporary file cleanup, writability verification,
download recovery, cache integrity verification, lifecycle management,
and path resolution.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import TYPE_CHECKING, get_args

from debcraft.infrastructure.errors import StorageError, StorageTimeoutError
from debcraft.infrastructure.events import StorageInitializedEvent, StorageShutdownEvent
from debcraft.platform.contracts.storage import StorageEngine, StoragePurpose

if TYPE_CHECKING:
    from pathlib import Path

    from debcraft.platform.contracts.events import EventBus
    from debcraft.platform.contracts.persistence import DatabaseProvider
    from debcraft.platform.contracts.storage import StorageProvider

logger = logging.getLogger(__name__)

_SHUTDOWN_TIMEOUT_SECONDS: float = 30.0

_MAX_DOWNLOAD_RETRIES: int = 3

# All 7 storage purposes extracted from the StoragePurpose Literal type
_ALL_PURPOSES: tuple[str, ...] = get_args(StoragePurpose)


class DefaultStorageEngine(StorageEngine):
    """Concrete StorageEngine managing filesystem layout and lifecycle.

    Delegates physical filesystem operations to a ``StorageProvider`` and
    publishes lifecycle events through the ``EventBus``. Supports async
    context manager usage for deterministic initialization and shutdown.

    Args:
        provider: The storage provider for filesystem operations.
        event_bus: The event bus for publishing lifecycle events.
    """

    def __init__(
        self,
        provider: StorageProvider,
        event_bus: EventBus,
        db_provider: DatabaseProvider | None = None,
    ) -> None:
        """Initialize DefaultStorageEngine.

        Args:
            provider: The storage provider for filesystem operations.
            event_bus: The event bus for publishing lifecycle events.
            db_provider: Optional database provider for recovery operations.
                If not provided, download recovery is skipped (useful in tests).
        """
        self._provider = provider
        self._event_bus = event_bus
        self._db_provider = db_provider

    async def initialize(self) -> None:
        """Create directories, remove temporaries, verify permissions, recover downloads.

        Performs the following steps in order:
        1. Create all 7 purpose directories via the storage provider.
        2. Remove temporary files from the workspace directory (files with
           ``.tmp`` suffix or ``tmp_`` prefix).
        3. Verify all directories are writable, raising StorageError if not.
        4. Recover interrupted downloads (transition DOWNLOADING entries in
           mirror.db back to QUEUED or FAILED based on retry_count).
        5. Verify mirror cache file integrity by comparing SHA256 checksums
           against stored values in mirror.db; remove mismatched files and
           mark cache.db entries as invalid.
        6. Publish a StorageInitializedEvent through the event bus.

        Raises:
            StorageError: If any directory is not writable after creation.
        """
        # Step 1: Create all purpose directories
        for purpose in _ALL_PURPOSES:
            path = self._provider.resolve_path(purpose)  # type: ignore[arg-type]
            await self._provider.create_directory(path)

        # Step 2: Remove temporary files from workspace
        workspace_path = self._provider.resolve_path("workspace")
        await self._provider.remove_matching(workspace_path, "*.tmp")
        await self._provider.remove_matching(workspace_path, "tmp_*")

        # Step 3: Verify all directories are writable
        for purpose in _ALL_PURPOSES:
            path = self._provider.resolve_path(purpose)  # type: ignore[arg-type]
            writable = await self._provider.check_writable(path)
            if not writable:
                raise StorageError(f"Directory is not writable: {path}")

        # Step 4: Recover interrupted downloads
        await self._recover_interrupted_downloads()

        # Step 5: Verify mirror cache file integrity
        await self._verify_cache_integrity()

        # Step 6: Publish initialization event
        base_path = str(self._provider.resolve_path("cache").parent)
        await self._event_bus.publish(StorageInitializedEvent(base_path=base_path))

    async def _recover_interrupted_downloads(self) -> None:
        """Recover RepositoryFile entries stuck in DOWNLOADING state.

        For each entry in mirror.db with state DOWNLOADING:
        - If retry_count < 3: transition to QUEUED and increment retry_count.
        - If retry_count >= 3: transition to FAILED.

        Skipped if no DatabaseProvider was injected (e.g. in tests without
        a full database setup).
        """
        if self._db_provider is None:
            return

        from debcraft.infrastructure.database.unit_of_work import SqliteUnitOfWork
        from debcraft.infrastructure.models.mirror import RepositoryFileState

        async with SqliteUnitOfWork(self._db_provider, "mirror") as uow:
            downloading = await uow.repository_files.find_by_state(RepositoryFileState.DOWNLOADING)
            for entry in downloading:
                if entry.retry_count < _MAX_DOWNLOAD_RETRIES:
                    entry.state = RepositoryFileState.QUEUED
                    entry.retry_count += 1
                else:
                    entry.state = RepositoryFileState.FAILED
                await uow.repository_files.update(entry)

            if downloading:
                logger.info(
                    "Recovered %d interrupted download(s) during initialization",
                    len(downloading),
                )

    async def _verify_cache_integrity(self) -> None:
        """Verify mirror cache file integrity by comparing SHA256 checksums.

        Scans the mirror cache directory and for each file:
        1. Computes its SHA256 hash.
        2. Looks up the corresponding RepositoryFile entry in mirror.db by local_path.
        3. If the SHA256 doesn't match the stored checksum, removes the file from disk.
        4. Marks any cache.db entries as valid=False if a mismatch is detected.

        Skipped if no DatabaseProvider was injected.
        """
        if self._db_provider is None:
            return

        from debcraft.infrastructure.database.unit_of_work import SqliteUnitOfWork

        mirror_dir = self._provider.resolve_path("mirror")

        # Collect all files in the mirror cache directory
        files = await asyncio.to_thread(self._list_mirror_files, mirror_dir)
        if not files:
            return

        mismatched_count = 0

        async with SqliteUnitOfWork(self._db_provider, "mirror") as uow:
            for file_path in files:
                # Compute SHA256 of the file on disk
                computed_sha256 = await asyncio.to_thread(self._compute_sha256, file_path)
                if computed_sha256 is None:
                    # File was removed or unreadable; skip
                    continue

                # Look up the RepositoryFile entry by local_path
                path_str = str(file_path)
                entries = await uow.repository_files.find(local_path=path_str)
                if not entries:
                    # No entry in mirror.db for this file; skip
                    continue

                entry = entries[0]
                if entry.sha256 != computed_sha256:
                    # SHA256 mismatch: remove the file from disk
                    await asyncio.to_thread(self._remove_file, file_path)
                    mismatched_count += 1
                    logger.warning(
                        "Cache integrity check failed for %s: expected %s, got %s",
                        file_path,
                        entry.sha256,
                        computed_sha256,
                    )

        # Mark cache.db entries as invalid if any mismatches were detected
        if mismatched_count > 0:
            await self._invalidate_cache_entries()
            logger.info(
                "Cache integrity verification: removed %d file(s) with SHA256 mismatch",
                mismatched_count,
            )

    async def _invalidate_cache_entries(self) -> None:
        """Mark all cache.db entries as valid=False when integrity mismatch detected.

        This implements Requirement 10.6: when a conflict between cached and
        metadata values is detected, cache entries are marked for recomputation.
        """
        if self._db_provider is None:
            return

        from sqlalchemy import update

        from debcraft.infrastructure.models.cache import (
            ChecksumCache,
            NormalizedLicense,
            ParsedDep5,
        )

        session = await self._db_provider.get_session("cache")
        try:
            await session.begin()
            # Mark all cache entries as invalid — they need recomputation
            for model in (ParsedDep5, NormalizedLicense, ChecksumCache):
                stmt = update(model).where(model.valid == True).values(valid=False)  # noqa: E712
                await session.execute(stmt)
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    @staticmethod
    def _list_mirror_files(mirror_dir: Path) -> list[Path]:
        """List all regular files in the mirror cache directory.

        Args:
            mirror_dir: The mirror cache directory path.

        Returns:
            A list of Path objects for regular files found in the directory tree.
        """
        if not mirror_dir.exists():
            return []
        return [p for p in mirror_dir.rglob("*") if p.is_file()]

    @staticmethod
    def _compute_sha256(file_path: Path) -> str | None:
        """Compute the SHA256 hex digest of a file.

        Args:
            file_path: Path to the file to hash.

        Returns:
            The lowercase hex SHA256 digest, or None if the file cannot be read.
        """
        try:
            sha256 = hashlib.sha256()
            with open(file_path, "rb") as f:
                while chunk := f.read(65536):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except OSError:
            return None

    @staticmethod
    def _remove_file(file_path: Path) -> None:
        """Remove a file from disk, ignoring errors if already gone.

        Args:
            file_path: Path to the file to remove.
        """
        import contextlib

        with contextlib.suppress(OSError):
            file_path.unlink(missing_ok=True)

    async def shutdown(self) -> None:
        """Flush pending writes and release resources within 30 seconds.

        Publishes a StorageShutdownEvent and enforces a 30-second timeout.

        Raises:
            StorageTimeoutError: If shutdown exceeds the 30-second limit.
        """
        try:
            await asyncio.wait_for(
                self._do_shutdown(),
                timeout=_SHUTDOWN_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise StorageTimeoutError(
                timeout_seconds=_SHUTDOWN_TIMEOUT_SECONDS,
                cause=exc,
            ) from exc

    async def _do_shutdown(self) -> None:
        """Internal shutdown logic that publishes the shutdown event."""
        await self._event_bus.publish(StorageShutdownEvent())

    def get_path(self, purpose: StoragePurpose, relative: str = "") -> Path:
        """Return absolute Path for a named storage purpose.

        Delegates to the provider's ``resolve_path`` method and appends
        the optional relative suffix.

        Args:
            purpose: The named storage purpose to resolve.
            relative: Optional relative path to append within the purpose directory.

        Returns:
            Absolute path to the resolved location.
        """
        base = self._provider.resolve_path(purpose)
        if relative:
            return base / relative
        return base

    async def __aenter__(self) -> DefaultStorageEngine:
        """Enter async context: calls initialize().

        Returns:
            This engine instance after initialization completes.
        """
        await self.initialize()
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Exit async context: calls shutdown()."""
        await self.shutdown()
