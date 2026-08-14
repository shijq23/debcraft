"""Unit tests for KernelEventBus and DomainEvent.

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10
"""

from __future__ import annotations

import logging
from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from debcraft.platform.contracts.events import DomainEvent
from debcraft.platform.kernel.events import KernelEventBus


@pytest.fixture
def bus() -> KernelEventBus:
    """Create a fresh KernelEventBus for each test."""
    return KernelEventBus()


class TestSubscribeAndDispatchSingleHandler:
    """Test subscribe and dispatch single handler (Req 2.1, 2.2)."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_single_sync_handler_receives_event(self, bus: KernelEventBus) -> None:
        received: list[DomainEvent] = []
        bus.subscribe(DomainEvent, received.append)

        event = DomainEvent(event_type="test.single")
        await bus.publish(event)

        assert len(received) == 1
        assert received[0] is event

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_multiple_events_dispatched_to_same_handler(self, bus: KernelEventBus) -> None:
        received: list[DomainEvent] = []
        bus.subscribe(DomainEvent, received.append)

        event1 = DomainEvent(event_type="first")
        event2 = DomainEvent(event_type="second")
        await bus.publish(event1)
        await bus.publish(event2)

        assert received == [event1, event2]


class TestSyncAndAsyncHandlerSupport:
    """Test sync and async handler support (Req 2.3, 2.4)."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_sync_handler_invoked(self, bus: KernelEventBus) -> None:
        received: list[DomainEvent] = []

        def sync_handler(event: DomainEvent) -> None:
            received.append(event)

        bus.subscribe(DomainEvent, sync_handler)
        event = DomainEvent(event_type="sync.test")
        await bus.publish(event)

        assert received == [event]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_async_handler_invoked(self, bus: KernelEventBus) -> None:
        received: list[DomainEvent] = []

        async def async_handler(event: DomainEvent) -> None:
            received.append(event)

        bus.subscribe(DomainEvent, async_handler)
        event = DomainEvent(event_type="async.test")
        await bus.publish(event)

        assert received == [event]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_mixed_sync_and_async_handlers(self, bus: KernelEventBus) -> None:
        order: list[str] = []

        def sync_handler(_: DomainEvent) -> None:
            order.append("sync")

        async def async_handler(_: DomainEvent) -> None:
            order.append("async")

        bus.subscribe(DomainEvent, sync_handler)
        bus.subscribe(DomainEvent, async_handler)
        await bus.publish(DomainEvent(event_type="mixed"))

        assert order == ["sync", "async"]


class TestHandlerRegistrationOrdering:
    """Test handler registration ordering (Req 2.5)."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_handlers_invoked_in_registration_order(self, bus: KernelEventBus) -> None:
        order: list[int] = []

        bus.subscribe(DomainEvent, lambda _: order.append(1))
        bus.subscribe(DomainEvent, lambda _: order.append(2))
        bus.subscribe(DomainEvent, lambda _: order.append(3))

        await bus.publish(DomainEvent(event_type="order.test"))

        assert order == [1, 2, 3]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_five_handlers_maintain_insertion_order(self, bus: KernelEventBus) -> None:
        order: list[int] = []

        for i in range(5):
            bus.subscribe(DomainEvent, lambda _, idx=i: order.append(idx))

        await bus.publish(DomainEvent(event_type="five.handlers"))

        assert order == [0, 1, 2, 3, 4]


class TestHandlerExceptionIsolationWithLogging:
    """Test handler exception isolation with logging (Req 2.6, 2.7)."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_failing_sync_handler_does_not_block_subsequent(self, bus: KernelEventBus) -> None:
        received: list[int] = []

        bus.subscribe(DomainEvent, lambda _: received.append(1))

        def failing(_: DomainEvent) -> None:
            msg = "boom"
            raise RuntimeError(msg)

        bus.subscribe(DomainEvent, failing)
        bus.subscribe(DomainEvent, lambda _: received.append(3))

        await bus.publish(DomainEvent(event_type="isolation.test"))

        assert received == [1, 3]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_failing_async_handler_does_not_block_subsequent(self, bus: KernelEventBus) -> None:
        received: list[int] = []

        async def failing(_: DomainEvent) -> None:
            msg = "async boom"
            raise ValueError(msg)

        bus.subscribe(DomainEvent, lambda _: received.append(1))
        bus.subscribe(DomainEvent, failing)
        bus.subscribe(DomainEvent, lambda _: received.append(3))

        await bus.publish(DomainEvent(event_type="async.isolation"))

        assert received == [1, 3]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_exception_is_logged_at_error_level(
        self, bus: KernelEventBus, caplog: pytest.LogCaptureFixture
    ) -> None:
        def failing(_: DomainEvent) -> None:
            msg = "expected error"
            raise RuntimeError(msg)

        bus.subscribe(DomainEvent, failing)

        with caplog.at_level(logging.ERROR):
            await bus.publish(DomainEvent(event_type="log.error"))

        assert any("failed while processing event" in record.message for record in caplog.records)
        assert any(record.levelno == logging.ERROR for record in caplog.records)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_all_handlers_run_despite_multiple_failures(self, bus: KernelEventBus) -> None:
        received: list[int] = []

        def fail1(_: DomainEvent) -> None:
            raise RuntimeError("fail1")

        def fail2(_: DomainEvent) -> None:
            raise RuntimeError("fail2")

        bus.subscribe(DomainEvent, lambda _: received.append(1))
        bus.subscribe(DomainEvent, fail1)
        bus.subscribe(DomainEvent, lambda _: received.append(3))
        bus.subscribe(DomainEvent, fail2)
        bus.subscribe(DomainEvent, lambda _: received.append(5))

        await bus.publish(DomainEvent(event_type="multi.fail"))

        assert received == [1, 3, 5]


