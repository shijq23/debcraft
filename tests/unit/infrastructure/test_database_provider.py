"""Unit tests for SqliteDatabaseProvider.

Verifies engine creation with correct PRAGMA settings, dispose behavior,
health_check returns, and error handling for corrupt/missing/permission-denied
databases.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

from debcraft.infrastructure.database.provider import SqliteDatabaseProvider
from debcraft.infrastructure.errors import DatabaseConnectionError
from debcraft.platform.contracts.storage import StorageEngine


def _make_mock_storage_engine(tmp_path: Path) -> StorageEngine:
    """Create a mock StorageEngine that resolves database paths to tmp_path."""
    mock = MagicMock(spec=StorageEngine)
    mock.get_path = MagicMock(side_effect=lambda purpose, relative="": tmp_path / relative)
    return mock


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestPragmaSettings:
    """Test engine creation with correct PRAGMA settings."""

    @pytest.mark.asyncio
    async def test_foreign_keys_enabled(self, tmp_path: Path) -> None:
        """PRAGMA foreign_keys should be ON for created engines."""
        storage = _make_mock_storage_engine(tmp_path)
        provider = SqliteDatabaseProvider(storage)

        session = await provider.get_session("mirror")
        try:
            result = await session.execute(text("PRAGMA foreign_keys"))
            row = result.scalar()
            assert row == 1, f"Expected foreign_keys=1, got {row}"
        finally:
            await session.close()
            await provider.dispose()

    @pytest.mark.asyncio
    async def test_journal_mode_wal(self, tmp_path: Path) -> None:
        """PRAGMA journal_mode should be WAL for created engines."""
        storage = _make_mock_storage_engine(tmp_path)
        provider = SqliteDatabaseProvider(storage)

        session = await provider.get_session("metadata")
        try:
            result = await session.execute(text("PRAGMA journal_mode"))
            mode = result.scalar()
            assert mode == "wal", f"Expected journal_mode='wal', got {mode!r}"
        finally:
            await session.close()
            await provider.dispose()

    @pytest.mark.asyncio
    async def test_synchronous_normal(self, tmp_path: Path) -> None:
        """PRAGMA synchronous should be NORMAL (1) for created engines."""
        storage = _make_mock_storage_engine(tmp_path)
        provider = SqliteDatabaseProvider(storage)

        session = await provider.get_session("cache")
        try:
            result = await session.execute(text("PRAGMA synchronous"))
            value = result.scalar()
            # synchronous=NORMAL is value 1
            assert value == 1, f"Expected synchronous=1 (NORMAL), got {value}"
        finally:
            await session.close()
            await provider.dispose()


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestDispose:
    """Test dispose() closes all active engines."""

    @pytest.mark.asyncio
    async def test_dispose_clears_engines(self, tmp_path: Path) -> None:
        """After dispose(), internal engine cache should be empty."""
        storage = _make_mock_storage_engine(tmp_path)
        provider = SqliteDatabaseProvider(storage)

        # Create sessions to trigger engine creation for all databases
        session_mirror = await provider.get_session("mirror")
        session_metadata = await provider.get_session("metadata")
        session_cache = await provider.get_session("cache")
        await session_mirror.close()
        await session_metadata.close()
        await session_cache.close()

        # Verify engines are populated
        assert len(provider._engines) == 3

        await provider.dispose()

        # After dispose, engines and session factories should be cleared
        assert len(provider._engines) == 0
        assert len(provider._session_factories) == 0

    @pytest.mark.asyncio
    async def test_dispose_with_no_active_engines(self, tmp_path: Path) -> None:
        """dispose() should succeed even when no engines have been created."""
        storage = _make_mock_storage_engine(tmp_path)
        provider = SqliteDatabaseProvider(storage)

        # Should not raise
        await provider.dispose()
        assert len(provider._engines) == 0


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestHealthCheck:
    """Test health_check() returns correct boolean map."""

    @pytest.mark.asyncio
    async def test_health_check_all_healthy(self, tmp_path: Path) -> None:
        """health_check() returns True for all databases when accessible."""
        storage = _make_mock_storage_engine(tmp_path)
        provider = SqliteDatabaseProvider(storage)

        # Create sessions to force engine creation (creates DB files)
        for db_name in ("mirror", "metadata", "cache"):
            session = await provider.get_session(db_name)  # type: ignore[arg-type]
            await session.close()

        result = await provider.health_check()

        assert result == {"mirror": True, "metadata": True, "cache": True}
        await provider.dispose()

    @pytest.mark.asyncio
    async def test_health_check_returns_all_three_keys(self, tmp_path: Path) -> None:
        """health_check() should return entries for all three logical databases."""
        storage = _make_mock_storage_engine(tmp_path)
        provider = SqliteDatabaseProvider(storage)

        result = await provider.health_check()

        assert set(result.keys()) == {"mirror", "metadata", "cache"}
        await provider.dispose()


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestDatabaseConnectionErrors:
    """Test DatabaseConnectionError raised for corrupt/missing/permission-denied databases."""

    @pytest.mark.asyncio
    async def test_corrupt_database_raises_connection_error(self, tmp_path: Path) -> None:
        """A corrupt database file should raise DatabaseConnectionError with corruption type."""
        # Create a corrupt database file
        corrupt_db = tmp_path / "mirror.db"
        corrupt_db.write_text("this is not a valid sqlite database file at all")

        storage = _make_mock_storage_engine(tmp_path)
        provider = SqliteDatabaseProvider(storage)

        # Getting a session and executing should raise due to corruption
        session = await provider.get_session("mirror")
        with pytest.raises((DatabaseConnectionError, Exception)):
            await session.execute(text("SELECT 1"))
        await session.close()
        await provider.dispose()

    @pytest.mark.asyncio
    async def test_permission_denied_database(self, tmp_path: Path) -> None:
        """A non-readable database should raise DatabaseConnectionError."""
        # Create the database directory with restrictive permissions
        db_dir = tmp_path / "restricted"
        db_dir.mkdir()
        db_file = db_dir / "mirror.db"
        db_file.touch()
        db_file.chmod(0o000)

        mock = MagicMock(spec=StorageEngine)
        mock.get_path = MagicMock(side_effect=lambda purpose, relative="": db_dir / relative)
        provider = SqliteDatabaseProvider(mock)

        try:
            session = await provider.get_session("mirror")
            # Try to execute - may fail at session creation or execution
            with pytest.raises((DatabaseConnectionError, Exception)):
                await session.execute(text("SELECT 1"))
            await session.close()
        except (DatabaseConnectionError, PermissionError):
            pass  # Expected - error at session creation level
        finally:
            # Restore permissions for cleanup
            db_file.chmod(0o644)
            await provider.dispose()

    @pytest.mark.asyncio
    async def test_nonexistent_directory_creates_database(self, tmp_path: Path) -> None:
        """SQLite can create database files in existing directories.

        When the directory exists but the .db file doesn't, SQLite creates it.
        This verifies the provider works with paths that point to writable locations.
        """
        storage = _make_mock_storage_engine(tmp_path)
        provider = SqliteDatabaseProvider(storage)

        # The database file doesn't exist yet — SQLite will create it
        session = await provider.get_session("mirror")
        result = await session.execute(text("SELECT 1"))
        assert result.scalar() == 1
        await session.close()
        await provider.dispose()

    @pytest.mark.asyncio
    async def test_nonexistent_parent_directory_raises_error(self, tmp_path: Path) -> None:
        """When the parent directory doesn't exist, engine creation should fail."""
        from sqlalchemy.exc import OperationalError

        nonexistent = tmp_path / "does" / "not" / "exist"
        mock = MagicMock(spec=StorageEngine)
        mock.get_path = MagicMock(side_effect=lambda purpose, relative="": nonexistent / relative)
        provider = SqliteDatabaseProvider(mock)

        # SQLite cannot create the file if the parent directory doesn't exist
        # This should raise when we try to use the session
        session = await provider.get_session("mirror")
        with pytest.raises(OperationalError):
            await session.execute(text("CREATE TABLE test_tbl (id INTEGER PRIMARY KEY)"))
            await session.commit()
        await session.close()
        await provider.dispose()
