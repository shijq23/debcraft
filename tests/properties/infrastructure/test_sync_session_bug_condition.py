"""Property-based test: Bug Condition — No SyncSession Row After sync_repository().

**Validates: Requirements 1.1, 1.2, 1.3**

Property 1: Bug Condition - SyncSession Persisted After Sync

For any call to sync_repository(config, session_id) that returns a SyncResult,
the sync_sessions table SHALL contain exactly one row with:
  - session_id matching the input
  - repository_name matching config.name
  - status in {"completed", "partial", "failed", "cancelled"}
  - file counts matching the returned SyncResult
  - started_at <= completed_at, both non-null

EXPECTED: This test FAILS on unfixed code — failure confirms the bug exists.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from debcraft.domain.mirror.config import RepositoryConfig
from debcraft.infrastructure.mirror.engine import MirrorEngine, SyncResult
from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.models.mirror import SyncSession

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_repo_name_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "Pd")),
    min_size=1,
    max_size=64,
).filter(lambda s: len(s.strip()) > 0)

_suite_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "Pd")),
    min_size=1,
    max_size=32,
).filter(lambda s: len(s.strip()) > 0)

_session_id_strategy = st.text(
    alphabet="0123456789abcdef",
    min_size=16,
    max_size=32,
)

_repository_config_strategy = st.builds(
    RepositoryConfig,
    name=_repo_name_strategy,
    base_url=st.just("https://deb.example.com/debian"),
    suites=st.lists(_suite_strategy, min_size=1, max_size=3),
    components=st.just(["main"]),
    architectures=st.just(["amd64"]),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _setup_db() -> tuple[async_sessionmaker[AsyncSession], object]:
    """Create in-memory SQLite engine with all tables."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


def _make_mock_engine(session_factory: async_sessionmaker[AsyncSession]) -> MirrorEngine:
    """Create a MirrorEngine with mocked dependencies but a real DB session factory."""
    db_provider = MagicMock()

    async def get_session(name: str) -> AsyncSession:
        return session_factory()

    db_provider.get_session = AsyncMock(side_effect=get_session)

    storage_engine = MagicMock()
    # Return a temp path for mirror root
    storage_engine.get_path = MagicMock(return_value=MagicMock())

    event_bus = MagicMock()
    cancellation_token = MagicMock()
    cancellation_token.is_cancelled = False
    progress = MagicMock()
    progress.report = MagicMock()
    logger = MagicMock()
    download_coordinator = MagicMock()

    engine = MirrorEngine(
        download_coordinator=download_coordinator,
        db_provider=db_provider,
        storage_engine=storage_engine,
        event_bus=event_bus,
        cancellation_token=cancellation_token,
        progress=progress,
        logger=logger,
    )
    return engine


# ---------------------------------------------------------------------------
# Property 1: Bug Condition — No SyncSession Row After sync_repository()
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty1BugConditionSyncSession:
    """Property 1: Bug Condition — sync_sessions table should contain a row after sync.

    On UNFIXED code, this test is EXPECTED TO FAIL because no code path
    inserts a SyncSession record.
    """

    @given(
        config=_repository_config_strategy,
        session_id=_session_id_strategy,
    )
    @settings(max_examples=20, deadline=10000)
    @pytest.mark.asyncio
    async def test_sync_session_persisted_after_sync(
        self,
        config: RepositoryConfig,
        session_id: str,
    ) -> None:
        """After sync_repository() completes, a SyncSession row must exist.

        **Validates: Requirements 1.1, 1.2, 1.3**

        The bug condition is: sync_repository() never persists a SyncSession.
        This test asserts the EXPECTED behavior — it will FAIL on unfixed code.
        """
        session_factory, db_engine = await _setup_db()
        engine = _make_mock_engine(session_factory)

        # Mock _stage_release to return None (suite up-to-date) so sync
        # completes quickly without network I/O
        with patch.object(engine, "_stage_release", new_callable=AsyncMock) as mock_release:
            mock_release.return_value = None

            result = await engine.sync_repository(config, session_id)

        # Verify SyncResult was returned
        assert isinstance(result, SyncResult)

        # Now query the database for the SyncSession row
        async with session_factory() as session:
            stmt = select(SyncSession).where(SyncSession.session_id == session_id)
            query_result = await session.execute(stmt)
            sync_session = query_result.scalar_one_or_none()

        # --- Assertions: SyncSession must exist with correct fields ---
        assert sync_session is not None, (
            f"No SyncSession row found for session_id={session_id!r}. "
            "Bug confirmed: sync_repository() does not persist a SyncSession."
        )
        assert sync_session.repository_name == config.name
        assert sync_session.status in {"completed", "partial", "failed", "cancelled"}
        assert sync_session.files_downloaded == result.files_downloaded
        assert sync_session.files_skipped == result.files_skipped
        assert sync_session.files_failed == result.files_failed
        assert sync_session.bytes_transferred == result.bytes_transferred
        assert sync_session.started_at is not None
        assert sync_session.completed_at is not None
        assert sync_session.started_at <= sync_session.completed_at

        # Clean up
        async with db_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db_engine.dispose()
