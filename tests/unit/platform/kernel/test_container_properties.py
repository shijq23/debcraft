"""Property-based tests for the KernelContainer dependency injection container.

Properties 1-6 validate core container behaviors across many randomized inputs:
singleton identity, transient distinctness, scoped isolation, constructor injection,
circular dependency detection, and scope disposal completeness.

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.8, 1.9**
"""

import asyncio
from abc import ABC, abstractmethod
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.platform.kernel.container import KernelContainer
from debcraft.platform.kernel.errors import CircularDependencyError

# ===========================================================================
# Helper classes for testing — simple services with no external dependencies
# ===========================================================================


class IServiceA(ABC):
    """Abstract interface for service A."""

    @abstractmethod
    def value(self) -> str: ...


class ServiceAImpl(IServiceA):
    """Concrete implementation of service A."""

    def value(self) -> str:
        return "A"


class IServiceB(ABC):
    """Abstract interface for service B."""

    @abstractmethod
    def value(self) -> str: ...


class ServiceBImpl(IServiceB):
    """Concrete implementation of service B."""

    def value(self) -> str:
        return "B"


class IServiceWithDep(ABC):
    """Abstract interface for a service that depends on IServiceA."""

    @abstractmethod
    def get_dep(self) -> IServiceA: ...


class ServiceWithDepImpl(IServiceWithDep):
    """Concrete service that takes IServiceA via constructor injection."""

    def __init__(self, dep: IServiceA) -> None:
        self._dep = dep

    def get_dep(self) -> IServiceA:
        return self._dep


class IServiceWithTwoDeps(ABC):
    """Abstract interface for a service that depends on two services."""

    @abstractmethod
    def get_deps(self) -> tuple[IServiceA, IServiceB]: ...


class ServiceWithTwoDepsImpl(IServiceWithTwoDeps):
    """Concrete service that takes IServiceA and IServiceB via constructor injection."""

    def __init__(self, dep_a: IServiceA, dep_b: IServiceB) -> None:
        self._dep_a = dep_a
        self._dep_b = dep_b

    def get_deps(self) -> tuple[IServiceA, IServiceB]:
        return (self._dep_a, self._dep_b)


# Services that form circular dependencies
class ICircularA(ABC):
    """Abstract interface for circular dep A."""

    @abstractmethod
    def name(self) -> str: ...


class ICircularB(ABC):
    """Abstract interface for circular dep B."""

    @abstractmethod
    def name(self) -> str: ...


class ICircularC(ABC):
    """Abstract interface for circular dep C."""

    @abstractmethod
    def name(self) -> str: ...


class CircularAImpl(ICircularA):
    """Depends on ICircularB, forming a cycle."""

    def __init__(self, dep: ICircularB) -> None:
        self._dep = dep

    def name(self) -> str:
        return "A"


class CircularBImpl(ICircularB):
    """Depends on ICircularA, forming a cycle."""

    def __init__(self, dep: ICircularA) -> None:
        self._dep = dep

    def name(self) -> str:
        return "B"


class CircularBToC(ICircularB):
    """Depends on ICircularC for longer cycles."""

    def __init__(self, dep: ICircularC) -> None:
        self._dep = dep

    def name(self) -> str:
        return "B"


class CircularCToA(ICircularC):
    """Depends on ICircularA, closing a 3-node cycle."""

    def __init__(self, dep: ICircularA) -> None:
        self._dep = dep

    def name(self) -> str:
        return "C"


# Disposable service for scope disposal testing
class DisposableService:
    """A service that tracks whether close() was called."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class AsyncDisposableService:
    """A service that tracks whether async close() was called."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


# ===========================================================================
# Strategies
# ===========================================================================

# Number of resolutions to perform (at least 2 to test identity/distinctness)
_resolution_count = st.integers(min_value=2, max_value=20)


# ===========================================================================
# Property 1: Singleton resolution identity
# ===========================================================================


@pytest.mark.unit
class TestProperty1SingletonResolutionIdentity:
    """Property 1: Singleton resolution identity.

    For any service registered with singleton lifetime, resolving it N times
    (N >= 2) from the same container SHALL return the same object identity
    every time.

    **Validates: Requirements 1.1, 1.8**
    """

    @given(n=_resolution_count)
    def test_singleton_returns_same_instance_n_times(self, n: int) -> None:
        """Resolving a singleton N times always returns the same object.

        Validates: Requirements 1.1
        """
        container = KernelContainer()
        container.register_singleton(IServiceA, ServiceAImpl)

        instances = [container.resolve(IServiceA) for _ in range(n)]

        # All instances must be the exact same object
        first = instances[0]
        for i, inst in enumerate(instances[1:], start=1):
            assert inst is first, (
                f"Resolution {i} returned a different object. "
                f"Expected same identity for singleton after {n} resolutions."
            )


# ===========================================================================
# Property 2: Transient resolution distinctness
# ===========================================================================


