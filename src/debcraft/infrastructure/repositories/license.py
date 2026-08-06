"""Repository implementation for LicenseExpression entities in metadata.db."""

from __future__ import annotations

from typing import TYPE_CHECKING

from debcraft.infrastructure.models.metadata import LicenseExpression
from debcraft.infrastructure.repositories.base import SqlAlchemyRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class LicenseRepository(SqlAlchemyRepository[LicenseExpression]):
    """Repository for managing LicenseExpression entities.

    Provides standard CRUD operations for SPDX license expressions
    associated with package instances.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the LicenseRepository.

        Args:
            session: The SQLAlchemy async session for database operations.
        """
        super().__init__(session, LicenseExpression)
