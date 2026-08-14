"""Preservation property tests for mirror state persistence.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

Property 2: Preservation — First-Run and Changed-Checksum Behavior

These tests verify baseline behaviors that must remain unchanged after
the bugfix is applied. They are run BEFORE the fix to establish the
expected behavior, and again AFTER the fix to confirm no regressions.

All tests in this file MUST PASS on both unfixed and fixed code.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from sqlalchemy import inspect, select

from debcraft.cli.mirror import _CliDatabaseProvider
from debcraft.domain.mirror.comparator import FileComparator
from debcraft.domain.mirror.values import FileEntry
from debcraft.infrastructure.models.mirror import RepositoryFile

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Valid SHA256 hex strings (64 hex characters)
_sha256_strategy = st.text(
    alphabet="0123456789abcdef",
    min_size=64,
    max_size=64,
)

# Relative paths: non-empty strings with path-like characters
_relative_path_strategy = st.text(
    st.characters(
        whitelist_categories=("L", "N"),
        whitelist_characters="/-_.",
    ),
    min_size=1,
    max_size=100,
).filter(lambda s: len(s.strip()) > 0)

# File sizes (positive integers)
_size_strategy = st.integers(min_value=1, max_value=10**9)


def _file_entry_strategy() -> st.SearchStrategy[FileEntry]:
    """Generate a valid FileEntry with random sha256, path, and size."""
    return st.builds(
        FileEntry,
        relative_path=_relative_path_strategy,
        sha256=_sha256_strategy,
        size_bytes=_size_strategy,
    )


# ---------------------------------------------------------------------------
# Test 1: First-run schema creation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestPreservationSchemaCreation:
    """First-run schema creation preserves expected table structure.

    For any new _CliDatabaseProvider instance, get_session("mirror")
    succeeds and the tables repository_files and sync_sessions exist.

    **Validates: Requirements 3.1**
    """

    @settings(
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(data=st.data())
    async def test_schema_tables_exist_on_first_run(
        self,
        data: st.DataObject,
    ) -> None:
        """A new provider instance creates the mirror schema tables.

        **Validates: Requirements 3.1**
        """
        tmp_dir = tempfile.mkdtemp()
        with patch(
            "debcraft.cli.mirror.resolve_xdg_path",
            side_effect=lambda purpose, **kw: Path(tmp_dir) / purpose,
        ):
            provider = _CliDatabaseProvider()
        try:
            session = await provider.get_session("mirror")
            try:
                # Use the engine's connection to inspect tables
                conn = await session.connection()
                _raw_conn = await conn.get_raw_connection()

                # Query sqlite_master for table names
                _result = await session.execute(select(RepositoryFile).limit(0))
                # If we get here without error, the table exists
                table_names_result = await conn.run_sync(lambda sync_conn: inspect(sync_conn.engine).get_table_names())

                assert "repository_files" in table_names_result, "Table 'repository_files' not found in schema"
                assert "sync_sessions" in table_names_result, "Table 'sync_sessions' not found in schema"
            finally:
                await session.close()
        finally:
            await provider.dispose()


# ---------------------------------------------------------------------------
# Test 2: Empty checksums on first run
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestPreservationEmptyFirstRun:
    """Empty database returns no records on first run.

    For a new provider instance, querying for RepositoryFile records
    returns nothing, confirming first-run triggers full download.

    **Validates: Requirements 3.1**
    """

    @settings(
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(data=st.data())
    async def test_empty_database_returns_no_records(
        self,
        data: st.DataObject,
    ) -> None:
        """A fresh provider has no RepositoryFile records.

        **Validates: Requirements 3.1**
        """
        tmp_dir = tempfile.mkdtemp()
        with patch(
            "debcraft.cli.mirror.resolve_xdg_path",
            side_effect=lambda purpose, **kw: Path(tmp_dir) / purpose,
        ):
            provider = _CliDatabaseProvider()
        try:
            session = await provider.get_session("mirror")
            try:
                stmt = select(RepositoryFile)
                result = await session.execute(stmt)
                records = result.scalars().all()

                assert len(records) == 0, f"Expected empty database but found {len(records)} records"
            finally:
                await session.close()
        finally:
            await provider.dispose()


# ---------------------------------------------------------------------------
# Test 3: FileComparator with empty local checksums
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestPreservationComparatorEmptyChecksums:
    """FileComparator produces all download decisions with empty checksums.

    For any list of FileEntry objects and an empty local_checksums dict,
    compute_sync_decisions() returns all decisions with action="download"
    and reason="file not cached".

    **Validates: Requirements 3.1**
    """

    @given(
        entries=st.lists(
            _file_entry_strategy(),
            min_size=1,
            max_size=20,
        ),
    )
    def test_empty_checksums_all_download(
        self,
        entries: list[FileEntry],
    ) -> None:
        """All entries produce download decisions when no local checksums exist.

        **Validates: Requirements 3.1**
        """
        comparator = FileComparator()
        local_checksums: dict[str, str] = {}

        decisions = comparator.compute_sync_decisions(entries, local_checksums)

        assert len(decisions) == len(entries)
        for decision in decisions:
            assert decision.action == "download", (
                f"Expected action='download' but got '{decision.action}' for path '{decision.file_entry.relative_path}'"
            )
            assert decision.reason == "file not cached", (
                f"Expected reason='file not cached' but got '{decision.reason}'"
            )


# ---------------------------------------------------------------------------
# Test 4: FileComparator with matching checksums
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestPreservationComparatorMatchingChecksums:
    """FileComparator produces skip decisions with matching checksums.

    For any FileEntry where the local_checksums dict has the same sha256
    for that path, compute_sync_decisions() returns action="skip" with
    reason="checksum matches".

    **Validates: Requirements 3.2**
    """

    @given(entry=_file_entry_strategy())
    def test_matching_checksum_produces_skip(
        self,
        entry: FileEntry,
    ) -> None:
        """A matching sha256 produces a skip decision.

        **Validates: Requirements 3.2**
        """
        comparator = FileComparator()
        local_checksums = {entry.relative_path: entry.sha256}

        decisions = comparator.compute_sync_decisions([entry], local_checksums)

        assert len(decisions) == 1
        assert decisions[0].action == "skip", (
            f"Expected action='skip' but got '{decisions[0].action}' for matching checksum"
        )
        assert decisions[0].reason == "checksum matches", (
            f"Expected reason='checksum matches' but got '{decisions[0].reason}'"
        )


# ---------------------------------------------------------------------------
# Test 5: FileComparator with differing checksums
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestPreservationComparatorDifferingChecksums:
    """FileComparator produces download decisions with differing checksums.

    For any FileEntry where the local_checksums dict has a DIFFERENT sha256
    for that path, compute_sync_decisions() returns action="download" with
    reason="checksum differs".

    **Validates: Requirements 3.2**
    """

    @given(
        entry=_file_entry_strategy(),
        different_sha256=_sha256_strategy,
    )
    def test_differing_checksum_produces_download(
        self,
        entry: FileEntry,
        different_sha256: str,
    ) -> None:
        """A differing sha256 produces a download decision.

        **Validates: Requirements 3.2**
        """
        assume(different_sha256 != entry.sha256)

        comparator = FileComparator()
        local_checksums = {entry.relative_path: different_sha256}

        decisions = comparator.compute_sync_decisions([entry], local_checksums)

        assert len(decisions) == 1
        assert decisions[0].action == "download", (
            f"Expected action='download' but got '{decisions[0].action}' for differing checksum"
        )
        assert decisions[0].reason == "checksum differs", (
            f"Expected reason='checksum differs' but got '{decisions[0].reason}'"
        )
