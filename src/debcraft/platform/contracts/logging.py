"""Logging contract defining structured logger and factory interfaces."""

from abc import ABC, abstractmethod
from uuid import UUID


class Logger(ABC):
    """Structured logger with component context.

    Provides leveled logging methods that attach component identity and
    optional correlation ID to every log entry. Implementations wrap
    Python's standard ``logging`` module with custom formatters.
    """

    @abstractmethod
    def debug(self, message: str, **kwargs: object) -> None:
        """Log a message at DEBUG level.

        Args:
            message: The log message.
            **kwargs: Additional structured fields to include in the log entry.
        """
        ...

    @abstractmethod
    def info(self, message: str, **kwargs: object) -> None:
        """Log a message at INFO level.

        Args:
            message: The log message.
            **kwargs: Additional structured fields to include in the log entry.
        """
        ...

    @abstractmethod
    def warning(self, message: str, **kwargs: object) -> None:
        """Log a message at WARNING level.

        Args:
            message: The log message.
            **kwargs: Additional structured fields to include in the log entry.
        """
        ...

    @abstractmethod
    def error(self, message: str, **kwargs: object) -> None:
        """Log a message at ERROR level.

        Args:
            message: The log message.
            **kwargs: Additional structured fields to include in the log entry.
        """
        ...

    @abstractmethod
    def with_correlation_id(self, correlation_id: UUID) -> "Logger":
        """Return a child logger with the correlation ID attached.

        The returned logger includes the given correlation ID in every
        log entry it produces.

        Args:
            correlation_id: The unique identifier for tracing related operations.

        Returns:
            A new Logger instance bound to the specified correlation ID.
        """
        ...


class LoggerFactory(ABC):
    """Creates Logger instances scoped to components.

    Each logger produced by the factory is pre-configured with the
    component name so that log entries identify their source.
    """

    @abstractmethod
    def get_logger(self, component: str) -> Logger:
        """Create a logger for the named component.

        Args:
            component: The name of the component requesting a logger
                (typically the module or service name).

        Returns:
            A Logger instance configured with the component context.
        """
        ...
