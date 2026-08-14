"""Property-based tests for enrichment cache equivalence.

**Validates: Requirements 17.1, 17.2, 17.3, 17.5**

Property 12: Cache Equivalence — For any identified package where a cache entry
exists with a snapshot_id matching the current latest published RepositorySnapshot ID,
the cached PackageEnrichment SHALL be byte-equivalent to the enrichment that would be
produced by a fresh query against the PackageRepository and LicenseRepository for the
same package.

Tests verify:
1. store(name, ver, arch, snap, enrichment) → get(name, ver, arch, snap) returns
   an enrichment where all fields are equal to the original.
2. Different snapshot_ids produce independent cache entries (store with snap=1,
   get with snap=2 → None).
3. Upsert: store twice with same key → get returns latest stored value.
"""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from debcraft.domain.scanner.values import PackageEnrichment
from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.models.cache import CachedEnrichment  # noqa: F401
from debcraft.infrastructure.scanners.cache_adapter import EnrichmentCacheAdapter

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------


def _safe_text(min_size: int = 1, max_size: int = 30) -> st.SearchStrategy[str]:
    """Generate safe non-empty strings suitable for DB columns."""
    return st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N"),
            blacklist_characters="\x00",
        ),
        min_size=min_size,
        max_size=max_size,
    )


def _optional_safe_text(max_size: int = 50) -> st.SearchStrategy[str | None]:
    """Generate optional text fields (None or non-empty string)."""
    return st.one_of(st.none(), _safe_text(min_size=1, max_size=max_size))


def _sha256_hex() -> st.SearchStrategy[str]:
    """Generate valid 64-character hex strings representing SHA256 hashes."""
    return st.text(
        alphabet="0123456789abcdef",
        min_size=64,
        max_size=64,
    )


def _license_expressions() -> st.SearchStrategy[list[tuple[str, str]]]:
    """Generate list of (spdx_expression, source_algorithm) tuples."""
    return st.lists(
        st.tuples(
            _safe_text(min_size=1, max_size=20),
            _safe_text(min_size=1, max_size=15),
        ),
        min_size=0,
        max_size=5,
    )


@st.composite
def st_package_enrichment(draw: st.DrawFn) -> PackageEnrichment:
    """Generate a random PackageEnrichment with various field combinations."""
    return PackageEnrichment(
        source_package=draw(_optional_safe_text()),
        maintainer=draw(_optional_safe_text()),
        homepage=draw(_optional_safe_text()),
        depends=draw(_optional_safe_text()),
        section=draw(_optional_safe_text(max_size=20)),
        priority=draw(_optional_safe_text(max_size=15)),
        description=draw(_optional_safe_text()),
        sha256=draw(st.one_of(st.none(), _sha256_hex())),
        download_url=draw(_optional_safe_text()),
        purl=draw(_optional_safe_text()),
        license_expressions=draw(_license_expressions()),
        local_deb_path=draw(_optional_safe_text()),
    )


