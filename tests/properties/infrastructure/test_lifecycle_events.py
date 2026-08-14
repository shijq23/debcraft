"""Property-based tests for lifecycle event publication.

**Validates: Requirements 9.6**

Property 22: Lifecycle Event Publication — For any storage lifecycle action
(initialization, shutdown, or migration application), the EventBus receives
a DomainEvent of the corresponding type (StorageInitializedEvent,
StorageShutdownEvent, or MigrationAppliedEvent).
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from debcraft.infrastructure.database.runner import MigrationRunner
from debcraft.infrastructure.events import (
    MigrationAppliedEvent,
    StorageInitializedEvent,
    StorageShutdownEvent,
)
from debcraft.infrastructure.storage.engine import DefaultStorageEngine


def _make_event_bus() -> MagicMock:
    """Create a mock EventBus with an async publish method."""
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


def _make_storage_provider(base_path: Path) -> MagicMock:
    """Create a mock StorageProvider that resolves paths under base_path.

    The provider returns valid paths, succeeds on create_directory and
    check_writable, so that StorageEngine initialization completes normally.
    """
    provider = MagicMock()
    provider.create_directory = AsyncMock()
    provider.remove_matching = AsyncMock()
    provider.check_writable = AsyncMock(return_value=True)

    def resolve_path(purpose: str, relative: str = "") -> Path:
        base = base_path / purpose
        if relative:
            return base / relative
        return base

    provider.resolve_path = MagicMock(side_effect=resolve_path)
    return provider


def _create_migration_file(directory: Path, version: int) -> None:
    """Create a minimal migration file that creates a marker table."""
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
class TestLifecycleEventPublication:
    """Property 22: Lifecycle Event Publication.

    For any storage lifecycle action (initialization, shutdown, or migration
    application), the EventBus receives a DomainEvent of the corresponding type.
    """

    @given(
        actions=st.lists(
            st.sampled_from(["initialize", "shutdown"]),
            min_size=1,
            max_size=10,
        ),
    )
    def test_storage_engine_publishes_events_on_lifecycle_actions(self, actions: list[str]) -> None:
        """StorageInitializedEvent on initialize; StorageShutdownEvent on shutdown.

        **Validates: Requirements 9.6**
        """

        async def _run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                base_path = Path(tmpdir)
                event_bus = _make_event_bus()
                provider = _make_storage_provider(base_path)
                engine = DefaultStorageEngine(provider=provider, event_bus=event_bus)

                for action in actions:
                    if action == "initialize":
                        await engine.initialize()
                    elif action == "shutdown":
                        await engine.shutdown()

                # Verify that each action produced the correct event type
                published_events = [c.args[0] for c in event_bus.publish.call_args_list]

                for idx, action in enumerate(actions):
                    assert idx < len(published_events), (
                        f"Expected event for action '{action}' at index "
                        f"{idx} but only {len(published_events)} "
                        f"events were published"
                    )
                    event = published_events[idx]

                    if action == "initialize":
                        assert isinstance(event, StorageInitializedEvent), (
                            f"Expected StorageInitializedEvent for initialize, got {type(event).__name__}"
                        )
                    elif action == "shutdown":
                        assert isinstance(event, StorageShutdownEvent), (
                            f"Expected StorageShutdownEvent for shutdown, got {type(event).__name__}"
                        )

                # Total number of published events must match actions count
                assert len(published_events) == len(actions), (
                    f"Expected {len(actions)} events, got {len(published_events)}"
                )

        asyncio.run(_run())

    @given(
        num_migrations=st.integers(min_value=1, max_value=10),
    )
    def test_migration_runner_publishes_event_per_migration(self, num_migrations: int) -> None:
        """MigrationAppliedEvent published for each successfully applied migration.

        **Validates: Requirements 9.6**
        """

        async def _run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                migration_dir = Path(tmpdir) / "migrations"
                migration_dir.mkdir()

                # Create migration files
                for version in range(1, num_migrations + 1):
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

                    # Verify one MigrationAppliedEvent per migration
                    publish_calls = event_bus.publish.call_args_list
                    assert len(publish_calls) == num_migrations, (
                        f"Expected {num_migrations} MigrationAppliedEvent publications, got {len(publish_calls)}"
                    )

                    published_versions: list[int] = []
                    for call_obj in publish_calls:
                        event = call_obj.args[0]
                        assert isinstance(event, MigrationAppliedEvent), (
                            f"Expected MigrationAppliedEvent, got {type(event).__name__}"
                        )
                        assert event.db_name == "test"
                        assert event.version > 0
                        assert event.duration_ms >= 0
                        published_versions.append(event.version)

                    # Events should correspond to all migration versions
                    assert set(published_versions) == set(range(1, num_migrations + 1)), (
                        f"Expected versions {set(range(1, num_migrations + 1))}, got {set(published_versions)}"
                    )

                    # Events should be in ascending order
                    assert published_versions == sorted(published_versions), (
                        f"Events published out of order: {published_versions}"
                    )
                finally:
                    await engine.dispose()  # type: ignore[union-attr]

        asyncio.run(_run())

    @given(
        num_migrations=st.integers(min_value=1, max_value=5),
    )
    def test_full_lifecycle_publishes_all_event_types(self, num_migrations: int) -> None:
        """Initialize, run migrations, and shutdown publishes all three event types.

        **Validates: Requirements 9.6**
        """

        async def _run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                base_path = Path(tmpdir)
                migration_dir = base_path / "migrations"
                migration_dir.mkdir()

                # Create migration files
                for version in range(1, num_migrations + 1):
                    _create_migration_file(migration_dir, version)

                event_bus = _make_event_bus()
                provider = _make_storage_provider(base_path)
                engine = DefaultStorageEngine(provider=provider, event_bus=event_bus)

                # Initialize storage engine
                await engine.initialize()

                # Run migrations with separate event bus tracking
                factory, db_engine = await _setup_engine()
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

                    # Shutdown storage engine
                    await engine.shutdown()

                    # Verify we got all three event types
                    published_events = [c.args[0] for c in event_bus.publish.call_args_list]

                    init_events = [e for e in published_events if isinstance(e, StorageInitializedEvent)]
                    shutdown_events = [e for e in published_events if isinstance(e, StorageShutdownEvent)]
                    migration_events = [e for e in published_events if isinstance(e, MigrationAppliedEvent)]

                    assert len(init_events) == 1, f"Expected 1 StorageInitializedEvent, got {len(init_events)}"
                    assert len(shutdown_events) == 1, f"Expected 1 StorageShutdownEvent, got {len(shutdown_events)}"
                    assert len(migration_events) == num_migrations, (
                        f"Expected {num_migrations} MigrationAppliedEvents, got {len(migration_events)}"
                    )

                    # Verify ordering: init -> migrations -> shutdown
                    init_idx = published_events.index(init_events[0])
                    shutdown_idx = published_events.index(shutdown_events[0])

                    assert init_idx < shutdown_idx, "StorageInitializedEvent should come before StorageShutdownEvent"

                    for mig_event in migration_events:
                        mig_idx = published_events.index(mig_event)
                        assert init_idx < mig_idx < shutdown_idx, (
                            "MigrationAppliedEvent should come between init and shutdown events"
                        )
                finally:
                    await db_engine.dispose()  # type: ignore[union-attr]

        asyncio.run(_run())
