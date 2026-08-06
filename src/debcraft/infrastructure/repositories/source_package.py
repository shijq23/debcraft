"""Repository implementation for SourcePackage entities in metadata.db."""

from __future__ import annotations

from typing import TYPE_CHECKING

from debcraft.infrastructure.models.metadata import SourcePackage
from debcraft.infrastructure.repositories.base import SqlAlchemyRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class SourcePackageRepository(SqlAlchemyRepository[SourcePackage]):
    """Repository for managing SourcePackage entities.

    Provides standard CRUD operations for Debian source packages.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the SourcePackageRepository.

        Args:
            session: The SQLAlchemy async session for database operations.
        """
        super().__init__(session, SourcePackage)
