"""SQLAlchemy-backed cache adapter for package enrichment metadata.

Stores and retrieves PackageEnrichment data keyed by
(package_name, version, architecture, snapshot_id), using the
CachedEnrichment model in cache.db. Cache failures are non-critical:
all database errors are caught, logged as warnings, and swallowed
so that the enrichment pipeline can proceed via direct repository queries.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from debcraft.domain.scanner.values import PackageEnrichment
from debcraft.infrastructure.models.cache import CachedEnrichment

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


class EnrichmentCacheAdapter:
    """SQLAlchemy adapter for caching package enrichment metadata.

    Persists PackageEnrichment objects in cache.db keyed by the composite
    key (package_name, version, architecture, snapshot_id). Entries are
    invalidated implicitly when a new snapshot_id is used for lookups.

    All database errors are handled gracefully — get() returns None and
    store() silently drops the write — so cache failures never interrupt
    the scanning pipeline.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Initialize the cache adapter.

        Args:
            session_factory: Async session factory for cache.db operations.
        """
        self._session_factory = session_factory

    async def get(
        self,
        package_name: str,
        version: str,
        architecture: str,
        snapshot_id: int,
    ) -> PackageEnrichment | None:
        """Retrieve cached enrichment for a package identity and snapshot.

        Args:
            package_name: The Debian package name.
            version: The package version string.
            architecture: The target architecture.
            snapshot_id: The RepositorySnapshot ID the enrichment was produced against.

        Returns:
            A PackageEnrichment value object if a cache hit is found, None otherwise.
        """
        try:
            async with self._session_factory() as session:
                stmt = select(CachedEnrichment).where(
                    CachedEnrichment.package_name == package_name,
                    CachedEnrichment.version == version,
                    CachedEnrichment.architecture == architecture,
                    CachedEnrichment.snapshot_id == snapshot_id,
                )
                result = await session.execute(stmt)
                row = result.scalar_one_or_none()

                if row is None:
                    return None

                return self._to_domain(row)
        except SQLAlchemyError as exc:
            logger.warning(
                "Enrichment cache read failed for %s/%s/%s (snapshot %d): %s",
                package_name,
                version,
                architecture,
                snapshot_id,
                exc,
            )
            return None

    async def store(
        self,
        package_name: str,
        version: str,
        architecture: str,
        snapshot_id: int,
        enrichment: PackageEnrichment,
    ) -> None:
        """Store enrichment metadata in the cache (upsert semantics).

        If an entry with the same (package_name, version, architecture, snapshot_id)
        already exists, it is updated in place. Otherwise a new row is inserted.

        Args:
            package_name: The Debian package name.
            version: The package version string.
            architecture: The target architecture.
            snapshot_id: The RepositorySnapshot ID the enrichment was produced against.
            enrichment: The PackageEnrichment value object to cache.
        """
        try:
            async with self._session_factory() as session:
                stmt = select(CachedEnrichment).where(
                    CachedEnrichment.package_name == package_name,
                    CachedEnrichment.version == version,
                    CachedEnrichment.architecture == architecture,
                    CachedEnrichment.snapshot_id == snapshot_id,
                )
                result = await session.execute(stmt)
                row = result.scalar_one_or_none()

                license_json = json.dumps(enrichment.license_expressions)

                if row is not None:
                    # Update existing entry
                    row.source_package = enrichment.source_package
                    row.maintainer = enrichment.maintainer
                    row.homepage = enrichment.homepage
                    row.depends = enrichment.depends
                    row.section = enrichment.section
                    row.priority = enrichment.priority
                    row.description = enrichment.description
                    row.sha256 = enrichment.sha256
                    row.download_url = enrichment.download_url
                    row.purl = enrichment.purl
                    row.license_expressions_json = license_json
                    row.local_deb_path = enrichment.local_deb_path
                else:
                    # Insert new entry
                    entry = CachedEnrichment(
                        package_name=package_name,
                        version=version,
                        architecture=architecture,
                        snapshot_id=snapshot_id,
                        source_package=enrichment.source_package,
                        maintainer=enrichment.maintainer,
                        homepage=enrichment.homepage,
                        depends=enrichment.depends,
                        section=enrichment.section,
                        priority=enrichment.priority,
                        description=enrichment.description,
                        sha256=enrichment.sha256,
                        download_url=enrichment.download_url,
                        purl=enrichment.purl,
                        license_expressions_json=license_json,
                        local_deb_path=enrichment.local_deb_path,
                    )
                    session.add(entry)

                await session.commit()
        except SQLAlchemyError as exc:
            logger.warning(
                "Enrichment cache write failed for %s/%s/%s (snapshot %d): %s",
                package_name,
                version,
                architecture,
                snapshot_id,
                exc,
            )

    @staticmethod
    def _to_domain(row: CachedEnrichment) -> PackageEnrichment:
        """Convert a database row to a domain PackageEnrichment value object."""
        license_expressions: list[tuple[str, str]] = []
        if row.license_expressions_json:
            raw = json.loads(row.license_expressions_json)
            license_expressions = [tuple(item) for item in raw]

        return PackageEnrichment(
            source_package=row.source_package,
            maintainer=row.maintainer,
            homepage=row.homepage,
            depends=row.depends,
            section=row.section,
            priority=row.priority,
            description=row.description,
            sha256=row.sha256,
            download_url=row.download_url,
            purl=row.purl,
            license_expressions=license_expressions,
            local_deb_path=row.local_deb_path,
        )
