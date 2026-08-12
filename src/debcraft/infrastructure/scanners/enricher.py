"""Enriches identified packages with M3/M4/M5 metadata.

Cross-references identified packages against the metadata database
(PackageRepository, LicenseRepository) and enrichment cache to produce
EnrichedPackage values with optional PackageEnrichment data. Caches
results keyed by (name, version, architecture, snapshot_id).

In the current milestone (M6), the enricher implements cache lookups
but defers full repository queries to a future milestone when
PackageRepository and LicenseRepository are wired into the scanner
subsystem.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from debcraft.domain.scanner.values import EnrichedPackage

if TYPE_CHECKING:
    from debcraft.domain.scanner.values import IdentifiedPackage
    from debcraft.infrastructure.scanners.cache_adapter import EnrichmentCacheAdapter

logger = logging.getLogger(__name__)


class MetadataEnricher:
    """Cross-references identified packages against metadata database.

    Uses PackageRepository and LicenseRepository (resolved from
    WorkflowContext scope) for lookups. Generates PURL and download
    URLs when the respective M5 services are available. Caches
    enrichment results keyed by (name, version, arch, snapshot_id).

    In the current implementation, only cache lookups are performed.
    Cache misses result in None enrichment with a diagnostic noting
    that direct repository queries are not yet implemented.
    """

    def __init__(self, cache_adapter: EnrichmentCacheAdapter) -> None:
        """Initialize the enricher.

        Args:
            cache_adapter: Adapter for reading/writing enrichment cache.
        """
        self._cache = cache_adapter

    async def enrich(
        self,
        packages: list[IdentifiedPackage],
        snapshot_id: int,
    ) -> tuple[list[EnrichedPackage], list[str]]:
        """Enrich identified packages with metadata.

        For each package, checks the enrichment cache keyed by
        (name, version, architecture, snapshot_id). On cache hit,
        the cached PackageEnrichment is used. On cache miss, the
        package is returned with None enrichment and a diagnostic
        is recorded.

        Args:
            packages: List of identified packages to enrich.
            snapshot_id: The RepositorySnapshot ID to use for cache
                lookups. Pass 0 to skip enrichment entirely.

        Returns:
            Tuple of (enriched_packages, diagnostics) where
            enriched_packages contains one EnrichedPackage per input
            package, and diagnostics contains informational messages
            about cache misses or skipped enrichment.
        """
        diagnostics: list[str] = []

        enriched: list[EnrichedPackage] = []

        if snapshot_id == 0:
            diagnostics.append("No published RepositorySnapshot available; skipping metadata enrichment.")
            enriched = [EnrichedPackage(package=pkg, enrichment=None) for pkg in packages]
            return enriched, diagnostics

        for pkg in packages:
            try:
                cached = await self._cache.get(
                    package_name=pkg.name,
                    version=pkg.version,
                    architecture=pkg.architecture,
                    snapshot_id=snapshot_id,
                )
            except Exception as exc:  # pragma: no cover
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
            else:
                enriched.append(EnrichedPackage(package=pkg, enrichment=None))
                diagnostics.append(
                    f"Enrichment unavailable for {pkg.name} "
                    f"{pkg.version} ({pkg.architecture}): "
                    f"metadata lookup not yet implemented."
                )

        return enriched, diagnostics
