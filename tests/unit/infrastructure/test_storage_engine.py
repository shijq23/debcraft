"""Unit tests for DefaultStorageEngine.

Verifies directory creation, writability checks, shutdown timeout,
and async context manager behavior using mocked StorageProvider and EventBus.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from debcraft.infrastructure.errors import StorageError, StorageTimeoutError
from debcraft.infrastructure.events import StorageInitializedEvent, StorageShutdownEvent
from debcraft.infrastructure.storage.engine import _ALL_PURPOSES, DefaultStorageEngine


@pytest.fixture
def mock_provider() -> AsyncMock:
    """Create a mock StorageProvider with all methods configured."""
    provider = AsyncMock()
    # resolve_path is sync, so use MagicMock for it
    provider.resolve_path = MagicMock()
    # Map each purpose to a unique Path
    provider.resolve_path.side_effect = lambda purpose, relative="": Path(f"/fake/{purpose}")
    # create_directory succeeds by default
    provider.create_directory = AsyncMock()
    # remove_matching succeeds by default
    provider.remove_matching = AsyncMock()
    # check_writable returns True by default
    provider.check_writable = AsyncMock(return_value=True)
    return provider


@pytest.fixture
def mock_event_bus() -> AsyncMock:
    """Create a mock EventBus."""
    event_bus = AsyncMock()
    event_bus.publish = AsyncMock()
    return event_bus


@pytest.fixture
def engine(mock_provider: AsyncMock, mock_event_bus: AsyncMock) -> DefaultStorageEngine:
    """Create a DefaultStorageEngine with mocked dependencies."""
    return DefaultStorageEngine(provider=mock_provider, event_bus=mock_event_bus)


@pytest.mark.unit
@pytest.mark.storage
class TestInitializeCreatesDirectories:
    """Test that initialize creates all expected directories via the StorageProvider."""

    @pytest.mark.asyncio
    async def test_creates_all_seven_purpose_directories(
        self, engine: DefaultStorageEngine, mock_provider: AsyncMock
    ) -> None:
        """Initialize should call create_directory for each of the 7 purposes."""
        await engine.initialize()

        # Verify create_directory was called 7 times (once per purpose)
        assert mock_provider.create_directory.call_count == 7

        # Verify each purpose had its path created
        created_paths = [c.args[0] for c in mock_provider.create_directory.call_args_list]
        for purpose in _ALL_PURPOSES:
            expected_path = Path(f"/fake/{purpose}")
            assert expected_path in created_paths, f"Expected create_directory to be called for purpose '{purpose}'"

    @pytest.mark.asyncio
    async def test_resolves_all_purpose_paths(self, engine: DefaultStorageEngine, mock_provider: AsyncMock) -> None:
        """Initialize should resolve paths for all 7 purposes."""
        await engine.initialize()

        resolved_purposes = [c.args[0] for c in mock_provider.resolve_path.call_args_list]
        for purpose in _ALL_PURPOSES:
            assert purpose in resolved_purposes

    @pytest.mark.asyncio
    async def test_removes_tmp_files_from_workspace(
        self, engine: DefaultStorageEngine, mock_provider: AsyncMock
    ) -> None:
        """Initialize should remove .tmp and tmp_ files from workspace."""
        await engine.initialize()

        workspace_path = Path("/fake/workspace")
        remove_calls = mock_provider.remove_matching.call_args_list
        assert call(workspace_path, "*.tmp") in remove_calls
        assert call(workspace_path, "tmp_*") in remove_calls

    @pytest.mark.asyncio
    async def test_publishes_storage_initialized_event(
        self, engine: DefaultStorageEngine, mock_event_bus: AsyncMock
    ) -> None:
        """Initialize should publish a StorageInitializedEvent."""
        await engine.initialize()

        mock_event_bus.publish.assert_called_once()
        event = mock_event_bus.publish.call_args.args[0]
        assert isinstance(event, StorageInitializedEvent)


@pytest.mark.unit
@pytest.mark.storage
class TestInitializeWritabilityCheck:
    """Test that initialize raises StorageError if a directory is not writable."""

    @pytest.mark.asyncio
    async def test_raises_storage_error_for_non_writable_directory(
        self, engine: DefaultStorageEngine, mock_provider: AsyncMock
    ) -> None:
        """Initialize should raise StorageError if any directory is not writable."""

        # Make the "logs" directory not writable
        async def check_writable_side_effect(path: Path) -> bool:
            return path != Path("/fake/logs")

        mock_provider.check_writable.side_effect = check_writable_side_effect

        with pytest.raises(StorageError, match="not writable"):
            await engine.initialize()

    @pytest.mark.asyncio
    async def test_error_message_identifies_unwritable_path(
        self, engine: DefaultStorageEngine, mock_provider: AsyncMock
    ) -> None:
        """The StorageError message should identify which directory is not writable."""

        async def check_writable_side_effect(path: Path) -> bool:
            return path != Path("/fake/config")

        mock_provider.check_writable.side_effect = check_writable_side_effect

        with pytest.raises(StorageError, match=re.escape(str(Path("/fake/config")))):
            await engine.initialize()

    @pytest.mark.asyncio
    async def test_event_not_published_when_directory_not_writable(
        self, engine: DefaultStorageEngine, mock_provider: AsyncMock, mock_event_bus: AsyncMock
    ) -> None:
        """StorageInitializedEvent should not be published if writability check fails."""
        # Make the first purpose directory non-writable
        mock_provider.check_writable.return_value = False

        with pytest.raises(StorageError):
            await engine.initialize()

        mock_event_bus.publish.assert_not_called()


@pytest.mark.unit
@pytest.mark.storage
class TestShutdownTimeout:
    """Test that shutdown raises StorageTimeoutError when timeout exceeded."""

    @pytest.mark.asyncio
    async def test_raises_storage_timeout_error_on_timeout(
        self, mock_provider: AsyncMock, mock_event_bus: AsyncMock
    ) -> None:
        """Shutdown should raise StorageTimeoutError if it exceeds 30 seconds."""

        # Make event_bus.publish sleep longer than the timeout
        async def slow_publish(*args: object) -> None:
            await asyncio.sleep(60)

        mock_event_bus.publish.side_effect = slow_publish

        engine = DefaultStorageEngine(provider=mock_provider, event_bus=mock_event_bus)

        # Patch the timeout to a very small value for testing speed
        with patch("debcraft.infrastructure.storage.engine._SHUTDOWN_TIMEOUT_SECONDS", 0.01):
            with pytest.raises(StorageTimeoutError) as exc_info:
                await engine.shutdown()

            assert exc_info.value.timeout_seconds == 0.01

    @pytest.mark.asyncio
    async def test_timeout_error_has_correct_timeout_value(
        self, mock_provider: AsyncMock, mock_event_bus: AsyncMock
    ) -> None:
        """StorageTimeoutError should report the configured timeout value."""

        async def slow_publish(*args: object) -> None:
            await asyncio.sleep(60)

        mock_event_bus.publish.side_effect = slow_publish

        engine = DefaultStorageEngine(provider=mock_provider, event_bus=mock_event_bus)

        with patch("debcraft.infrastructure.storage.engine._SHUTDOWN_TIMEOUT_SECONDS", 0.05):
            with pytest.raises(StorageTimeoutError) as exc_info:
                await engine.shutdown()

            assert exc_info.value.timeout_seconds == 0.05

    @pytest.mark.asyncio
    async def test_shutdown_publishes_event_when_successful(
        self, engine: DefaultStorageEngine, mock_event_bus: AsyncMock
    ) -> None:
        """Shutdown should publish StorageShutdownEvent when it completes normally."""
        await engine.shutdown()

        mock_event_bus.publish.assert_called_once()
        event = mock_event_bus.publish.call_args.args[0]
        assert isinstance(event, StorageShutdownEvent)


@pytest.mark.unit
@pytest.mark.storage
class TestAsyncContextManager:
    """Test __aenter__/__aexit__ calls initialize/shutdown in order."""

    @pytest.mark.asyncio
    async def test_aenter_calls_initialize(self, mock_provider: AsyncMock, mock_event_bus: AsyncMock) -> None:
        """__aenter__ should call initialize and return the engine."""
        engine = DefaultStorageEngine(provider=mock_provider, event_bus=mock_event_bus)

        result = await engine.__aenter__()

        assert result is engine
        # Verify initialize was called (directories were created)
        assert mock_provider.create_directory.call_count == 7

    @pytest.mark.asyncio
    async def test_aexit_calls_shutdown(self, mock_provider: AsyncMock, mock_event_bus: AsyncMock) -> None:
        """__aexit__ should call shutdown."""
        engine = DefaultStorageEngine(provider=mock_provider, event_bus=mock_event_bus)

        await engine.__aenter__()
        mock_event_bus.publish.reset_mock()

        await engine.__aexit__(None, None, None)

        # Verify shutdown was called (StorageShutdownEvent published)
        mock_event_bus.publish.assert_called_once()
        event = mock_event_bus.publish.call_args.args[0]
        assert isinstance(event, StorageShutdownEvent)

    @pytest.mark.asyncio
    async def test_context_manager_protocol_order(self, mock_provider: AsyncMock, mock_event_bus: AsyncMock) -> None:
        """Using async with should call initialize then shutdown in order."""
        engine = DefaultStorageEngine(provider=mock_provider, event_bus=mock_event_bus)

        events_published: list[str] = []

        async def track_publish(event: object) -> None:
            if isinstance(event, StorageInitializedEvent):
                events_published.append("initialized")
            elif isinstance(event, StorageShutdownEvent):
                events_published.append("shutdown")

        mock_event_bus.publish.side_effect = track_publish

        async with engine as eng:
            assert eng is engine
            # After __aenter__, initialize should have been called
            assert "initialized" in events_published
            assert "shutdown" not in events_published

        # After __aexit__, shutdown should have been called
        assert events_published == ["initialized", "shutdown"]

    @pytest.mark.asyncio
    async def test_aexit_called_even_on_exception(self, mock_provider: AsyncMock, mock_event_bus: AsyncMock) -> None:
        """__aexit__ (shutdown) should still be called if an exception occurs in the block."""
        engine = DefaultStorageEngine(provider=mock_provider, event_bus=mock_event_bus)

        events_published: list[str] = []

        async def track_publish(event: object) -> None:
            if isinstance(event, StorageInitializedEvent):
                events_published.append("initialized")
            elif isinstance(event, StorageShutdownEvent):
                events_published.append("shutdown")

        mock_event_bus.publish.side_effect = track_publish

        with pytest.raises(ValueError, match="test error"):
            async with engine:
                raise ValueError("test error")

        # shutdown should still have been called
        assert "shutdown" in events_published
