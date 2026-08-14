"""Mirror engine orchestrating the synchronization pipeline.

Coordinates the five-stage sync pipeline (Release → Indexes → Artifacts →
Verify → Publish) for a single repository, managing RepositoryFile state
transitions, progress reporting, cancellation checks, and batch database
commits.
"""

from __future__ import annotations

import gzip
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from debcraft.domain.mirror.comparator import FileComparator, generate_index_paths
from debcraft.domain.mirror.packages_parser import PackagesParser
from debcraft.domain.mirror.release_parser import ReleaseMetadata, ReleaseParser
from debcraft.infrastructure.mirror import _checksums, _persistence, _staging
from debcraft.infrastructure.mirror.download import DownloadTask
from debcraft.infrastructure.models.mirror import RepositoryFile, RepositoryFileState, SyncSession

if TYPE_CHECKING:
    from debcraft.domain.mirror.config import RepositoryConfig
    from debcraft.domain.mirror.values import DownloadResult, FileEntry
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
        *,
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

            cancelled = await self._sync_single_suite(config, suite, suite_idx, total_suites)
            if cancelled:
                break

        # Finalize: compute status, persist session, log summary
        elapsed = time.monotonic() - start_time
        status = self._determine_sync_status()
        await self._persist_sync_session(config, session_id, status, started_at)
        self._log_sync_summary(config, session_id, status, elapsed)

        self._progress.report(100.0, "Synchronization complete")
        return self._result

    async def _sync_single_suite(
        self,
        config: RepositoryConfig,
        suite: str,
        suite_idx: int,
        total_suites: int,
    ) -> bool:
        """Run the five-stage pipeline for a single suite.

        Args:
            config: Repository configuration.
            suite: The distribution suite to sync.
            suite_idx: Index of the suite in the config list.
            total_suites: Total number of suites to sync.

        Returns:
            True if cancellation was detected and the caller should break.
        """
        suite_base = (suite_idx / total_suites) * 100
        suite_range = 100 / total_suites

        # Stage 1: Release (0-20% of suite range)
        self._progress.report(
            suite_base + suite_range * 0.0,
            f"Downloading Release file for {suite}",
        )
        release = await self._stage_release(config, suite)

        if release is None:
            return False

        if self._cancellation_token.is_cancelled:
            self._logger.info("Cancellation detected after release stage", session_id=self._session_id)
            return True

        # Stage 2: Indexes (20-50% of suite range)
        self._progress.report(suite_base + suite_range * 0.2, f"Downloading indexes for {suite}")
        entries = await self._stage_indexes(config, suite, release)

        if self._cancellation_token.is_cancelled:
            self._logger.info("Cancellation detected after indexes stage", session_id=self._session_id)
            return True

        # Stage 3: Artifacts (50-80% of suite range)
        self._progress.report(suite_base + suite_range * 0.5, f"Downloading artifacts for {suite}")
        await self._stage_artifacts(config, entries)

        if self._cancellation_token.is_cancelled:
            self._logger.info("Cancellation detected after artifacts stage", session_id=self._session_id)
            return True

        # Stage 4: Verify (80-95% of suite range)
        self._progress.report(suite_base + suite_range * 0.8, f"Verifying downloads for {suite}")
        # Verification is handled during download (SHA256 check)

        if self._cancellation_token.is_cancelled:
            self._logger.info("Cancellation detected after verify stage", session_id=self._session_id)
            return True

        # Stage 5: Publish (95-100% of suite range)
        self._progress.report(suite_base + suite_range * 0.95, f"Publishing snapshot for {suite}")
        await self._stage_publish(config)
        return False

    def _determine_sync_status(self) -> str:
        """Determine the final sync session status based on results.

        Returns:
            One of "cancelled", "partial", "failed", or "completed".
        """
        if self._cancellation_token.is_cancelled:
            return "cancelled"
        if self._result.files_failed > 0 and self._result.files_downloaded > 0:
            return "partial"
        if self._result.files_failed > 0:
            return "failed"
        return "completed"

    async def _persist_sync_session(
        self,
        config: RepositoryConfig,
        session_id: str,
        status: str,
        started_at: datetime,
    ) -> None:
        """Persist the SyncSession record for observability.

        Args:
            config: Repository configuration.
            session_id: Unique identifier for this sync session.
            status: Final status string.
            started_at: Timestamp when sync started.
        """
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
        except Exception as exc:  # pylint: disable=broad-exception-caught  # DB may raise non-SQLAlchemy errors
            self._logger.error(
                "Failed to persist sync session",
                session_id=session_id,
                error=str(exc),
            )

    def _log_sync_summary(
        self,
        config: RepositoryConfig,
        session_id: str,
        status: str,
        elapsed: float,
    ) -> None:
        """Emit the summary log entry for the completed sync.

        Args:
            config: Repository configuration.
            session_id: Unique identifier for this sync session.
            status: Final status string.
            elapsed: Elapsed wall-clock time in seconds.
        """
        total_processed = self._result.files_downloaded + self._result.files_skipped + self._result.files_failed
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

    async def _stage_release(self, config: RepositoryConfig, suite: str) -> ReleaseMetadata | None:
        """Download and parse Release file for a suite."""
        mirror_root = self._get_mirror_root(config)
        release, downloaded, failed = await _staging.stage_release(
            self._db_provider,
            self._download_coordinator,
            self._release_parser,
            self._logger,
            self._session_id,
            config=config,
            suite=suite,
            mirror_root=mirror_root,
            upsert_fn=self._upsert_repository_file,
        )
        self._result.files_downloaded += downloaded
        self._result.files_failed += failed
        return release

    async def _check_release_unchanged(self, url: str, cached_path: Path) -> bool:
        """Check if a cached Release file is still current via conditional request."""
        return await _staging.check_release_unchanged(
            self._db_provider,
            self._download_coordinator,
            url=url,
            cached_path=cached_path,
        )

    async def _parse_and_store_release(
        self,
        download_result: tuple[str, str, dict[str, str] | None],
        dest_path: Path,
    ) -> ReleaseMetadata | None:
        """Parse a downloaded Release file and persist it as a RepositoryFile."""
        release, downloaded, failed = await _staging.parse_and_store_release(
            self._release_parser,
            self._logger,
            self._session_id,
            download_result=download_result,
            dest_path=dest_path,
            upsert_fn=self._upsert_repository_file,
        )
        self._result.files_downloaded += downloaded
        self._result.files_failed += failed
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

        entries = self._deduplicate_entries(entries)
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

        # Build and execute download tasks
        tasks = self._build_download_tasks(to_download, base_url, mirror_root)
        results = await self._download_coordinator.download_batch(
            tasks=tasks,
            max_concurrent=self._download_coordinator.config.max_connections_per_repo,
        )

        # Process results and update state
        await self._process_artifact_results(to_download, tasks, results)

    def _deduplicate_entries(self, entries: list[FileEntry]) -> list[FileEntry]:
        """Remove duplicate entries by relative_path.

        Deduplication avoids concurrent downloads racing on the same file,
        which happens when arch-independent packages appear in multiple indexes.

        Args:
            entries: List of artifact file entries.

        Returns:
            Deduplicated list preserving first-seen order.
        """
        return _checksums.deduplicate_entries(
            entries,
            self._logger,
            self._session_id,
        )

    def _build_download_tasks(
        self,
        to_download: list[FileEntry],
        base_url: str,
        mirror_root: Path,
    ) -> list[DownloadTask]:
        """Build DownloadTask objects for artifact entries.

        Args:
            to_download: File entries to create download tasks for.
            base_url: Repository base URL.
            mirror_root: Local mirror root path.

        Returns:
            List of DownloadTask objects ready for batch download.
        """
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
        return tasks

    async def _process_artifact_results(
        self,
        to_download: list[FileEntry],
        tasks: list[DownloadTask],
        results: list[DownloadResult],
    ) -> None:
        """Process batch download results and update RepositoryFile state.

        Args:
            to_download: The file entries that were downloaded.
            tasks: The download tasks that were executed.
            results: Download results corresponding to each task.
        """
        succeeded: list[tuple[str, str, int]] = []
        failed_urls: list[str] = []

        for entry, task, result in zip(to_download, tasks, results, strict=True):
            if result.success:
                succeeded.append((task.url, str(task.dest_path), result.bytes_transferred))
                self._result.files_downloaded += 1
                self._result.bytes_transferred += result.bytes_transferred
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

        await self._batch_update_state(succeeded, RepositoryFileState.VERIFIED)
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
        """Download a Release/InRelease file and return its content."""
        return await _staging.download_release_file(
            self._download_coordinator,
            url=url,
            dest_path=dest_path,
        )

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
        except SQLAlchemyError:
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
        *,
        state: RepositoryFileState,
        local_path: str | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        """Create or update a RepositoryFile entity keyed by URL."""
        await _persistence.upsert_repository_file(
            self._db_provider,
            self._logger,
            self._session_id,
            url=url,
            sha256=sha256,
            size_bytes=size_bytes,
            state=state,
            local_path=local_path,
            etag=etag,
            last_modified=last_modified,
        )

    async def _batch_create_repository_files(
        self,
        config: RepositoryConfig,
        entries: list[FileEntry],
        state: RepositoryFileState,
    ) -> None:
        """Create RepositoryFile entities in batches of ≤500."""
        await _persistence.batch_create_repository_files(
            self._db_provider,
            self._logger,
            self._session_id,
            config=config,
            entries=entries,
            state=state,
        )

    async def _batch_update_state(
        self,
        succeeded: list[tuple[str, str, int]],
        state: RepositoryFileState,
    ) -> None:
        """Update RepositoryFile entities to a new state in batches."""
        await _persistence.batch_update_state(
            self._db_provider,
            self._logger,
            self._session_id,
            succeeded=succeeded,
            state=state,
        )

    async def _batch_mark_failed(self, failed_urls: list[str]) -> None:
        """Mark RepositoryFile entities as FAILED in batches."""
        await _persistence.batch_mark_failed(
            self._db_provider,
            self._logger,
            self._session_id,
            failed_urls=failed_urls,
        )

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
        return await _checksums.get_local_checksums(
            self._db_provider,
            config,
            suite,
            paths,
        )

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
        return await _checksums.get_artifact_checksums(
            self._db_provider,
            config,
            entries,
        )

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
