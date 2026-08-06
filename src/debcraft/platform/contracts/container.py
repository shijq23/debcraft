"""Dependency injection container contracts defining service registration and resolution."""

from abc import ABC, abstractmethod
from typing import TypeVar

T = TypeVar("T")


class Scope(ABC):
    """A bounded lifetime context owning scoped service instances.

    Scopes inherit singleton registrations from the parent container and
    maintain their own cache of scoped instances. When closed, all scoped
    instances are disposed.
    """

    @abstractmethod
    def resolve(self, service_type: type[T]) -> T:
        """Resolve a service within this scope.

        Args:
            service_type: The abstract interface type to resolve.

        Returns:
            The resolved service instance.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Dispose all scoped instances owned by this scope."""
        ...


class Container(ABC):
    """Dependency injection container with constructor injection.

    Supports three service lifetimes (singleton, transient, scoped) and
    resolves dependencies by inspecting constructor type annotations.
    """

    @abstractmethod
    def register_singleton(self, interface: type[T], implementation: type[T] | None = None) -> None:
        """Register a service with singleton lifetime.

        A single instance is shared across the entire application.

        Args:
            interface: The abstract interface type to register.
            implementation: The concrete implementation type. If None, the
                interface type is used as its own implementation.
        """
        ...

    @abstractmethod
    def register_transient(self, interface: type[T], implementation: type[T] | None = None) -> None:
        """Register a service with transient lifetime.

        A new instance is created on each resolution.

        Args:
            interface: The abstract interface type to register.
            implementation: The concrete implementation type. If None, the
                interface type is used as its own implementation.
        """
        ...

    @abstractmethod
    def register_scoped(self, interface: type[T], implementation: type[T] | None = None) -> None:
        """Register a service with scoped lifetime.

        A single instance is shared within a Scope and disposed when the
        Scope ends.

        Args:
            interface: The abstract interface type to register.
            implementation: The concrete implementation type. If None, the
                interface type is used as its own implementation.
        """
        ...

    @abstractmethod
    def register_instance(self, interface: type[T], instance: T) -> None:
        """Register a pre-built instance as a singleton.

        Args:
            interface: The abstract interface type to register.
            instance: The pre-built instance to use for resolution.
        """
        ...

    @abstractmethod
    def resolve(self, service_type: type[T]) -> T:
        """Resolve a service by its interface type.

        Performs constructor injection by inspecting type annotations on
        the implementation's ``__init__`` method.

        Args:
            service_type: The abstract interface type to resolve.

        Returns:
            The resolved service instance.
        """
        ...

    @abstractmethod
    def create_scope(self) -> Scope:
        """Create a child scope inheriting singleton registrations.

        Returns:
            A new Scope with access to parent singletons and its own
            scoped instance cache.
        """
        ...
