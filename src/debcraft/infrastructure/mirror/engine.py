"""Mirror engine orchestrating the synchronization pipeline.

Coordinates the five-stage sync pipeline (Release → Indexes → Artifacts →
Verify → Publish) for a single repository, managing RepositoryFile state
transitions, progress reporting, cancellation checks, and batch database
commits.
"""

from __future__ import annotations

import gzip
import hashlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from sqlalchemy import select, update

from debcraft.domain.mirror.comparator import FileComparator, generate_index_paths
from debcraft.domain.mirror.packages_parser import PackagesParser
from debcraft.domain.mirror.release_parser import ReleaseMetadata, ReleaseParser
from debcraft.infrastructure.mirror.download import DownloadTask
from debcraft.infrastructure.mirror.errors import ReleaseParseError
from debcraft.infrastructure.models.mirror import RepositoryFile, RepositoryFileState, SyncSession

if TYPE_CHECKING:
    from debcraft.domain.mirror.config import RepositoryConfig
    from debcraft.domain.mirror.values import FileEntry
    from debcraft.infrastructure.mirror.download import DownloadCoordinator
    from debcraft.platform.contracts.events import EventBus
    from debcraft.platform.contracts.logging import Logger
    from debcraft.platform.contracts.persistence import DatabaseProvider
    from debcraft.platform.contracts.storage import StorageEngine
    from debcraft.platform.contracts.workflow import CancellationToken, ProgressReporter


_BATCH_SIZE = 500
_MAX_RETRIES = 3


@dataclass
class SyncResult:
    """Outcome of a repository synchronization session.

    Attributes:
        files_downloaded: Number of files successfully downloaded.
        files_skipped: Number of files skipped (already cached).
        files_failed: Number of files that failed after all retries.
        bytes_transferred: Total bytes transferred during the session.
    """

    files_downloaded: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    bytes_transferred: int = 0


