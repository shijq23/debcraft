"""Unit tests for MetadataEnricher.

Validates enrichment behavior: cache hits return cached data,
cache misses produce None enrichment with diagnostics, and
snapshot_id 0 skips enrichment entirely.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from debcraft.domain.scanner.values import (
    IdentifiedPackage,
    PackageEnrichment,
)
from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.scanners.cache_adapter import EnrichmentCacheAdapter
from debcraft.infrastructure.scanners.enricher import MetadataEnricher

pytestmark = [pytest.mark.unit]


async def _setup_db() -> async_sessionmaker[AsyncSession]:
    """Create an in-memory SQLite engine with all tables."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def _make_package(
    name: str = "bash",
    version: str = "5.2",
    arch: str = "amd64",
    status: str = "installed",
) -> IdentifiedPackage:
    return IdentifiedPackage(name=name, version=version, architecture=arch, status=status)


@pytest.mark.asyncio
async def test_enrich_with_zero_snapshot_id_skips_enrichment() -> None:
    """When snapshot_id is 0, all packages get None enrichment and a diagnostic."""
    factory = await _setup_db()
    adapter = EnrichmentCacheAdapter(factory)
    enricher = MetadataEnricher(cache_adapter=adapter)

    packages = [_make_package("bash"), _make_package("curl", version="7.88")]
    enriched, diagnostics = await enricher.enrich(packages, snapshot_id=0)

    assert len(enriched) == 2
    assert all(ep.enrichment is None for ep in enriched)
    assert enriched[0].package.name == "bash"
    assert enriched[1].package.name == "curl"
    assert len(diagnostics) == 1
    assert "No published RepositorySnapshot" in diagnostics[0]


@pytest.mark.asyncio
async def test_enrich_empty_package_list() -> None:
    """Enriching an empty list returns empty results and no diagnostics."""
    factory = await _setup_db()
    adapter = EnrichmentCacheAdapter(factory)
    enricher = MetadataEnricher(cache_adapter=adapter)

    enriched, diagnostics = await enricher.enrich([], snapshot_id=42)

    assert enriched == []
    assert diagnostics == []


@pytest.mark.asyncio
async def test_enrich_cache_miss_returns_none_enrichment() -> None:
    """Cache miss produces EnrichedPackage with None enrichment and diagnostic."""
    factory = await _setup_db()
    adapter = EnrichmentCacheAdapter(factory)
    enricher = MetadataEnricher(cache_adapter=adapter)

    packages = [_make_package("unknown-pkg")]
    enriched, diagnostics = await enricher.enrich(packages, snapshot_id=10)

    assert len(enriched) == 1
    assert enriched[0].package.name == "unknown-pkg"
    assert enriched[0].enrichment is None
    assert len(diagnostics) == 1
    assert "unknown-pkg" in diagnostics[0]
    assert "metadata lookup not yet implemented" in diagnostics[0]


@pytest.mark.asyncio
async def test_enrich_cache_hit_returns_cached_enrichment() -> None:
    """Cache hit produces EnrichedPackage with cached enrichment, no diagnostic."""
    factory = await _setup_db()
    adapter = EnrichmentCacheAdapter(factory)
    enricher = MetadataEnricher(cache_adapter=adapter)

    # Pre-populate cache
    enrichment = PackageEnrichment(
        source_package="bash-src",
        maintainer="Dev <dev@example.org>",
        homepage="https://example.org",
        purl="pkg:deb/debian/bash@5.2?arch=amd64",
        license_expressions=[("GPL-3.0-or-later", "declared")],
    )
    await adapter.store("bash", "5.2", "amd64", 10, enrichment)

    packages = [_make_package("bash", "5.2", "amd64")]
    enriched, diagnostics = await enricher.enrich(packages, snapshot_id=10)

    assert len(enriched) == 1
    assert enriched[0].package.name == "bash"
    assert enriched[0].enrichment is not None
    assert enriched[0].enrichment.source_package == "bash-src"
    assert enriched[0].enrichment.purl == "pkg:deb/debian/bash@5.2?arch=amd64"
    assert enriched[0].enrichment.license_expressions == [("GPL-3.0-or-later", "declared")]
    # No diagnostics for cache hit
    assert diagnostics == []


@pytest.mark.asyncio
async def test_enrich_mixed_cache_hits_and_misses() -> None:
    """Mix of cache hits and misses produces correct enrichment per package."""
    factory = await _setup_db()
    adapter = EnrichmentCacheAdapter(factory)
    enricher = MetadataEnricher(cache_adapter=adapter)

    # Cache only bash
    enrichment = PackageEnrichment(source_package="bash-src")
    await adapter.store("bash", "5.2", "amd64", 7, enrichment)

    packages = [
        _make_package("bash", "5.2", "amd64"),
        _make_package("curl", "7.88", "amd64"),
    ]
    enriched, diagnostics = await enricher.enrich(packages, snapshot_id=7)

    assert len(enriched) == 2
    # bash: cache hit
    assert enriched[0].enrichment is not None
    assert enriched[0].enrichment.source_package == "bash-src"
    # curl: cache miss
    assert enriched[1].enrichment is None

    # Only one diagnostic for the cache miss
    assert len(diagnostics) == 1
    assert "curl" in diagnostics[0]


@pytest.mark.asyncio
async def test_enrich_preserves_package_order() -> None:
    """Output EnrichedPackage list preserves input package order."""
    factory = await _setup_db()
    adapter = EnrichmentCacheAdapter(factory)
    enricher = MetadataEnricher(cache_adapter=adapter)

    packages = [
        _make_package("zlib", "1.2"),
        _make_package("alpha", "0.1"),
        _make_package("beta", "2.0"),
    ]
    enriched, _ = await enricher.enrich(packages, snapshot_id=5)

    assert [ep.package.name for ep in enriched] == ["zlib", "alpha", "beta"]


@pytest.mark.asyncio
async def test_enrich_cache_miss_different_snapshot() -> None:
    """Cache entry for different snapshot_id is not returned."""
    factory = await _setup_db()
    adapter = EnrichmentCacheAdapter(factory)
    enricher = MetadataEnricher(cache_adapter=adapter)

    # Store for snapshot 10
    enrichment = PackageEnrichment(source_package="old-src")
    await adapter.store("pkg", "1.0", "amd64", 10, enrichment)

    # Query with snapshot 11 — should be a miss
    packages = [_make_package("pkg", "1.0", "amd64")]
    enriched, diagnostics = await enricher.enrich(packages, snapshot_id=11)

    assert enriched[0].enrichment is None
    assert len(diagnostics) == 1
    assert "metadata lookup not yet implemented" in diagnostics[0]


@pytest.mark.asyncio
async def test_enrich_handles_cache_error_gracefully() -> None:
    """When the cache adapter errors, enrichment returns None without crashing."""
    engine = create_async_engine("sqlite+aiosqlite:///nonexistent/path/db.sqlite")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    adapter = EnrichmentCacheAdapter(factory)
    enricher = MetadataEnricher(cache_adapter=adapter)

    packages = [_make_package("bash")]
    enriched, diagnostics = await enricher.enrich(packages, snapshot_id=1)

    # Should not crash; returns None enrichment
    assert len(enriched) == 1
    assert enriched[0].enrichment is None
    assert len(diagnostics) == 1
