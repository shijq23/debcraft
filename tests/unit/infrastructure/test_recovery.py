"""Unit tests for recovery mechanisms in DefaultStorageEngine.

Verifies:
- Download recovery transitions DOWNLOADING → QUEUED with retry_count < 3
- Download recovery transitions DOWNLOADING → FAILED with retry_count >= 3
- Cache integrity removes mismatched files
- cache.db recreation on missing file

Requirements: 7.1, 7.3, 7.5, 10.5
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Import scan models to ensure all relationships resolve correctly
import debcraft.infrastructure.models.scan  # noqa: F401
from debcraft.infrastructure.database.unit_of_work import SqliteUnitOfWork
from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.models.mirror import RepositoryFile, RepositoryFileState
from debcraft.platform.contracts.persistence import DatabaseProvider


async def _create_mirror_db() -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    """Create an in-memory SQLite engine with mirror.db tables."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


def _make_mock_db_provider(
    session_factory: async_sessionmaker[AsyncSession],
) -> DatabaseProvider:
    """Create a mock DatabaseProvider that returns sessions from the factory."""
    mock = MagicMock(spec=DatabaseProvider)

    async def _get_session(db_name: str) -> AsyncSession:
        return session_factory()

    mock.get_session = _get_session
    return mock


def _make_mock_provider(mirror_dir: Path | None = None) -> AsyncMock:
    """Create a mock StorageProvider with all methods configured."""
    provider = AsyncMock()
    provider.resolve_path = MagicMock()
    provider.resolve_path.side_effect = lambda purpose, relative="": (
        mirror_dir if purpose == "mirror" and mirror_dir is not None else Path(f"/fake/{purpose}")
    )
    provider.create_directory = AsyncMock()
    provider.remove_matching = AsyncMock()
    provider.check_writable = AsyncMock(return_value=True)
    return provider