def _snapshot_id() -> st.SearchStrategy[int]:
    """Generate valid snapshot IDs (positive integers)."""
    return st.integers(min_value=1, max_value=2**31 - 1)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _setup_engine() -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    """Create in-memory SQLite engine with all tables and return factory + engine."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


def _enrichments_equal(a: PackageEnrichment, b: PackageEnrichment) -> bool:
    """Compare two PackageEnrichment objects field-by-field."""
    return (
        a.source_package == b.source_package
        and a.maintainer == b.maintainer
        and a.homepage == b.homepage
        and a.depends == b.depends
        and a.section == b.section
        and a.priority == b.priority
        and a.description == b.description
        and a.sha256 == b.sha256
        and a.download_url == b.download_url
        and a.purl == b.purl
        and a.license_expressions == b.license_expressions
        and a.local_deb_path == b.local_deb_path
    )


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.storage
class TestCacheEquivalenceStoreGet:
    """Property 12a: store then get returns equal enrichment.

    After store(name, ver, arch, snap, enrichment) → get(name, ver, arch, snap)
    returns an enrichment where all fields are equal to the original.
    """

    @given(
        package_name=_safe_text(),
        version=_safe_text(),
        architecture=_safe_text(max_size=10),
        snapshot_id=_snapshot_id(),
        enrichment=st_package_enrichment(),
    )
    def test_store_then_get_returns_equal_enrichment(
        self,
        package_name: str,
        version: str,
        architecture: str,
        snapshot_id: int,
        enrichment: PackageEnrichment,
    ) -> None:
        """Cached enrichment is byte-equivalent to the stored enrichment.

        **Validates: Requirements 17.1, 17.2, 17.3, 17.5**
        """

        async def _run() -> None:
            factory, engine = await _setup_engine()
            try:
                adapter = EnrichmentCacheAdapter(factory)

                await adapter.store(
                    package_name=package_name,
                    version=version,
                    architecture=architecture,
                    snapshot_id=snapshot_id,
                    enrichment=enrichment,
                )

                retrieved = await adapter.get(
                    package_name=package_name,
                    version=version,
                    architecture=architecture,
                    snapshot_id=snapshot_id,
                )

                assert retrieved is not None, "Expected cache hit, got None"
                assert _enrichments_equal(enrichment, retrieved), (
                    f"Stored enrichment does not match retrieved.\nStored: {enrichment}\nRetrieved: {retrieved}"
                )
            finally:
                await engine.dispose()

        asyncio.run(_run())


@pytest.mark.unit
@pytest.mark.storage
class TestCacheEquivalenceSnapshotIsolation:
    """Property 12b: Different snapshot_ids produce independent cache entries.

    Store with snap=X, get with snap=Y (Y != X) → None.
    """

    @given(
        package_name=_safe_text(),
        version=_safe_text(),
        architecture=_safe_text(max_size=10),
        snapshot_id_store=_snapshot_id(),
        snapshot_id_get=_snapshot_id(),
        enrichment=st_package_enrichment(),
    )
    def test_different_snapshot_ids_are_independent(
        self,
        package_name: str,
        version: str,
        architecture: str,
        snapshot_id_store: int,
        snapshot_id_get: int,
        enrichment: PackageEnrichment,
    ) -> None:
        """Getting with a different snapshot_id returns None.

        **Validates: Requirements 17.2, 17.3, 17.5**
        """
        # Only test when snapshot IDs differ
        if snapshot_id_store == snapshot_id_get:
            return

        async def _run() -> None:
            factory, engine = await _setup_engine()
            try:
                adapter = EnrichmentCacheAdapter(factory)

                await adapter.store(
                    package_name=package_name,
                    version=version,
                    architecture=architecture,
                    snapshot_id=snapshot_id_store,
                    enrichment=enrichment,
                )

                retrieved = await adapter.get(
                    package_name=package_name,
                    version=version,
                    architecture=architecture,
                    snapshot_id=snapshot_id_get,
                )

                assert retrieved is None, (
                    f"Expected None for different snapshot_id "
                    f"(stored={snapshot_id_store}, queried={snapshot_id_get}), "
                    f"got {retrieved}"
                )
            finally:
                await engine.dispose()

        asyncio.run(_run())


@pytest.mark.unit
@pytest.mark.storage
class TestCacheEquivalenceUpsert:
    """Property 12c: Upsert semantics — store twice with same key returns latest.

    Store enrichment_1, then store enrichment_2 with same key → get returns
    enrichment_2.
    """

    @given(
        package_name=_safe_text(),
        version=_safe_text(),
        architecture=_safe_text(max_size=10),
        snapshot_id=_snapshot_id(),
        enrichment_first=st_package_enrichment(),
        enrichment_second=st_package_enrichment(),
    )
    def test_upsert_returns_latest_stored_value(
        self,
        package_name: str,
        version: str,
        architecture: str,
        snapshot_id: int,
        enrichment_first: PackageEnrichment,
        enrichment_second: PackageEnrichment,
    ) -> None:
        """Store twice with same key, get returns the latest value.

        **Validates: Requirements 17.1, 17.2, 17.5**
        """

        async def _run() -> None:
            factory, engine = await _setup_engine()
            try:
                adapter = EnrichmentCacheAdapter(factory)

                # Store first enrichment
                await adapter.store(
                    package_name=package_name,
                    version=version,
                    architecture=architecture,
                    snapshot_id=snapshot_id,
                    enrichment=enrichment_first,
                )

                # Overwrite with second enrichment (same key)
                await adapter.store(
                    package_name=package_name,
                    version=version,
                    architecture=architecture,
                    snapshot_id=snapshot_id,
                    enrichment=enrichment_second,
                )

                retrieved = await adapter.get(
                    package_name=package_name,
                    version=version,
                    architecture=architecture,
                    snapshot_id=snapshot_id,
                )

                assert retrieved is not None, "Expected cache hit, got None"
                assert _enrichments_equal(enrichment_second, retrieved), (
                    f"Expected latest stored enrichment after upsert.\nExpected: {enrichment_second}\nGot: {retrieved}"
                )
            finally:
                await engine.dispose()

        asyncio.run(_run())
