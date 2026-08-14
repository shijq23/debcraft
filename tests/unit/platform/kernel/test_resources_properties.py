"""Property-based tests for the KernelResourceManager.

Properties 20-21 validate resource cleanup ordering and failure isolation
across many randomized inputs.

**Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5**
"""

import asyncio
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from types import TracebackType

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.platform.kernel.resources import KernelResourceManager

# ===========================================================================
# Helper context managers that track enter/exit ordering
# ===========================================================================


class TrackingAsyncContextManager(AbstractAsyncContextManager["TrackingAsyncContextManager"]):
    """Async context manager that records cleanup order in a shared list."""

    def __init__(self, index: int, cleanup_log: list[int]) -> None:
        self.index = index
        self._cleanup_log = cleanup_log

    async def __aenter__(self) -> "TrackingAsyncContextManager":
        """Enter the async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the async context manager and record cleanup."""
        self._cleanup_log.append(self.index)


class TrackingSyncContextManager(AbstractContextManager["TrackingSyncContextManager"]):
    """Sync context manager that records cleanup order in a shared list."""

    def __init__(self, index: int, cleanup_log: list[int]) -> None:
        self.index = index
        self._cleanup_log = cleanup_log

    def __enter__(self) -> "TrackingSyncContextManager":
        """Enter the sync context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the sync context manager and record cleanup."""
        self._cleanup_log.append(self.index)


class FailingAsyncContextManager(AbstractAsyncContextManager["FailingAsyncContextManager"]):
    """Async context manager that raises on exit to simulate cleanup failure."""

    def __init__(self, index: int, cleanup_log: list[int]) -> None:
        self.index = index
        self._cleanup_log = cleanup_log

    async def __aenter__(self) -> "FailingAsyncContextManager":
        """Enter the async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the async context manager, record cleanup, and raise."""
        self._cleanup_log.append(self.index)
        msg = f"Simulated cleanup failure for resource {self.index}"
        raise RuntimeError(msg)


class FailingSyncContextManager(AbstractContextManager["FailingSyncContextManager"]):
    """Sync context manager that raises on exit to simulate cleanup failure."""

    def __init__(self, index: int, cleanup_log: list[int]) -> None:
        self.index = index
        self._cleanup_log = cleanup_log

    def __enter__(self) -> "FailingSyncContextManager":
        """Enter the sync context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the sync context manager, record cleanup, and raise."""
        self._cleanup_log.append(self.index)
        msg = f"Simulated cleanup failure for resource {self.index}"
        raise RuntimeError(msg)


# ===========================================================================
# Strategies
# ===========================================================================

# Number of resources to acquire (at least 2 to test ordering)
_resource_count = st.integers(min_value=2, max_value=20)


# Strategy for selecting which resources should fail during cleanup.
# Produces a sorted list of unique indices in [0, n) representing failing resources.
def _failing_indices(n: int) -> st.SearchStrategy[list[int]]:
    """Generate a sorted list of K unique failing indices for N resources."""
    return st.lists(
        st.integers(min_value=0, max_value=n - 1),
        min_size=1,
        max_size=max(1, n - 1),  # At least one must succeed to verify isolation
        unique=True,
    ).map(sorted)


# ===========================================================================
# Property 20: Resource cleanup reverse ordering
# ===========================================================================


@pytest.mark.unit
class TestProperty20ResourceCleanupReverseOrdering:
    """Property 20: Resource cleanup reverse ordering.

    For any sequence of N resources acquired through the ResourceManager,
    cleanup SHALL invoke their exit methods in reverse acquisition order (LIFO).

    **Validates: Requirements 6.1, 6.2, 6.3, 6.4**
    """

    @given(n=_resource_count)
    def test_async_resources_cleaned_in_lifo_order(self, n: int) -> None:
        """N async resources are cleaned up in reverse acquisition order.

        Validates: Requirements 6.1, 6.3, 6.4
        """
        cleanup_log: list[int] = []
        manager = KernelResourceManager()

        async def _run() -> None:
            for i in range(n):
                await manager.acquire_async(TrackingAsyncContextManager(i, cleanup_log))
            await manager.cleanup()

        asyncio.run(_run())

        # Cleanup should happen in reverse order: n-1, n-2, ..., 1, 0
        expected = list(reversed(range(n)))
        assert cleanup_log == expected, f"Expected LIFO cleanup order {expected}, got {cleanup_log}"

    @given(n=_resource_count)
    def test_sync_resources_cleaned_in_lifo_order(self, n: int) -> None:
        """N sync resources are cleaned up in reverse acquisition order.

        Validates: Requirements 6.2, 6.3, 6.4
        """
        cleanup_log: list[int] = []
        manager = KernelResourceManager()

        async def _run() -> None:
            for i in range(n):
                manager.acquire_sync(TrackingSyncContextManager(i, cleanup_log))
            await manager.cleanup()

        asyncio.run(_run())

        expected = list(reversed(range(n)))
        assert cleanup_log == expected, f"Expected LIFO cleanup order {expected}, got {cleanup_log}"

    @given(n=_resource_count)
    def test_mixed_resources_cleaned_in_lifo_order(self, n: int) -> None:
        """Interleaved async and sync resources are cleaned in reverse order.

        Validates: Requirements 6.1, 6.2, 6.3, 6.4
        """
        cleanup_log: list[int] = []
        manager = KernelResourceManager()

        async def _run() -> None:
            for i in range(n):
                if i % 2 == 0:
                    await manager.acquire_async(TrackingAsyncContextManager(i, cleanup_log))
                else:
                    manager.acquire_sync(TrackingSyncContextManager(i, cleanup_log))
            await manager.cleanup()

        asyncio.run(_run())

        expected = list(reversed(range(n)))
        assert cleanup_log == expected, f"Expected LIFO cleanup order {expected}, got {cleanup_log}"


