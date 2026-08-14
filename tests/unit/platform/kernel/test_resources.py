"""Unit tests for KernelResourceManager.

Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

import pytest

from debcraft.platform.kernel.resources import KernelResourceManager


@pytest.fixture
def manager() -> KernelResourceManager:
    """Create a fresh KernelResourceManager for each test."""
    return KernelResourceManager()


# ---------------------------------------------------------------------------
# Helper context managers for testing
# ---------------------------------------------------------------------------


@asynccontextmanager
async def async_resource(name: str, log: list[str]) -> AsyncIterator[str]:
    """Async context manager that logs acquire and cleanup."""
    log.append(f"acquire:{name}")
    try:
        yield name
    finally:
        log.append(f"cleanup:{name}")


@contextmanager
def sync_resource(name: str, log: list[str]) -> Iterator[str]:
    """Sync context manager that logs acquire and cleanup."""
    log.append(f"acquire:{name}")
    try:
        yield name
    finally:
        log.append(f"cleanup:{name}")


@asynccontextmanager
async def failing_async_resource(name: str, log: list[str]) -> AsyncIterator[str]:
    """Async context manager whose cleanup raises an exception."""
    log.append(f"acquire:{name}")
    try:
        yield name
    finally:
        log.append(f"cleanup:{name}")
        raise RuntimeError(f"Cleanup failed for {name}")


@contextmanager
def failing_sync_resource(name: str, log: list[str]) -> Iterator[str]:
    """Sync context manager whose cleanup raises an exception."""
    log.append(f"acquire:{name}")
    try:
        yield name
    finally:
        log.append(f"cleanup:{name}")
        raise RuntimeError(f"Cleanup failed for {name}")


# ---------------------------------------------------------------------------
# Async resource acquisition and cleanup (Requirement 6.1, 6.3)
# ---------------------------------------------------------------------------


class TestAsyncResourceAcquisitionAndCleanup:
    """Test async resource acquisition and cleanup."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_acquire_async_returns_resource_value(self, manager: KernelResourceManager) -> None:
        log: list[str] = []

        value = await manager.acquire_async(async_resource("db", log))

        assert value == "db"
        assert "acquire:db" in log

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_cleanup_calls_aexit_on_async_resource(self, manager: KernelResourceManager) -> None:
        log: list[str] = []
        await manager.acquire_async(async_resource("session", log))

        await manager.cleanup()

        assert "cleanup:session" in log

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_multiple_async_resources_all_cleaned(self, manager: KernelResourceManager) -> None:
        log: list[str] = []
        await manager.acquire_async(async_resource("r1", log))
        await manager.acquire_async(async_resource("r2", log))
        await manager.acquire_async(async_resource("r3", log))

        await manager.cleanup()

        assert "cleanup:r1" in log
        assert "cleanup:r2" in log
        assert "cleanup:r3" in log


# ---------------------------------------------------------------------------
# Sync resource acquisition and cleanup (Requirement 6.2, 6.3)
# ---------------------------------------------------------------------------


