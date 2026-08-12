"""Integration tests for EnrichmentCacheAdapter SQLAlchemy persistence.

Verifies that:
1. Storing enrichment → retrieving same enrichment (all fields match).
2. Storing with snapshot_id=1 → retrieving with snapshot_id=2 returns None (invalidation).
3. Storing twice with same key → retrieving returns latest (upsert).
4. Retrieving a non-existent entry → returns None (cache miss).
5. Storing with all fields populated (including license_expressions list).
6. Storing with minimal fields (all None except required) → retrieves correctly.
7. Integration with MetadataEnricher: cache hit flows through to enriched packages.

Requirements: 17.1, 17.2, 17.3, 17.7
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from debcraft.domain.scanner.values import IdentifiedPackage, PackageEnrichment
from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.scanners.cache_adapter import EnrichmentCacheAdapter
from debcraft.infrastructure.scanners.enricher import MetadataEnricher


async def _create_session_factory(
    tmp_path: Path,
) -> async_sessionmaker[AsyncSession]:
    """Create a real SQLite file-based engine with all tables and return session factory."""
    db_path = tmp_path / "test_cache.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def _make_full_enrichment() -> PackageEnrichment:
    """Create a PackageEnrichment with all fields populated."""
    return PackageEnrichment(
        source_package="libfoo-src",
        maintainer="Test Dev <dev@example.com>",
        homepage="https://example.com/libfoo",
        depends="libc6 (>= 2.17), libssl3 (>= 3.0.0)",
        section="libs",
        priority="optional",
        description="A test library for unit testing",
        sha256="a" * 64,
        download_url="https://deb.debian.org/debian/pool/main/l/libfoo/libfoo_1.2.3-1_amd64.deb",
        purl="pkg:deb/debian/libfoo@1.2.3-1?arch=amd64",
        license_expressions=[
            ("MIT", "scancode"),
            ("Apache-2.0 OR MIT", "dep5"),
        ],
        local_deb_path="/var/cache/debcraft/pool/libfoo_1.2.3-1_amd64.deb",
    )


def _make_minimal_enrichment() -> PackageEnrichment:
    """Create a PackageEnrichment with only default (None/empty) fields."""
    return PackageEnrichment()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_store_and_retrieve_enrichment(tmp_path: Path) -> None:
    """Store enrichment → retrieve same enrichment with all fields matching.

    Validates: Requirements 17.1, 17.2
    """
    factory = await _create_session_factory(tmp_path)
    adapter = EnrichmentCacheAdapter(factory)
    enrichment = _make_full_enrichment()

    await adapter.store(
        package_name="libfoo",
        version="1.2.3-1",
        architecture="amd64",
        snapshot_id=1,
        enrichment=enrichment,
    )

    retrieved = await adapter.get(
        package_name="libfoo",
        version="1.2.3-1",
        architecture="amd64",
        snapshot_id=1,
    )

    assert retrieved is not None
    assert retrieved.source_package == enrichment.source_package
    assert retrieved.maintainer == enrichment.maintainer
    assert retrieved.homepage == enrichment.homepage
    assert retrieved.depends == enrichment.depends
    assert retrieved.section == enrichment.section
    assert retrieved.priority == enrichment.priority
    assert retrieved.description == enrichment.description
    assert retrieved.sha256 == enrichment.sha256
    assert retrieved.download_url == enrichment.download_url
    assert retrieved.purl == enrichment.purl
    assert retrieved.license_expressions == enrichment.license_expressions
    assert retrieved.local_deb_path == enrichment.local_deb_path


@pytest.mark.integration
@pytest.mark.asyncio
async def test_snapshot_invalidation(tmp_path: Path) -> None:
    """Store with snapshot_id=1 → retrieve with snapshot_id=2 returns None.

    Validates: Requirement 17.3
    """
    factory = await _create_session_factory(tmp_path)
    adapter = EnrichmentCacheAdapter(factory)
    enrichment = _make_full_enrichment()

    await adapter.store(
        package_name="libfoo",
        version="1.2.3-1",
        architecture="amd64",
        snapshot_id=1,
        enrichment=enrichment,
    )

    # Different snapshot_id should return None (cache invalidation)
    retrieved = await adapter.get(
        package_name="libfoo",
        version="1.2.3-1",
        architecture="amd64",
        snapshot_id=2,
    )

    assert retrieved is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upsert_returns_latest(tmp_path: Path) -> None:
    """Store twice with same key → retrieve returns latest data (upsert semantics).

    Validates: Requirement 17.1
    """
    factory = await _create_session_factory(tmp_path)
    adapter = EnrichmentCacheAdapter(factory)

    original = PackageEnrichment(
        source_package="libfoo-src",
        maintainer="Original Dev <orig@example.com>",
        homepage="https://example.com/original",
        description="Original description",
    )
    updated = PackageEnrichment(
        source_package="libfoo-src",
        maintainer="Updated Dev <updated@example.com>",
        homepage="https://example.com/updated",
        description="Updated description",
        purl="pkg:deb/debian/libfoo@1.2.3-1?arch=amd64",
    )

    await adapter.store(
        package_name="libfoo",
        version="1.2.3-1",
        architecture="amd64",
        snapshot_id=1,
        enrichment=original,
    )

    await adapter.store(
        package_name="libfoo",
        version="1.2.3-1",
        architecture="amd64",
        snapshot_id=1,
        enrichment=updated,
    )

    retrieved = await adapter.get(
        package_name="libfoo",
        version="1.2.3-1",
        architecture="amd64",
        snapshot_id=1,
    )

    assert retrieved is not None
    assert retrieved.maintainer == "Updated Dev <updated@example.com>"
    assert retrieved.homepage == "https://example.com/updated"
    assert retrieved.description == "Updated description"
    assert retrieved.purl == "pkg:deb/debian/libfoo@1.2.3-1?arch=amd64"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cache_miss_returns_none(tmp_path: Path) -> None:
    """Retrieve non-existent entry → returns None (cache miss fallthrough).

    Validates: Requirement 17.7
    """
    factory = await _create_session_factory(tmp_path)
    adapter = EnrichmentCacheAdapter(factory)

    retrieved = await adapter.get(
        package_name="nonexistent-pkg",
        version="0.0.1",
        architecture="amd64",
        snapshot_id=1,
    )

    assert retrieved is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_all_fields_populated_roundtrip(tmp_path: Path) -> None:
    """Store with all fields populated including license_expressions list.

    Validates: Requirement 17.1
    """
    factory = await _create_session_factory(tmp_path)
    adapter = EnrichmentCacheAdapter(factory)
    enrichment = _make_full_enrichment()

    await adapter.store(
        package_name="libbar",
        version="2.0.0-1",
        architecture="arm64",
        snapshot_id=5,
        enrichment=enrichment,
    )

    retrieved = await adapter.get(
        package_name="libbar",
        version="2.0.0-1",
        architecture="arm64",
        snapshot_id=5,
    )

    assert retrieved is not None
    # Verify license_expressions list preserved with tuples
    assert len(retrieved.license_expressions) == 2
    assert retrieved.license_expressions[0] == ("MIT", "scancode")
    assert retrieved.license_expressions[1] == ("Apache-2.0 OR MIT", "dep5")
    # Verify all other fields
    assert retrieved.source_package == "libfoo-src"
    assert retrieved.maintainer == "Test Dev <dev@example.com>"
    assert retrieved.homepage == "https://example.com/libfoo"
    assert retrieved.depends == "libc6 (>= 2.17), libssl3 (>= 3.0.0)"
    assert retrieved.section == "libs"
    assert retrieved.priority == "optional"
    assert retrieved.description == "A test library for unit testing"
    assert retrieved.sha256 == "a" * 64
    assert retrieved.download_url == ("https://deb.debian.org/debian/pool/main/l/libfoo/libfoo_1.2.3-1_amd64.deb")
    assert retrieved.purl == "pkg:deb/debian/libfoo@1.2.3-1?arch=amd64"
    assert retrieved.local_deb_path == "/var/cache/debcraft/pool/libfoo_1.2.3-1_amd64.deb"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_minimal_fields_roundtrip(tmp_path: Path) -> None:
    """Store with minimal fields (all None except required) → retrieve correctly.

    Validates: Requirement 17.1
    """
    factory = await _create_session_factory(tmp_path)
    adapter = EnrichmentCacheAdapter(factory)
    enrichment = _make_minimal_enrichment()

    await adapter.store(
        package_name="minimal-pkg",
        version="0.1.0",
        architecture="all",
        snapshot_id=1,
        enrichment=enrichment,
    )

    retrieved = await adapter.get(
        package_name="minimal-pkg",
        version="0.1.0",
        architecture="all",
        snapshot_id=1,
    )

    assert retrieved is not None
    assert retrieved.source_package is None
    assert retrieved.maintainer is None
    assert retrieved.homepage is None
    assert retrieved.depends is None
    assert retrieved.section is None
    assert retrieved.priority is None
    assert retrieved.description is None
    assert retrieved.sha256 is None
    assert retrieved.download_url is None
    assert retrieved.purl is None
    assert retrieved.license_expressions == []
    assert retrieved.local_deb_path is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_enricher_cache_hit_flows_through(tmp_path: Path) -> None:
    """Integration with MetadataEnricher: cache hit flows through to enriched packages.

    Validates: Requirements 17.2, 17.7
    """
    factory = await _create_session_factory(tmp_path)
    adapter = EnrichmentCacheAdapter(factory)
    enricher = MetadataEnricher(cache_adapter=adapter)

    # Pre-populate cache with enrichment for a package
    enrichment = _make_full_enrichment()
    await adapter.store(
        package_name="libfoo",
        version="1.2.3-1",
        architecture="amd64",
        snapshot_id=10,
        enrichment=enrichment,
    )

    # Create identified packages — one with cache hit, one with cache miss
    packages = [
        IdentifiedPackage(
            name="libfoo",
            version="1.2.3-1",
            architecture="amd64",
            status="installed",
        ),
        IdentifiedPackage(
            name="libmissing",
            version="0.0.1",
            architecture="amd64",
            status="installed",
        ),
    ]

    enriched_packages, diagnostics = await enricher.enrich(
        packages=packages,
        snapshot_id=10,
    )

    # Verify cache hit for libfoo
    assert len(enriched_packages) == 2
    assert enriched_packages[0].package.name == "libfoo"
    assert enriched_packages[0].enrichment is not None
    assert enriched_packages[0].enrichment.source_package == "libfoo-src"
    assert enriched_packages[0].enrichment.purl == "pkg:deb/debian/libfoo@1.2.3-1?arch=amd64"
    assert enriched_packages[0].enrichment.license_expressions == [
        ("MIT", "scancode"),
        ("Apache-2.0 OR MIT", "dep5"),
    ]

    # Verify cache miss for libmissing
    assert enriched_packages[1].package.name == "libmissing"
    assert enriched_packages[1].enrichment is None

    # Verify diagnostics include cache miss message
    assert any("libmissing" in d for d in diagnostics)
