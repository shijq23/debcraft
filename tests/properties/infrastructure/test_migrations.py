"""Property-based tests for migration ordering and history recording.

**Validates: Requirements 6.2, 6.3, 6.9**

Property 16: Migration Ordering and Idempotence — For any set of migration
files with version identifiers, the migration runner applies them in strictly
ascending version order, and for any migration whose version is already recorded
in the history table, re-running the migration system skips it without re-execution.

Property 17: Migration History Recording — For any migration that executes
successfully, the migration history table contains a row with the migration's
version identifier, a valid ISO-8601 UTC timestamp, and a non-negative duration
in milliseconds.
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from debcraft.infrastructure.database.runner import MigrationRunner


def _make_event_bus() -> MagicMock:
    """Create a mock EventBus with an async publish method."""
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


def _create_migration_file(directory: Path, version: int) -> None:
    """Create a minimal migration file at the given directory with the given version.

    Each migration creates a table named ``_test_v{version}`` to make its
    execution observable and verifiable.
    """
    filename = f"v{version}_test_migration.py"
    content = (
        f'"""Test migration v{version}."""\n'
        "\n"
        "from sqlalchemy import text\n"
        "from sqlalchemy.ext.asyncio import AsyncSession\n"
        "\n"
        "\n"
        f"async def migrate(session: AsyncSession) -> None:\n"
        f'    """Create a marker table for version {version}."""\n'
        f'    await session.execute(text("CREATE TABLE IF NOT EXISTS _test_v{version} (id INTEGER PRIMARY KEY)"))\n'
    )
    (directory / filename).write_text(content)


async def _setup_engine() -> tuple[async_sessionmaker[AsyncSession], object]:
    """Create an in-memory SQLite engine and return (session_factory, engine)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestMigrationOrdering:
    """Property 16: Migration Ordering and Idempotence.

    Regardless of discovery order on disk, migrations are always applied
    in strictly ascending version order. Re-running skips already-applied
    migrations.
    """

    @settings(max_examples=200)
    @given(
        version_order=st.permutations(range(1, 6)),
    )
    def test_migrations_applied_in_ascending_order(
        self,
        version_order: list[int],
    ) -> None:
        """Migrations are applied in ascending version order regardless of file creation order.

        **Validates: Requirements 6.2**
        """

        async def _run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                migration_dir = Path(tmpdir) / "migrations"
                migration_dir.mkdir()

                # Write migration files in the given (possibly shuffled) order
                for version in version_order:
                    _create_migration_file(migration_dir, version)

                event_bus = _make_event_bus()
                factory, engine = await _setup_engine()

                try:
                    runner = MigrationRunner(
                        session_factory=factory,
                        migration_directory=migration_dir,
                        event_bus=event_bus,
                        db_name="test",
                    )

                    async with factory() as session:
                        await runner.run_pending(session)
                        await session.commit()

                        # Verify migrations were applied in ascending order
                        result = await session.execute(text("SELECT version FROM _migration_history ORDER BY rowid"))
                        applied_versions = [row[0] for row in result.fetchall()]

                        # Applied versions must be sorted ascending
                        assert applied_versions == sorted(applied_versions), (
                            f"Migrations applied out of order: {applied_versions}"
                        )

                        # All versions should have been applied
                        assert set(applied_versions) == set(version_order), (
                            f"Not all migrations applied. Expected: {sorted(version_order)}, Got: {applied_versions}"
                        )
                finally:
                    await engine.dispose()  # type: ignore[union-attr]

        asyncio.run(_run())

    @settings(max_examples=200)
    @given(
        version_order=st.permutations(range(1, 6)),
    )
    def test_already_applied_migrations_are_skipped(
        self,
        version_order: list[int],
    ) -> None:
        """Re-running the migration system skips already-applied migrations.

        **Validates: Requirements 6.9**
        """

        async def _run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                migration_dir = Path(tmpdir) / "migrations"
                migration_dir.mkdir()

                # Create migration files
                for version in version_order:
                    _create_migration_file(migration_dir, version)

                event_bus = _make_event_bus()
                factory, engine = await _setup_engine()

                try:
                    runner = MigrationRunner(
                        session_factory=factory,
                        migration_directory=migration_dir,
                        event_bus=event_bus,
                        db_name="test",
                    )

                    # Run migrations first time
                    async with factory() as session:
                        await runner.run_pending(session)
                        await session.commit()

                    # Reset event_bus call count
                    event_bus.publish.reset_mock()

                    # Run migrations second time — should skip all
                    async with factory() as session:
                        await runner.run_pending(session)
                        await session.commit()

                        # Verify no new rows were added
                        result = await session.execute(text("SELECT COUNT(*) FROM _migration_history"))
                        count = result.scalar()
                        assert count == len(version_order), f"Expected {len(version_order)} history rows, got {count}"

                    # No new publish events on second run
                    event_bus.publish.assert_not_called()
                finally:
                    await engine.dispose()  # type: ignore[union-attr]

        asyncio.run(_run())


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestMigrationHistoryRecording:
    """Property 17: Migration History Recording.

    For any migration that executes successfully, the history table contains
    a row with the version identifier, a valid ISO-8601 UTC timestamp, and
    a non-negative duration in milliseconds.
    """

    @settings(max_examples=200)
    @given(
        version_order=st.permutations(range(1, 6)),
    )
    def test_history_has_valid_timestamp_and_duration(
        self,
        version_order: list[int],
    ) -> None:
        """Each applied migration records a valid ISO-8601 timestamp and duration_ms >= 0.

        **Validates: Requirements 6.3**
        """

        async def _run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                migration_dir = Path(tmpdir) / "migrations"
                migration_dir.mkdir()

                # Create migration files
                for version in version_order:
                    _create_migration_file(migration_dir, version)

                event_bus = _make_event_bus()
                factory, engine = await _setup_engine()

                try:
                    runner = MigrationRunner(
                        session_factory=factory,
                        migration_directory=migration_dir,
                        event_bus=event_bus,
                        db_name="test",
                    )

                    async with factory() as session:
                        await runner.run_pending(session)
                        await session.commit()

                        # Query all history rows
                        result = await session.execute(
                            text("SELECT version, applied_at, duration_ms FROM _migration_history")
                        )
                        rows = result.fetchall()

                        # We should have one row per version
                        assert len(rows) == len(version_order)

                        for row in rows:
                            version_val = row[0]
                            applied_at_val = row[1]
                            duration_ms_val = row[2]

                            # Version must be in our set
                            assert version_val in version_order, f"Unexpected version {version_val} in history"

                            # applied_at must be a valid ISO-8601 timestamp
                            assert applied_at_val is not None
                            # SQLite datetime('now') produces format like
                            # "2024-01-15 10:30:00"
                            try:
                                parsed = datetime.fromisoformat(str(applied_at_val))
                                assert isinstance(parsed, datetime)
                            except ValueError:
                                pytest.fail(f"applied_at '{applied_at_val}' is not a valid ISO-8601 timestamp")

                            # duration_ms must be non-negative
                            assert isinstance(duration_ms_val, int)
                            assert duration_ms_val >= 0, f"duration_ms should be >= 0, got {duration_ms_val}"
                finally:
                    await engine.dispose()  # type: ignore[union-attr]

        asyncio.run(_run())
