"""Event bus contract defining domain event types and publish/subscribe interface."""

from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import UUID, uuid4

E = TypeVar("E", bound="DomainEvent")


@dataclass(frozen=True)
class DomainEvent:
    """Base class for all domain events.

    Immutable frozen dataclass representing a completed fact dispatched
    through the event bus. All domain events carry a type identifier,
    timestamp, and correlation ID for tracing.

    Attributes:
        event_type: Identifier string for the event type.
        timestamp: UTC timestamp when the event was created.
        correlation_id: Unique identifier for tracing related events.
    """

    event_type: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    correlation_id: UUID = field(default_factory=uuid4)


# Type alias for event handlers (sync or async callables)
EventHandler = Callable[[Any], None] | Callable[[Any], Coroutine[Any, Any, None]]


class EventBus(ABC):
    """In-process publish/subscribe event bus.

    Dispatches typed domain events to registered handlers. Supports both
    synchronous and asynchronous handler callables. Handler isolation
    ensures one failing handler does not affect others.
    """

    @abstractmethod
    def subscribe(self, event_type: type[E], handler: EventHandler) -> None:
        """Register a handler for a specific event type.

        Args:
            event_type: The domain event class to subscribe to.
            handler: Callable invoked when an event of this type is published.
        """
        ...

    @abstractmethod
    def unsubscribe(self, event_type: type[E], handler: EventHandler) -> None:
        """Remove a handler for a specific event type.

        Args:
            event_type: The domain event class to unsubscribe from.
            handler: The previously registered handler to remove.
        """
        ...

    @abstractmethod
    async def publish(self, event: DomainEvent) -> None:
        """Dispatch an event to all registered handlers.

        Args:
            event: The domain event instance to publish.
        """
        ...
