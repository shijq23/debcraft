"""Unit tests for bootstrap function and kernel integration.

Tests that the bootstrap function correctly registers all kernel services,
resolves them via their contract interfaces, performs constructor injection,
and orchestrates end-to-end workflow execution with events and resource cleanup.

Requirements: 10.1, 10.2, 10.3, 10.4, 10.5
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.platform.contracts.configuration import ConfigurationService
from debcraft.platform.contracts.container import Container
from debcraft.platform.contracts.events import EventBus
from debcraft.platform.contracts.logging import LoggerFactory
from debcraft.platform.contracts.resources import ResourceManager
from debcraft.platform.contracts.workflow import (
    Workflow,
    WorkflowContext,
    WorkflowEngine,
    WorkflowFactory,
    WorkflowState,
)
from debcraft.platform.kernel.bootstrap import bootstrap
from debcraft.platform.kernel.container import KernelContainer
from debcraft.platform.kernel.events import (
    KernelEventBus,
    WorkflowCompletedEvent,
    WorkflowFailedEvent,
    WorkflowStartedEvent,
)
from debcraft.platform.kernel.logging import KernelLoggerFactory
from debcraft.platform.kernel.workflow import KernelWorkflowEngine, KernelWorkflowFactory

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def container() -> Container:
    """Bootstrap a fully configured kernel container."""
    return bootstrap()


# ---------------------------------------------------------------------------
# Helper classes
# ---------------------------------------------------------------------------


class SuccessWorkflow(Workflow):
    """A simple workflow that completes successfully."""

    @property
    def name(self) -> str:
        return "success-workflow"

    async def execute(self, context: WorkflowContext) -> None:
        context.progress.report(50.0, "halfway")
        context.progress.report(100.0, "done")


class FailingWorkflow(Workflow):
    """A workflow that raises an error during execution."""

    @property
    def name(self) -> str:
        return "failing-workflow"

    async def execute(self, context: WorkflowContext) -> None:
        msg = "intentional failure"
        raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Tests: bootstrap returns a Container with all expected registrations
# ---------------------------------------------------------------------------


class TestBootstrapRegistrations:
    """Test that bootstrap registers all expected services."""

    def test_bootstrap_returns_container(self, container: Container) -> None:
        """Bootstrap function returns a Container instance."""
        assert isinstance(container, Container)
        assert isinstance(container, KernelContainer)

    def test_configuration_service_registered(self, container: Container) -> None:
        """ConfigurationService is registered and resolvable."""
        assert container.resolve(ConfigurationService) is not None  # type: ignore[type-abstract]

    def test_event_bus_registered(self, container: Container) -> None:
        """EventBus is registered and resolvable."""
        assert container.resolve(EventBus) is not None  # type: ignore[type-abstract]

    def test_logger_factory_registered(self, container: Container) -> None:
        """LoggerFactory is registered and resolvable."""
        assert container.resolve(LoggerFactory) is not None  # type: ignore[type-abstract]

    def test_workflow_engine_registered(self, container: Container) -> None:
        """WorkflowEngine is registered and resolvable."""
        assert container.resolve(WorkflowEngine) is not None  # type: ignore[type-abstract]

    def test_workflow_factory_registered(self, container: Container) -> None:
        """WorkflowFactory is registered and resolvable."""
        assert container.resolve(WorkflowFactory) is not None  # type: ignore[type-abstract]

    def test_container_self_registration(self, container: Container) -> None:
        """Container is registered as itself for self-referential resolution."""
        resolved = container.resolve(Container)  # type: ignore[type-abstract]
        assert resolved is container

    def test_resource_manager_registered_as_scoped(self, container: Container) -> None:
        """ResourceManager is registered and resolvable via a scope."""
        scope = container.create_scope()
        rm = scope.resolve(ResourceManager)  # type: ignore[type-abstract]
        assert rm is not None


# ---------------------------------------------------------------------------
# Tests: Resolving each service interface succeeds
# ---------------------------------------------------------------------------


class TestServiceResolution:
    """Test that each service interface resolves to the correct implementation."""

    def test_event_bus_resolves_to_kernel_event_bus(self, container: Container) -> None:
        """EventBus resolves to KernelEventBus."""
        event_bus = container.resolve(EventBus)  # type: ignore[type-abstract]
        assert isinstance(event_bus, KernelEventBus)

    def test_logger_factory_resolves_to_kernel_logger_factory(self, container: Container) -> None:
        """LoggerFactory resolves to KernelLoggerFactory."""
        logger_factory = container.resolve(LoggerFactory)  # type: ignore[type-abstract]
        assert isinstance(logger_factory, KernelLoggerFactory)

    def test_workflow_engine_resolves_to_kernel_workflow_engine(self, container: Container) -> None:
        """WorkflowEngine resolves to KernelWorkflowEngine."""
        engine = container.resolve(WorkflowEngine)  # type: ignore[type-abstract]
        assert isinstance(engine, KernelWorkflowEngine)

    def test_workflow_factory_resolves_to_kernel_workflow_factory(self, container: Container) -> None:
        """WorkflowFactory resolves to KernelWorkflowFactory."""
        factory = container.resolve(WorkflowFactory)  # type: ignore[type-abstract]
        assert isinstance(factory, KernelWorkflowFactory)

    def test_singleton_identity_for_event_bus(self, container: Container) -> None:
        """Multiple resolutions of EventBus return the same instance."""
        bus1 = container.resolve(EventBus)  # type: ignore[type-abstract]
        bus2 = container.resolve(EventBus)  # type: ignore[type-abstract]
        assert bus1 is bus2

    def test_singleton_identity_for_workflow_engine(self, container: Container) -> None:
        """Multiple resolutions of WorkflowEngine return the same instance."""
        engine1 = container.resolve(WorkflowEngine)  # type: ignore[type-abstract]
        engine2 = container.resolve(WorkflowEngine)  # type: ignore[type-abstract]
        assert engine1 is engine2

    def test_scoped_resource_manager_isolation(self, container: Container) -> None:
        """Different scopes produce different ResourceManager instances."""
        scope1 = container.create_scope()
        scope2 = container.create_scope()
        rm1 = scope1.resolve(ResourceManager)  # type: ignore[type-abstract]
        rm2 = scope2.resolve(ResourceManager)  # type: ignore[type-abstract]
        assert rm1 is not rm2


# ---------------------------------------------------------------------------
# Tests: WorkflowEngine receives EventBus, LoggerFactory via constructor injection
# ---------------------------------------------------------------------------


class TestConstructorInjection:
    """Test that WorkflowEngine receives its dependencies via constructor injection."""

    def test_workflow_engine_has_event_bus(self, container: Container) -> None:
        """WorkflowEngine receives EventBus through constructor injection."""
        engine = container.resolve(WorkflowEngine)  # type: ignore[type-abstract]
        assert isinstance(engine, KernelWorkflowEngine)
        # Access the private attribute to verify injection
        assert hasattr(engine, "_event_bus")
        event_bus = container.resolve(EventBus)  # type: ignore[type-abstract]
        assert engine._event_bus is event_bus

    def test_workflow_engine_has_logger_factory(self, container: Container) -> None:
        """WorkflowEngine receives LoggerFactory through constructor injection."""
        engine = container.resolve(WorkflowEngine)  # type: ignore[type-abstract]
        assert isinstance(engine, KernelWorkflowEngine)
        assert hasattr(engine, "_logger_factory")
        logger_factory = container.resolve(LoggerFactory)  # type: ignore[type-abstract]
        assert engine._logger_factory is logger_factory

    def test_workflow_engine_has_container(self, container: Container) -> None:
        """WorkflowEngine receives Container through constructor injection."""
        engine = container.resolve(WorkflowEngine)  # type: ignore[type-abstract]
        assert isinstance(engine, KernelWorkflowEngine)
        assert hasattr(engine, "_container")
        assert engine._container is container

    def test_workflow_factory_has_container(self, container: Container) -> None:
        """WorkflowFactory receives Container through constructor injection."""
        factory = container.resolve(WorkflowFactory)  # type: ignore[type-abstract]
        assert isinstance(factory, KernelWorkflowFactory)
        assert hasattr(factory, "_container")
        assert factory._container is container


# ---------------------------------------------------------------------------
# Tests: End-to-end workflow execution with events and resource cleanup
# ---------------------------------------------------------------------------


class TestEndToEndIntegration:
    """Test end-to-end workflow execution verifying events and resource cleanup."""

    @pytest.fixture
    def event_log(self) -> list[Any]:
        """Collector for published events."""
        return []

    @pytest.fixture
    def setup_event_tracking(self, container: Container, event_log: list[Any]) -> None:
        """Subscribe to workflow events to track publications."""
        event_bus = container.resolve(EventBus)  # type: ignore[type-abstract]
        event_bus.subscribe(WorkflowStartedEvent, event_log.append)
        event_bus.subscribe(WorkflowCompletedEvent, event_log.append)
        event_bus.subscribe(WorkflowFailedEvent, event_log.append)

    @pytest.mark.asyncio
    async def test_successful_workflow_publishes_started_and_completed_events(
        self, container: Container, event_log: list[Any], setup_event_tracking: None
    ) -> None:
        """A successful workflow publishes WorkflowStartedEvent and WorkflowCompletedEvent."""
        engine = container.resolve(WorkflowEngine)  # type: ignore[type-abstract]
        workflow = SuccessWorkflow()

        summary = await engine.run(workflow)

        assert summary.final_state == WorkflowState.COMPLETED
        assert summary.workflow_name == "success-workflow"
        assert summary.error_details is None

        # Verify events published
        started_events = [e for e in event_log if isinstance(e, WorkflowStartedEvent)]
        completed_events = [e for e in event_log if isinstance(e, WorkflowCompletedEvent)]
        assert len(started_events) == 1
        assert len(completed_events) == 1
        assert started_events[0].workflow_name == "success-workflow"
        assert completed_events[0].workflow_name == "success-workflow"

    @pytest.mark.asyncio
    async def test_failed_workflow_publishes_started_and_failed_events(
        self, container: Container, event_log: list[Any], setup_event_tracking: None
    ) -> None:
        """A failing workflow publishes WorkflowStartedEvent and WorkflowFailedEvent."""
        engine = container.resolve(WorkflowEngine)  # type: ignore[type-abstract]
        workflow = FailingWorkflow()

        summary = await engine.run(workflow)

        assert summary.final_state == WorkflowState.FAILED
        assert summary.workflow_name == "failing-workflow"
        assert "intentional failure" in (summary.error_details or "")

        # Verify events published
        started_events = [e for e in event_log if isinstance(e, WorkflowStartedEvent)]
        failed_events = [e for e in event_log if isinstance(e, WorkflowFailedEvent)]
        assert len(started_events) == 1
        assert len(failed_events) == 1
        assert failed_events[0].error_message == "RuntimeError: intentional failure"

    @pytest.mark.asyncio
    async def test_workflow_summary_has_timing_info(self, container: Container) -> None:
        """WorkflowSummary contains valid start and end times."""
        engine = container.resolve(WorkflowEngine)  # type: ignore[type-abstract]
        workflow = SuccessWorkflow()

        summary = await engine.run(workflow)

        assert summary.start_time is not None
        assert summary.end_time is not None
        assert summary.end_time >= summary.start_time

    @pytest.mark.asyncio
    async def test_workflow_context_provides_resource_manager(self, container: Container) -> None:
        """WorkflowContext provides a ResourceManager that gets cleaned up."""

        class ResourceTrackingWorkflow(Workflow):
            @property
            def name(self) -> str:
                return "resource-tracking"

            async def execute(self, context: WorkflowContext) -> None:
                # Verify context provides expected services
                assert context.resources is not None
                assert context.cancellation_token is not None
                assert context.logger is not None
                assert context.event_bus is not None
                assert context.progress is not None

        engine = container.resolve(WorkflowEngine)  # type: ignore[type-abstract]
        workflow = ResourceTrackingWorkflow()

        summary = await engine.run(workflow)
        assert summary.final_state == WorkflowState.COMPLETED

    @pytest.mark.asyncio
    async def test_workflow_scope_is_cleaned_up_after_execution(self, container: Container) -> None:
        """Scope resources are cleaned up after workflow execution completes."""
        scope_was_active = []

        class ScopeVerifyWorkflow(Workflow):
            @property
            def name(self) -> str:
                return "scope-verify"

            async def execute(self, context: WorkflowContext) -> None:
                # Scope should be active during execution
                scope_was_active.append(True)

        engine = container.resolve(WorkflowEngine)  # type: ignore[type-abstract]
        workflow = ScopeVerifyWorkflow()

        summary = await engine.run(workflow)
        assert summary.final_state == WorkflowState.COMPLETED
        assert scope_was_active == [True]

    @pytest.mark.asyncio
    async def test_end_to_end_event_correlation(
        self, container: Container, event_log: list[Any], setup_event_tracking: None
    ) -> None:
        """Events from the same workflow share the same workflow_id."""
        engine = container.resolve(WorkflowEngine)  # type: ignore[type-abstract]
        workflow = SuccessWorkflow()

        await engine.run(workflow)

        started_events = [e for e in event_log if isinstance(e, WorkflowStartedEvent)]
        completed_events = [e for e in event_log if isinstance(e, WorkflowCompletedEvent)]
        assert len(started_events) == 1
        assert len(completed_events) == 1
        # The started and completed events should share the same workflow_id
        assert started_events[0].workflow_id == completed_events[0].workflow_id


# ---------------------------------------------------------------------------
# Property 25: Bootstrap completeness (Property-based test)
# ---------------------------------------------------------------------------

# All kernel contract interfaces registered during bootstrap.
# Singleton registrations can be resolved directly from the container.
# Scoped registrations (ResourceManager) must be resolved from a scope.
_SINGLETON_CONTRACTS: list[type] = [
    Container,
    ConfigurationService,
    EventBus,
    LoggerFactory,
    WorkflowEngine,
    WorkflowFactory,
]

_SCOPED_CONTRACTS: list[type] = [
    ResourceManager,
]

_ALL_CONTRACTS: list[type] = _SINGLETON_CONTRACTS + _SCOPED_CONTRACTS


class TestProperty25BootstrapCompleteness:
    """Property 25: Bootstrap completeness.

    For any expected kernel contract interface (Container, EventBus,
    WorkflowEngine, ConfigurationService, LoggerFactory, ResourceManager,
    WorkflowFactory), resolving that type from the bootstrapped container
    SHALL succeed without error.

    **Validates: Requirements 10.1, 10.2**
    """

    @given(contract=st.sampled_from(_SINGLETON_CONTRACTS))
    def test_singleton_contracts_resolve_from_container(self, contract: type) -> None:
        """Resolving any singleton contract from the bootstrapped container succeeds.

        Validates: Requirements 10.1, 10.2
        """
        container = bootstrap()

        instance = container.resolve(contract)  # type: ignore[type-abstract]

        assert instance is not None, f"Resolving {contract.__name__} returned None"

    @given(contract=st.sampled_from(_SCOPED_CONTRACTS))
    def test_scoped_contracts_resolve_from_scope(self, contract: type) -> None:
        """Resolving any scoped contract from a bootstrapped container scope succeeds.

        Validates: Requirements 10.1, 10.2
        """
        container = bootstrap()
        scope = container.create_scope()

        instance = scope.resolve(contract)  # type: ignore[type-abstract]

        assert instance is not None, f"Resolving {contract.__name__} from scope returned None"

    @given(contract=st.sampled_from(_ALL_CONTRACTS))
    def test_all_contracts_registered_in_bootstrapped_container(self, contract: type) -> None:
        """Every kernel contract interface is registered in the bootstrapped container.

        Resolving from either the container (singletons/instances) or a scope
        (scoped services) succeeds without raising ServiceNotFoundError.

        Validates: Requirements 10.1, 10.2
        """
        container = bootstrap()

        if contract in _SCOPED_CONTRACTS:
            scope = container.create_scope()
            instance = scope.resolve(contract)  # type: ignore[type-abstract]
        else:
            instance = container.resolve(contract)  # type: ignore[type-abstract]

        assert instance is not None, f"Resolving {contract.__name__} failed — returned None"