class TestUnsubscribeRemovesHandler:
    """Test unsubscribe removes handler (Req 2.8)."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_unsubscribed_handler_not_invoked(self, bus: KernelEventBus) -> None:
        received: list[DomainEvent] = []
        handler = received.append

        bus.subscribe(DomainEvent, handler)
        bus.unsubscribe(DomainEvent, handler)

        await bus.publish(DomainEvent(event_type="unsub.test"))

        assert received == []

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_unsubscribe_leaves_other_handlers_intact(self, bus: KernelEventBus) -> None:
        results: list[str] = []

        def handler_a(_: DomainEvent) -> None:
            results.append("a")

        def handler_b(_: DomainEvent) -> None:
            results.append("b")

        bus.subscribe(DomainEvent, handler_a)
        bus.subscribe(DomainEvent, handler_b)
        bus.unsubscribe(DomainEvent, handler_a)

        await bus.publish(DomainEvent(event_type="partial.unsub"))

        assert results == ["b"]

    @pytest.mark.unit
    def test_unsubscribe_nonexistent_handler_is_noop(self, bus: KernelEventBus) -> None:
        # Should not raise
        bus.unsubscribe(DomainEvent, lambda _: None)

    @pytest.mark.unit
    def test_unsubscribe_from_nonexistent_event_type_is_noop(self, bus: KernelEventBus) -> None:
        from debcraft.platform.kernel.events import WorkflowStartedEvent

        # Should not raise when event type was never registered
        bus.unsubscribe(WorkflowStartedEvent, lambda _: None)


class TestPublishToZeroHandlersIsNoop:
    """Test publish to zero handlers is no-op (Req 2.10)."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_publish_with_no_subscribers_does_not_raise(self, bus: KernelEventBus) -> None:
        # Should complete without error
        await bus.publish(DomainEvent(event_type="no.handlers"))

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_publish_to_unregistered_event_type_is_noop(self, bus: KernelEventBus) -> None:
        from debcraft.platform.kernel.events import WorkflowStartedEvent

        # Subscribe to one type, publish a different one
        received: list[DomainEvent] = []
        bus.subscribe(DomainEvent, received.append)

        await bus.publish(WorkflowStartedEvent(workflow_name="test"))

        assert received == []


class TestDomainEventImmutability:
    """Test DomainEvent immutability (Req 2.9)."""

    @pytest.mark.unit
    def test_domain_event_is_frozen(self) -> None:
        event = DomainEvent(event_type="immutable.test")
        with pytest.raises(FrozenInstanceError):
            event.event_type = "modified"  # type: ignore[misc]

    @pytest.mark.unit
    def test_domain_event_timestamp_is_frozen(self) -> None:
        from datetime import UTC, datetime

        event = DomainEvent(event_type="ts.test")
        with pytest.raises(FrozenInstanceError):
            event.timestamp = datetime.now(UTC)  # type: ignore[misc]

    @pytest.mark.unit
    def test_domain_event_correlation_id_is_frozen(self) -> None:
        event = DomainEvent(event_type="corr.test")
        with pytest.raises(FrozenInstanceError):
            event.correlation_id = uuid4()  # type: ignore[misc]

    @pytest.mark.unit
    def test_domain_event_has_auto_generated_fields(self) -> None:
        event = DomainEvent(event_type="auto.fields")
        assert event.timestamp is not None
        assert event.correlation_id is not None
