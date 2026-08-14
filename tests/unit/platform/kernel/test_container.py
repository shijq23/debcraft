"""Unit tests for KernelContainer and KernelScope dependency injection."""

import pytest

from debcraft.platform.contracts.container import Container, Scope
from debcraft.platform.kernel.container import KernelContainer, KernelScope, Lifetime
from debcraft.platform.kernel.errors import CircularDependencyError, ServiceNotFoundError


@pytest.fixture
def container() -> KernelContainer:
    return KernelContainer()


# ---------------------------------------------------------------------------
# Helper classes for testing
# ---------------------------------------------------------------------------


class ServiceA:
    """Simple service with no dependencies."""


class ServiceB:
    """Service depending on ServiceA via constructor injection."""

    def __init__(self, a: ServiceA) -> None:
        self.a = a


class ServiceC:
    """Service depending on ServiceA and ServiceB."""

    def __init__(self, a: ServiceA, b: ServiceB) -> None:
        self.a = a
        self.b = b


class AbstractBase:
    """Simulated abstract interface."""


class ConcreteImpl(AbstractBase):
    """Concrete implementation of AbstractBase."""


class CircularY:
    """Second half of a circular dependency pair (defined first for forward ref)."""


class CircularX:
    """First half of a circular dependency pair."""

    def __init__(self, y: CircularY) -> None:
        self.y = y


# Now patch CircularY's __init__ to depend on CircularX
def _circular_y_init(self: CircularY, x: CircularX) -> None:
    self.x = x


_circular_y_init.__annotations__ = {"x": CircularX, "return": None}
CircularY.__init__ = _circular_y_init  # type: ignore[assignment]


class Closeable:
    """A service with a close method, used to verify scope disposal."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class AsyncCloseable:
    """A service with an async close method."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Singleton tests (Requirement 1.1)
# ---------------------------------------------------------------------------


class TestSingletonRegistrationAndResolution:
    @pytest.mark.unit
    def test_singleton_returns_same_instance(self, container: KernelContainer) -> None:
        container.register_singleton(ServiceA)

        first = container.resolve(ServiceA)
        second = container.resolve(ServiceA)

        assert first is second

    @pytest.mark.unit
    def test_singleton_with_interface_and_implementation(self, container: KernelContainer) -> None:
        container.register_singleton(AbstractBase, ConcreteImpl)

        instance = container.resolve(AbstractBase)

        assert isinstance(instance, ConcreteImpl)

    @pytest.mark.unit
    def test_singleton_same_instance_across_multiple_resolves(self, container: KernelContainer) -> None:
        container.register_singleton(ServiceA)

        instances = [container.resolve(ServiceA) for _ in range(5)]

        assert all(inst is instances[0] for inst in instances)


# ---------------------------------------------------------------------------
# Transient tests (Requirement 1.2)
# ---------------------------------------------------------------------------


class TestTransientRegistrationAndResolution:
    @pytest.mark.unit
    def test_transient_returns_new_instance_each_time(self, container: KernelContainer) -> None:
        container.register_transient(ServiceA)

        first = container.resolve(ServiceA)
        second = container.resolve(ServiceA)

        assert first is not second

    @pytest.mark.unit
    def test_transient_all_instances_are_distinct(self, container: KernelContainer) -> None:
        container.register_transient(ServiceA)

        instances = [container.resolve(ServiceA) for _ in range(5)]
        ids = {id(inst) for inst in instances}

        assert len(ids) == 5

    @pytest.mark.unit
    def test_transient_with_interface_and_implementation(self, container: KernelContainer) -> None:
        container.register_transient(AbstractBase, ConcreteImpl)

        instance = container.resolve(AbstractBase)

        assert isinstance(instance, ConcreteImpl)


# ---------------------------------------------------------------------------
# Scoped tests (Requirement 1.3, 1.8, 1.9)
# ---------------------------------------------------------------------------


