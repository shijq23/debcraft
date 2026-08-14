"""Bug condition exploration test for mirror state persistence.

**Validates: Requirements 1.1, 1.2**

Property 1: Bug Condition — Mirror State Persists Between Invocations

This test demonstrates that `_CliDatabaseProvider` uses an ephemeral in-memory
SQLite database that loses all state between provider instances. When a
`RepositoryFile` record is written via one provider instance, creating a second
provider instance results in an empty database with no prior records.

This test is EXPECTED TO FAIL on unfixed code, confirming the bug exists:
the in-memory database is discarded when the provider is disposed, so the
second provider instance cannot find any previously stored records.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select

from debcraft.cli.mirror import _CliDatabaseProvider
from debcraft.infrastructure.models.mirror import (
    RepositoryFile,
    RepositoryFileState,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate valid URLs for RepositoryFile records
url_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N"),
        whitelist_characters="-._~/:",
    ),
    min_size=5,
    max_size=200,
).map(lambda s: f"http://example.com/{s}")

# Generate valid SHA256 hex strings (64 hex characters)
sha256_strategy = st.text(
    alphabet="0123456789abcdef",
    min_size=64,
    max_size=64,
)

# Generate valid file sizes
size_bytes_strategy = st.integers(min_value=1, max_value=2**40)


# ---------------------------------------------------------------------------
# Property 1: Bug Condition - Mirror State Persists Between Invocations
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestProperty1MirrorStatePersistence:
    """Property 1: Bug Condition — Mirror State Persists Between Invocations.

    This test MUST FAIL on unfixed code to confirm the bug exists.
    The `_CliDatabaseProvider` uses `sqlite+aiosqlite:///` (in-memory),
    so all records are lost when the provider is disposed and a new one is created.
    """

    @settings(
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        url=url_strategy,
        sha256=sha256_strategy,
        size_bytes=size_bytes_strategy,
    )
    async def test_record_persists_across_provider_instances(
        self,
        url: str,
        sha256: str,
        size_bytes: int,
    ) -> None:
        """A RepositoryFile written via one provider instance is retrievable from a second instance.

        **Validates: Requirements 1.1, 1.2**

        Steps:
        1. Create first _CliDatabaseProvider → get session → add RepositoryFile → commit → dispose
        2. Create second _CliDatabaseProvider → get session → query for same URL
        3. Assert the record is found with matching sha256

        On unfixed code, the second provider creates a fresh in-memory database,
        so the query returns no results — demonstrating the bug.
        """
        # Use a fresh temp directory per example for full isolation
        tmp_dir = tempfile.mkdtemp()
        with patch(
            "debcraft.cli.mirror.resolve_xdg_path",
            side_effect=lambda purpose, **kw: Path(tmp_dir) / purpose,
        ):
            # --- First provider: write a record ---
            provider1 = _CliDatabaseProvider()
            session1 = await provider1.get_session("mirror")
            try:
                record = RepositoryFile(
                    url=url,
                    sha256=sha256,
                    size_bytes=size_bytes,
                    state=RepositoryFileState.VERIFIED,
                    retry_count=0,
                )
                session1.add(record)
                await session1.commit()
            finally:
                await session1.close()
            await provider1.dispose()

            # --- Second provider: query for the same record ---
            provider2 = _CliDatabaseProvider()
            session2 = await provider2.get_session("mirror")
            try:
                stmt = select(RepositoryFile).where(RepositoryFile.url == url)
                result = await session2.execute(stmt)
                found = result.scalar_one_or_none()

                # BUG: On unfixed code, found is None because
                # in-memory DB was discarded
                assert found is not None, (
                    f"RepositoryFile(url='{url}') stored in first "
                    f"provider instance was NOT found in second "
                    f"provider instance — in-memory database does "
                    f"not persist state between invocations"
                )
                assert found.sha256 == sha256, (
                    f"Retrieved record has sha256='{found.sha256}' but expected '{sha256}' — data corruption"
                )
                assert found.size_bytes == size_bytes, (
                    f"Retrieved record has size_bytes={found.size_bytes} but expected {size_bytes}"
                )
            finally:
                await session2.close()
            await provider2.dispose()
