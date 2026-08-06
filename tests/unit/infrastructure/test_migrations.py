"""Unit tests for MigrationRunner.

Verifies that the migration runner correctly creates history tables,
skips already-applied migrations, rolls back on failure, and publishes
MigrationAppliedEvent on success.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from debcraft.infrastructure.database.runner import MigrationRunner
from debcraft.infrastructure.errors import MigrationError
from debcraft.infrastructure.events import MigrationAppliedEvent


def _create_in_memory_session_factory() -> async_sessionmaker[AsyncSession]:
    """Create an in-memory SQLite async session factory for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    return async_sessionmaker(engine, expire_on_commit=False)


def _create_mock_event_bus() -> MagicMock:
    """Create a mock EventBus with an async publish method."""
    mock = MagicMock()
    mock.publish = AsyncMock()
    return mock


def _write_migration_file(directory: Path, version: int, body: str) -> Path:
    """Write a migration file with the given version and body content."""
    filename = f"v{version}_test_migration.py"
    filepath = directory / filename
    filepath.write_text(
        "from sqlalchemy.ext.asyncio import AsyncSession\n"
        "from sqlalchemy import text\n\n"
        f"async def migrate(session: AsyncSession) -> None:\n"
        f"    {body}\n"
    )
    return filepath


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestEnsureHistoryTable:
    """Test ensure_history_table creates table on empty database."""

    @pytest.mark.asyncio
    async def test_creates_history_table_on_empty_database(self, tmp_path: Path) -> None:
        """ensure_history_table should create _migration_history on a fresh database."""
        session_factory = _create_in_memory_session_factory()
        event_bus = _create_mock_event_bus()
        runner = MigrationRunner(session_factory, tmp_path, event_bus, "test")

        async with session_factory() as session:
            await runner.ensure_history_table(session)
            await session.commit()

            # Verify table exists by querying it
            result = await session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='_migration_history'")
            )
            tables = result.fetchall()
            assert len(tables) == 1
            assert tables[0][0] == "_migration_history"

    @pytest.mark.asyncio
    async def test_idempotent_creation(self, tmp_path: Path) -> None:
        """Calling ensure_history_table twice should not raise."""
        session_factory = _create_in_memory_session_factory()
        event_bus = _create_mock_event_bus()
        runner = MigrationRunner(session_factory, tmp_path, event_bus, "test")

        async with session_factory() as session:
            await runner.ensure_history_table(session)
            await session.commit()
            # Second call should succeed without error
            await runner.ensure_history_table(session)
            await session.commit()

    @pytest.mark.asyncio
    async def test_history_table_has_correct_columns(self, tmp_path: Path) -> None:
        """The _migration_history table should have version, applied_at, duration_ms columns."""
        session_factory = _create_in_memory_session_factory()
        event_bus = _create_mock_event_bus()
        runner = MigrationRunner(session_factory, tmp_path, event_bus, "test")

        async with session_factory() as session:
            await runner.ensure_history_table(session)
            await session.commit()

            result = await session.execute(text("PRAGMA table_info(_migration_history)"))
            columns = {row[1] for row in result.fetchall()}
            assert "version" in columns
            assert "applied_at" in columns
            assert "duration_ms" in columns


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestMigrationSkipped:
    """Test migration is skipped if already in history."""

    @pytest.mark.asyncio
    async def test_skips_already_applied_migration(self, tmp_path: Path) -> None:
        """A migration whose version is already in history should not be re-executed."""
        migration_dir = tmp_path / "migrations"
        migration_dir.mkdir()
        _write_migration_file(
            migration_dir,
            1,
            'await session.execute(text("CREATE TABLE test_table (id INTEGER PRIMARY KEY)"))',
        )

        session_factory = _create_in_memory_session_factory()
        event_bus = _create_mock_event_bus()
        runner = MigrationRunner(session_factory, migration_dir, event_bus, "test")

        async with session_factory() as session:
            # Run once — should apply migration
            await runner.run_pending(session)
            await session.commit()

            # Verify migration was applied
            applied = await runner.get_applied_versions(session)
            assert 1 in applied

        # Reset mock to track only new calls
        event_bus.publish.reset_mock()

        async with session_factory() as session:
            # Run again — should skip v1
            await runner.run_pending(session)
            await session.commit()

            # publish should NOT have been called again for v1
            event_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_applies_only_unapplied_migrations(self, tmp_path: Path) -> None:
        """Only migrations not in history should be applied."""
        migration_dir = tmp_path / "migrations"
        migration_dir.mkdir()
        _write_migration_file(
            migration_dir,
            1,
            'await session.execute(text("CREATE TABLE t1 (id INTEGER PRIMARY KEY)"))',
        )
        _write_migration_file(
            migration_dir,
            2,
            'await session.execute(text("CREATE TABLE t2 (id INTEGER PRIMARY KEY)"))',
        )

        session_factory = _create_in_memory_session_factory()
        event_bus = _create_mock_event_bus()
        runner = MigrationRunner(session_factory, migration_dir, event_bus, "test")

        # Apply only v1 first
        async with session_factory() as session:
            await runner.ensure_history_table(session)
            await session.execute(
                text(
                    "INSERT INTO _migration_history (version, applied_at, duration_ms) VALUES (1, datetime('now'), 10)"
                )
            )
            await session.commit()

        # Reset mock
        event_bus.publish.reset_mock()

        # Run pending — should only apply v2
        async with session_factory() as session:
            await runner.run_pending(session)
            await session.commit()

            applied = await runner.get_applied_versions(session)
            assert 1 in applied
            assert 2 in applied

        # Only v2 event published
        assert event_bus.publish.call_count == 1
        published_event = event_bus.publish.call_args[0][0]
        assert isinstance(published_event, MigrationAppliedEvent)
        assert published_event.version == 2


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestFailedMigrationRollback:
    """Test failed migration rolls back and raises MigrationError."""

    @pytest.mark.asyncio
    async def test_failed_migration_raises_migration_error(self, tmp_path: Path) -> None:
        """A migration that raises should trigger MigrationError with correct fields."""
        migration_dir = tmp_path / "migrations"
        migration_dir.mkdir()
        # Write a migration that raises an exception
        filepath = migration_dir / "v1_failing_migration.py"
        filepath.write_text(
            "from sqlalchemy.ext.asyncio import AsyncSession\n\n"
            "async def migrate(session: AsyncSession) -> None:\n"
            "    raise RuntimeError('deliberate failure')\n"
        )

        session_factory = _create_in_memory_session_factory()
        event_bus = _create_mock_event_bus()
        runner = MigrationRunner(session_factory, migration_dir, event_bus, "test")

        async with session_factory() as session:
            with pytest.raises(MigrationError) as exc_info:
                await runner.run_pending(session)

            assert exc_info.value.migration_version == 1
            assert exc_info.value.db_name == "test"

    @pytest.mark.asyncio
    async def test_failed_migration_not_recorded_in_history(self, tmp_path: Path) -> None:
        """A failed migration should not appear in the history table."""
        migration_dir = tmp_path / "migrations"
        migration_dir.mkdir()
        filepath = migration_dir / "v1_failing_migration.py"
        filepath.write_text(
            "from sqlalchemy.ext.asyncio import AsyncSession\n\n"
            "async def migrate(session: AsyncSession) -> None:\n"
            "    raise RuntimeError('deliberate failure')\n"
        )

        session_factory = _create_in_memory_session_factory()
        event_bus = _create_mock_event_bus()
        runner = MigrationRunner(session_factory, migration_dir, event_bus, "test")

        async with session_factory() as session:
            with pytest.raises(MigrationError):
                await runner.run_pending(session)

            # The failed migration should not be recorded
            applied = await runner.get_applied_versions(session)
            assert 1 not in applied

    @pytest.mark.asyncio
    async def test_failed_migration_halts_further_execution(self, tmp_path: Path) -> None:
        """After a migration fails, subsequent migrations should not be attempted."""
        migration_dir = tmp_path / "migrations"
        migration_dir.mkdir()

        # v1 fails, v2 should never be attempted
        filepath_v1 = migration_dir / "v1_failing.py"
        filepath_v1.write_text(
            "from sqlalchemy.ext.asyncio import AsyncSession\n\n"
            "async def migrate(session: AsyncSession) -> None:\n"
            "    raise RuntimeError('v1 failed')\n"
        )
        _write_migration_file(
            migration_dir,
            2,
            'await session.execute(text("CREATE TABLE t2 (id INTEGER PRIMARY KEY)"))',
        )

        session_factory = _create_in_memory_session_factory()
        event_bus = _create_mock_event_bus()
        runner = MigrationRunner(session_factory, migration_dir, event_bus, "test")

        async with session_factory() as session:
            with pytest.raises(MigrationError) as exc_info:
                await runner.run_pending(session)

            # Only v1 should have been attempted
            assert exc_info.value.migration_version == 1
            applied = await runner.get_applied_versions(session)
            assert 1 not in applied
            assert 2 not in applied

    @pytest.mark.asyncio
    async def test_failed_migration_rolls_back_partial_changes(self, tmp_path: Path) -> None:
        """Partial changes from a failed migration should be rolled back via savepoint."""
        migration_dir = tmp_path / "migrations"
        migration_dir.mkdir()

        # Migration creates a table then fails — table should not persist
        filepath = migration_dir / "v1_partial_fail.py"
        filepath.write_text(
            "from sqlalchemy.ext.asyncio import AsyncSession\n"
            "from sqlalchemy import text\n\n"
            "async def migrate(session: AsyncSession) -> None:\n"
            "    await session.execute(text('CREATE TABLE partial_table (id INTEGER)'))\n"
            "    raise RuntimeError('fail after create')\n"
        )

        session_factory = _create_in_memory_session_factory()
        event_bus = _create_mock_event_bus()
        runner = MigrationRunner(session_factory, migration_dir, event_bus, "test")

        async with session_factory() as session:
            with pytest.raises(MigrationError):
                await runner.run_pending(session)

            # The table created inside the failed migration should be rolled back
            result = await session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='partial_table'")
            )
            tables = result.fetchall()
            assert len(tables) == 0


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestMigrationAppliedEvent:
    """Test MigrationAppliedEvent published on success."""

    @pytest.mark.asyncio
    async def test_event_published_on_successful_migration(self, tmp_path: Path) -> None:
        """A successful migration should publish MigrationAppliedEvent."""
        migration_dir = tmp_path / "migrations"
        migration_dir.mkdir()
        _write_migration_file(
            migration_dir,
            1,
            'await session.execute(text("CREATE TABLE event_test (id INTEGER PRIMARY KEY)"))',
        )

        session_factory = _create_in_memory_session_factory()
        event_bus = _create_mock_event_bus()
        runner = MigrationRunner(session_factory, migration_dir, event_bus, "mydb")

        async with session_factory() as session:
            await runner.run_pending(session)
            await session.commit()

        event_bus.publish.assert_called_once()
        published_event = event_bus.publish.call_args[0][0]
        assert isinstance(published_event, MigrationAppliedEvent)
        assert published_event.db_name == "mydb"
        assert published_event.version == 1
        assert published_event.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_event_not_published_on_failed_migration(self, tmp_path: Path) -> None:
        """A failed migration should not publish MigrationAppliedEvent."""
        migration_dir = tmp_path / "migrations"
        migration_dir.mkdir()
        filepath = migration_dir / "v1_failing.py"
        filepath.write_text(
            "from sqlalchemy.ext.asyncio import AsyncSession\n\n"
            "async def migrate(session: AsyncSession) -> None:\n"
            "    raise RuntimeError('fail')\n"
        )

        session_factory = _create_in_memory_session_factory()
        event_bus = _create_mock_event_bus()
        runner = MigrationRunner(session_factory, migration_dir, event_bus, "mydb")

        async with session_factory() as session:
            with pytest.raises(MigrationError):
                await runner.run_pending(session)

        event_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_events_for_multiple_migrations(self, tmp_path: Path) -> None:
        """Each successful migration should publish its own event."""
        migration_dir = tmp_path / "migrations"
        migration_dir.mkdir()
        _write_migration_file(
            migration_dir,
            1,
            'await session.execute(text("CREATE TABLE t1 (id INTEGER PRIMARY KEY)"))',
        )
        _write_migration_file(
            migration_dir,
            2,
            'await session.execute(text("CREATE TABLE t2 (id INTEGER PRIMARY KEY)"))',
        )

        session_factory = _create_in_memory_session_factory()
        event_bus = _create_mock_event_bus()
        runner = MigrationRunner(session_factory, migration_dir, event_bus, "mydb")

        async with session_factory() as session:
            await runner.run_pending(session)
            await session.commit()

        assert event_bus.publish.call_count == 2
        events = [call[0][0] for call in event_bus.publish.call_args_list]
        assert all(isinstance(e, MigrationAppliedEvent) for e in events)
        assert events[0].version == 1
        assert events[1].version == 2
        assert all(e.db_name == "mydb" for e in events)