@pytest.mark.unit
class TestProperty2TransientResolutionDistinctness:
    """Property 2: Transient resolution distinctness.

    For any service registered with transient lifetime, resolving it N times
    (N >= 2) SHALL return N distinct object instances.

    **Validates: Requirements 1.2**
    """

    @given(n=_resolution_count)
    def test_transient_returns_distinct_instances(self, n: int) -> None:
        """Resolving a transient N times returns N distinct objects.

        Validates: Requirements 1.2
        """
        container = KernelContainer()
        container.register_transient(IServiceA, ServiceAImpl)

        instances = [container.resolve(IServiceA) for _ in range(n)]

        # All instances must be distinct (no two share identity)
        ids = [id(inst) for inst in instances]
        assert len(set(ids)) == n, f"Expected {n} distinct instances, got {len(set(ids))} unique identities."


# ===========================================================================
# Property 3: Scoped resolution isolation
# ===========================================================================


@pytest.mark.unit
class TestProperty3ScopedResolutionIsolation:
    """Property 3: Scoped resolution isolation.

    For any service registered with scoped lifetime, resolving it within the same
    scope always returns the same instance, while resolving it from a different
    scope returns a distinct instance.

    **Validates: Requirements 1.3, 1.8**
    """

    @given(n_within=_resolution_count, n_scopes=st.integers(min_value=2, max_value=8))
    def test_same_scope_same_instance_different_scope_different_instance(self, n_within: int, n_scopes: int) -> None:
        """Same scope returns same instance; different scopes return different instances.

        Validates: Requirements 1.3
        """
        container = KernelContainer()
        container.register_scoped(IServiceA, ServiceAImpl)

        scope_instances: list[list[Any]] = []
        for _ in range(n_scopes):
            scope = container.create_scope()
            instances = [scope.resolve(IServiceA) for _ in range(n_within)]
            scope_instances.append(instances)

        # Within each scope, all instances must be the same object
        for scope_idx, instances in enumerate(scope_instances):
            first = instances[0]
            for inst in instances[1:]:
                assert inst is first, f"Scope {scope_idx}: expected same identity within scope"

        # Across different scopes, instances must be distinct
        first_per_scope = [instances[0] for instances in scope_instances]
        ids = [id(inst) for inst in first_per_scope]
        assert len(set(ids)) == n_scopes, (
            f"Expected {n_scopes} distinct instances across scopes, got {len(set(ids))} unique identities."
        )


# ===========================================================================
# Property 4: Constructor injection resolution
# ===========================================================================


@pytest.mark.unit
class TestProperty4ConstructorInjectionResolution:
    """Property 4: Constructor injection resolution.

    For any service whose __init__ declares typed parameters that each have
    registrations in the container, resolving that service SHALL produce an
    instance whose injected dependencies are instances of the declared parameter types.

    **Validates: Requirements 1.4**
    """

    @given(n=st.integers(min_value=1, max_value=10))
    def test_single_dependency_injection(self, n: int) -> None:
        """Resolving a service with one dependency injects the correct type.

        Validates: Requirements 1.4
        """
        container = KernelContainer()
        container.register_singleton(IServiceA, ServiceAImpl)
        container.register_transient(IServiceWithDep, ServiceWithDepImpl)

        for _ in range(n):
            instance = container.resolve(IServiceWithDep)
            dep = instance.get_dep()
            assert isinstance(dep, ServiceAImpl), (
                f"Expected injected dependency to be ServiceAImpl, got {type(dep).__name__}"
            )

    @given(n=st.integers(min_value=1, max_value=10))
    def test_multiple_dependency_injection(self, n: int) -> None:
        """Resolving a service with multiple dependencies injects correct types.

        Validates: Requirements 1.4
        """
        container = KernelContainer()
        container.register_singleton(IServiceA, ServiceAImpl)
        container.register_singleton(IServiceB, ServiceBImpl)
        container.register_transient(IServiceWithTwoDeps, ServiceWithTwoDepsImpl)

        for _ in range(n):
            instance = container.resolve(IServiceWithTwoDeps)
            dep_a, dep_b = instance.get_deps()
            assert isinstance(dep_a, ServiceAImpl), f"Expected dep_a to be ServiceAImpl, got {type(dep_a).__name__}"
            assert isinstance(dep_b, ServiceBImpl), f"Expected dep_b to be ServiceBImpl, got {type(dep_b).__name__}"


# ===========================================================================
# Property 5: Circular dependency detection
# ===========================================================================


