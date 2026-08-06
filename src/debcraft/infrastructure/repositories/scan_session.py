"""Repository implementation for ScanSession entities in metadata.db."""

from __future__ import annotations

from typing import TYPE_CHECKING

from debcraft.infrastructure.models.scan import ScanSession
from debcraft.infrastructure.repositories.base import SqlAlchemyRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ScanSessionRepository(SqlAlchemyRepository[ScanSession]):
    """Repository for managing ScanSession entities.

    Provides standard CRUD operations for scan session records.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the ScanSessionRepository.

        Args:
            session: The SQLAlchemy async session for database operations.
        """
        super().__init__(session, ScanSession)
