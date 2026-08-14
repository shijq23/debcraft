"""Unit tests for the _sbom_db module: DatabaseEngines and resolve_snapshot_id.

Tests cover:
- resolve_snapshot_id with explicit ID, None session factory, published/unpublished snapshots
- DatabaseEngines creation and disposal
- Cache schema initialization on missing cache.db

Requirements: 1.1, 1.3, 6.1, 6.2, 6.3, 6.4, 6.5
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.models.metadata import Repository, RepositorySnapshot

pytestmark = [pytest.mark.unit]


async def _setup_metadata_db(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    """Create an in-memory metadata.db with the RepositorySnapshot table."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory


async def _seed_snapshots(
    factory: async_sessionmaker[AsyncSession],
    snapshots: list[tuple[int, bool]],
) -> None:
    """Seed RepositorySnapshot records. Each tuple is (id, published)."""
    from datetime import UTC, datetime

    async with factory() as session:
        # First create a repository to satisfy the FK
        await session.execute(
            insert(Repository).values(
                id=1,
                name="test-repo",
                base_url="http://example.com",
                suite="bookworm",
                component="main",
            )
        )
        await session.commit()

    async with factory() as session:
        for snap_id, published in snapshots:
            await session.execute(
                insert(RepositorySnapshot).values(
                    id=snap_id,
                    repository_id=1,
                    schema_version=1,
                    captured_at=datetime.now(UTC),
                    published=published,
                )
            )
        await session.commit()


# ---------------------------------------------------------------------------
# resolve_snapshot_id tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestResolveSnapshotId:
    """Tests for resolve_snapshot_id function."""

    async def test_explicit_id_returned_directly(self) -> None:
        """When explicit_id is provided, it is returned without querying."""
        from debcraft.cli._sbom_db import resolve_snapshot_id

        result = await resolve_snapshot_id(session_factory=None, explicit_id=42)
        assert result == 42

    async def test_none_session_factory_returns_zero(self) -> None:
        """When session_factory is None and no explicit_id, returns 0."""
        from debcraft.cli._sbom_db import resolve_snapshot_id

        result = await resolve_snapshot_id(session_factory=None, explicit_id=None)
        assert result == 0

    async def test_returns_highest_published_id(self, tmp_path: Path) -> None:
        """Returns the highest ID among published snapshots."""
        from debcraft.cli._sbom_db import resolve_snapshot_id

        factory = await _setup_metadata_db(tmp_path)
        await _seed_snapshots(factory, [(1, True), (5, True), (3, True), (10, False)])

        result = await resolve_snapshot_id(session_factory=factory, explicit_id=None)
        assert result == 5

    async def test_no_published_snapshots_returns_zero(self, tmp_path: Path) -> None:
        """When no published snapshots exist, returns 0."""
        from debcraft.cli._sbom_db import resolve_snapshot_id

        factory = await _setup_metadata_db(tmp_path)
        await _seed_snapshots(factory, [(1, False), (2, False)])

        result = await resolve_snapshot_id(session_factory=factory, explicit_id=None)
        assert result == 0

    async def test_empty_db_returns_zero(self, tmp_path: Path) -> None:
        """When the database has no snapshots at all, returns 0."""
        from debcraft.cli._sbom_db import resolve_snapshot_id

        factory = await _setup_metadata_db(tmp_path)

        result = await resolve_snapshot_id(session_factory=factory, explicit_id=None)
        assert result == 0

    async def test_explicit_id_overrides_db(self, tmp_path: Path) -> None:
        """Explicit ID takes precedence even when DB has published snapshots."""
        from debcraft.cli._sbom_db import resolve_snapshot_id

        factory = await _setup_metadata_db(tmp_path)
        await _seed_snapshots(factory, [(1, True), (5, True)])

        result = await resolve_snapshot_id(session_factory=factory, explicit_id=99)
        assert result == 99


# ---------------------------------------------------------------------------
# DatabaseEngines tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDatabaseEngines:
    """Tests for DatabaseEngines dataclass."""

    async def test_dispose_with_none_engines(self) -> None:
        """dispose() succeeds when engines are None."""
        from debcraft.cli._sbom_db import DatabaseEngines

        engines = DatabaseEngines(
            metadata_engine=None,
            cache_engine=None,
            metadata_session_factory=None,
            cache_session_factory=None,
        )
        # Should not raise
        await engines.dispose()

    async def test_dispose_disposes_real_engines(self) -> None:
        """dispose() calls dispose on real engines."""
        from debcraft.cli._sbom_db import DatabaseEngines

        engine1 = create_async_engine("sqlite+aiosqlite:///:memory:")
        engine2 = create_async_engine("sqlite+aiosqlite:///:memory:")

        engines = DatabaseEngines(
            metadata_engine=engine1,
            cache_engine=engine2,
            metadata_session_factory=None,
            cache_session_factory=None,
        )
        await engines.dispose()
        # Engines should be disposed (pool closed)
        # Accessing pool after dispose is possible but pool is invalidated


# ---------------------------------------------------------------------------
# create_database_engines tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCreateDatabaseEngines:
    """Tests for create_database_engines function."""

    async def test_missing_metadata_db_returns_none_metadata(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When metadata.db doesn't exist, metadata fields are None."""
        from debcraft.cli._sbom_db import create_database_engines

        # Point XDG paths to tmp dirs where no metadata.db exists
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

        engines = await create_database_engines()
        try:
            assert engines.metadata_engine is None
            assert engines.metadata_session_factory is None
        finally:
            await engines.dispose()

    async def test_cache_db_created_on_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When cache.db doesn't exist, it is created with schema."""
        from debcraft.cli._sbom_db import create_database_engines

        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

        engines = await create_database_engines()
        try:
            assert engines.cache_engine is not None
            assert engines.cache_session_factory is not None
            # cache.db file should have been created
            cache_db_path = tmp_path / "cache" / "debcraft" / "cache" / "cache.db"
            assert cache_db_path.exists()
        finally:
            await engines.dispose()

    async def test_metadata_db_exists_creates_engine(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When metadata.db exists, engine and session factory are created."""
        from debcraft.cli._sbom_db import create_database_engines

        data_dir = tmp_path / "data" / "debcraft"
        data_dir.mkdir(parents=True)
        (data_dir / "metadata.db").write_bytes(b"")  # Empty file is enough for engine creation

        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

        engines = await create_database_engines()
        try:
            assert engines.metadata_engine is not None
            assert engines.metadata_session_factory is not None
        finally:
            await engines.dispose()