@pytest.mark.unit
class TestProperty5CircularDependencyDetection:
    """Property 5: Circular dependency detection.

    For any set of service registrations that form a dependency cycle
    (A->B->...->A), resolving any service in the cycle SHALL raise
    CircularDependencyError before any constructor is invoked.

    **Validates: Requirements 1.5**
    """

    @given(start_at=st.sampled_from(["A", "B"]))
    def test_two_node_cycle_detected(self, start_at: str) -> None:
        """A two-node cycle (A->B->A) raises CircularDependencyError.

        Validates: Requirements 1.5
        """
        container = KernelContainer()
        container.register_transient(ICircularA, CircularAImpl)
        container.register_transient(ICircularB, CircularBImpl)

        resolve_type = ICircularA if start_at == "A" else ICircularB
        with pytest.raises(CircularDependencyError):
            container.resolve(resolve_type)

    @given(start_at=st.sampled_from(["A", "B", "C"]))
    def test_three_node_cycle_detected(self, start_at: str) -> None:
        """A three-node cycle (A->B->C->A) raises CircularDependencyError.

        Validates: Requirements 1.5
        """
        container = KernelContainer()
        container.register_transient(ICircularA, CircularAImpl)
        container.register_transient(ICircularB, CircularBToC)
        container.register_transient(ICircularC, CircularCToA)

        type_map = {"A": ICircularA, "B": ICircularB, "C": ICircularC}
        with pytest.raises(CircularDependencyError):
            container.resolve(type_map[start_at])

    @given(start_at=st.sampled_from(["A", "B"]))
    def test_circular_dependency_error_has_chain(self, start_at: str) -> None:
        """CircularDependencyError contains the dependency chain.

        Validates: Requirements 1.5
        """
        container = KernelContainer()
        container.register_transient(ICircularA, CircularAImpl)
        container.register_transient(ICircularB, CircularBImpl)

        resolve_type = ICircularA if start_at == "A" else ICircularB
        with pytest.raises(CircularDependencyError) as exc_info:
            container.resolve(resolve_type)

        # The chain should contain at least 2 types
        assert len(exc_info.value.chain) >= 2, f"Expected chain length >= 2, got {len(exc_info.value.chain)}"


# ===========================================================================
# Property 6: Scope disposal completeness
# ===========================================================================


@pytest.mark.unit
class TestProperty6ScopeDisposalCompleteness:
    """Property 6: Scope disposal completeness.

    For any scope containing N scoped service instances, closing the scope
    SHALL invoke the disposal method on all N instances.

    **Validates: Requirements 1.9**
    """

    @given(n=st.integers(min_value=1, max_value=10))
    def test_closing_scope_disposes_all_sync_instances(self, n: int) -> None:
        """Closing a scope calls close() on all N scoped instances.

        Validates: Requirements 1.9
        """
        container = KernelContainer()

        # Dynamically create N distinct service interfaces and implementations
        interfaces: list[type] = []
        for i in range(n):
            # Create unique interface and implementation types per iteration
            iface = type(f"IDisposable{i}", (), {})
            container.register_scoped(iface, DisposableService)
            interfaces.append(iface)

        scope = container.create_scope()

        # Resolve all services to populate the scope
        resolved = [scope.resolve(iface) for iface in interfaces]

        # All should be unclosed initially
        for inst in resolved:
            assert not inst.closed

        # Close the scope
        asyncio.run(scope.close())

        # All should be closed now
        for i, inst in enumerate(resolved):
            assert inst.closed, f"Instance {i} was not disposed after scope.close()"

    @given(n=st.integers(min_value=1, max_value=10))
    def test_closing_scope_disposes_all_async_instances(self, n: int) -> None:
        """Closing a scope calls async close() on all N async-disposable instances.

        Validates: Requirements 1.9
        """
        container = KernelContainer()

        interfaces: list[type] = []
        for i in range(n):
            iface = type(f"IAsyncDisposable{i}", (), {})
            container.register_scoped(iface, AsyncDisposableService)
            interfaces.append(iface)

        scope = container.create_scope()

        # Resolve all services to populate the scope
        resolved = [scope.resolve(iface) for iface in interfaces]

        # All should be unclosed initially
        for inst in resolved:
            assert not inst.closed

        # Close the scope
        asyncio.run(scope.close())

        # All should be closed now
        for i, inst in enumerate(resolved):
            assert inst.closed, f"Async instance {i} was not disposed after scope.close()"

    @given(n=st.integers(min_value=1, max_value=8))
    def test_scope_cache_cleared_after_close(self, n: int) -> None:
        """After closing a scope, the scoped instance cache is empty.

        Validates: Requirements 1.9
        """
        container = KernelContainer()

        interfaces: list[type] = []
        for i in range(n):
            iface = type(f"IClearable{i}", (), {})
            container.register_scoped(iface, DisposableService)
            interfaces.append(iface)

        scope = container.create_scope()

        # Resolve to populate cache
        for iface in interfaces:
            scope.resolve(iface)

        # Close
        asyncio.run(scope.close())

        # Internal cache should be empty
        assert len(scope._scoped_instances) == 0, (
            f"Expected empty scope cache after close, got {len(scope._scoped_instances)} entries"
        )