class TestScopedIsolation:
    @pytest.mark.unit
    def test_scoped_same_instance_within_scope(self, container: KernelContainer) -> None:
        container.register_scoped(ServiceA)
        scope = container.create_scope()

        first = scope.resolve(ServiceA)
        second = scope.resolve(ServiceA)

        assert first is second

    @pytest.mark.unit
    def test_scoped_different_instances_between_scopes(self, container: KernelContainer) -> None:
        container.register_scoped(ServiceA)

        scope1 = container.create_scope()
        scope2 = container.create_scope()

        instance1 = scope1.resolve(ServiceA)
        instance2 = scope2.resolve(ServiceA)

        assert instance1 is not instance2

    @pytest.mark.unit
    def test_scope_inherits_singleton_from_parent(self, container: KernelContainer) -> None:
        container.register_singleton(ServiceA)
        singleton = container.resolve(ServiceA)

        scope = container.create_scope()
        scoped_resolve = scope.resolve(ServiceA)

        assert scoped_resolve is singleton

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_scope_close_disposes_scoped_instances(self, container: KernelContainer) -> None:
        container.register_scoped(Closeable)

        scope = container.create_scope()
        instance = scope.resolve(Closeable)

        assert not instance.closed
        await scope.close()
        assert instance.closed

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_scope_close_disposes_async_closeable(self, container: KernelContainer) -> None:
        container.register_scoped(AsyncCloseable)

        scope = container.create_scope()
        instance = scope.resolve(AsyncCloseable)

        assert not instance.closed
        await scope.close()
        assert instance.closed

    @pytest.mark.unit
    def test_create_scope_returns_scope_instance(self, container: KernelContainer) -> None:
        scope = container.create_scope()

        assert isinstance(scope, KernelScope)
        assert isinstance(scope, Scope)


# ---------------------------------------------------------------------------
# register_instance tests (Requirement 1.4)
# ---------------------------------------------------------------------------


class TestRegisterInstance:
    @pytest.mark.unit
    def test_register_instance_resolves_to_exact_object(self, container: KernelContainer) -> None:
        pre_built = ServiceA()
        container.register_instance(ServiceA, pre_built)

        resolved = container.resolve(ServiceA)

        assert resolved is pre_built

    @pytest.mark.unit
    def test_register_instance_with_interface(self, container: KernelContainer) -> None:
        pre_built = ConcreteImpl()
        container.register_instance(AbstractBase, pre_built)

        resolved = container.resolve(AbstractBase)

        assert resolved is pre_built
        assert isinstance(resolved, ConcreteImpl)

    @pytest.mark.unit
    def test_register_instance_always_returns_same_object(self, container: KernelContainer) -> None:
        pre_built = ServiceA()
        container.register_instance(ServiceA, pre_built)

        instances = [container.resolve(ServiceA) for _ in range(3)]

        assert all(inst is pre_built for inst in instances)


# ---------------------------------------------------------------------------
# ServiceNotFoundError tests (Requirement 1.10)
# ---------------------------------------------------------------------------


class TestServiceNotFoundError:
    @pytest.mark.unit
    def test_resolve_unregistered_type_raises_error(self, container: KernelContainer) -> None:
        with pytest.raises(ServiceNotFoundError) as exc_info:
            container.resolve(ServiceA)

        assert exc_info.value.service_type is ServiceA

    @pytest.mark.unit
    def test_service_not_found_error_message_contains_type_name(self, container: KernelContainer) -> None:
        with pytest.raises(ServiceNotFoundError, match="ServiceA"):
            container.resolve(ServiceA)

    @pytest.mark.unit
    def test_scope_resolve_unregistered_type_raises_error(self, container: KernelContainer) -> None:
        scope = container.create_scope()

        with pytest.raises(ServiceNotFoundError):
            scope.resolve(ServiceA)


# ---------------------------------------------------------------------------
# CircularDependencyError tests (Requirement 1.5)
# ---------------------------------------------------------------------------