def _make_mock_event_bus() -> AsyncMock:
    """Create a mock EventBus."""
    event_bus = AsyncMock()
    event_bus.publish = AsyncMock()
    return event_bus


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestDownloadRecoveryToQueued:
    """Test download recovery transitions DOWNLOADING → QUEUED with retry_count < 3."""

    @pytest.mark.asyncio
    async def test_retry_count_zero_transitions_to_queued(self) -> None:
        """Entry with retry_count=0 should transition to QUEUED with retry_count=1."""
        factory, engine = await _create_mirror_db()
        provider = _make_mock_db_provider(factory)

        try:
            # Pre-populate with a DOWNLOADING entry
            async with factory() as session, session.begin():
                entry = RepositoryFile(
                    url="http://example.com/file1.deb",
                    sha256="a" * 64,
                    size_bytes=1024,
                    state=RepositoryFileState.DOWNLOADING,
                    retry_count=0,
                )
                session.add(entry)

            # Run recovery via the engine's method
            from debcraft.infrastructure.storage.engine import DefaultStorageEngine

            mock_storage_provider = _make_mock_provider()
            mock_event_bus = _make_mock_event_bus()
            storage_engine = DefaultStorageEngine(
                provider=mock_storage_provider,
                event_bus=mock_event_bus,
                db_provider=provider,
            )
            await storage_engine._recover_interrupted_downloads()

            # Verify the entry transitioned to QUEUED with retry_count=1
            async with SqliteUnitOfWork(provider, "mirror") as uow:
                entries = await uow.repository_files.find_by_state(RepositoryFileState.QUEUED)
                assert len(entries) == 1
                assert entries[0].retry_count == 1
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_retry_count_one_transitions_to_queued(self) -> None:
        """Entry with retry_count=1 should transition to QUEUED with retry_count=2."""
        factory, engine = await _create_mirror_db()
        provider = _make_mock_db_provider(factory)

        try:
            async with factory() as session, session.begin():
                entry = RepositoryFile(
                    url="http://example.com/file2.deb",
                    sha256="b" * 64,
                    size_bytes=2048,
                    state=RepositoryFileState.DOWNLOADING,
                    retry_count=1,
                )
                session.add(entry)

            from debcraft.infrastructure.storage.engine import DefaultStorageEngine

            mock_storage_provider = _make_mock_provider()
            mock_event_bus = _make_mock_event_bus()
            storage_engine = DefaultStorageEngine(
                provider=mock_storage_provider,
                event_bus=mock_event_bus,
                db_provider=provider,
            )
            await storage_engine._recover_interrupted_downloads()

            async with SqliteUnitOfWork(provider, "mirror") as uow:
                entries = await uow.repository_files.find_by_state(RepositoryFileState.QUEUED)
                assert len(entries) == 1
                assert entries[0].retry_count == 2
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_retry_count_two_transitions_to_queued(self) -> None:
        """Entry with retry_count=2 should transition to QUEUED with retry_count=3."""
        factory, engine = await _create_mirror_db()
        provider = _make_mock_db_provider(factory)

        try:
            async with factory() as session, session.begin():
                entry = RepositoryFile(
                    url="http://example.com/file3.deb",
                    sha256="c" * 64,
                    size_bytes=4096,
                    state=RepositoryFileState.DOWNLOADING,
                    retry_count=2,
                )
                session.add(entry)

            from debcraft.infrastructure.storage.engine import DefaultStorageEngine

            mock_storage_provider = _make_mock_provider()
            mock_event_bus = _make_mock_event_bus()
            storage_engine = DefaultStorageEngine(
                provider=mock_storage_provider,
                event_bus=mock_event_bus,
                db_provider=provider,
            )
            await storage_engine._recover_interrupted_downloads()

            async with SqliteUnitOfWork(provider, "mirror") as uow:
                entries = await uow.repository_files.find_by_state(RepositoryFileState.QUEUED)
                assert len(entries) == 1
                assert entries[0].retry_count == 3
        finally:
            await engine.dispose()


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestDownloadRecoveryToFailed:
    """Test download recovery transitions DOWNLOADING → FAILED with retry_count >= 3."""

    @pytest.mark.asyncio
    async def test_retry_count_three_transitions_to_failed(self) -> None:
        """Entry with retry_count=3 should transition to FAILED."""
        factory, engine = await _create_mirror_db()
        provider = _make_mock_db_provider(factory)

        try:
            async with factory() as session, session.begin():
                entry = RepositoryFile(
                    url="http://example.com/file4.deb",
                    sha256="d" * 64,
                    size_bytes=8192,
                    state=RepositoryFileState.DOWNLOADING,
                    retry_count=3,
                )
                session.add(entry)

            from debcraft.infrastructure.storage.engine import DefaultStorageEngine

            mock_storage_provider = _make_mock_provider()
            mock_event_bus = _make_mock_event_bus()
            storage_engine = DefaultStorageEngine(
                provider=mock_storage_provider,
                event_bus=mock_event_bus,
                db_provider=provider,
            )
            await storage_engine._recover_interrupted_downloads()

            async with SqliteUnitOfWork(provider, "mirror") as uow:
                entries = await uow.repository_files.find_by_state(RepositoryFileState.FAILED)
                assert len(entries) == 1
                # retry_count remains at 3 (not incremented further)
                assert entries[0].retry_count == 3
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_retry_count_five_transitions_to_failed(self) -> None:
        """Entry with retry_count=5 (well above threshold) should transition to FAILED."""
        factory, engine = await _create_mirror_db()
        provider = _make_mock_db_provider(factory)

        try:
            async with factory() as session, session.begin():
                entry = RepositoryFile(
                    url="http://example.com/file5.deb",
                    sha256="e" * 64,
                    size_bytes=16384,
                    state=RepositoryFileState.DOWNLOADING,
                    retry_count=5,
                )
                session.add(entry)

            from debcraft.infrastructure.storage.engine import DefaultStorageEngine

            mock_storage_provider = _make_mock_provider()
            mock_event_bus = _make_mock_event_bus()
            storage_engine = DefaultStorageEngine(
                provider=mock_storage_provider,
                event_bus=mock_event_bus,
                db_provider=provider,
            )
            await storage_engine._recover_interrupted_downloads()

            async with SqliteUnitOfWork(provider, "mirror") as uow:
                entries = await uow.repository_files.find_by_state(RepositoryFileState.FAILED)
                assert len(entries) == 1
                assert entries[0].retry_count == 5
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_mixed_retry_counts_recovers_correctly(self) -> None:
        """Multiple entries with different retry counts are handled correctly."""
        factory, engine = await _create_mirror_db()
        provider = _make_mock_db_provider(factory)

        try:
            async with factory() as session, session.begin():
                # Should transition to QUEUED (retry_count < 3)
                session.add(
                    RepositoryFile(
                        url="http://example.com/low-retry.deb",
                        sha256="f" * 64,
                        size_bytes=100,
                        state=RepositoryFileState.DOWNLOADING,
                        retry_count=1,
                    )
                )
                # Should transition to FAILED (retry_count >= 3)
                session.add(
                    RepositoryFile(
                        url="http://example.com/high-retry.deb",
                        sha256="0" * 64,
                        size_bytes=200,
                        state=RepositoryFileState.DOWNLOADING,
                        retry_count=4,
                    )
                )

            from debcraft.infrastructure.storage.engine import DefaultStorageEngine

            mock_storage_provider = _make_mock_provider()
            mock_event_bus = _make_mock_event_bus()
            storage_engine = DefaultStorageEngine(
                provider=mock_storage_provider,
                event_bus=mock_event_bus,
                db_provider=provider,
            )
            await storage_engine._recover_interrupted_downloads()

            async with SqliteUnitOfWork(provider, "mirror") as uow:
                queued = await uow.repository_files.find_by_state(RepositoryFileState.QUEUED)
                failed = await uow.repository_files.find_by_state(RepositoryFileState.FAILED)
                assert len(queued) == 1
                assert queued[0].retry_count == 2
                assert len(failed) == 1
                assert failed[0].retry_count == 4
        finally:
            await engine.dispose()


