"""Kernel dependency injection container implementation.

Provides constructor injection via type annotation introspection, three service
lifetimes (singleton, transient, scoped), and circular dependency detection.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeVar

from debcraft.platform.contracts.container import Container, Scope
from debcraft.platform.kernel.errors import CircularDependencyError, ServiceNotFoundError

T = TypeVar("T")


class Lifetime(Enum):
    """Service lifetime within the container."""

    SINGLETON = "singleton"
    TRANSIENT = "transient"
    SCOPED = "scoped"


@dataclass
class ServiceRegistration:
    """Internal registration record for a service.

    Attributes:
        interface: The abstract interface type registered.
        implementation: The concrete implementation type to instantiate.
        lifetime: The lifetime policy for this registration.
    """

    interface: type
    implementation: type
    lifetime: Lifetime


class KernelContainer(Container):
    """Lightweight dependency injection container with constructor injection.

    Resolves services by inspecting ``__init__.__annotations__`` and recursively
    resolving dependencies. Supports singleton, transient, and scoped lifetimes
    with circular dependency detection via a resolution stack.
    """

    def __init__(self) -> None:
        """Initialize an empty container."""
        self._registrations: dict[type, ServiceRegistration] = {}
        self._singletons: dict[type, Any] = {}
        self._resolution_stack: set[type] = set()

    def register_singleton(self, interface: type[T], implementation: type[T] | None = None) -> None:
        """Register a service with singleton lifetime.

        A single instance is shared across the entire application.

        Args:
            interface: The abstract interface type to register.
            implementation: The concrete implementation type. If None, the
                interface type is used as its own implementation.
        """
        impl = implementation if implementation is not None else interface
        self._registrations[interface] = ServiceRegistration(
            interface=interface,
            implementation=impl,
            lifetime=Lifetime.SINGLETON,
        )

    def register_transient(self, interface: type[T], implementation: type[T] | None = None) -> None:
        """Register a service with transient lifetime.

        A new instance is created on each resolution.

        Args:
            interface: The abstract interface type to register.
            implementation: The concrete implementation type. If None, the
                interface type is used as its own implementation.
        """
        impl = implementation if implementation is not None else interface
        self._registrations[interface] = ServiceRegistration(
            interface=interface,
            implementation=impl,
            lifetime=Lifetime.TRANSIENT,
        )

    def register_scoped(self, interface: type[T], implementation: type[T] | None = None) -> None:
        """Register a service with scoped lifetime.

        A single instance is shared within a Scope and disposed when the
        Scope ends.

        Args:
            interface: The abstract interface type to register.
            implementation: The concrete implementation type. If None, the
                interface type is used as its own implementation.
        """
        impl = implementation if implementation is not None else interface
        self._registrations[interface] = ServiceRegistration(
            interface=interface,
            implementation=impl,
            lifetime=Lifetime.SCOPED,
        )

    def register_instance(self, interface: type[T], instance: T) -> None:
        """Register a pre-built instance as a singleton.

        Args:
            interface: The abstract interface type to register.
            instance: The pre-built instance to use for resolution.
        """
        self._registrations[interface] = ServiceRegistration(
            interface=interface,
            implementation=type(instance),
            lifetime=Lifetime.SINGLETON,
        )
        self._singletons[interface] = instance

    def resolve(self, service_type: type[T]) -> T:
        """Resolve a service by its interface type.

        Performs constructor injection by inspecting type annotations on
        the implementation's ``__init__`` method.

        Args:
            service_type: The abstract interface type to resolve.

        Returns:
            The resolved service instance.

        Raises:
            ServiceNotFoundError: If no registration exists for the type.
            CircularDependencyError: If a circular dependency is detected.
        """
        if service_type not in self._registrations:
            raise ServiceNotFoundError(service_type)

        registration = self._registrations[service_type]

        # Return cached singleton if available
        if registration.lifetime == Lifetime.SINGLETON and service_type in self._singletons:
            return self._singletons[service_type]  # type: ignore[no-any-return]

        # Circular dependency detection
        if service_type in self._resolution_stack:
            chain = [*self._resolution_stack, service_type]
            raise CircularDependencyError(chain)

        self._resolution_stack.add(service_type)
        try:
            instance = self._create_instance(registration.implementation)
        finally:
            self._resolution_stack.discard(service_type)

        # Cache singleton
        if registration.lifetime == Lifetime.SINGLETON:
            self._singletons[service_type] = instance

        return instance  # type: ignore[return-value]

    def create_scope(self) -> Scope:
        """Create a child scope inheriting singleton registrations.

        Returns:
            A new KernelScope with access to parent singletons and its own
            scoped instance cache.
        """
        return KernelScope(self)

    def _create_instance(self, implementation: type) -> object:
        """Create an instance of the implementation type via constructor injection.

        Args:
            implementation: The concrete type to instantiate.

        Returns:
            A new instance with all dependencies injected.
        """
        annotations = _get_constructor_annotations(implementation)
        dependencies: dict[str, object] = {}
        for param_name, param_type in annotations.items():
            dependencies[param_name] = self.resolve(param_type)
        return implementation(**dependencies)


class KernelScope(Scope):
    """A bounded lifetime context owning scoped service instances.

    Inherits singleton registrations from the parent container and maintains
    its own cache of scoped instances. When closed, all scoped instances are
    disposed.
    """

    def __init__(self, parent: KernelContainer) -> None:
        """Initialize the scope with a reference to the parent container.

        Args:
            parent: The parent container providing registrations and singletons.
        """
        self._parent = parent
        self._scoped_instances: dict[type, Any] = {}
        self._resolution_stack: set[type] = set()

    def resolve(self, service_type: type[T]) -> T:
        """Resolve a service within this scope.

        Singletons are resolved from the parent container. Scoped services
        are cached per-scope. Transient services create a new instance each time.

        Args:
            service_type: The abstract interface type to resolve.

        Returns:
            The resolved service instance.

        Raises:
            ServiceNotFoundError: If no registration exists for the type.
            CircularDependencyError: If a circular dependency is detected.
        """
        if service_type not in self._parent._registrations:
            raise ServiceNotFoundError(service_type)

        registration = self._parent._registrations[service_type]

        # Singletons delegate to parent container
        if registration.lifetime == Lifetime.SINGLETON:
            return self._parent.resolve(service_type)

        # Return cached scoped instance if available
        if registration.lifetime == Lifetime.SCOPED and service_type in self._scoped_instances:
            return self._scoped_instances[service_type]  # type: ignore[no-any-return]

        # Circular dependency detection
        if service_type in self._resolution_stack:
            chain = [*self._resolution_stack, service_type]
            raise CircularDependencyError(chain)

        self._resolution_stack.add(service_type)
        try:
            instance = self._create_instance(registration.implementation)
        finally:
            self._resolution_stack.discard(service_type)

        # Cache scoped instance
        if registration.lifetime == Lifetime.SCOPED:
            self._scoped_instances[service_type] = instance

        return instance  # type: ignore[return-value]

    async def close(self) -> None:
        """Dispose all scoped instances owned by this scope.

        Calls ``close()`` on any scoped instance that has a ``close`` method.
        Clears the scoped instance cache after disposal.
        """
        for instance in self._scoped_instances.values():
            close_method = getattr(instance, "close", None)
            if callable(close_method):
                if inspect.iscoroutinefunction(close_method):
                    await close_method()
                else:
                    close_method()
        self._scoped_instances.clear()

    def _create_instance(self, implementation: type) -> object:
        """Create an instance of the implementation type via constructor injection.

        Args:
            implementation: The concrete type to instantiate.

        Returns:
            A new instance with all dependencies injected.
        """
        annotations = _get_constructor_annotations(implementation)
        dependencies: dict[str, object] = {}
        for param_name, param_type in annotations.items():
            dependencies[param_name] = self.resolve(param_type)
        return implementation(**dependencies)


def _get_constructor_annotations(cls: type) -> dict[str, type]:
    """Extract constructor parameter type annotations excluding 'return'.

    Args:
        cls: The class to inspect.

    Returns:
        A dictionary mapping parameter names to their annotated types.
    """
    init = getattr(cls, "__init__", None)
    if init is None:
        return {}
    annotations = getattr(init, "__annotations__", {})
    return {k: v for k, v in annotations.items() if k != "return"}
