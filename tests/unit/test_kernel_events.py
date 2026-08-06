"""Unit tests for KernelEventBus and workflow domain events."""

import logging
from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from debcraft.platform.contracts.events import DomainEvent
from debcraft.platform.kernel.events import (
    KernelEventBus,
    WorkflowCancelledEvent,
    WorkflowCompletedEvent,
    WorkflowFailedEvent,
    WorkflowStartedEvent,
)


@pytest.fixture
def bus() -> KernelEventBus:
    return KernelEventBus()


class TestKernelEventBusSubscribeAndPublish:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_publish_invokes_sync_handler(self, bus: KernelEventBus) -> None:
        received: list[DomainEvent] = []
        bus.subscribe(DomainEvent, received.append)

        event = DomainEvent(event_type="test")
        await bus.publish(event)

        assert received == [event]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_publish_invokes_async_handler(self, bus: KernelEventBus) -> None:
        received: list[DomainEvent] = []

        async def async_handler(e: DomainEvent) -> None:
            received.append(e)

        bus.subscribe(DomainEvent, async_handler)

        event = DomainEvent(event_type="test.async")
        await bus.publish(event)

        assert received == [event]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_publish_invokes_handlers_in_registration_order(self, bus: KernelEventBus) -> None:
        order: list[int] = []

        bus.subscribe(DomainEvent, lambda _: order.append(1))
        bus.subscribe(DomainEvent, lambda _: order.append(2))
        bus.subscribe(DomainEvent, lambda _: order.append(3))

        await bus.publish(DomainEvent(event_type="order.test"))

        assert order == [1, 2, 3]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_publish_with_no_handlers_is_noop(self, bus: KernelEventBus) -> None:
        # Should not raise
        await bus.publish(DomainEvent(event_type="no.handlers"))

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_publish_dispatches_only_to_matching_event_type(self, bus: KernelEventBus) -> None:
        received: list[DomainEvent] = []
        bus.subscribe(WorkflowStartedEvent, received.append)

        await bus.publish(DomainEvent(event_type="other"))

        assert received == []

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_publish_propagates_correlation_id(self, bus: KernelEventBus) -> None:
        correlation_id = uuid4()
        received: list[DomainEvent] = []
        bus.subscribe(DomainEvent, received.append)

        event = DomainEvent(event_type="corr.test", correlation_id=correlation_id)
        await bus.publish(event)

        assert received[0].correlation_id == correlation_id


class TestKernelEventBusHandlerIsolation:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_failing_handler_does_not_interrupt_others(self, bus: KernelEventBus) -> None:
        received: list[int] = []

        bus.subscribe(DomainEvent, lambda _: received.append(1))

        def failing_handler(_: DomainEvent) -> None:
            msg = "handler error"
            raise RuntimeError(msg)

        bus.subscribe(DomainEvent, failing_handler)
        bus.subscribe(DomainEvent, lambda _: received.append(3))

        await bus.publish(DomainEvent(event_type="fail.test"))

        assert received == [1, 3]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_failing_handler_logs_error(self, bus: KernelEventBus, caplog: pytest.LogCaptureFixture) -> None:
        def failing_handler(_: DomainEvent) -> None:
            msg = "oops"
            raise ValueError(msg)

        bus.subscribe(DomainEvent, failing_handler)

        with caplog.at_level(logging.ERROR):
            await bus.publish(DomainEvent(event_type="log.test"))

        assert any("failed while processing event" in record.message for record in caplog.records)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_failing_async_handler_does_not_interrupt_others(self, bus: KernelEventBus) -> None:
        received: list[int] = []

        async def failing_async(_: DomainEvent) -> None:
            msg = "async fail"
            raise RuntimeError(msg)

        bus.subscribe(DomainEvent, lambda _: received.append(1))
        bus.subscribe(DomainEvent, failing_async)
        bus.subscribe(DomainEvent, lambda _: received.append(3))

        await bus.publish(DomainEvent(event_type="async.fail.test"))

        assert received == [1, 3]


class TestKernelEventBusUnsubscribe:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_unsubscribed_handler_not_invoked(self, bus: KernelEventBus) -> None:
        received: list[DomainEvent] = []
        bus.subscribe(DomainEvent, received.append)
        bus.unsubscribe(DomainEvent, received.append)

        await bus.publish(DomainEvent(event_type="unsub.test"))

        assert received == []

    @pytest.mark.unit
    def test_unsubscribe_nonexistent_handler_is_noop(self, bus: KernelEventBus) -> None:
        # Should not raise
        bus.unsubscribe(DomainEvent, lambda _: None)

    @pytest.mark.unit
    def test_unsubscribe_from_nonexistent_event_type_is_noop(self, bus: KernelEventBus) -> None:
        # Should not raise
        bus.unsubscribe(WorkflowStartedEvent, lambda _: None)


class TestWorkflowEventDataclasses:
    @pytest.mark.unit
    def test_workflow_started_event_defaults(self) -> None:
        event = WorkflowStartedEvent()
        assert event.event_type == "workflow.started"
        assert event.workflow_name == ""
        assert event.workflow_id is not None
        assert event.correlation_id is not None
        assert event.timestamp is not None

    @pytest.mark.unit
    def test_workflow_completed_event_defaults(self) -> None:
        event = WorkflowCompletedEvent(workflow_name="build", duration_seconds=1.5)
        assert event.event_type == "workflow.completed"
        assert event.workflow_name == "build"
        assert event.duration_seconds == 1.5

    @pytest.mark.unit
    def test_workflow_failed_event_defaults(self) -> None:
        event = WorkflowFailedEvent(workflow_name="build", error_message="timeout")
        assert event.event_type == "workflow.failed"
        assert event.error_message == "timeout"

    @pytest.mark.unit
    def test_workflow_cancelled_event_defaults(self) -> None:
        event = WorkflowCancelledEvent(workflow_name="build")
        assert event.event_type == "workflow.cancelled"
        assert event.workflow_name == "build"

    @pytest.mark.unit
    def test_workflow_events_are_frozen(self) -> None:
        event = WorkflowStartedEvent(workflow_name="test")
        with pytest.raises(FrozenInstanceError):
            event.workflow_name = "changed"  # type: ignore[misc]

    @pytest.mark.unit
    def test_workflow_events_inherit_domain_event(self) -> None:
        event = WorkflowStartedEvent()
        assert isinstance(event, DomainEvent)
