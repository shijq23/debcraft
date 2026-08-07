"""Preservation property tests for SnapshotPublisher._get_schema_version baseline behavior.

**Validates: Requirements 3.1, 3.2, 3.4**

Property 2: Preservation — Existing Table Behavior Unchanged

These tests capture baseline behavior of _get_schema_version that already works
correctly on the UNFIXED code and must continue to work after the fix is applied:
- Table with rows returns MAX(version)
- Empty table returns 0
- Non-"no such table" OperationalErrors propagate

These tests MUST PASS on the current unfixed code (they verify baseline behavior
to preserve) and MUST STILL PASS after the fix is applied (confirming no regressions).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from debcraft.infrastructure.mirror.publisher import SnapshotPublisher


async def _create_db_with_history_table(
    versions: list[int],
) -> tuple[async_sessionmaker[AsyncSession], object]:
    """Create an in-memory SQLite database with _migration_history table and given versions."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE _migration_history ("
                "    version INTEGER PRIMARY KEY,"
                "    applied_at TEXT NOT NULL DEFAULT (datetime('now')),"
                "    duration_ms INTEGER NOT NULL DEFAULT 0"
                ")"
            )
        )
        for v in versions:
            await conn.execute(
                text(
                    "INSERT OR IGNORE INTO _migration_history "
                    "(version, applied_at, duration_ms) "
                    "VALUES (:version, datetime('now'), 0)"
                ),
                {"version": v},
            )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


@pytest.mark.unit
class TestPublisherPreservationVersionRetrieval:
    """Property 2: Preservation — _get_schema_version returns MAX(version).

    For any database with _migration_history containing rows,
    _get_schema_version must return the maximum version number.

    **Validates: Requirements 3.1**
    """

    @settings(max_examples=50)
    @given(
        versions=st.lists(
            st.integers(min_value=1, max_value=10000),
            min_size=1,
            max_size=20,
            unique=True,
        )
    )
    def test_returns_max_version_from_populated_table(self, versions: list[int]) -> None:
        """_get_schema_version returns max(versions) for any non-empty version set.

        **Validates: Requirements 3.1**
        """

        async def _run() -> None:
            factory, engine = await _create_db_with_history_table(versions)
            try:
                async with factory() as session:
                    result = await SnapshotPublisher._get_schema_version(session)
                    assert result == max(versions), f"Expected max({versions})={max(versions)}, got {result}"
            finally:
                await engine.dispose()

        asyncio.run(_run())


@pytest.mark.unit
class TestPublisherPreservationEmptyTable:
    """Property 2: Preservation — empty _migration_history returns 0.

    For a database with an empty _migration_history table,
    _get_schema_version must return 0.

    **Validates: Requirements 3.2**
    """

    def test_returns_zero_for_empty_migration_history(self) -> None:
        """_get_schema_version returns 0 when _migration_history exists but is empty.

        **Validates: Requirements 3.2**
        """

        async def _run() -> None:
            factory, engine = await _create_db_with_history_table([])
            try:
                async with factory() as session:
                    result = await SnapshotPublisher._get_schema_version(session)
                    assert result == 0, f"Expected 0 for empty table, got {result}"
            finally:
                await engine.dispose()

        asyncio.run(_run())


@pytest.mark.unit
class TestPublisherPreservationErrorPropagation:
    """Property 2: Preservation — non-"no such table" OperationalErrors propagate.

    For any OperationalError whose message does NOT contain "no such table",
    _get_schema_version must re-raise the exception (not swallow it).

    **Validates: Requirements 3.4**
    """

    @settings(max_examples=30)
    @given(
        error_message=st.sampled_from(
            [
                "database is locked",
                "disk I/O error",
                "attempt to write a readonly database",
                "database or disk is full",
                "unable to open database file",
            ]
        )
    )
    def test_non_table_missing_errors_propagate(self, error_message: str) -> None:
        """OperationalError with messages NOT containing "no such table" must propagate.

        **Validates: Requirements 3.4**
        """

        async def _run() -> None:
            # Create a mock session that raises an OperationalError
            mock_session = AsyncMock(spec=AsyncSession)
            # OperationalError requires (message, params, orig) args
            orig_error = Exception(error_message)
            op_error = OperationalError(
                statement="SELECT MAX(version) FROM _migration_history",
                params=None,
                orig=orig_error,
            )
            mock_session.execute = AsyncMock(side_effect=op_error)

            with pytest.raises(OperationalError) as exc_info:
                await SnapshotPublisher._get_schema_version(mock_session)

            # Verify the error propagated (not swallowed)
            assert "no such table" not in str(exc_info.value)

        asyncio.run(_run())
