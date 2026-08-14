"""Property-based tests for error wrapping.

**Validates: Requirements 11.11**

Property 25: For any filesystem PermissionError, FileNotFoundError, or OSError
encountered during a storage operation, the storage layer raises a domain-specific
StorageError rather than propagating the underlying platform exception directly.
The original exception is preserved as __cause__.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.infrastructure.database.provider import SqliteDatabaseProvider
from debcraft.infrastructure.errors import StorageError
from debcraft.infrastructure.storage.providers import LocalStorageProvider


@pytest.mark.unit
@pytest.mark.storage
class TestErrorWrappingStorageProvider:
    """Property 25: Error Wrapping — LocalStorageProvider.

    For any OS-level exception injected into create_directory,
    a StorageError is raised with the original exception as __cause__.
    """

    @given(
        exc_type=st.sampled_from([PermissionError, FileNotFoundError, OSError]),
    )
    def test_create_directory_wraps_os_errors(self, exc_type: type[Exception]) -> None:
        """create_directory wraps filesystem errors as StorageError."""

        async def _run() -> None:
            provider = LocalStorageProvider()
            target_path = Path("/nonexistent/test/path")
            original_exc = exc_type("simulated failure")

            with patch("asyncio.to_thread", side_effect=original_exc):
                with pytest.raises(StorageError) as exc_info:
                    await provider.create_directory(target_path)

                # Original exception preserved as __cause__
                assert exc_info.value.__cause__ is original_exc

        asyncio.run(_run())


@pytest.mark.unit
@pytest.mark.storage
class TestErrorWrappingDatabaseProvider:
    """Property 25: Error Wrapping — SqliteDatabaseProvider.

    For any OperationalError encountered when creating a session,
    a StorageError (specifically DatabaseConnectionError) is raised
    with the original exception as __cause__.
    """

    @given(
        exc_type=st.sampled_from([PermissionError, FileNotFoundError, OSError]),
    )
    def test_database_provider_wraps_connection_errors(self, exc_type: type[Exception]) -> None:
        """get_session wraps OperationalError as DatabaseConnectionError."""
        from sqlalchemy.exc import OperationalError

        async def _run() -> None:
            mock_engine = MagicMock()
            mock_engine.get_path.return_value = Path("/nonexistent/fake.db")
            provider = SqliteDatabaseProvider(storage_engine=mock_engine)

            # Simulate an OperationalError wrapping the OS exception
            original_os_exc = exc_type("simulated OS failure")
            op_error = OperationalError(
                "test",
                params=None,
                orig=original_os_exc,
            )

            with patch.object(provider, "_get_or_create_engine", side_effect=op_error):
                with pytest.raises(StorageError) as exc_info:
                    await provider.get_session("mirror")

                # StorageError raised (DatabaseConnectionError is a subclass)
                assert isinstance(exc_info.value, StorageError)
                # Original OperationalError preserved as __cause__
                assert exc_info.value.__cause__ is op_error

        asyncio.run(_run())
