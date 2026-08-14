"""Property-based tests for the KernelEventBus (Properties 7-11).

**Validates: Requirements 2.2, 2.3, 2.5, 2.6, 2.7, 2.8, 2.9**
"""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from uuid import UUID, uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.platform.contracts.events import DomainEvent
from debcraft.platform.kernel.events import KernelEventBus


@pytest.mark.unit
class TestProperty7EventDispatchCompletenessAndOrdering:
    """Property 7: Event dispatch completeness and ordering.

    For any event type with N registered handlers (sync or async), publishing
    an event of that type SHALL invoke all N handlers in their registration order.

    **Validates: Requirements 2.2, 2.3, 2.5**
    """

    @given(
        num_handlers=st.integers(min_value=1, max_value=20),
    )
    def test_all_n_handlers_invoked_in_registration_order(self, num_handlers: int) -> None:
        """All N handlers are invoked exactly once in registration order.

        **Validates: Requirements 2.2, 2.3, 2.5**
        """

        async def _run() -> None:
            bus = KernelEventBus()
            invocation_order: list[int] = []

            for i in range(num_handlers):
                bus.subscribe(DomainEvent, lambda _, idx=i: invocation_order.append(idx))

            await bus.publish(DomainEvent(event_type="prop7.test"))

            assert len(invocation_order) == num_handlers, (
                f"Expected {num_handlers} invocations, got {len(invocation_order)}"
            )
            assert invocation_order == list(range(num_handlers)), (
                f"Handlers not in registration order: {invocation_order}"
            )

        asyncio.run(_run())

    @given(
        num_sync=st.integers(min_value=0, max_value=10),
        num_async=st.integers(min_value=0, max_value=10),
    )
    def test_mixed_sync_async_handlers_maintain_order(self, num_sync: int, num_async: int) -> None:
        """Mixed sync and async handlers are invoked in registration order.

        **Validates: Requirements 2.2, 2.3, 2.5**
        """
        total = num_sync + num_async
        if total == 0:
            return

        async def _run() -> None:
            bus = KernelEventBus()
            invocation_order: list[int] = []

            # Register sync handlers first, then async
            for i in range(num_sync):
                bus.subscribe(DomainEvent, lambda _, idx=i: invocation_order.append(idx))

            for i in range(num_async):

                async def async_handler(_, idx: int = num_sync + i) -> None:
                    invocation_order.append(idx)

                bus.subscribe(DomainEvent, async_handler)

            await bus.publish(DomainEvent(event_type="prop7.mixed"))

            assert len(invocation_order) == total, f"Expected {total} invocations, got {len(invocation_order)}"
            assert invocation_order == list(range(total)), (
                f"Mixed handlers not in registration order: {invocation_order}"
            )

        asyncio.run(_run())


@pytest.mark.unit
class TestProperty8HandlerIsolationOnFailure:
    """Property 8: Handler isolation on failure.

    For any set of handlers registered for an event type, if K handlers raise
    exceptions during dispatch, the remaining (N - K) handlers SHALL still be invoked.

    **Validates: Requirements 2.6**
    """

    @given(
        num_handlers=st.integers(min_value=2, max_value=15),
        data=st.data(),
    )
    def test_k_failing_handlers_dont_prevent_remaining(self, num_handlers: int, data: st.DataObject) -> None:
        """K failing handlers don't prevent remaining (N-K) from running.

        **Validates: Requirements 2.6**
        """
        # Choose which handler indices will fail
        failing_indices = set(
            data.draw(
                st.lists(
                    st.integers(min_value=0, max_value=num_handlers - 1),
                    min_size=1,
                    max_size=num_handlers - 1,
                    unique=True,
                ),
            )
        )

        async def _run() -> None:
            bus = KernelEventBus()
            successful_invocations: list[int] = []

            for i in range(num_handlers):
                if i in failing_indices:

                    def failing_handler(_, idx: int = i) -> None:
                        raise RuntimeError(f"Handler {idx} failed")

                    bus.subscribe(DomainEvent, failing_handler)
                else:
                    bus.subscribe(DomainEvent, lambda _, idx=i: successful_invocations.append(idx))

            await bus.publish(DomainEvent(event_type="prop8.test"))

            expected_successful = [i for i in range(num_handlers) if i not in failing_indices]
            assert successful_invocations == expected_successful, (
                f"Expected successful handlers {expected_successful}, "
                f"got {successful_invocations}. Failing indices: {failing_indices}"
            )

        asyncio.run(_run())


