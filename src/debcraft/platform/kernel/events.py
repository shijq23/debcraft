"""Kernel event bus implementation and workflow domain events.

Provides the concrete KernelEventBus implementing the EventBus contract,
along with workflow lifecycle domain event dataclasses.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from debcraft.platform.contracts.events import DomainEvent, EventBus, EventHandler

logger = logging.getLogger(__name__)


class KernelEventBus(EventBus):
    """In-process publish/subscribe event bus implementation.

    Dispatches typed domain events to registered handlers in insertion order.
    Supports both synchronous and asynchronous handler callables. Handler
    isolation ensures one failing handler does not affect others.
    """

    def __init__(self) -> None:
        """Initialize the event bus with an empty handler registry."""
        self._handlers: dict[type, list[EventHandler]] = {}

    def subscribe(self, event_type: type[Any], handler: EventHandler) -> None:
        """Register a handler for a specific event type.

        Handlers are invoked in registration order when an event of the
        given type is published.

        Args:
            event_type: The domain event class to subscribe to.
            handler: Callable invoked when an event of this type is published.
        """
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: type[Any], handler: EventHandler) -> None:
        """Remove a handler for a specific event type.

        If the handler is not found in the registry for the given event type,
        this method is a no-op.

        Args:
            event_type: The domain event class to unsubscribe from.
            handler: The previously registered handler to remove.
        """
        handlers = self._handlers.get(event_type)
        if handlers is None:
            return
        with contextlib.suppress(ValueError):
            handlers.remove(handler)

    async def publish(self, event: DomainEvent) -> None:
        """Dispatch an event to all registered handlers for its type.

        Iterates handlers in registration order. Async handlers are awaited;
        sync handlers are called directly. If a handler raises an exception,
        the error is logged and dispatch continues to remaining handlers.

        Publishing to an event type with zero handlers is a silent no-op.

        Args:
            event: The domain event instance to publish.
        """
        handlers = self._handlers.get(type(event))
        if not handlers:
            return

        for handler in handlers:
            try:
                if inspect.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception:  # pylint: disable=broad-exception-caught  # Event bus dispatch: must not crash other handlers
                logger.exception(
                    "Handler %r failed while processing event %s",
                    handler,
                    event.event_type,
                )


@dataclass(frozen=True)
class WorkflowStartedEvent(DomainEvent):
    """Published when a workflow transitions to Running.

    Attributes:
        event_type: Identifier for this event kind.
        workflow_name: Name of the workflow that started.
        workflow_id: Unique identifier for the workflow instance.
    """

    event_type: str = "workflow.started"
    workflow_name: str = ""
    workflow_id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True)
class WorkflowCompletedEvent(DomainEvent):
    """Published when a workflow transitions to Completed.

    Attributes:
        event_type: Identifier for this event kind.
        workflow_name: Name of the workflow that completed.
        workflow_id: Unique identifier for the workflow instance.
        duration_seconds: Total execution time in seconds.
    """

    event_type: str = "workflow.completed"
    workflow_name: str = ""
    workflow_id: UUID = field(default_factory=uuid4)
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class WorkflowFailedEvent(DomainEvent):
    """Published when a workflow transitions to Failed.

    Attributes:
        event_type: Identifier for this event kind.
        workflow_name: Name of the workflow that failed.
        workflow_id: Unique identifier for the workflow instance.
        error_message: Description of the failure cause.
    """

    event_type: str = "workflow.failed"
    workflow_name: str = ""
    workflow_id: UUID = field(default_factory=uuid4)
    error_message: str = ""


@dataclass(frozen=True)
class WorkflowCancelledEvent(DomainEvent):
    """Published when a workflow transitions to Cancelled.

    Attributes:
        event_type: Identifier for this event kind.
        workflow_name: Name of the workflow that was cancelled.
        workflow_id: Unique identifier for the workflow instance.
    """

    event_type: str = "workflow.cancelled"
    workflow_name: str = ""
    workflow_id: UUID = field(default_factory=uuid4)