class TestCircularDependencyError:
    @pytest.mark.unit
    def test_circular_dependency_raises_error(self, container: KernelContainer) -> None:
        container.register_singleton(CircularX)
        container.register_singleton(CircularY)

        with pytest.raises(CircularDependencyError):
            container.resolve(CircularX)

    @pytest.mark.unit
    def test_circular_dependency_error_has_chain(self, container: KernelContainer) -> None:
        container.register_singleton(CircularX)
        container.register_singleton(CircularY)

        with pytest.raises(CircularDependencyError) as exc_info:
            container.resolve(CircularX)

        assert len(exc_info.value.chain) > 1

    @pytest.mark.unit
    def test_circular_dependency_error_message_is_descriptive(self, container: KernelContainer) -> None:
        container.register_singleton(CircularX)
        container.register_singleton(CircularY)

        with pytest.raises(CircularDependencyError, match="Circular dependency detected"):
            container.resolve(CircularX)

    @pytest.mark.unit
    def test_circular_dependency_message_contains_chain_names(self, container: KernelContainer) -> None:
        container.register_singleton(CircularX)
        container.register_singleton(CircularY)

        with pytest.raises(CircularDependencyError) as exc_info:
            container.resolve(CircularX)

        message = str(exc_info.value)
        assert "CircularX" in message or "CircularY" in message


# ---------------------------------------------------------------------------
# Constructor injection tests (Requirement 1.4, 1.6)
# ---------------------------------------------------------------------------


class TestConstructorInjection:
    @pytest.mark.unit
    def test_single_dependency_injection(self, container: KernelContainer) -> None:
        container.register_singleton(ServiceA)
        container.register_singleton(ServiceB)

        b = container.resolve(ServiceB)

        assert isinstance(b.a, ServiceA)

    @pytest.mark.unit
    def test_multiple_dependency_injection(self, container: KernelContainer) -> None:
        container.register_singleton(ServiceA)
        container.register_singleton(ServiceB)
        container.register_singleton(ServiceC)

        c = container.resolve(ServiceC)

        assert isinstance(c.a, ServiceA)
        assert isinstance(c.b, ServiceB)

    @pytest.mark.unit
    def test_injected_singletons_are_shared(self, container: KernelContainer) -> None:
        container.register_singleton(ServiceA)
        container.register_singleton(ServiceB)
        container.register_singleton(ServiceC)

        c = container.resolve(ServiceC)
        direct_a = container.resolve(ServiceA)

        assert c.a is direct_a
        assert c.b.a is direct_a

    @pytest.mark.unit
    def test_interface_based_injection(self, container: KernelContainer) -> None:
        """Resolving by interface returns implementation and injected deps are correct types."""
        container.register_singleton(AbstractBase, ConcreteImpl)

        result = container.resolve(AbstractBase)

        assert isinstance(result, ConcreteImpl)
        assert isinstance(result, AbstractBase)


# ---------------------------------------------------------------------------
# Type-safe resolution tests (Requirement 1.7)
# ---------------------------------------------------------------------------


class TestTypeSafeResolution:
    @pytest.mark.unit
    def test_resolve_returns_correct_type(self, container: KernelContainer) -> None:
        container.register_singleton(ServiceA)

        result = container.resolve(ServiceA)

        assert type(result) is ServiceA

    @pytest.mark.unit
    def test_resolve_interface_returns_implementation_type(self, container: KernelContainer) -> None:
        container.register_singleton(AbstractBase, ConcreteImpl)

        result = container.resolve(AbstractBase)

        assert type(result) is ConcreteImpl

    @pytest.mark.unit
    def test_container_implements_contract(self, container: KernelContainer) -> None:
        assert isinstance(container, Container)


# ---------------------------------------------------------------------------
# Registration lifetime correctness
# ---------------------------------------------------------------------------


class TestLifetimeRegistration:
    @pytest.mark.unit
    def test_register_singleton_sets_correct_lifetime(self, container: KernelContainer) -> None:
        container.register_singleton(ServiceA)

        reg = container.registrations[ServiceA]

        assert reg.lifetime == Lifetime.SINGLETON

    @pytest.mark.unit
    def test_register_transient_sets_correct_lifetime(self, container: KernelContainer) -> None:
        container.register_transient(ServiceA)

        reg = container.registrations[ServiceA]

        assert reg.lifetime == Lifetime.TRANSIENT

    @pytest.mark.unit
    def test_register_scoped_sets_correct_lifetime(self, container: KernelContainer) -> None:
        container.register_scoped(ServiceA)

        reg = container.registrations[ServiceA]

        assert reg.lifetime == Lifetime.SCOPED