# ===========================================================================
# Property 21: Resource cleanup isolation on failure
# ===========================================================================


@pytest.mark.unit
class TestProperty21ResourceCleanupIsolationOnFailure:
    """Property 21: Resource cleanup isolation on failure.

    For any set of N managed resources where K resources raise exceptions
    during cleanup, the remaining (N - K) resources SHALL still have their
    exit methods called.

    **Validates: Requirements 6.5**
    """

    @given(
        data=st.data(),
        n=st.integers(min_value=2, max_value=15),
    )
    def test_async_failing_cleanups_dont_prevent_others(self, data: st.DataObject, n: int) -> None:
        """K failing async cleanups don't prevent remaining (N-K) from being called.

        Validates: Requirements 6.5
        """
        failing = data.draw(_failing_indices(n), label="failing_indices")
        cleanup_log: list[int] = []
        manager = KernelResourceManager()

        async def _run() -> None:
            for i in range(n):
                if i in failing:
                    await manager.acquire_async(FailingAsyncContextManager(i, cleanup_log))
                else:
                    await manager.acquire_async(TrackingAsyncContextManager(i, cleanup_log))
            await manager.cleanup()

        asyncio.run(_run())

        # ALL N resources should have their cleanup called regardless of failures
        assert len(cleanup_log) == n, (
            f"Expected all {n} resources cleaned up, but only {len(cleanup_log)} "
            f"were. Failing indices: {failing}. Log: {cleanup_log}"
        )

        # Verify LIFO ordering is maintained even with failures
        expected = list(reversed(range(n)))
        assert cleanup_log == expected, (
            f"Expected LIFO order {expected} even with failures at {failing}, got {cleanup_log}"
        )

    @given(
        data=st.data(),
        n=st.integers(min_value=2, max_value=15),
    )
    def test_sync_failing_cleanups_dont_prevent_others(self, data: st.DataObject, n: int) -> None:
        """K failing sync cleanups don't prevent remaining (N-K) from being called.

        Validates: Requirements 6.5
        """
        failing = data.draw(_failing_indices(n), label="failing_indices")
        cleanup_log: list[int] = []
        manager = KernelResourceManager()

        async def _run() -> None:
            for i in range(n):
                if i in failing:
                    manager.acquire_sync(FailingSyncContextManager(i, cleanup_log))
                else:
                    manager.acquire_sync(TrackingSyncContextManager(i, cleanup_log))
            await manager.cleanup()

        asyncio.run(_run())

        # ALL N resources should have their cleanup called
        assert len(cleanup_log) == n, (
            f"Expected all {n} resources cleaned up, but only {len(cleanup_log)} "
            f"were. Failing indices: {failing}. Log: {cleanup_log}"
        )

        # Verify LIFO ordering is maintained
        expected = list(reversed(range(n)))
        assert cleanup_log == expected, (
            f"Expected LIFO order {expected} even with failures at {failing}, got {cleanup_log}"
        )

    @given(
        data=st.data(),
        n=st.integers(min_value=2, max_value=15),
    )
    def test_mixed_failing_cleanups_dont_prevent_others(self, data: st.DataObject, n: int) -> None:
        """K failing mixed cleanups don't prevent remaining (N-K) from being called.

        Validates: Requirements 6.5
        """
        failing = data.draw(_failing_indices(n), label="failing_indices")
        cleanup_log: list[int] = []
        manager = KernelResourceManager()

        async def _run() -> None:
            for i in range(n):
                if i in failing:
                    # Alternate between async and sync failing resources
                    if i % 2 == 0:
                        await manager.acquire_async(FailingAsyncContextManager(i, cleanup_log))
                    else:
                        manager.acquire_sync(FailingSyncContextManager(i, cleanup_log))
                else:
                    if i % 2 == 0:
                        await manager.acquire_async(TrackingAsyncContextManager(i, cleanup_log))
                    else:
                        manager.acquire_sync(TrackingSyncContextManager(i, cleanup_log))
            await manager.cleanup()

        asyncio.run(_run())

        # ALL N resources should have their cleanup called
        assert len(cleanup_log) == n, (
            f"Expected all {n} resources cleaned up, but only {len(cleanup_log)} "
            f"were. Failing indices: {failing}. Log: {cleanup_log}"
        )

        # Verify LIFO ordering is maintained
        expected = list(reversed(range(n)))
        assert cleanup_log == expected, (
            f"Expected LIFO order {expected} even with failures at {failing}, got {cleanup_log}"
        )
