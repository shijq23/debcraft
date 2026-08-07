"""Preservation property tests for SyncResult return value contract.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

Property 2: Preservation — SyncResult Return Value Unchanged

These tests verify that `sync_repository()` returns a `SyncResult` with
correct file counts regardless of database state. They are run BEFORE the
fix to establish baseline behavior, and again AFTER the fix to confirm no
regressions.

All tests in this file MUST PASS on both unfixed and fixed code.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from debcraft.domain.mirror.config import RepositoryConfig
from debcraft.infrastructure.mirror.engine import MirrorEngine, SyncResult
from debcraft.infrastructure.models.base import Base

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Suite names: short alphabetic strings
_suite_strategy = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz",
    min_size=3,
    max_size=12,
)

# Repository names
_repo_name_strategy = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-",
    min_size=1,
    max_size=30,
).filter(lambda s: len(s.strip()) > 0 and not s.startswith("-"))

# Session IDs
_session_id_strategy = st.text(
    alphabet="abcdef0123456789",
    min_size=8,
    max_size=32,
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


def _make_mock_engine(
    session_factory: async_sessionmaker[AsyncSession],
) -> MirrorEngine:
    """Create a MirrorEngine with mocked dependencies but a real DB."""
    db_provider = MagicMock()

    async def get_session(name: str) -> AsyncSession:
        return session_factory()

    db_provider.get_session = AsyncMock(side_effect=get_session)

    storage_engine = MagicMock()
    storage_engine.get_path = MagicMock(return_value=MagicMock(__truediv__=lambda self, other: MagicMock()))
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


def _make_cancelled_engine(
    session_factory: async_sessionmaker[AsyncSession],
) -> MirrorEngine:
    """Create a MirrorEngine with cancellation token already set."""
    db_provider = MagicMock()

    async def get_session(name: str) -> AsyncSession:
        return session_factory()

    db_provider.get_session = AsyncMock(side_effect=get_session)

    storage_engine = MagicMock()
    storage_engine.get_path = MagicMock(return_value=MagicMock(__truediv__=lambda self, other: MagicMock()))
    event_bus = MagicMock()
    cancellation_token = MagicMock()
    cancellation_token.is_cancelled = True  # Already cancelled
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


def _make_db_failing_engine() -> MirrorEngine:
    """Create a MirrorEngine whose db_provider.get_session always raises."""
    db_provider = MagicMock()
    db_provider.get_session = AsyncMock(
        side_effect=RuntimeError("DB connection failed"),
    )

    storage_engine = MagicMock()
    storage_engine.get_path = MagicMock(return_value=MagicMock(__truediv__=lambda self, other: MagicMock()))
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
# Property 2.1: SyncResult all zeros when all suites up-to-date
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestPreservationSyncResultUpToDate:
    """SyncResult is all zeros when _stage_release returns None for all suites.

    When all suites are up-to-date (conditional requests return 304 or
    checksums match), sync_repository() returns a SyncResult with all
    fields at zero. This contract must hold regardless of DB state.

    **Validates: Requirements 3.1**
    """

    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        repo_name=_repo_name_strategy,
        suites=st.lists(_suite_strategy, min_size=1, max_size=5),
        session_id=_session_id_strategy,
    )
    async def test_all_suites_up_to_date_returns_zero_counts(
        self,
        repo_name: str,
        suites: list[str],
        session_id: str,
    ) -> None:
        """When _stage_release returns None for all suites, SyncResult is zeros.

        **Validates: Requirements 3.1**
        """
        factory, db_engine = await _setup_db()
        try:
            engine = _make_mock_engine(factory)

            config = RepositoryConfig(
                name=repo_name,
                base_url="https://deb.example.com/debian",
                suites=suites,
                components=["main"],
                architectures=["amd64"],
            )

            # Mock _stage_release to return None (all suites up-to-date)
            engine._stage_release = AsyncMock(return_value=None)

            result = await engine.sync_repository(config, session_id)

            assert isinstance(result, SyncResult)
            assert result.files_downloaded == 0
            assert result.files_skipped == 0
            assert result.files_failed == 0
            assert result.bytes_transferred == 0
        finally:
            await db_engine.dispose()


# ---------------------------------------------------------------------------
# Property 2.2: SyncResult unchanged when DB fails
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestPreservationSyncResultWithDbFailure:
    """SyncResult is returned unchanged even when DB operations fail.

    When db_provider.get_session() raises an exception, sync_repository()
    still returns a SyncResult. On unfixed code, the pipeline uses the DB
    internally for file tracking; by mocking _stage_release to return None,
    no DB calls happen in the pipeline and SyncResult is returned as zeros.

    This test simulates the try/except path in the fixed code: even if
    session persistence fails, SyncResult must still be returned.

    **Validates: Requirements 3.1, 3.2**
    """

    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        repo_name=_repo_name_strategy,
        suites=st.lists(_suite_strategy, min_size=1, max_size=5),
        session_id=_session_id_strategy,
    )
    async def test_db_failure_still_returns_sync_result(
        self,
        repo_name: str,
        suites: list[str],
        session_id: str,
    ) -> None:
        """DB failure does not prevent SyncResult from being returned.

        **Validates: Requirements 3.1, 3.2**
        """
        engine = _make_db_failing_engine()

        config = RepositoryConfig(
            name=repo_name,
            base_url="https://deb.example.com/debian",
            suites=suites,
            components=["main"],
            architectures=["amd64"],
        )

        # Mock _stage_release to return None so no further DB calls happen
        # in the pipeline stages (no file tracking needed)
        engine._stage_release = AsyncMock(return_value=None)
        # Mock _resume_interrupted_downloads to avoid the DB call at start
        engine._resume_interrupted_downloads = AsyncMock()

        result = await engine.sync_repository(config, session_id)

        assert isinstance(result, SyncResult)
        assert result.files_downloaded == 0
        assert result.files_skipped == 0
        assert result.files_failed == 0
        assert result.bytes_transferred == 0


# ---------------------------------------------------------------------------
# Property 2.3: Cancellation stops processing and returns partial results
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestPreservationCancellationReturnsPartialResults:
    """Cancellation token stops processing and returns partial SyncResult.

    When cancellation_token.is_cancelled is True before processing begins,
    sync_repository() should return immediately with zero counts (no suites
    processed). This verifies the cancellation behavior is preserved.

    **Validates: Requirements 3.2, 3.3**
    """

    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        repo_name=_repo_name_strategy,
        suites=st.lists(_suite_strategy, min_size=1, max_size=5),
        session_id=_session_id_strategy,
    )
    async def test_cancellation_returns_zero_counts(
        self,
        repo_name: str,
        suites: list[str],
        session_id: str,
    ) -> None:
        """Pre-set cancellation returns SyncResult with zero counts.

        **Validates: Requirements 3.2, 3.3**
        """
        factory, db_engine = await _setup_db()
        try:
            engine = _make_cancelled_engine(factory)

            config = RepositoryConfig(
                name=repo_name,
                base_url="https://deb.example.com/debian",
                suites=suites,
                components=["main"],
                architectures=["amd64"],
            )

            result = await engine.sync_repository(config, session_id)

            # With cancellation set from the start, no suites should be
            # processed so all counts remain at zero
            assert isinstance(result, SyncResult)
            assert result.files_downloaded == 0
            assert result.files_skipped == 0
            assert result.files_failed == 0
            assert result.bytes_transferred == 0
        finally:
            await db_engine.dispose()


# ---------------------------------------------------------------------------
# Property 2.4: SyncResult fields match pipeline accumulation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestPreservationSyncResultMatchesPipelineAccumulation:
    """SyncResult fields match what the pipeline stages accumulate.

    For any combination of download outcomes (success, skip, fail counts
    and bytes), when we directly set _result fields and then return via
    the normal path, the returned SyncResult matches exactly.

    This verifies the return contract: sync_repository() always returns
    self._result unchanged.

    **Validates: Requirements 3.1, 3.4**
    """

    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        repo_name=_repo_name_strategy,
        session_id=_session_id_strategy,
        files_downloaded=st.integers(min_value=0, max_value=10000),
        files_skipped=st.integers(min_value=0, max_value=10000),
        files_failed=st.integers(min_value=0, max_value=10000),
        bytes_transferred=st.integers(min_value=0, max_value=10**12),
    )
    async def test_sync_result_matches_accumulated_counts(
        self,
        repo_name: str,
        session_id: str,
        files_downloaded: int,
        files_skipped: int,
        files_failed: int,
        bytes_transferred: int,
    ) -> None:
        """SyncResult fields match pipeline-accumulated values exactly.

        **Validates: Requirements 3.1, 3.4**
        """
        factory, db_engine = await _setup_db()
        try:
            engine = _make_mock_engine(factory)

            config = RepositoryConfig(
                name=repo_name,
                base_url="https://deb.example.com/debian",
                suites=["bookworm"],
                components=["main"],
                architectures=["amd64"],
            )

            # Mock _stage_release to simulate a stage that accumulates
            # specific counts, then returns None to stop further processing
            async def fake_stage_release(cfg, suite):
                engine._result.files_downloaded = files_downloaded
                engine._result.files_skipped = files_skipped
                engine._result.files_failed = files_failed
                engine._result.bytes_transferred = bytes_transferred
                return None  # No further stages

            engine._stage_release = AsyncMock(side_effect=fake_stage_release)

            result = await engine.sync_repository(config, session_id)

            assert isinstance(result, SyncResult)
            assert result.files_downloaded == files_downloaded
            assert result.files_skipped == files_skipped
            assert result.files_failed == files_failed
            assert result.bytes_transferred == bytes_transferred
        finally:
            await db_engine.dispose()
