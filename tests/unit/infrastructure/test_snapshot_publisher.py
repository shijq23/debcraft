"""Unit tests for SnapshotPublisher.

Tests the atomic snapshot publication workflow including:
- Successful publication with verified files
- Failure event when zero verified files
- Transaction rollback on database errors
- Schema version retrieval from _migration_history
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Import scan models to ensure all relationships resolve correctly
import debcraft.infrastructure.models.scan  # noqa: F401
from debcraft.infrastructure.mirror.events import MirrorSyncFailedEvent, SnapshotPublishedEvent
from debcraft.infrastructure.mirror.publisher import SnapshotPublisher
from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.models.metadata import Repository, RepositorySnapshot


async def _setup_db() -> tuple[async_sessionmaker[AsyncSession], object]:
    """Create in-memory SQLite database with tables and migration history."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Create the _migration_history table
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS _migration_history ("
                "    version INTEGER PRIMARY KEY,"
                "    applied_at TEXT NOT NULL,"
                "    duration_ms INTEGER NOT NULL DEFAULT 0"
                ")"
            )
        )
        # Insert a migration version
        await conn.execute(
            text("INSERT INTO _migration_history (version, applied_at, duration_ms) VALUES (3, datetime('now'), 10)")
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


async def _create_repository(session: AsyncSession) -> Repository:
    """Create a test repository."""
    repo = Repository(
        name="test-repo",
        base_url="https://mirror.elxr.dev/elxr",
        suite="elxr3",
        component="main",
    )
    session.add(repo)
    await session.flush()
    return repo


class _FakeDbProvider:
    """Fake DatabaseProvider that returns sessions from a factory."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def get_session(self, db_name: str) -> AsyncSession:
        return self._factory()

    async def dispose(self) -> None:
        pass

    async def health_check(self) -> dict[str, bool]:
        return {"metadata": True}


@pytest.mark.unit
@pytest.mark.asyncio
class TestSnapshotPublisher:
    """Tests for SnapshotPublisher.publish_snapshot()."""

    async def test_publish_snapshot_success(self) -> None:
        """publish_snapshot creates an atomic snapshot and publishes event."""
        factory, engine = await _setup_db()
        try:
            # Create a repository
            async with factory() as session:
                repo = await _create_repository(session)
                await session.commit()
                repo_id = repo.id

            db_provider = _FakeDbProvider(factory)
            event_bus = AsyncMock()
            publisher = SnapshotPublisher(db_provider, event_bus)

            result = await publisher.publish_snapshot(
                repository_id=repo_id,
                verified_file_count=42,
                failed_file_count=3,
            )

            # Verify snapshot was created
            assert result is not None
            assert result.repository_id == repo_id
            assert result.published is True
            assert result.schema_version == 3
            assert result.captured_at is not None

            # Verify event was published
            event_bus.publish.assert_called_once()
            event = event_bus.publish.call_args[0][0]
            assert isinstance(event, SnapshotPublishedEvent)
            assert event.snapshot_id == result.id
            assert event.verified_file_count == 42
            assert event.failed_file_count == 3

            # Verify snapshot is persisted in the database
            async with factory() as session:
                persisted = await session.get(RepositorySnapshot, result.id)
                assert persisted is not None
                assert persisted.published is True
        finally:
            from sqlalchemy.ext.asyncio import AsyncEngine

            assert isinstance(engine, AsyncEngine)
            await engine.dispose()

    async def test_publish_snapshot_zero_verified_files_returns_none(self) -> None:
        """publish_snapshot returns None and publishes failure when no verified files."""
        factory, engine = await _setup_db()
        try:
            db_provider = _FakeDbProvider(factory)
            event_bus = AsyncMock()
            publisher = SnapshotPublisher(db_provider, event_bus)

            result = await publisher.publish_snapshot(
                repository_id=1,
                verified_file_count=0,
                failed_file_count=5,
            )

            # Should return None
            assert result is None

            # Should publish failure event
            event_bus.publish.assert_called_once()
            event = event_bus.publish.call_args[0][0]
            assert isinstance(event, MirrorSyncFailedEvent)
            assert event.files_failed == 5
        finally:
            from sqlalchemy.ext.asyncio import AsyncEngine

            assert isinstance(engine, AsyncEngine)
            await engine.dispose()

    async def test_publish_snapshot_rollback_on_error(self) -> None:
        """publish_snapshot rolls back transaction on database error."""
        _factory, engine = await _setup_db()
        try:
            # Use a provider that returns a session that will fail on commit
            mock_session = AsyncMock(spec=AsyncSession)
            mock_session.begin = AsyncMock()
            mock_session.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=1)))
            mock_session.add = MagicMock()
            mock_session.flush = AsyncMock(side_effect=RuntimeError("DB error"))
            mock_session.rollback = AsyncMock()
            mock_session.close = AsyncMock()
            mock_session.commit = AsyncMock()

            mock_provider = AsyncMock()
            mock_provider.get_session = AsyncMock(return_value=mock_session)

            event_bus = AsyncMock()
            publisher = SnapshotPublisher(mock_provider, event_bus)

            with pytest.raises(RuntimeError, match="DB error"):
                await publisher.publish_snapshot(
                    repository_id=1,
                    verified_file_count=10,
                    failed_file_count=0,
                )

            # Verify rollback was called
            mock_session.rollback.assert_called_once()
            # Verify no event was published
            event_bus.publish.assert_not_called()
        finally:
            from sqlalchemy.ext.asyncio import AsyncEngine

            assert isinstance(engine, AsyncEngine)
            await engine.dispose()

    async def test_publish_snapshot_schema_version_zero_when_no_migrations(self) -> None:
        """publish_snapshot sets schema_version=0 when no migrations exist."""
        # Create DB without migration history entries
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS _migration_history ("
                    "    version INTEGER PRIMARY KEY,"
                    "    applied_at TEXT NOT NULL,"
                    "    duration_ms INTEGER NOT NULL DEFAULT 0"
                    ")"
                )
            )
            # No migrations inserted

        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                repo = await _create_repository(session)
                await session.commit()
                repo_id = repo.id

            db_provider = _FakeDbProvider(factory)
            event_bus = AsyncMock()
            publisher = SnapshotPublisher(db_provider, event_bus)

            result = await publisher.publish_snapshot(
                repository_id=repo_id,
                verified_file_count=5,
                failed_file_count=0,
            )

            assert result is not None
            assert result.schema_version == 0
        finally:
            await engine.dispose()

    async def test_publish_snapshot_captured_at_is_utc(self) -> None:
        """publish_snapshot sets captured_at to current UTC time."""
        factory, engine = await _setup_db()
        try:
            async with factory() as session:
                repo = await _create_repository(session)
                await session.commit()
                repo_id = repo.id

            db_provider = _FakeDbProvider(factory)
            event_bus = AsyncMock()
            publisher = SnapshotPublisher(db_provider, event_bus)

            before = datetime.now(UTC)
            result = await publisher.publish_snapshot(
                repository_id=repo_id,
                verified_file_count=1,
                failed_file_count=0,
            )
            after = datetime.now(UTC)

            assert result is not None
            assert before <= result.captured_at <= after
        finally:
            from sqlalchemy.ext.asyncio import AsyncEngine

            assert isinstance(engine, AsyncEngine)
            await engine.dispose()