@pytest.mark.unit
class TestProperty9CorrelationIdPropagation:
    """Property 9: Correlation ID propagation.

    For any DomainEvent published with a specific correlation_id, all handlers
    invoked for that event SHALL receive an event object with the same correlation_id.

    **Validates: Requirements 2.7**
    """

    @given(
        num_handlers=st.integers(min_value=1, max_value=15),
    )
    def test_all_handlers_receive_same_correlation_id(self, num_handlers: int) -> None:
        """All handlers receive event with the same correlation_id.

        **Validates: Requirements 2.7**
        """

        async def _run() -> None:
            bus = KernelEventBus()
            correlation_id = uuid4()
            received_ids: list[UUID] = []

            for _ in range(num_handlers):
                bus.subscribe(DomainEvent, lambda e: received_ids.append(e.correlation_id))

            event = DomainEvent(event_type="prop9.test", correlation_id=correlation_id)
            await bus.publish(event)

            assert len(received_ids) == num_handlers
            assert all(rid == correlation_id for rid in received_ids), (
                f"Not all handlers received correlation_id={correlation_id}. Received: {received_ids}"
            )

        asyncio.run(_run())

    @given(
        correlation_id=st.uuids(),
        num_handlers=st.integers(min_value=1, max_value=10),
    )
    def test_arbitrary_correlation_id_propagated(self, correlation_id: UUID, num_handlers: int) -> None:
        """Any UUID correlation_id is propagated unchanged to all handlers.

        **Validates: Requirements 2.7**
        """

        async def _run() -> None:
            bus = KernelEventBus()
            received_ids: list[UUID] = []

            for _ in range(num_handlers):
                bus.subscribe(DomainEvent, lambda e: received_ids.append(e.correlation_id))

            event = DomainEvent(event_type="prop9.arbitrary", correlation_id=correlation_id)
            await bus.publish(event)

            assert all(rid == correlation_id for rid in received_ids)

        asyncio.run(_run())


@pytest.mark.unit
class TestProperty10UnsubscribeRemovesHandlerFromDispatch:
    """Property 10: Unsubscribe removes handler from dispatch.

    For any handler that has been unsubscribed from an event type, subsequent
    publishes of that event type SHALL not invoke that handler.

    **Validates: Requirements 2.8**
    """

    @given(
        num_handlers=st.integers(min_value=2, max_value=15),
        data=st.data(),
    )
    def test_unsubscribed_handler_not_invoked_on_subsequent_publish(
        self, num_handlers: int, data: st.DataObject
    ) -> None:
        """Unsubscribed handler not invoked on subsequent publish.

        **Validates: Requirements 2.8**
        """
        # Choose which handler indices will be unsubscribed
        unsub_indices = set(
            data.draw(
                st.lists(
                    st.integers(min_value=0, max_value=num_handlers - 1),
                    min_size=1,
                    max_size=num_handlers - 1,
                    unique=True,
                ),
            )
        )

        async def _run() -> None:
            bus = KernelEventBus()
            invocations: list[int] = []
            handlers: list[object] = []

            for i in range(num_handlers):

                def make_handler(idx: int):
                    def handler(_: DomainEvent) -> None:
                        invocations.append(idx)

                    return handler

                h = make_handler(i)
                handlers.append(h)
                bus.subscribe(DomainEvent, h)

            # Unsubscribe selected handlers
            for idx in unsub_indices:
                bus.unsubscribe(DomainEvent, handlers[idx])

            # Publish after unsubscribing
            await bus.publish(DomainEvent(event_type="prop10.test"))

            expected = [i for i in range(num_handlers) if i not in unsub_indices]
            assert invocations == expected, (
                f"Expected invocations {expected}, got {invocations}. Unsubscribed: {unsub_indices}"
            )

        asyncio.run(_run())


@pytest.mark.unit
class TestProperty11FrozenDataclassImmutability:
    """Property 11: Frozen dataclass immutability.

    For any instance of DomainEvent, attempting to assign to any attribute
    SHALL raise FrozenInstanceError.

    **Validates: Requirements 2.9**
    """

    @given(
        event_type=st.text(min_size=1, max_size=50),
        correlation_id=st.uuids(),
    )
    def test_assigning_event_type_raises_frozen_instance_error(self, event_type: str, correlation_id: UUID) -> None:
        """Assigning to DomainEvent.event_type raises FrozenInstanceError.

        **Validates: Requirements 2.9**
        """
        event = DomainEvent(event_type=event_type, correlation_id=correlation_id)
        with pytest.raises(FrozenInstanceError):
            event.event_type = "mutated"  # type: ignore[misc]

    @given(
        event_type=st.text(min_size=1, max_size=50),
    )
    def test_assigning_correlation_id_raises_frozen_instance_error(self, event_type: str) -> None:
        """Assigning to DomainEvent.correlation_id raises FrozenInstanceError.

        **Validates: Requirements 2.9**
        """
        event = DomainEvent(event_type=event_type)
        with pytest.raises(FrozenInstanceError):
            event.correlation_id = uuid4()  # type: ignore[misc]

    @given(
        event_type=st.text(min_size=1, max_size=50),
    )
    def test_assigning_timestamp_raises_frozen_instance_error(self, event_type: str) -> None:
        """Assigning to DomainEvent.timestamp raises FrozenInstanceError.

        **Validates: Requirements 2.9**
        """
        from datetime import UTC, datetime

        event = DomainEvent(event_type=event_type)
        with pytest.raises(FrozenInstanceError):
            event.timestamp = datetime.now(UTC)  # type: ignore[misc]
