"""Snapshot publisher for repository mirroring.

Publishes atomic RepositorySnapshots after successful synchronization.
Creates a snapshot entity in metadata.db with all state transitions
persisted in a single transaction to guarantee consistency.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from debcraft.infrastructure.mirror.events import MirrorSyncFailedEvent, SnapshotPublishedEvent
from debcraft.infrastructure.models.metadata import RepositorySnapshot

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from debcraft.platform.contracts.events import EventBus
    from debcraft.platform.contracts.persistence import DatabaseProvider

logger = logging.getLogger(__name__)


class SnapshotPublisher:
    """Publishes atomic RepositorySnapshots after successful sync.

    Coordinates the creation of a RepositorySnapshot entity in metadata.db,
    ensuring that the snapshot creation and published=True flag are persisted
    in a single database transaction. On success, publishes a domain event
    through the EventBus.
    """

    def __init__(
        self,
        db_provider: DatabaseProvider,
        event_bus: EventBus,
    ) -> None:
        """Initialize the SnapshotPublisher.

        Args:
            db_provider: Provider for database sessions (metadata.db).
            event_bus: Event bus for publishing domain events.
        """
        self._db_provider = db_provider
        self._event_bus = event_bus

    async def publish_snapshot(
        self,
        repository_id: int,
        verified_file_count: int,
        failed_file_count: int,
    ) -> RepositorySnapshot | None:
        """Create and publish a RepositorySnapshot atomically.

        If no verified files exist, returns None and publishes a failure event.
        Otherwise, creates a snapshot with published=True in a single transaction.

        Args:
            repository_id: The ID of the repository this snapshot belongs to.
            verified_file_count: Number of verified files in this sync session.
            failed_file_count: Number of failed files in this sync session.

        Returns:
            The published RepositorySnapshot, or None if no verified files exist.
        """
        if verified_file_count == 0:
            logger.warning(
                "No verified files for repository_id=%d, skipping snapshot publication",
                repository_id,
            )
            await self._event_bus.publish(
                MirrorSyncFailedEvent(
                    repository_name="",
                    session_id="",
                    error_message="No verified files available for snapshot",
                    files_failed=failed_file_count,
                )
            )
            return None

        session = await self._db_provider.get_session("metadata")
        try:
            await session.begin()

            # Get the current schema version from _migration_history
            schema_version = await self._get_schema_version(session)

            # Create the snapshot with published=False initially
            snapshot = RepositorySnapshot(
                repository_id=repository_id,
                schema_version=schema_version,
                captured_at=datetime.now(UTC),
                published=False,
            )
            session.add(snapshot)
            await session.flush()

            # Set published=True within the same transaction
            snapshot.published = True
            await session.flush()

            # Commit the entire transaction atomically
            await session.commit()

            logger.info(
                "Published snapshot id=%d for repository_id=%d with %d verified files",
                snapshot.id,
                repository_id,
                verified_file_count,
            )

            # Publish domain event after successful commit
            await self._event_bus.publish(
                SnapshotPublishedEvent(
                    snapshot_id=snapshot.id,
                    repository_name="",
                    captured_at=snapshot.captured_at.isoformat(),
                    verified_file_count=verified_file_count,
                    failed_file_count=failed_file_count,
                )
            )

            return snapshot

        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to publish snapshot for repository_id=%d",
                repository_id,
            )
            raise
        finally:
            await session.close()

    @staticmethod
    async def _get_schema_version(session: AsyncSession) -> int:
        """Query the highest applied migration version from metadata.db.

        Args:
            session: The active database session.

        Returns:
            The highest version number, or 0 if no migrations have been applied
            or if the _migration_history table does not exist.
        """
        try:
            result = await session.execute(text("SELECT MAX(version) FROM _migration_history"))
            row = result.scalar()
            return row if row is not None else 0
        except OperationalError as exc:
            if "no such table" in str(exc):
                return 0
            raise
