"""Repository implementation for RepositorySnapshot entities in metadata.db."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from debcraft.infrastructure.errors import ImmutableEntityError
from debcraft.infrastructure.models.metadata import RepositorySnapshot
from debcraft.infrastructure.repositories.base import SqlAlchemyRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class SnapshotRepository(SqlAlchemyRepository[RepositorySnapshot]):
    """Repository for managing RepositorySnapshot entities.

    Overrides update and delete to enforce immutability on published
    snapshots. A published snapshot cannot be modified or removed.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the SnapshotRepository.

        Args:
            session: The SQLAlchemy async session for database operations.
        """
        super().__init__(session, RepositorySnapshot)

    @override
    async def update(self, entity: RepositorySnapshot) -> RepositorySnapshot:
        """Persist modifications to a snapshot.

        Checks whether the snapshot is published before allowing the update.

        Args:
            entity: The modified snapshot entity.

        Returns:
            The merged snapshot entity.

        Raises:
            ImmutableEntityError: If the snapshot has been published.
        """
        existing = await self.get_by_id(entity.id)
        if existing.published:
            raise ImmutableEntityError(
                entity_type="RepositorySnapshot",
                entity_id=entity.id,
            )
        return await super().update(entity)

    @override
    async def delete(self, entity_id: int) -> None:
        """Remove a snapshot by surrogate key.

        Checks whether the snapshot is published before allowing deletion.

        Args:
            entity_id: The primary key of the snapshot to remove.

        Raises:
            ImmutableEntityError: If the snapshot has been published.
        """
        existing = await self.get_by_id(entity_id)
        if existing.published:
            raise ImmutableEntityError(
                entity_type="RepositorySnapshot",
                entity_id=entity_id,
            )
        await super().delete(entity_id)
