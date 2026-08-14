"""Enriches identified packages with M3/M4/M5 metadata.

Cross-references identified packages against the metadata database
(PackageRepository, LicenseRepository) and enrichment cache to produce
EnrichedPackage values with optional PackageEnrichment data. Caches
results keyed by (name, version, architecture, snapshot_id).

The enricher implements a three-tier fallback chain:
1. Enrichment cache (cache.db)
2. Metadata database (metadata.db) — PackageInstance + LicenseExpression query
3. Direct .deb extraction from ISO (when available)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from debcraft.domain.package_intelligence.errors import PURLGenerationError
from debcraft.domain.package_intelligence.purl_generator import generate_purl
from debcraft.domain.scanner.values import EnrichedPackage, PackageEnrichment
from debcraft.infrastructure.models.metadata import LicenseExpression, PackageInstance

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from debcraft.domain.scanner.values import IdentifiedPackage
    from debcraft.infrastructure.scanners.cache_adapter import EnrichmentCacheAdapter
    from debcraft.infrastructure.scanners.deb_extractor import DebExtractor

logger = logging.getLogger(__name__)


class MetadataEnricher:
    """Cross-references identified packages against metadata database.

    Uses the enrichment cache, metadata.db PackageInstance table, and
    optionally direct .deb extraction for lookups. Generates PURL and
    download URLs from metadata. Caches enrichment results keyed by
    (name, version, arch, snapshot_id).

    Fallback chain on cache miss:
    1. Query PackageInstance in metadata.db (when snapshot_id > 0)
    2. Direct .deb extraction from ISO (when deb_extractor is available)
    """

    def __init__(
        self,
        cache_adapter: EnrichmentCacheAdapter,
        metadata_session_factory: async_sessionmaker[AsyncSession] | None = None,
        deb_extractor: DebExtractor | None = None,
    ) -> None:
        """Initialize the enricher.

        Args:
            cache_adapter: Adapter for reading/writing enrichment cache.
            metadata_session_factory: Async session factory for metadata.db,
                or None if metadata.db is unavailable.
            deb_extractor: Optional extractor for .deb files from ISO,
                used as fallback when metadata.db lookup misses.
        """
        self._cache = cache_adapter
        self._metadata_session_factory = metadata_session_factory
        self._deb_extractor = deb_extractor

    async def enrich(
        self,
        packages: list[IdentifiedPackage],
        snapshot_id: int,
    ) -> tuple[list[EnrichedPackage], list[str]]:
        """Enrich identified packages with metadata.

        Implements a three-tier fallback chain for each package:

        When snapshot_id > 0:
          1. Check enrichment cache
          2. On cache miss, query metadata.db (PackageInstance)
          3. On metadata.db miss, fall back to .deb extraction (if deb_extractor available)
          4. If all fail → None enrichment + diagnostic

        When snapshot_id == 0:
          - If deb_extractor available → extract from .deb in ISO
          - If no deb_extractor → None enrichment + diagnostic

        Args:
            packages: List of identified packages to enrich.
            snapshot_id: The RepositorySnapshot ID to use for cache
                lookups. Pass 0 to skip cache/metadata.db enrichment.

        Returns:
            Tuple of (enriched_packages, diagnostics) where
            enriched_packages contains one EnrichedPackage per input
            package, and diagnostics contains informational messages
            about fallback paths taken.
        """
        diagnostics: list[str] = []
        enriched: list[EnrichedPackage] = []

        if snapshot_id == 0:
            return self._enrich_without_snapshot(packages, diagnostics)

        for pkg in packages:
            try:
                cached = await self._cache.get(
                    package_name=pkg.name,
                    version=pkg.version,
                    architecture=pkg.architecture,
                    snapshot_id=snapshot_id,
                )
            except (SQLAlchemyError, OSError) as exc:  # pragma: no cover
                logger.warning(
                    "Cache lookup failed for %s/%s/%s: %s",
                    pkg.name,
                    pkg.version,
                    pkg.architecture,
                    exc,
                )
                cached = None

            if cached is not None:
                enriched.append(EnrichedPackage(package=pkg, enrichment=cached))
                continue

            # Fallback tier 2: query metadata.db
            logger.debug(
                "Cache miss for %s %s (%s); querying metadata.db",
                pkg.name,
                pkg.version,
                pkg.architecture,
            )
            metadata_enrichment = await self._query_metadata_db(pkg, snapshot_id)
            if metadata_enrichment is not None:
                enriched.append(EnrichedPackage(package=pkg, enrichment=metadata_enrichment))
                continue

            # Fallback tier 3: direct .deb extraction from ISO
            if self._deb_extractor is not None:
                logger.debug(
                    "metadata.db miss for %s %s (%s); attempting .deb extraction from ISO",
                    pkg.name,
                    pkg.version,
                    pkg.architecture,
                )
                deb_enrichment = self._deb_extractor.extract_enrichment(pkg)
                if deb_enrichment is not None:
                    enriched.append(EnrichedPackage(package=pkg, enrichment=deb_enrichment))
                    diagnostics.append(
                        f"Enrichment for {pkg.name} {pkg.version} ({pkg.architecture}) "
                        f"sourced from direct .deb extraction (metadata.db had no match)."
                    )
                    continue

            # All fallback paths exhausted
            enriched.append(EnrichedPackage(package=pkg, enrichment=None))
            diagnostics.append(
                f"Enrichment unavailable for {pkg.name} "
                f"{pkg.version} ({pkg.architecture}): "
                f"no matching PackageInstance in metadata.db"
                + (" and .deb extraction failed." if self._deb_extractor else ".")
            )

        return enriched, diagnostics

    def _enrich_without_snapshot(
        self,
        packages: list[IdentifiedPackage],
        diagnostics: list[str],
    ) -> tuple[list[EnrichedPackage], list[str]]:
        """Enrich packages when snapshot_id == 0 (no RepositorySnapshot).

        When a deb_extractor is available, attempts direct .deb extraction
        from the ISO for each package. Otherwise, returns None enrichment
        with a diagnostic.

        Args:
            packages: List of identified packages to enrich.
            diagnostics: Mutable diagnostics list to append to.

        Returns:
            Tuple of (enriched_packages, diagnostics).
        """
        enriched: list[EnrichedPackage] = []

        if self._deb_extractor is None:
            diagnostics.append(
                "No published RepositorySnapshot available and no .deb extractor configured; "
                "skipping metadata enrichment."
            )
            enriched = [EnrichedPackage(package=pkg, enrichment=None) for pkg in packages]
            return enriched, diagnostics

        logger.debug(
            "snapshot_id == 0; using direct .deb extraction for %d packages",
            len(packages),
        )

        for pkg in packages:
            deb_enrichment = self._deb_extractor.extract_enrichment(pkg)
            if deb_enrichment is not None:
                enriched.append(EnrichedPackage(package=pkg, enrichment=deb_enrichment))
            else:
                enriched.append(EnrichedPackage(package=pkg, enrichment=None))
                diagnostics.append(
                    f"Enrichment unavailable for {pkg.name} {pkg.version} ({pkg.architecture}): "
                    f".deb not found or unparseable in ISO pool."
                )

        return enriched, diagnostics

    async def _query_metadata_db(
        self,
        pkg: IdentifiedPackage,
        snapshot_id: int,
    ) -> PackageEnrichment | None:
        """Query PackageInstance table for enrichment data.

        Looks up a matching PackageInstance by (name, version, arch, snapshot_id),
        uses the highest id on multiple matches, joins LicenseExpression records,
        and constructs a PackageEnrichment. On success, stores the result in the
        enrichment cache (swallowing store failures with a warning).

        Args:
            pkg: The identified package to look up.
            snapshot_id: The RepositorySnapshot ID to query against.

        Returns:
            A PackageEnrichment if a matching record is found, None otherwise.
        """
        if self._metadata_session_factory is None:
            return None

        try:
            async with self._metadata_session_factory() as session:
                # Query for the PackageInstance with highest id matching the criteria
                pi_stmt = (
                    select(PackageInstance)
                    .where(
                        PackageInstance.package_name == pkg.name,
                        PackageInstance.version == pkg.version,
                        PackageInstance.architecture == pkg.architecture,
                        PackageInstance.snapshot_id == snapshot_id,
                    )
                    .order_by(PackageInstance.id.desc())
                    .limit(1)
                )
                result = await session.execute(pi_stmt)
                pi_row = result.scalar_one_or_none()

                if pi_row is None:
                    logger.debug(
                        "No PackageInstance found for %s %s (%s) in snapshot %d",
                        pkg.name,
                        pkg.version,
                        pkg.architecture,
                        snapshot_id,
                    )
                    return None

                # Query associated LicenseExpression records
                le_stmt = select(LicenseExpression).where(
                    LicenseExpression.package_id == pi_row.id,
                )
                le_result = await session.execute(le_stmt)
                le_rows = le_result.scalars().all()

                license_expressions: list[tuple[str, str]] = [(le.expression, le.source) for le in le_rows]

        except SQLAlchemyError as exc:
            logger.warning(
                "metadata.db query failed for %s %s (%s): %s",
                pkg.name,
                pkg.version,
                pkg.architecture,
                exc,
            )
            return None

        # Generate PURL
        purl: str | None = None
        try:
            purl = generate_purl(pkg.name, pkg.version, pkg.architecture)
        except PURLGenerationError:
            logger.debug(
                "PURL generation failed for %s %s (%s)",
                pkg.name,
                pkg.version,
                pkg.architecture,
            )

        # Construct PackageEnrichment from the query result
        enrichment = PackageEnrichment(
            source_package=pi_row.source_package,
            maintainer=pi_row.maintainer,
            homepage=pi_row.homepage,
            depends=pi_row.depends,
            section=pi_row.section,
            priority=pi_row.priority,
            description=pi_row.description,
            sha256=pi_row.sha256,
            download_url=pi_row.download_url,
            purl=purl,
            license_expressions=license_expressions,
        )

        # Store in cache (swallow failures)
        try:
            await self._cache.store(
                package_name=pkg.name,
                version=pkg.version,
                architecture=pkg.architecture,
                snapshot_id=snapshot_id,
                enrichment=enrichment,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            logger.warning(
                "Failed to store enrichment in cache for %s %s (%s): %s",
                pkg.name,
                pkg.version,
                pkg.architecture,
                exc,
            )

        return enrichment
