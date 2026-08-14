"""Bug condition exploration test for _get_schema_version when _migration_history table is missing.

**Validates: Requirements 1.1, 1.2**

Property 1: Bug Condition — Missing Table Raises OperationalError

This test demonstrates that SnapshotPublisher._get_schema_version() crashes with
`sqlalchemy.exc.OperationalError: no such table: _migration_history` when the
migration history table does not exist in the database.

The test encodes the EXPECTED behavior: _get_schema_version() should return 0
without raising an exception when the table is missing. On unfixed code, this test
FAILS with OperationalError, confirming the bug exists. After the fix is applied,
this test PASSES.
"""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from debcraft.infrastructure.mirror.publisher import SnapshotPublisher

# Strategy: generate a list of arbitrary table names (other than _migration_history)
# to optionally create in the database before calling _get_schema_version.
# This demonstrates the bug occurs regardless of what other tables exist.
# Filter out SQL reserved words that would cause syntax errors when used as unquoted
# table names in CREATE TABLE statements.
_SQL_RESERVED_WORDS = frozenset(
    {
        "abort",
        "action",
        "add",
        "after",
        "all",
        "alter",
        "always",
        "analyze",
        "and",
        "as",
        "asc",
        "attach",
        "autoincrement",
        "before",
        "begin",
        "between",
        "by",
        "cascade",
        "case",
        "cast",
        "check",
        "collate",
        "column",
        "commit",
        "conflict",
        "constraint",
        "create",
        "cross",
        "current",
        "current_date",
        "current_time",
        "current_timestamp",
        "database",
        "default",
        "deferrable",
        "deferred",
        "delete",
        "desc",
        "detach",
        "distinct",
        "do",
        "drop",
        "each",
        "else",
        "end",
        "escape",
        "except",
        "exclude",
        "exclusive",
        "exists",
        "explain",
        "fail",
        "filter",
        "first",
        "following",
        "for",
        "foreign",
        "from",
        "full",
        "generated",
        "glob",
        "group",
        "groups",
        "having",
        "if",
        "ignore",
        "immediate",
        "in",
        "index",
        "indexed",
        "initially",
        "inner",
        "insert",
        "instead",
        "intersect",
        "into",
        "is",
        "isnull",
        "join",
        "key",
        "last",
        "left",
        "like",
        "limit",
        "match",
        "materialized",
        "natural",
        "no",
        "not",
        "nothing",
        "notnull",
        "null",
        "nulls",
        "of",
        "offset",
        "on",
        "or",
        "order",
        "others",
        "outer",
        "over",
        "partition",
        "plan",
        "pragma",
        "preceding",
        "primary",
        "query",
        "raise",
        "range",
        "recursive",
        "references",
        "regexp",
        "reindex",
        "release",
        "rename",
        "replace",
        "restrict",
        "returning",
        "right",
        "rollback",
        "row",
        "rows",
        "savepoint",
        "select",
        "set",
        "table",
        "temp",
        "temporary",
        "then",
        "ties",
        "to",
        "transaction",
        "trigger",
        "unbounded",
        "union",
        "unique",
        "update",
        "using",
        "vacuum",
        "values",
        "view",
        "virtual",
        "when",
        "where",
        "window",
        "with",
        "without",
    }
)

other_table_names_strategy = st.lists(
    st.from_regex(r"[a-z][a-z0-9_]{0,15}", fullmatch=True).filter(
        lambda name: name != "_migration_history" and name not in _SQL_RESERVED_WORDS
    ),
    min_size=0,
    max_size=3,
    unique=True,
)


@pytest.mark.unit
class TestGetSchemaVersionBugCondition:
    """Exploration test confirming _get_schema_version crashes when table is missing.

    These tests encode the EXPECTED behavior (return 0 without exception).
    They FAIL on unfixed code with OperationalError, confirming the bug exists.
    After the fix, they PASS.
    """

    @given(other_tables=other_table_names_strategy)
    def test_missing_migration_history_returns_zero(self, other_tables: list[str]) -> None:
        """For any database without _migration_history, _get_schema_version returns 0.

        On unfixed code, this raises:
        sqlalchemy.exc.OperationalError: no such table: _migration_history

        This confirms the bug: the method queries _migration_history unconditionally
        without handling the case where the table does not exist.

        **Validates: Requirements 1.1, 1.2**
        """

        async def _run() -> int:
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
            try:
                # Create optional other tables (but NOT _migration_history)
                if other_tables:
                    async with engine.begin() as conn:
                        for table_name in other_tables:
                            await conn.execute(text(f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY)"))

                factory = async_sessionmaker(engine, expire_on_commit=False)
                async with factory() as session:
                    # Call the method under test - on unfixed code this raises OperationalError
                    result = await SnapshotPublisher._get_schema_version(session)
                    return result
            finally:
                await engine.dispose()

        result = asyncio.run(_run())

        # Expected behavior: should return 0 when table is missing
        assert result == 0, (
            f"_get_schema_version should return 0 when _migration_history table is missing, "
            f"but got {result}. Other tables present: {other_tables}"
        )