class MirrorEngine:
    """Orchestrates synchronization stages for a single repository.

    Coordinates the five-stage pipeline: Release download, index sync,
    artifact download, verification, and snapshot publication. Checks
    the CancellationToken between each stage and reports progress at
    defined milestones.
    """

    def __init__(
        self,
        download_coordinator: DownloadCoordinator,
        db_provider: DatabaseProvider,
        storage_engine: StorageEngine,
        event_bus: EventBus,
        cancellation_token: CancellationToken,
        progress: ProgressReporter,
        logger: Logger,
    ) -> None:
        """Initialize the MirrorEngine with all dependencies.

        Args:
            download_coordinator: Manages concurrent HTTP downloads.
            db_provider: Provides database sessions for mirror.db.
            storage_engine: Resolves filesystem paths.
            event_bus: Publishes domain events.
            cancellation_token: Cooperative cancellation signal.
            progress: Reports progress percentage and messages.
            logger: Structured logger for this component.
        """
        self._download_coordinator = download_coordinator
        self._db_provider = db_provider
        self._storage_engine = storage_engine
        self._event_bus = event_bus
        self._cancellation_token = cancellation_token
        self._progress = progress
        self._logger = logger
        self._release_parser = ReleaseParser()
        self._packages_parser = PackagesParser()
        self._comparator = FileComparator()
        self._result = SyncResult()
        self._session_id = ""

    async def sync_repository(
        self,
        config: RepositoryConfig,
        session_id: str,
    ) -> SyncResult:
        """Run full sync pipeline for one repository.

        Orchestrates the five-stage pipeline for each suite in the
        repository configuration. Checks the cancellation token between
        each stage. Reports progress at defined milestones.

        Args:
            config: Repository configuration specifying URL, suites, etc.
            session_id: Unique identifier for this sync session.

        Returns:
            SyncResult with counts of downloaded/skipped/failed files.
        """
        self._result = SyncResult()
        self._session_id = session_id
        started_at = datetime.now(UTC)
        start_time = time.monotonic()

        # Handle resumption: re-queue DOWNLOADING entities to QUEUED
        await self._resume_interrupted_downloads()

        self._logger.info(
            "Starting repository sync",
            repository=config.name,
            session_id=session_id,
            suites=",".join(config.suites),
        )

        total_suites = len(config.suites)
        for suite_idx, suite in enumerate(config.suites):
            if self._cancellation_token.is_cancelled:
                self._logger.info(
                    "Cancellation detected, stopping sync",
                    session_id=session_id,
                )
                break

            # Calculate progress offsets for this suite
            suite_base = (suite_idx / total_suites) * 100
            suite_range = 100 / total_suites

            # Stage 1: Release (0-20% of suite range)
            self._progress.report(
                suite_base + suite_range * 0.0,
                f"Downloading Release file for {suite}",
            )
            release = await self._stage_release(config, suite)

            if release is None:
                # Suite is up-to-date or failed
                continue

            if self._cancellation_token.is_cancelled:
                self._logger.info(
                    "Cancellation detected after release stage",
                    session_id=session_id,
                )
                break

            # Stage 2: Indexes (20-50% of suite range)
            self._progress.report(
                suite_base + suite_range * 0.2,
                f"Downloading indexes for {suite}",
            )
            entries = await self._stage_indexes(config, suite, release)

            if self._cancellation_token.is_cancelled:
                self._logger.info(
                    "Cancellation detected after indexes stage",
                    session_id=session_id,
                )
                break

            # Stage 3: Artifacts (50-80% of suite range)
            self._progress.report(
                suite_base + suite_range * 0.5,
                f"Downloading artifacts for {suite}",
            )
            await self._stage_artifacts(config, entries)

            if self._cancellation_token.is_cancelled:
                self._logger.info(
                    "Cancellation detected after artifacts stage",
                    session_id=session_id,
                )
                break

            # Stage 4: Verify (80-95% of suite range)
            self._progress.report(
                suite_base + suite_range * 0.8,
                f"Verifying downloads for {suite}",
            )
            # Verification is handled during download (SHA256 check)
            # Files that pass are already in VERIFIED state

            if self._cancellation_token.is_cancelled:
                self._logger.info(
                    "Cancellation detected after verify stage",
                    session_id=session_id,
                )
                break

            # Stage 5: Publish (95-100% of suite range)
            self._progress.report(
                suite_base + suite_range * 0.95,
                f"Publishing snapshot for {suite}",
            )
            await self._stage_publish(config)

        # Compute elapsed time and determine final status
        elapsed = time.monotonic() - start_time
        total_processed = self._result.files_downloaded + self._result.files_skipped + self._result.files_failed

        if self._cancellation_token.is_cancelled:
            status = "cancelled"
        elif self._result.files_failed > 0 and self._result.files_downloaded > 0:
            status = "partial"
        elif self._result.files_failed > 0:
            status = "failed"
        else:
            status = "completed"

        # Persist SyncSession for observability
        try:
            db_session = await self._db_provider.get_session("mirror")
            try:
                sync_session = SyncSession(
                    session_id=session_id,
                    repository_name=config.name,
                    status=status,
                    files_downloaded=self._result.files_downloaded,
                    files_skipped=self._result.files_skipped,
                    files_failed=self._result.files_failed,
                    bytes_transferred=self._result.bytes_transferred,
                    started_at=started_at,
                    completed_at=datetime.now(UTC),
                )
                db_session.add(sync_session)
                await db_session.commit()
            finally:
                await db_session.close()
        except Exception as exc:
            self._logger.error(
                "Failed to persist sync session",
                session_id=session_id,
                error=str(exc),
            )

        # Emit summary log entry (Requirement 14.6)
        self._logger.info(
            "Repository sync finished",
            repository=config.name,
            session_id=session_id,
            status=status,
            files_processed=total_processed,
            files_downloaded=self._result.files_downloaded,
            files_skipped=self._result.files_skipped,
            files_failed=self._result.files_failed,
            bytes_transferred=self._result.bytes_transferred,
            elapsed_seconds=round(elapsed, 2),
        )

        self._progress.report(100.0, "Synchronization complete")
        return self._result

    async def _stage_release(self, config: RepositoryConfig, suite: str) -> ReleaseMetadata | None:
        """Download and parse Release file for a suite.

        Attempts to download InRelease first, falling back to Release
        on HTTP 404. Uses conditional requests if a cached version exists.
        Returns None if the suite is already up-to-date (304 response or
        matching checksum).

        Args:
            config: Repository configuration.
            suite: The distribution suite to download Release for.

        Returns:
            Parsed ReleaseMetadata, or None if up-to-date or failed.
        """
        base_url = config.base_url.rstrip("/")
        inrelease_url = f"{base_url}/dists/{suite}/InRelease"
        release_url = f"{base_url}/dists/{suite}/Release"
        mirror_root = self._get_mirror_root(config)

        # Check if we have a cached version for conditional requests
        inrelease_path = mirror_root / "dists" / suite / "InRelease"

        # Try conditional request first if cached copy exists
        if inrelease_path.exists():
            # Query stored headers for conditional request
            stored_etag: str | None = None
            stored_last_modified: str | None = None
            session = await self._db_provider.get_session("mirror")
            try:
                stmt = select(RepositoryFile).where(RepositoryFile.url == inrelease_url)
                db_result = await session.execute(stmt)
                existing = db_result.scalar_one_or_none()
                if existing is not None:
                    stored_etag = existing.etag
                    stored_last_modified = existing.last_modified
            finally:
                await session.close()

            is_unchanged = await self._download_coordinator.check_conditional(
                inrelease_url,
                etag=stored_etag,
                last_modified=stored_last_modified,
            )
            if is_unchanged:
                self._logger.info(
                    "Suite is up-to-date (conditional request)",
                    suite=suite,
                    session_id=self._session_id,
                )
                return None

        # Try InRelease first
        dest_path = mirror_root / "dists" / suite / "InRelease"
        result = await self._download_release_file(inrelease_url, dest_path)

        # Fall back to Release if InRelease returned 404
        if result is None:
            dest_path = mirror_root / "dists" / suite / "Release"
            result = await self._download_release_file(release_url, dest_path)

        if result is None:
            self._logger.error(
                "Failed to download Release file",
                suite=suite,
                repository=config.name,
                session_id=self._session_id,
            )
            self._result.files_failed += 1
            return None

        # Parse the Release file
        content, file_url, response_headers = result
        try:
            release = self._release_parser.parse(content, url=file_url)
        except ReleaseParseError as exc:
            self._logger.error(
                "Failed to parse Release file",
                url=file_url,
                error=str(exc),
                session_id=self._session_id,
            )
            self._result.files_failed += 1
            return None

        # Extract ETag and Last-Modified from response headers
        etag = None
        last_modified = None
        if response_headers:
            etag = response_headers.get("ETag")
            last_modified = response_headers.get("Last-Modified")

        # Store as RepositoryFile with VERIFIED state
        release_content_bytes = content.encode()
        release_sha256 = hashlib.sha256(release_content_bytes).hexdigest()
        await self._upsert_repository_file(
            url=file_url,
            sha256=release_sha256,
            size_bytes=len(release_content_bytes),
            state=RepositoryFileState.VERIFIED,
            local_path=str(dest_path),
            etag=etag,
            last_modified=last_modified,
        )

        self._logger.debug(
            "Release file downloaded and verified",
            url=file_url,
            session_id=self._session_id,
            state="VERIFIED",
        )

        self._result.files_downloaded += 1
        return release

    async def _stage_indexes(
        self,
        config: RepositoryConfig,
        suite: str,
        release: ReleaseMetadata,
    ) -> list[FileEntry]:
        """Download changed index files and parse package entries.

        Generates index paths from config components x architectures,
        compares against local cache checksums to determine which need
        downloading, downloads changed indexes, and parses Packages files
        to extract artifact FileEntry objects.

        Args:
            config: Repository configuration.
            suite: The distribution suite.
            release: Parsed Release metadata with file checksums.

        Returns:
            List of package artifact FileEntry objects from parsed indexes.
        """
        base_url = config.base_url.rstrip("/")
        mirror_root = self._get_mirror_root(config)

        # Generate expected index paths
        expected_paths = generate_index_paths(config.components, config.architectures)

        # Filter release entries to only indexes we care about
        remote_entries = [entry for entry in release.files if entry.relative_path in expected_paths]

        # Log warning for missing indexes
        found_paths = {e.relative_path for e in remote_entries}
        for path in expected_paths:
            if path not in found_paths:
                self._logger.warning(
                    "Index not found in Release file",
                    path=path,
                    suite=suite,
                    session_id=self._session_id,
                )

        # Build local checksums map from existing verified files
        local_checksums = await self._get_local_checksums(config, suite, expected_paths)

        # Compute sync decisions
        decisions = self._comparator.compute_sync_decisions(remote_entries, local_checksums)

        # Download changed indexes
        all_artifact_entries: list[FileEntry] = []
        for decision in decisions:
            if decision.action == "skip":
                self._result.files_skipped += 1
                self._logger.debug(
                    "Index file skipped (already cached)",
                    url=f"{base_url}/dists/{suite}/{decision.file_entry.relative_path}",
                    reason="already_cached",
                    session_id=self._session_id,
                )
                # Still parse the cached file for artifact entries
                cached_path = mirror_root / "dists" / suite / decision.file_entry.relative_path
                if cached_path.exists():
                    entries = self._parse_packages_file(cached_path)
                    all_artifact_entries.extend(entries)
                continue

            # Download the index
            index_url = f"{base_url}/dists/{suite}/{decision.file_entry.relative_path}"
            dest_path = mirror_root / "dists" / suite / decision.file_entry.relative_path
            download_result = await self._download_coordinator.download_file(
                url=index_url,
                dest_path=dest_path,
                expected_sha256=decision.file_entry.sha256,
                expected_size=decision.file_entry.size_bytes,
            )

            if download_result.success:
                self._result.files_downloaded += 1
                self._result.bytes_transferred += download_result.bytes_transferred
                await self._upsert_repository_file(
                    url=index_url,
                    sha256=decision.file_entry.sha256,
                    size_bytes=decision.file_entry.size_bytes,
                    state=RepositoryFileState.VERIFIED,
                    local_path=str(dest_path),
                )
                self._logger.debug(
                    "Index file downloaded and verified",
                    url=index_url,
                    session_id=self._session_id,
                    state="VERIFIED",
                )
                # Parse the downloaded index for artifact entries
                entries = self._parse_packages_file(dest_path)
                all_artifact_entries.extend(entries)
            else:
                self._result.files_failed += 1
                self._logger.error(
                    "Failed to download index",
                    url=index_url,
                    error=download_result.error,
                    session_id=self._session_id,
                )

        return all_artifact_entries

    async def _stage_artifacts(
        self,
        config: RepositoryConfig,
        entries: list[FileEntry],
    ) -> None:
        """Download package artifacts concurrently.

        Filters out artifacts already present in the local cache, creates
        download tasks for the remaining ones, and uses the download
        coordinator's batch download with concurrency control. Updates
        RepositoryFile state in batches of ≤500 per transaction.

        Args:
            config: Repository configuration.
            entries: List of artifact FileEntry objects to download.
        """
        if not entries:
            return

        # Deduplicate entries by relative_path to avoid concurrent downloads
        # racing on the same destination file. This happens when arch-independent
        # packages (_all.deb) appear in multiple architecture indexes.
        seen_paths: set[str] = set()
        unique_entries: list[FileEntry] = []
        for entry in entries:
            if entry.relative_path not in seen_paths:
                seen_paths.add(entry.relative_path)
                unique_entries.append(entry)
        if len(unique_entries) < len(entries):
            self._logger.debug(
                "Deduplicated artifact entries",
                original_count=len(entries),
                unique_count=len(unique_entries),
                duplicates_removed=len(entries) - len(unique_entries),
                session_id=self._session_id,
            )
        entries = unique_entries

        base_url = config.base_url.rstrip("/")
        mirror_root = self._get_mirror_root(config)

        # Filter out already-cached artifacts
        local_checksums = await self._get_artifact_checksums(config, entries)
        decisions = self._comparator.compute_sync_decisions(entries, local_checksums)

        to_download = [d.file_entry for d in decisions if d.action == "download"]
        skipped_count = sum(1 for d in decisions if d.action == "skip")
        self._result.files_skipped += skipped_count

        if skipped_count > 0:
            self._logger.debug(
                "Artifacts skipped (already cached)",
                skipped_count=skipped_count,
                session_id=self._session_id,
            )

        if not to_download:
            return

        # Create RepositoryFile entities in QUEUED state
        await self._batch_create_repository_files(config, to_download, RepositoryFileState.QUEUED)

        # Build download tasks
        tasks: list[DownloadTask] = []
        for entry in to_download:
            url = f"{base_url}/{entry.relative_path}"
            dest_path = mirror_root / entry.relative_path
            tasks.append(
                DownloadTask(
                    url=url,
                    dest_path=dest_path,
                    expected_sha256=entry.sha256,
                    expected_size=entry.size_bytes,
                )
            )

        # Execute batch download
        results = await self._download_coordinator.download_batch(
            tasks=tasks,
            max_concurrent=self._download_coordinator._config.max_connections_per_repo,
        )

        # Process results and update state in batches
        succeeded: list[tuple[str, str, int]] = []  # (url, local_path, bytes)
        failed_urls: list[str] = []

        for entry, task, result in zip(to_download, tasks, results, strict=True):
            if result.success:
                succeeded.append((task.url, str(task.dest_path), result.bytes_transferred))
                self._result.files_downloaded += 1
                self._result.bytes_transferred += result.bytes_transferred
                # Extract package name from relative path
                # (e.g., pool/main/l/lib/libfoo_1.0_amd64.deb → libfoo_1.0_amd64.deb)
                package_name = Path(entry.relative_path).name
                self._logger.debug(
                    "Artifact downloaded successfully",
                    url=task.url,
                    package_name=package_name,
                    bytes_transferred=result.bytes_transferred,
                    session_id=self._session_id,
                )
            else:
                failed_urls.append(task.url)
                self._result.files_failed += 1
                self._logger.error(
                    "Artifact download failed",
                    url=task.url,
                    error=result.error,
                    status_code=result.status_code,
                    retry_count=result.retry_count,
                    session_id=self._session_id,
                )

        # Batch update succeeded files to VERIFIED
        await self._batch_update_state(succeeded, RepositoryFileState.VERIFIED)

        # Batch update failed files to FAILED
        await self._batch_mark_failed(failed_urls)

    async def _stage_publish(self, config: RepositoryConfig) -> None:
        """Publish a RepositorySnapshot for verified files.

        Delegates to the SnapshotPublisher which handles atomic snapshot
        creation in metadata.db.

        Args:
            config: Repository configuration.
        """
        session = await self._db_provider.get_session("mirror")
        try:
            # Count verified and failed files for this repository
            verified_stmt = select(RepositoryFile).where(
                RepositoryFile.state == RepositoryFileState.VERIFIED,
                RepositoryFile.url.like(f"{config.base_url.rstrip('/')}%"),
            )
            result = await session.execute(verified_stmt)
            verified_count = len(result.scalars().all())

            failed_stmt = select(RepositoryFile).where(
                RepositoryFile.state == RepositoryFileState.FAILED,
                RepositoryFile.url.like(f"{config.base_url.rstrip('/')}%"),
            )
            result = await session.execute(failed_stmt)
            failed_count = len(result.scalars().all())
        finally:
            await session.close()

        if verified_count == 0:
            self._logger.warning(
                "No verified files, skipping snapshot publication",
                repository=config.name,
                session_id=self._session_id,
            )
            return

        # Import here to avoid circular imports at module level
        from debcraft.infrastructure.mirror.publisher import SnapshotPublisher

        publisher = SnapshotPublisher(self._db_provider, self._event_bus)
        await publisher.publish_snapshot(
            repository_id=0,  # Will be resolved by publisher
            verified_file_count=verified_count,
            failed_file_count=failed_count,
        )

    # ──────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────

    def _get_mirror_root(self, config: RepositoryConfig) -> Path:
        """Derive the local mirror root path from repository base URL.

        Maps base_url to {mirror_root}/{hostname}/{url_path}/ using the
        StorageEngine's mirror path resolution.

        Args:
            config: Repository configuration with base_url.

        Returns:
            Absolute path to the repository's local mirror root.
        """
        parsed = urlparse(config.base_url)
        hostname = parsed.hostname or "unknown"
        url_path = parsed.path.strip("/")
        mirror_base = self._storage_engine.get_path("mirror")
        if url_path:
            return mirror_base / hostname / url_path
        return mirror_base / hostname

    async def _download_release_file(
        self,
        url: str,
        dest_path: Path,
    ) -> tuple[str, str, dict[str, str] | None] | None:
        """Download a Release/InRelease file and return its content.

        Creates parent directories as needed. Returns None if the
        download fails (e.g., 404 for InRelease).

        Args:
            url: URL of the Release file to download.
            dest_path: Local destination path.

        Returns:
            Tuple of (file_content, url, response_headers) on success,
            None on failure.
        """
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # For Release files, we download without strict size/hash validation
        # since the Release file IS the source of truth for hashes.
        # We use a dummy hash and accept any size.
        # Download with relaxed validation for the Release file itself.
        try:
            result = await self._download_coordinator.download_file(
                url=url,
                dest_path=dest_path,
                expected_sha256="",  # Will be overridden
                expected_size=0,
                timeout=60,
            )
        except Exception:
            # If download_file raises (e.g., 404 HttpClientError), treat as failure
            return None

        # If the coordinator reports failure (e.g., 404), return None
        if not result.success:
            return None

        # Read the downloaded content
        try:
            content = dest_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

        return (content, url, result.response_headers)

    async def _resume_interrupted_downloads(self) -> None:
        """Re-queue files stuck in DOWNLOADING state from a previous session.

        On startup, any RepositoryFile in DOWNLOADING state indicates an
        interrupted download. Transition these back to QUEUED so they can
        be retried.
        """
        session = await self._db_provider.get_session("mirror")
        try:
            stmt = (
                update(RepositoryFile)
                .where(RepositoryFile.state == RepositoryFileState.DOWNLOADING)
                .values(
                    state=RepositoryFileState.QUEUED,
                    updated_at=datetime.now(UTC),
                )
            )
            await session.execute(stmt)
            await session.commit()
        except Exception:
            await session.rollback()
            self._logger.error(
                "Failed to resume interrupted downloads",
                session_id=getattr(self, "_session_id", ""),
            )
        finally:
            await session.close()

    async def _upsert_repository_file(
        self,
        url: str,
        sha256: str,
        size_bytes: int,
        state: RepositoryFileState,
        local_path: str | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        """Create or update a RepositoryFile entity keyed by URL.

        If a RepositoryFile with the given URL exists, updates its fields.
        Otherwise creates a new entity with the specified state.

        Args:
            url: Unique URL identifying the file.
            sha256: SHA256 checksum of the file.
            size_bytes: File size in bytes.
            state: Target lifecycle state.
            local_path: Local filesystem path (set on VERIFIED).
            etag: ETag header from the HTTP response.
            last_modified: Last-Modified header from the HTTP response.
        """
        session = await self._db_provider.get_session("mirror")
        try:
            stmt = select(RepositoryFile).where(RepositoryFile.url == url)
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing is not None:
                existing.sha256 = sha256
                existing.size_bytes = size_bytes
                existing.state = state
                existing.updated_at = datetime.now(UTC)
                if local_path is not None:
                    existing.local_path = local_path
                if etag is not None:
                    existing.etag = etag
                if last_modified is not None:
                    existing.last_modified = last_modified
            else:
                entity = RepositoryFile(
                    url=url,
                    sha256=sha256,
                    size_bytes=size_bytes,
                    state=state,
                    retry_count=0,
                    local_path=local_path,
                    etag=etag,
                    last_modified=last_modified,
                )
                session.add(entity)

            await session.commit()
        except Exception:
            await session.rollback()
            self._logger.error(
                "Failed to upsert RepositoryFile",
                url=url,
                session_id=self._session_id,
            )
        finally:
            await session.close()

    async def _batch_create_repository_files(
        self,
        config: RepositoryConfig,
        entries: list[FileEntry],
        state: RepositoryFileState,
    ) -> None:
        """Create RepositoryFile entities in batches of ≤500.

        Creates or updates entities for each file entry, committing
        in batches to bound memory usage.

        Args:
            config: Repository configuration for URL construction.
            entries: File entries to create entities for.
            state: Initial state for created entities.
        """
        base_url = config.base_url.rstrip("/")
        session = await self._db_provider.get_session("mirror")
        try:
            batch_count = 0
            for entry in entries:
                url = f"{base_url}/{entry.relative_path}"
                stmt = select(RepositoryFile).where(RepositoryFile.url == url)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing is not None:
                    existing.sha256 = entry.sha256
                    existing.size_bytes = entry.size_bytes
                    existing.state = state
                    existing.updated_at = datetime.now(UTC)
                else:
                    entity = RepositoryFile(
                        url=url,
                        sha256=entry.sha256,
                        size_bytes=entry.size_bytes,
                        state=state,
                        retry_count=0,
                    )
                    session.add(entity)

                batch_count += 1
                if batch_count >= _BATCH_SIZE:
                    await session.commit()
                    batch_count = 0

            # Commit remaining
            if batch_count > 0:
                await session.commit()
        except Exception:
            await session.rollback()
            self._logger.error(
                "Failed to batch create RepositoryFiles",
                session_id=self._session_id,
            )
        finally:
            await session.close()

    async def _batch_update_state(
        self,
        succeeded: list[tuple[str, str, int]],
        state: RepositoryFileState,
    ) -> None:
        """Update RepositoryFile entities to a new state in batches.

        Commits in batches of ≤500 entities per transaction.

        Args:
            succeeded: List of (url, local_path, bytes_transferred) tuples.
            state: Target state for the entities.
        """
        if not succeeded:
            return

        session = await self._db_provider.get_session("mirror")
        try:
            batch_count = 0
            for url, local_path, _bytes in succeeded:
                stmt = select(RepositoryFile).where(RepositoryFile.url == url)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing is not None:
                    existing.state = state
                    existing.local_path = local_path
                    existing.updated_at = datetime.now(UTC)

                batch_count += 1
                if batch_count >= _BATCH_SIZE:
                    await session.commit()
                    batch_count = 0

            if batch_count > 0:
                await session.commit()
        except Exception:
            await session.rollback()
            self._logger.error(
                "Failed to batch update RepositoryFile states",
                session_id=self._session_id,
            )
        finally:
            await session.close()

    async def _batch_mark_failed(self, failed_urls: list[str]) -> None:
        """Mark RepositoryFile entities as FAILED in batches.

        Increments retry_count and transitions to FAILED state.
        Commits in batches of ≤500.

        Args:
            failed_urls: URLs of files that failed download.
        """
        if not failed_urls:
            return

        session = await self._db_provider.get_session("mirror")
        try:
            batch_count = 0
            for url in failed_urls:
                stmt = select(RepositoryFile).where(RepositoryFile.url == url)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing is not None:
                    existing.retry_count += 1
                    if existing.retry_count >= _MAX_RETRIES:
                        existing.state = RepositoryFileState.FAILED
                    else:
                        existing.state = RepositoryFileState.QUEUED
                    existing.updated_at = datetime.now(UTC)

                batch_count += 1
                if batch_count >= _BATCH_SIZE:
                    await session.commit()
                    batch_count = 0

            if batch_count > 0:
                await session.commit()
        except Exception:
            await session.rollback()
            self._logger.error(
                "Failed to batch mark RepositoryFiles as failed",
                session_id=self._session_id,
            )
        finally:
            await session.close()

    async def _get_local_checksums(
        self,
        config: RepositoryConfig,
        suite: str,
        paths: list[str],
    ) -> dict[str, str]:
        """Get local SHA256 checksums for index files from mirror.db.

        Queries RepositoryFile entities for the given index paths to
        build a mapping of relative_path → sha256 for comparison.

        Args:
            config: Repository configuration.
            suite: Suite name.
            paths: List of relative index paths to look up.

        Returns:
            Dictionary mapping relative_path to sha256 hex digest.
        """
        base_url = config.base_url.rstrip("/")
        checksums: dict[str, str] = {}

        session = await self._db_provider.get_session("mirror")
        try:
            for path in paths:
                url = f"{base_url}/dists/{suite}/{path}"
                stmt = select(RepositoryFile).where(
                    RepositoryFile.url == url,
                    RepositoryFile.state == RepositoryFileState.VERIFIED,
                )
                result = await session.execute(stmt)
                entity = result.scalar_one_or_none()
                if entity is not None:
                    checksums[path] = entity.sha256
        finally:
            await session.close()

        return checksums

    async def _get_artifact_checksums(
        self,
        config: RepositoryConfig,
        entries: list[FileEntry],
    ) -> dict[str, str]:
        """Get local SHA256 checksums for artifact files from mirror.db.

        Queries RepositoryFile entities for artifact paths to determine
        which ones are already cached.

        Args:
            config: Repository configuration.
            entries: File entries to check.

        Returns:
            Dictionary mapping relative_path to sha256 hex digest.
        """
        base_url = config.base_url.rstrip("/")
        checksums: dict[str, str] = {}

        session = await self._db_provider.get_session("mirror")
        try:
            for entry in entries:
                url = f"{base_url}/{entry.relative_path}"
                stmt = select(RepositoryFile).where(
                    RepositoryFile.url == url,
                    RepositoryFile.state.in_(
                        [
                            RepositoryFileState.VERIFIED,
                            RepositoryFileState.INDEXED,
                        ]
                    ),
                )
                result = await session.execute(stmt)
                entity = result.scalar_one_or_none()
                if entity is not None:
                    checksums[entry.relative_path] = entity.sha256
        finally:
            await session.close()

        return checksums

    def _parse_packages_file(self, path: Path) -> list[FileEntry]:
        """Parse a Packages.gz file from disk to extract artifact entries.

        Handles gzip-compressed Packages files. Falls back gracefully
        if the file cannot be read or decompressed.

        Args:
            path: Path to the Packages.gz file on disk.

        Returns:
            List of FileEntry objects parsed from the Packages content.
        """
        try:
            if path.suffix == ".gz":
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    content = f.read()
            else:
                content = path.read_text(encoding="utf-8")
        except (OSError, gzip.BadGzipFile) as exc:
            self._logger.error(
                "Failed to read Packages file",
                path=str(path),
                error=str(exc),
                session_id=self._session_id,
            )
            return []

        return self._packages_parser.parse(content)
