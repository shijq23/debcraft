"""Repository implementation for PackageInstance entities in metadata.db."""

from __future__ import annotations

from typing import TYPE_CHECKING

from debcraft.infrastructure.errors import EntityNotFoundError
from debcraft.infrastructure.models.metadata import PackageInstance
from debcraft.infrastructure.repositories.base import SqlAlchemyRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class PackageRepository(SqlAlchemyRepository[PackageInstance]):
    """Repository for managing PackageInstance entities.

    Provides domain-specific queries including lookup by the natural key
    combination of package_name, version, architecture, and filename.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the PackageRepository.

        Args:
            session: The SQLAlchemy async session for database operations.
        """
        super().__init__(session, PackageInstance)

    async def get_by_natural_key(
        self,
        package_name: str,
        version: str,
        architecture: str,
        filename: str,
    ) -> PackageInstance:
        """Lookup a package by its natural key combination.

        Args:
            package_name: The name of the package.
            version: The version string of the package.
            architecture: The target architecture (e.g. "amd64", "arm64").
            filename: The filename of the package file.

        Returns:
            The matching PackageInstance entity.

        Raises:
            EntityNotFoundError: If no entity matches the natural key.
        """
        results = await self.find(
            package_name=package_name,
            version=version,
            architecture=architecture,
            filename=filename,
        )
        if not results:
            key_value = (
                f"package_name={package_name!r}, version={version!r}, "
                f"architecture={architecture!r}, filename={filename!r}"
            )
            raise EntityNotFoundError(
                entity_type="PackageInstance",
                key_name="natural_key",
                key_value=key_value,
            )
        return results[0]
