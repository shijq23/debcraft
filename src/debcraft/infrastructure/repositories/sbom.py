"""Repository implementation for SBOMDocument entities in metadata.db."""

from __future__ import annotations

from typing import TYPE_CHECKING

from debcraft.infrastructure.models.scan import SBOMDocument
from debcraft.infrastructure.repositories.base import SqlAlchemyRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class SBOMRepository(SqlAlchemyRepository[SBOMDocument]):
    """Repository for managing SBOMDocument entities.

    Provides standard CRUD operations for generated Software Bill
    of Materials documents.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the SBOMRepository.

        Args:
            session: The SQLAlchemy async session for database operations.
        """
        super().__init__(session, SBOMDocument)
