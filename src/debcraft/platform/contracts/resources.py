"""Resource manager contract defining lifecycle management for workflow resources."""

from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from typing import TypeVar

T = TypeVar("T")


class ResourceManager(ABC):
    """Manages lifecycle of workflow resources with deterministic cleanup.

    Tracks async and synchronous context-managed resources acquired during
    workflow execution and ensures they are cleaned up in reverse acquisition
    order when the workflow completes, fails, or is cancelled.
    """

    @abstractmethod
    async def acquire_async(self, resource: AbstractAsyncContextManager[T]) -> T:
        """Acquire an async context-managed resource.

        Args:
            resource: An async context manager to enter and track.

        Returns:
            The value produced by entering the async context manager.
        """
        ...

    @abstractmethod
    def acquire_sync(self, resource: AbstractContextManager[T]) -> T:
        """Acquire a synchronous context-managed resource.

        Args:
            resource: A synchronous context manager to enter and track.

        Returns:
            The value produced by entering the context manager.
        """
        ...

    @abstractmethod
    async def cleanup(self) -> None:
        """Clean up all acquired resources in reverse acquisition order."""
        ...