class TestSyncResourceAcquisitionAndCleanup:
    """Test sync resource acquisition and cleanup."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_acquire_sync_returns_resource_value(self, manager: KernelResourceManager) -> None:
        log: list[str] = []

        value = manager.acquire_sync(sync_resource("tmpdir", log))

        assert value == "tmpdir"
        assert "acquire:tmpdir" in log

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_cleanup_calls_exit_on_sync_resource(self, manager: KernelResourceManager) -> None:
        log: list[str] = []
        manager.acquire_sync(sync_resource("file", log))

        await manager.cleanup()

        assert "cleanup:file" in log

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_multiple_sync_resources_all_cleaned(self, manager: KernelResourceManager) -> None:
        log: list[str] = []
        manager.acquire_sync(sync_resource("s1", log))
        manager.acquire_sync(sync_resource("s2", log))
        manager.acquire_sync(sync_resource("s3", log))

        await manager.cleanup()

        assert "cleanup:s1" in log
        assert "cleanup:s2" in log
        assert "cleanup:s3" in log


# ---------------------------------------------------------------------------
# Reverse-order cleanup (Requirement 6.4)
# ---------------------------------------------------------------------------


class TestReverseOrderCleanup:
    """Test resources are cleaned up in reverse acquisition order (LIFO)."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_async_resources_cleaned_in_reverse_order(self, manager: KernelResourceManager) -> None:
        log: list[str] = []
        await manager.acquire_async(async_resource("first", log))
        await manager.acquire_async(async_resource("second", log))
        await manager.acquire_async(async_resource("third", log))

        await manager.cleanup()

        cleanup_events = [e for e in log if e.startswith("cleanup:")]
        assert cleanup_events == ["cleanup:third", "cleanup:second", "cleanup:first"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_sync_resources_cleaned_in_reverse_order(self, manager: KernelResourceManager) -> None:
        log: list[str] = []
        manager.acquire_sync(sync_resource("A", log))
        manager.acquire_sync(sync_resource("B", log))
        manager.acquire_sync(sync_resource("C", log))

        await manager.cleanup()

        cleanup_events = [e for e in log if e.startswith("cleanup:")]
        assert cleanup_events == ["cleanup:C", "cleanup:B", "cleanup:A"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_mixed_async_and_sync_cleaned_in_reverse_order(self, manager: KernelResourceManager) -> None:
        log: list[str] = []
        await manager.acquire_async(async_resource("async1", log))
        manager.acquire_sync(sync_resource("sync1", log))
        await manager.acquire_async(async_resource("async2", log))

        await manager.cleanup()

        cleanup_events = [e for e in log if e.startswith("cleanup:")]
        assert cleanup_events == ["cleanup:async2", "cleanup:sync1", "cleanup:async1"]


# ---------------------------------------------------------------------------
# Cleanup failure isolation (Requirement 6.5)
# ---------------------------------------------------------------------------


class TestCleanupFailureIsolation:
    """Test that one failing cleanup doesn't stop others."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_failing_cleanup_does_not_prevent_others(self, manager: KernelResourceManager) -> None:
        log: list[str] = []
        await manager.acquire_async(async_resource("healthy1", log))
        await manager.acquire_async(failing_async_resource("broken", log))
        await manager.acquire_async(async_resource("healthy2", log))

        await manager.cleanup()

        # All three should have cleanup attempted
        assert "cleanup:healthy1" in log
        assert "cleanup:broken" in log
        assert "cleanup:healthy2" in log

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_failing_sync_cleanup_does_not_prevent_others(self, manager: KernelResourceManager) -> None:
        log: list[str] = []
        manager.acquire_sync(sync_resource("ok1", log))
        manager.acquire_sync(failing_sync_resource("bad", log))
        manager.acquire_sync(sync_resource("ok2", log))

        await manager.cleanup()

        assert "cleanup:ok1" in log
        assert "cleanup:bad" in log
        assert "cleanup:ok2" in log

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_multiple_failures_still_clean_all(self, manager: KernelResourceManager) -> None:
        log: list[str] = []
        await manager.acquire_async(async_resource("good", log))
        await manager.acquire_async(failing_async_resource("bad1", log))
        await manager.acquire_async(failing_async_resource("bad2", log))

        await manager.cleanup()

        assert "cleanup:good" in log
        assert "cleanup:bad1" in log
        assert "cleanup:bad2" in log

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_cleanup_failure_logs_error(
        self, manager: KernelResourceManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        log: list[str] = []
        await manager.acquire_async(failing_async_resource("broken", log))

        with caplog.at_level(logging.ERROR):
            await manager.cleanup()

        assert any(record.levelno == logging.ERROR for record in caplog.records)
        assert any("Failed to clean up resource" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Empty cleanup (Requirement 6.3)
# ---------------------------------------------------------------------------


class TestEmptyCleanup:
    """Test cleanup with no resources acquired."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_cleanup_with_no_resources_is_noop(self, manager: KernelResourceManager) -> None:
        # Should not raise
        await manager.cleanup()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_double_cleanup_is_safe(self, manager: KernelResourceManager) -> None:
        log: list[str] = []
        await manager.acquire_async(async_resource("res", log))

        await manager.cleanup()
        await manager.cleanup()  # Second cleanup should be a no-op

        cleanup_events = [e for e in log if e.startswith("cleanup:")]
        assert cleanup_events == ["cleanup:res"]
