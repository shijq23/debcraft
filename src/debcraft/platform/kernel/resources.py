"""Kernel resource manager implementation.

Provides deterministic lifecycle management for workflow resources using
individual cleanup callbacks that ensure all resources are cleaned up even
if individual cleanup operations fail.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, TypeVar

from debcraft.platform.contracts.resources import ResourceManager

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager, AbstractContextManager

T = TypeVar("T")

_logger = logging.getLogger(__name__)

# Type alias for async cleanup callbacks.
_AsyncCleanupCallback = Callable[[], Coroutine[Any, Any, None]]


class KernelResourceManager(ResourceManager):
    """Manages lifecycle of workflow resources with deterministic cleanup.

    Tracks async and synchronous context-managed resources acquired during
    workflow execution and ensures they are cleaned up in reverse acquisition
    order. Individual cleanup failures are caught and logged, allowing
    remaining resources to still be cleaned.

    Each instance is owned by a single ``WorkflowContext``, providing
    isolation between concurrent workflow executions.
    """

    def __init__(self) -> None:
        """Initialize the resource manager with an empty cleanup list."""
        self._cleanups: list[tuple[str, _AsyncCleanupCallback]] = []

    async def acquire_async(self, resource: AbstractAsyncContextManager[T]) -> T:
        """Acquire an async context-managed resource.

        Enters the async context manager and registers its cleanup for
        deterministic reverse-order teardown when the owning workflow
        completes.

        Args:
            resource: An async context manager to enter and track.

        Returns:
            The value produced by entering the async context manager.
        """
        value = await resource.__aenter__()
        resource_name = type(resource).__qualname__

        async def _cleanup() -> None:
            await resource.__aexit__(None, None, None)

        self._cleanups.append((resource_name, _cleanup))
        return value

    def acquire_sync(self, resource: AbstractContextManager[T]) -> T:
        """Acquire a synchronous context-managed resource.

        Enters the synchronous context manager and registers its cleanup
        for deterministic reverse-order teardown when the owning workflow
        completes.

        Args:
            resource: A synchronous context manager to enter and track.

        Returns:
            The value produced by entering the context manager.
        """
        value = resource.__enter__()
        resource_name = type(resource).__qualname__

        async def _cleanup() -> None:
            resource.__exit__(None, None, None)

        self._cleanups.append((resource_name, _cleanup))
        return value

    async def cleanup(self) -> None:
        """Clean up all acquired resources in reverse acquisition order.

        Iterates through registered cleanup callbacks in LIFO order.
        If any individual resource cleanup raises an exception, the error
        is logged at ERROR level and cleanup continues for remaining
        resources.
        """
        cleanups = list(reversed(self._cleanups))
        self._cleanups.clear()

        for resource_name, callback in cleanups:
            try:
                await callback()
            except Exception:
                _logger.exception(
                    "Failed to clean up resource '%s'",
                    resource_name,
                )
