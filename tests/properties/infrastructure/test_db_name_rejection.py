"""Property-based tests for invalid database name rejection.

**Validates: Requirements 2.9**

Property 3: For any string that is not one of "mirror", "metadata", or "cache",
requesting a session from DatabaseProvider raises a StorageError identifying
the unrecognized database name.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.infrastructure.database.provider import SqliteDatabaseProvider
from debcraft.infrastructure.errors import StorageError


def _make_provider() -> SqliteDatabaseProvider:
    """Create a SqliteDatabaseProvider with a mocked StorageEngine."""
    mock_engine = MagicMock()
    mock_engine.get_path.return_value = Path("/nonexistent/fake.db")
    return SqliteDatabaseProvider(storage_engine=mock_engine)


@pytest.mark.unit
@pytest.mark.storage
class TestInvalidDatabaseNameRejection:
    """Property 3: Invalid Database Name Rejection.

    For any string that is not one of "mirror", "metadata", or "cache",
    get_session() raises a StorageError indicating the unrecognized name.
    """

    @given(
        invalid_name=st.text().filter(lambda s: s not in {"mirror", "metadata", "cache"}),
    )
    def test_invalid_name_raises_storage_error(self, invalid_name: str) -> None:
        """get_session rejects any name not in the valid set."""

        async def _run() -> None:
            provider = _make_provider()
            with pytest.raises(StorageError) as exc_info:
                await provider.get_session(invalid_name)  # type: ignore[arg-type]

            # The error message should identify the unrecognized name.
            assert "Unrecognized" in str(exc_info.value)

        asyncio.run(_run())