async def _create_all_tables_db() -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    """Create an in-memory SQLite engine with ALL tables (mirror + cache)."""
    from debcraft.infrastructure.models.cache import (  # noqa: F401
        ChecksumCache,
        NormalizedLicense,
        ParsedDep5,
    )

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestCacheIntegrityRemovesMismatchedFiles:
    """Test cache integrity verification removes files with mismatched SHA256."""

    @pytest.mark.asyncio
    async def test_mismatched_file_is_removed(self, tmp_path: Path) -> None:
        """File with SHA256 mismatch against mirror.db entry should be removed."""
        # Use a single in-memory DB with all tables for both "mirror" and "cache"
        factory, engine = await _create_all_tables_db()
        provider = _make_mock_db_provider(factory)

        try:
            # Create a file in the mirror directory
            mirror_dir = tmp_path / "mirror"
            mirror_dir.mkdir()
            file_path = mirror_dir / "package.deb"
            file_path.write_bytes(b"actual content on disk")

            # Compute the real SHA256 of the file
            actual_sha256 = hashlib.sha256(b"actual content on disk").hexdigest()
            # Store a DIFFERENT sha256 in the database (simulating mismatch)
            stored_sha256 = "0" * 64

            async with factory() as session, session.begin():
                entry = RepositoryFile(
                    url="http://example.com/package.deb",
                    sha256=stored_sha256,
                    size_bytes=len(b"actual content on disk"),
                    state=RepositoryFileState.DOWNLOADED,
                    retry_count=0,
                    local_path=str(file_path),
                )
                session.add(entry)

            from debcraft.infrastructure.storage.engine import DefaultStorageEngine

            mock_storage_provider = _make_mock_provider(mirror_dir=mirror_dir)
            mock_event_bus = _make_mock_event_bus()
            storage_engine = DefaultStorageEngine(
                provider=mock_storage_provider,
                event_bus=mock_event_bus,
                db_provider=provider,
            )
            await storage_engine._verify_cache_integrity()

            # The mismatched file should have been removed
            assert not file_path.exists(), (
                f"File with SHA256 mismatch should be removed: actual={actual_sha256}, stored={stored_sha256}"
            )
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_matching_file_is_not_removed(self, tmp_path: Path) -> None:
        """File with matching SHA256 should NOT be removed."""
        factory, engine = await _create_all_tables_db()
        provider = _make_mock_db_provider(factory)

        try:
            mirror_dir = tmp_path / "mirror"
            mirror_dir.mkdir()
            file_content = b"correct content"
            file_path = mirror_dir / "good-package.deb"
            file_path.write_bytes(file_content)

            # Compute the real SHA256 and store it correctly in the DB
            correct_sha256 = hashlib.sha256(file_content).hexdigest()

            async with factory() as session, session.begin():
                entry = RepositoryFile(
                    url="http://example.com/good-package.deb",
                    sha256=correct_sha256,
                    size_bytes=len(file_content),
                    state=RepositoryFileState.DOWNLOADED,
                    retry_count=0,
                    local_path=str(file_path),
                )
                session.add(entry)

            from debcraft.infrastructure.storage.engine import DefaultStorageEngine

            mock_storage_provider = _make_mock_provider(mirror_dir=mirror_dir)
            mock_event_bus = _make_mock_event_bus()
            storage_engine = DefaultStorageEngine(
                provider=mock_storage_provider,
                event_bus=mock_event_bus,
                db_provider=provider,
            )
            await storage_engine._verify_cache_integrity()

            # The matching file should still exist
            assert file_path.exists(), "File with matching SHA256 should NOT be removed"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_file_without_db_entry_is_not_removed(self, tmp_path: Path) -> None:
        """File with no corresponding entry in mirror.db should be left alone."""
        factory, engine = await _create_all_tables_db()
        provider = _make_mock_db_provider(factory)

        try:
            mirror_dir = tmp_path / "mirror"
            mirror_dir.mkdir()
            orphan_file = mirror_dir / "orphan.deb"
            orphan_file.write_bytes(b"orphan data")

            # No entry in mirror.db for this file

            from debcraft.infrastructure.storage.engine import DefaultStorageEngine

            mock_storage_provider = _make_mock_provider(mirror_dir=mirror_dir)
            mock_event_bus = _make_mock_event_bus()
            storage_engine = DefaultStorageEngine(
                provider=mock_storage_provider,
                event_bus=mock_event_bus,
                db_provider=provider,
            )
            await storage_engine._verify_cache_integrity()

            # The orphan file should still exist (no db entry to compare against)
            assert orphan_file.exists(), "File with no DB entry should NOT be removed"
        finally:
            await engine.dispose()


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestCacheDbRecreation:
    """Test cache.db recreation on missing file (Requirement 10.5)."""

    @pytest.mark.asyncio
    async def test_missing_cache_db_is_recreated_on_initialization(self, tmp_path: Path) -> None:
        """When cache.db is missing, StorageEngine initialization recreates it without error.

        This tests that the initialization process handles a missing cache.db
        gracefully — the SqliteDatabaseProvider creates a new empty cache.db
        when it does not exist.
        """
        from sqlalchemy import text

        from debcraft.infrastructure.database.provider import SqliteDatabaseProvider

        db_dir = tmp_path / "data"
        db_dir.mkdir()

        # Create a mock StorageEngine that SqliteDatabaseProvider uses
        # SqliteDatabaseProvider calls self._storage_engine.get_path("database", f"{db_name}.db")
        mock_se_for_provider = MagicMock()

        def get_path(purpose: str, relative: str = "") -> Path:
            if purpose == "database":
                if relative:
                    return db_dir / relative
                return db_dir
            return tmp_path / purpose

        mock_se_for_provider.get_path = MagicMock(side_effect=get_path)

        # Ensure cache.db does NOT exist
        cache_db_path = db_dir / "cache.db"
        assert not cache_db_path.exists()

        db_provider = SqliteDatabaseProvider(mock_se_for_provider)

        # Getting a session for "cache" and executing a query creates cache.db
        session = await db_provider.get_session("cache")
        await session.execute(text("SELECT 1"))
        await session.close()

        # The cache.db file should now exist
        assert cache_db_path.exists(), "cache.db should be recreated automatically when missing"

        await db_provider.dispose()

    @pytest.mark.asyncio
    async def test_cache_db_recreation_does_not_affect_mirror_db(self, tmp_path: Path) -> None:
        """Recreating cache.db should not affect data in mirror.db."""
        from debcraft.infrastructure.database.provider import SqliteDatabaseProvider

        db_dir = tmp_path / "data"
        db_dir.mkdir()

        def get_path(purpose: str, relative: str = "") -> Path:
            if purpose == "database":
                if relative:
                    return db_dir / relative
                return db_dir
            return tmp_path / purpose

        mock_se_for_provider = MagicMock()
        mock_se_for_provider.get_path = MagicMock(side_effect=get_path)

        db_provider = SqliteDatabaseProvider(mock_se_for_provider)

        try:
            # Create mirror.db and add data
            from sqlalchemy import text

            mirror_session = await db_provider.get_session("mirror")
            await mirror_session.execute(
                text("CREATE TABLE IF NOT EXISTS test_table (id INTEGER PRIMARY KEY, value TEXT)")
            )
            await mirror_session.execute(text("INSERT INTO test_table (value) VALUES ('preserved')"))
            await mirror_session.commit()
            await mirror_session.close()

            # Now get a cache session (creates cache.db)
            cache_session = await db_provider.get_session("cache")
            await cache_session.close()

            # Verify mirror data is still intact
            mirror_session2 = await db_provider.get_session("mirror")
            result = await mirror_session2.execute(text("SELECT value FROM test_table WHERE value = 'preserved'"))
            row = result.one()
            assert row[0] == "preserved", "mirror.db data should not be affected"
            await mirror_session2.close()
        finally:
            await db_provider.dispose()


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestRecoverySkippedWithoutDbProvider:
    """Test that recovery is gracefully skipped when no db_provider is injected."""

    @pytest.mark.asyncio
    async def test_no_db_provider_skips_download_recovery(self) -> None:
        """Without a db_provider, _recover_interrupted_downloads is a no-op."""
        from debcraft.infrastructure.storage.engine import DefaultStorageEngine

        mock_storage_provider = _make_mock_provider()
        mock_event_bus = _make_mock_event_bus()
        storage_engine = DefaultStorageEngine(
            provider=mock_storage_provider,
            event_bus=mock_event_bus,
            db_provider=None,
        )

        # Should complete without error
        await storage_engine._recover_interrupted_downloads()

    @pytest.mark.asyncio
    async def test_no_db_provider_skips_cache_integrity(self) -> None:
        """Without a db_provider, _verify_cache_integrity is a no-op."""
        from debcraft.infrastructure.storage.engine import DefaultStorageEngine

        mock_storage_provider = _make_mock_provider()
        mock_event_bus = _make_mock_event_bus()
        storage_engine = DefaultStorageEngine(
            provider=mock_storage_provider,
            event_bus=mock_event_bus,
            db_provider=None,
        )

        # Should complete without error
        await storage_engine._verify_cache_integrity()
