"""MirrorWorkflow implementing the mirror synchronization lifecycle.

Concrete Workflow that orchestrates the full mirror synchronization pipeline
using the M1 platform infrastructure (WorkflowEngine, CancellationToken,
ProgressReporter, EventBus).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import update

from debcraft.infrastructure.mirror.config_reader import ConfigReader
from debcraft.infrastructure.mirror.download import DownloadCoordinator
from debcraft.infrastructure.mirror.engine import MirrorEngine, SyncResult
from debcraft.infrastructure.mirror.events import (
    MirrorSyncCompletedEvent,
    MirrorSyncFailedEvent,
    MirrorSyncStartedEvent,
)
from debcraft.infrastructure.models.mirror import RepositoryFile, RepositoryFileState
from debcraft.platform.contracts.persistence import DatabaseProvider
from debcraft.platform.contracts.storage import StorageEngine
from debcraft.platform.contracts.workflow import Workflow, WorkflowContext

if TYPE_CHECKING:
    from debcraft.domain.mirror.config import RepositoryConfig


class MirrorWorkflow(Workflow):
    """Concrete Workflow implementing the mirror synchronization lifecycle.

    Resolves dependencies from the WorkflowContext's DI scope, reads
    configuration, iterates over configured repositories calling
    MirrorEngine.sync_repository() for each, and handles per-repository
    isolation so that one failure does not stop others.

    Publishes MirrorSyncStartedEvent at begin, MirrorSyncCompletedEvent
    or MirrorSyncFailedEvent at end. Checks CancellationToken between
    repositories and performs state rollback on cancellation.
    """

    @property
    def name(self) -> str:
        """The workflow name.

        Returns:
            The string identifier "mirror-sync".
        """
        return "mirror-sync"

    async def execute(self, context: WorkflowContext) -> None:
        """Execute the full mirror synchronization pipeline.

        Stages:
        1. Resolve dependencies and load configuration
        2. For each repository: sync via MirrorEngine (Release → Index →
           Artifact → Verify → Publish)
        3. Report summary

        Checks CancellationToken between each repository. One repository
        failure does not prevent synchronization of others.

        Args:
            context: The execution context providing services and utilities.
        """
        # Resolve dependencies from DI scope
        config_reader = context.scope.resolve(ConfigReader)
        download_coordinator = context.scope.resolve(DownloadCoordinator)
        db_provider = context.scope.resolve(DatabaseProvider)
        storage_engine = context.scope.resolve(StorageEngine)

        # Read configuration
        config = config_reader.read()

        context.logger.info(
            "Mirror workflow execution started",
            workflow="mirror-sync",
            repositories=len(config.repositories),
            repository_names=",".join(r.name for r in config.repositories),
        )
        workflow_start = time.monotonic()

        # Start download coordinator (initializes aiohttp session)
        await download_coordinator.start()
        try:
            await self._sync_all_repositories(
                context=context,
                config_repositories=config.repositories,
                download_coordinator=download_coordinator,
                db_provider=db_provider,
                storage_engine=storage_engine,
            )
        finally:
            await download_coordinator.close()

        elapsed = time.monotonic() - workflow_start
        context.logger.info(
            "Mirror workflow execution completed",
            workflow="mirror-sync",
            elapsed_seconds=round(elapsed, 2),
        )

        # On cancellation: rollback state transitions
        if context.cancellation_token.is_cancelled:
            await self._rollback_cancellation_state(context, db_provider)

    async def _sync_all_repositories(
        self,
        context: WorkflowContext,
        config_repositories: list[RepositoryConfig],
        download_coordinator: DownloadCoordinator,
        db_provider: DatabaseProvider,
        storage_engine: StorageEngine,
    ) -> None:
        """Iterate over repositories, syncing each with isolation.

        Publishes started/completed/failed events and handles per-repository
        error isolation.

        Args:
            context: The workflow execution context.
            config_repositories: List of repository configurations to sync.
            download_coordinator: The download coordinator instance.
            db_provider: Database provider for session access.
            storage_engine: Storage engine for path resolution.
        """
        total_results = SyncResult()
        failed_repositories: list[str] = []

        for repo_config in config_repositories:
            # Check cancellation between repositories
            if context.cancellation_token.is_cancelled:
                context.logger.info("Cancellation detected, stopping repository iteration")
                break

            session_id = str(uuid4())

            # Publish started event
            await context.event_bus.publish(
                MirrorSyncStartedEvent(
                    repository_name=repo_config.name,
                    session_id=session_id,
                    suites=tuple(repo_config.suites),
                )
            )

            try:
                engine = MirrorEngine(
                    download_coordinator=download_coordinator,
                    db_provider=db_provider,
                    storage_engine=storage_engine,
                    event_bus=context.event_bus,
                    cancellation_token=context.cancellation_token,
                    progress=context.progress,
                    logger=context.logger,
                )
                result = await engine.sync_repository(repo_config, session_id)

                # Accumulate results
                total_results.files_downloaded += result.files_downloaded
                total_results.files_skipped += result.files_skipped
                total_results.files_failed += result.files_failed
                total_results.bytes_transferred += result.bytes_transferred

                # Publish completed event for this repository
                await context.event_bus.publish(
                    MirrorSyncCompletedEvent(
                        repository_name=repo_config.name,
                        session_id=session_id,
                        files_downloaded=result.files_downloaded,
                        files_skipped=result.files_skipped,
                        files_failed=result.files_failed,
                        bytes_transferred=result.bytes_transferred,
                    )
                )

            except Exception as exc:
                # Per-repository isolation: one failure doesn't stop others
                failed_repositories.append(repo_config.name)
                context.logger.error(
                    "Repository synchronization failed",
                    repository=repo_config.name,
                    session_id=session_id,
                    error=str(exc),
                )
                await context.event_bus.publish(
                    MirrorSyncFailedEvent(
                        repository_name=repo_config.name,
                        session_id=session_id,
                        error_message=str(exc),
                    )
                )

        # Log summary
        context.logger.info(
            "Mirror synchronization summary",
            files_downloaded=total_results.files_downloaded,
            files_skipped=total_results.files_skipped,
            files_failed=total_results.files_failed,
            bytes_transferred=total_results.bytes_transferred,
            failed_repositories=",".join(failed_repositories) if failed_repositories else "",
            repositories_synced=len(config_repositories) - len(failed_repositories),
            repositories_failed=len(failed_repositories),
        )

    async def _rollback_cancellation_state(
        self,
        context: WorkflowContext,
        db_provider: DatabaseProvider,
    ) -> None:
        """Rollback state transitions on cancellation.

        Transitions:
        - QUEUED → DISCOVERED
        - DOWNLOADING → QUEUED (preserving .part files on disk)
        - DOWNLOADED stays as-is (verification can proceed next session)

        Args:
            context: The workflow execution context.
            db_provider: Database provider for session access.
        """
        context.logger.info("Rolling back state transitions due to cancellation")

        try:
            session = await db_provider.get_session("mirror")
            async with session.begin():
                # Order matters: QUEUED → DISCOVERED first, then
                # DOWNLOADING → QUEUED, so that entities transitioning
                # from DOWNLOADING don't also get moved to DISCOVERED.

                # QUEUED → DISCOVERED
                await session.execute(
                    update(RepositoryFile)
                    .where(RepositoryFile.state == RepositoryFileState.QUEUED)
                    .values(
                        state=RepositoryFileState.DISCOVERED,
                        updated_at=datetime.now(UTC),
                    )
                )

                # DOWNLOADING → QUEUED (preserving .part files for resumption)
                await session.execute(
                    update(RepositoryFile)
                    .where(RepositoryFile.state == RepositoryFileState.DOWNLOADING)
                    .values(
                        state=RepositoryFileState.QUEUED,
                        updated_at=datetime.now(UTC),
                    )
                )

            context.logger.info("Cancellation state rollback completed successfully")
        except Exception as exc:
            # Req 9.5: If commit fails, log at ERROR and exit without
            # modifying Part_Files — next session will detect and recover
            context.logger.error(
                "Failed to commit cancellation state rollback",
                error=str(exc),
            )
