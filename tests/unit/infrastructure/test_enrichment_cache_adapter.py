"""Unit tests for EnrichmentCacheAdapter.

Validates cache retrieval, storage (insert and upsert), and graceful
error handling for the enrichment cache backed by SQLAlchemy.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from debcraft.domain.scanner.values import PackageEnrichment
from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.scanners.cache_adapter import EnrichmentCacheAdapter

pytestmark = [pytest.mark.unit]


async def _setup_db() -> async_sessionmaker[AsyncSession]:
    """Create an in-memory SQLite engine with all tables."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_get_returns_none_on_cache_miss() -> None:
    """get() returns None when no matching entry exists."""
    factory = await _setup_db()
    adapter = EnrichmentCacheAdapter(factory)

    result = await adapter.get("nonexistent", "1.0", "amd64", 1)
    assert result is None


@pytest.mark.asyncio
async def test_store_and_get_roundtrip() -> None:
    """Stored enrichment can be retrieved with matching keys."""
    factory = await _setup_db()
    adapter = EnrichmentCacheAdapter(factory)

    enrichment = PackageEnrichment(
        source_package="bash-src",
        maintainer="Maintainer <m@test.org>",
        homepage="https://www.gnu.org/software/bash/",
        depends="libc6 (>= 2.17)",
        section="shells",
        priority="required",
        description="GNU Bourne Again SHell",
        sha256="abcdef1234567890" * 4,
        download_url="https://deb.debian.org/pool/main/b/bash/bash_5.2.deb",
        purl="pkg:deb/debian/bash@5.2?arch=amd64",
        license_expressions=[("GPL-3.0-or-later", "declared"), ("MIT", "concluded")],
        local_deb_path="/var/cache/debcraft/bash_5.2_amd64.deb",
    )

    await adapter.store("bash", "5.2", "amd64", 42, enrichment)
    result = await adapter.get("bash", "5.2", "amd64", 42)

    assert result is not None
    assert result.source_package == "bash-src"
    assert result.maintainer == "Maintainer <m@test.org>"
    assert result.homepage == "https://www.gnu.org/software/bash/"
    assert result.depends == "libc6 (>= 2.17)"
    assert result.section == "shells"
    assert result.priority == "required"
    assert result.description == "GNU Bourne Again SHell"
    assert result.sha256 == "abcdef1234567890" * 4
    assert result.download_url == "https://deb.debian.org/pool/main/b/bash/bash_5.2.deb"
    assert result.purl == "pkg:deb/debian/bash@5.2?arch=amd64"
    assert result.license_expressions == [("GPL-3.0-or-later", "declared"), ("MIT", "concluded")]
    assert result.local_deb_path == "/var/cache/debcraft/bash_5.2_amd64.deb"


@pytest.mark.asyncio
async def test_get_returns_none_for_different_snapshot() -> None:
    """get() returns None when snapshot_id doesn't match stored entry."""
    factory = await _setup_db()
    adapter = EnrichmentCacheAdapter(factory)

    enrichment = PackageEnrichment(source_package="curl-src")
    await adapter.store("curl", "7.88", "amd64", 10, enrichment)

    # Different snapshot_id should miss
    result = await adapter.get("curl", "7.88", "amd64", 11)
    assert result is None


@pytest.mark.asyncio
async def test_store_upserts_existing_entry() -> None:
    """Storing with same key updates the existing row."""
    factory = await _setup_db()
    adapter = EnrichmentCacheAdapter(factory)

    enrichment_v1 = PackageEnrichment(
        source_package="pkg-src",
        description="Version 1",
        license_expressions=[("MIT", "declared")],
    )
    enrichment_v2 = PackageEnrichment(
        source_package="pkg-src-new",
        description="Version 2",
        license_expressions=[("Apache-2.0", "concluded")],
    )

    await adapter.store("pkg", "1.0", "amd64", 5, enrichment_v1)
    await adapter.store("pkg", "1.0", "amd64", 5, enrichment_v2)

    result = await adapter.get("pkg", "1.0", "amd64", 5)
    assert result is not None
    assert result.source_package == "pkg-src-new"
    assert result.description == "Version 2"
    assert result.license_expressions == [("Apache-2.0", "concluded")]


@pytest.mark.asyncio
async def test_store_with_empty_license_expressions() -> None:
    """Enrichment with no license expressions stores and retrieves correctly."""
    factory = await _setup_db()
    adapter = EnrichmentCacheAdapter(factory)

    enrichment = PackageEnrichment(
        source_package="simple-pkg",
        license_expressions=[],
    )

    await adapter.store("simple", "1.0", "all", 1, enrichment)
    result = await adapter.get("simple", "1.0", "all", 1)

    assert result is not None
    assert result.source_package == "simple-pkg"
    assert result.license_expressions == []


@pytest.mark.asyncio
async def test_store_with_none_fields() -> None:
    """Enrichment with all-None fields stores and retrieves correctly."""
    factory = await _setup_db()
    adapter = EnrichmentCacheAdapter(factory)

    enrichment = PackageEnrichment()

    await adapter.store("minimal", "0.1", "arm64", 3, enrichment)
    result = await adapter.get("minimal", "0.1", "arm64", 3)

    assert result is not None
    assert result.source_package is None
    assert result.maintainer is None
    assert result.homepage is None
    assert result.depends is None
    assert result.section is None
    assert result.priority is None
    assert result.description is None
    assert result.sha256 is None
    assert result.download_url is None
    assert result.purl is None
    assert result.license_expressions == []
    assert result.local_deb_path is None


@pytest.mark.asyncio
async def test_get_handles_db_error_gracefully() -> None:
    """get() returns None and logs warning when DB is unavailable."""
    engine = create_async_engine("sqlite+aiosqlite:///nonexistent/path/db.sqlite")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    adapter = EnrichmentCacheAdapter(factory)

    result = await adapter.get("pkg", "1.0", "amd64", 1)
    assert result is None


@pytest.mark.asyncio
async def test_store_handles_db_error_gracefully() -> None:
    """store() does not raise when DB is unavailable."""
    engine = create_async_engine("sqlite+aiosqlite:///nonexistent/path/db.sqlite")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    adapter = EnrichmentCacheAdapter(factory)

    enrichment = PackageEnrichment(source_package="test")
    # Should not raise
    await adapter.store("pkg", "1.0", "amd64", 1, enrichment)
