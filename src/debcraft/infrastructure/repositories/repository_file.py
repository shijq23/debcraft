"""Repository implementation for RepositoryFile entities in mirror.db."""

from __future__ import annotations

from typing import TYPE_CHECKING

from debcraft.infrastructure.models.mirror import RepositoryFile, RepositoryFileState
from debcraft.infrastructure.repositories.base import SqlAlchemyRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class RepositoryFileRepository(SqlAlchemyRepository[RepositoryFile]):
    """Repository for managing RepositoryFile entities.

    Provides domain-specific queries on top of the generic CRUD operations,
    including lookup by lifecycle state.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the RepositoryFileRepository.

        Args:
            session: The SQLAlchemy async session for database operations.
        """
        super().__init__(session, RepositoryFile)

    async def find_by_state(self, state: RepositoryFileState) -> list[RepositoryFile]:
        """Find all repository files in a specific lifecycle state.

        Args:
            state: The lifecycle state to filter by.

        Returns:
            A list of RepositoryFile entities matching the given state,
            or an empty list if none match.
        """
        return await self.find(state=state)
