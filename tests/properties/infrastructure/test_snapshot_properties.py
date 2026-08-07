"""Property-based tests for snapshot publication atomicity.

**Validates: Requirements 7.6**

Property 15: Snapshot publication atomicity — The snapshot entity creation,
its association with verified files, and the published=True flag update SHALL
be persisted in a single database transaction; IF the transaction fails,
THEN no partial snapshot remains.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Import scan models to ensure all relationships resolve correctly
import debcraft.infrastructure.models.scan  # noqa: F401
from debcraft.infrastructure.mirror.publisher import SnapshotPublisher
from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.models.metadata import Repository, RepositorySnapshot

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Valid repository IDs (positive integers)
_repo_id_strategy = st.integers(min_value=1, max_value=10000)

# Positive verified file counts
_verified_count_strategy = st.integers(min_value=1, max_value=100000)

# Non-negative failed file counts
_failed_count_strategy = st.integers(min_value=0, max_value=100000)

# Schema versions for migration history
_schema_version_strategy = st.integers(min_value=1, max_value=100)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _setup_db(
    schema_version: int = 3,
) -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    """Create in-memory SQLite database with tables and migration history."""
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
        await conn.execute(
            text(
                "INSERT INTO _migration_history "
                "(version, applied_at, duration_ms) "
                f"VALUES ({schema_version}, datetime('now'), 10)"
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


async def _create_repository(session: AsyncSession) -> Repository:
    """Create a test repository and return it."""
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


# ---------------------------------------------------------------------------
# Property 15: Snapshot publication atomicity
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty15SnapshotPublicationAtomicity:
    """Property 15: Snapshot publication atomicity.

    The snapshot entity creation, its association with verified files, and
    the published=True flag update SHALL be persisted in a single database
    transaction; IF the transaction fails, THEN no partial snapshot remains.
    """

    @settings(max_examples=50)
    @given(
        verified_count=_verified_count_strategy,
        failed_count=_failed_count_strategy,
        schema_version=_schema_version_strategy,
    )
    def test_successful_publish_persists_snapshot_with_published_true(
        self,
        verified_count: int,
        failed_count: int,
        schema_version: int,
    ) -> None:
        """On success, snapshot is persisted with published=True.

        **Validates: Requirements 7.6**

        For any valid repository_id and positive verified_file_count,
        after publish_snapshot succeeds, the snapshot exists in DB with
        published=True and has a valid schema_version.
        """

        async def _run() -> None:
            factory, engine = await _setup_db(schema_version)
            try:
                # Create a repository to satisfy FK constraint
                async with factory() as session:
                    repo = await _create_repository(session)
                    await session.commit()
                    repo_id = repo.id

                db_provider = _FakeDbProvider(factory)
                event_bus = AsyncMock()
                publisher = SnapshotPublisher(db_provider, event_bus)

                result = await publisher.publish_snapshot(
                    repository_id=repo_id,
                    verified_file_count=verified_count,
                    failed_file_count=failed_count,
                )

                # Snapshot must be returned and published
                assert result is not None
                assert result.published is True
                assert result.repository_id == repo_id
                assert result.schema_version == schema_version
                assert result.captured_at is not None

                # Verify the snapshot is actually persisted in DB
                async with factory() as session:
                    persisted = await session.get(RepositorySnapshot, result.id)
                    assert persisted is not None
                    assert persisted.published is True
                    assert persisted.schema_version == schema_version
            finally:
                await engine.dispose()

        asyncio.run(_run())

    @settings(max_examples=50)
    @given(
        verified_count=_verified_count_strategy,
        failed_count=_failed_count_strategy,
        schema_version=_schema_version_strategy,
    )
    def test_transaction_failure_leaves_no_partial_snapshot(
        self,
        verified_count: int,
        failed_count: int,
        schema_version: int,
    ) -> None:
        """On transaction failure, no snapshot remains in DB.

        **Validates: Requirements 7.6**

        If the transaction fails (e.g., database error during commit),
        no snapshot exists in DB at all — no partial state with
        published=False or any other intermediate form.
        """

        async def _run() -> None:
            factory, engine = await _setup_db(schema_version)
            try:
                # Create a repository
                async with factory() as session:
                    repo = await _create_repository(session)
                    await session.commit()
                    repo_id = repo.id

                # Count snapshots before the failed attempt
                async with factory() as session:
                    result = await session.execute(select(RepositorySnapshot))
                    before_count = len(result.scalars().all())

                db_provider = _FakeDbProvider(factory)
                event_bus = AsyncMock()
                publisher = SnapshotPublisher(db_provider, event_bus)

                # Patch the commit to simulate a transaction failure
                # after the snapshot has been added but before commit
                with (
                    patch.object(
                        AsyncSession,
                        "commit",
                        side_effect=RuntimeError("Simulated DB failure"),
                    ),
                    pytest.raises(RuntimeError, match="Simulated DB"),
                ):
                    await publisher.publish_snapshot(
                        repository_id=repo_id,
                        verified_file_count=verified_count,
                        failed_file_count=failed_count,
                    )

                # Verify NO snapshot was persisted (rollback worked)
                async with factory() as session:
                    result = await session.execute(select(RepositorySnapshot))
                    after_count = len(result.scalars().all())

                assert after_count == before_count, (
                    f"Expected {before_count} snapshots after rollback, "
                    f"but found {after_count}. "
                    "Partial snapshot leaked through failed transaction."
                )

                # No event should have been published
                event_bus.publish.assert_not_called()
            finally:
                await engine.dispose()

        asyncio.run(_run())
