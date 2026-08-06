"""Kernel logging implementation with structured output and correlation tracking.

Provides `KernelLoggerFactory` and `KernelLogger` wrapping Python's standard
logging module with human-readable and JSON formatters, correlation ID
propagation via context variables, and Rich console integration.
"""

from __future__ import annotations

import contextvars
import json
import logging as _logging
from datetime import UTC, datetime
from uuid import UUID  # noqa: TC003 - UUID used at runtime in ContextVar

from rich.console import Console
from rich.logging import RichHandler

from debcraft.platform.contracts.logging import Logger, LoggerFactory

# Module-level context variable for correlation ID propagation.
_correlation_id_var: contextvars.ContextVar[UUID | None] = contextvars.ContextVar("correlation_id", default=None)


def get_correlation_id() -> UUID | None:
    """Get the current correlation ID from context.

    Returns:
        The active correlation ID, or None if not set.
    """
    return _correlation_id_var.get()


def set_correlation_id(correlation_id: UUID | None) -> contextvars.Token[UUID | None]:
    """Set the correlation ID in the current context.

    Args:
        correlation_id: The correlation ID to set, or None to clear.

    Returns:
        A token that can be used to reset the context variable.
    """
    return _correlation_id_var.set(correlation_id)


class _CorrelationIdFilter(_logging.Filter):
    """Logging filter that attaches a specific correlation ID to log records."""

    def __init__(self, correlation_id: UUID) -> None:
        """Initialize the filter with a fixed correlation ID.

        Args:
            correlation_id: The correlation ID to attach to all records.
        """
        super().__init__()
        self._correlation_id = correlation_id

    def filter(self, record: _logging.LogRecord) -> bool:
        """Attach the correlation ID to the log record.

        Args:
            record: The log record to augment.

        Returns:
            Always True so the record is not filtered out.
        """
        record.correlation_id = str(self._correlation_id)  # type: ignore[attr-defined]
        return True


class _ContextCorrelationIdFilter(_logging.Filter):
    """Logging filter that reads correlation ID from the context variable."""

    def filter(self, record: _logging.LogRecord) -> bool:
        """Attach the context correlation ID to the log record.

        Args:
            record: The log record to augment.

        Returns:
            Always True so the record is not filtered out.
        """
        cid = _correlation_id_var.get()
        record.correlation_id = str(cid) if cid is not None else ""  # type: ignore[attr-defined]
        return True


class HumanFormatter(_logging.Formatter):
    """Human-readable log formatter.

    Produces output in the format:
        TIMESTAMP LEVEL COMPONENT MESSAGE [correlation_id=UUID]

    The correlation_id portion is only included when a correlation ID is active.
    """

    def format(self, record: _logging.LogRecord) -> str:
        """Format a log record for human-readable output.

        Args:
            record: The log record to format.

        Returns:
            The formatted log string.
        """
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        level = record.levelname
        component = record.name
        message = record.getMessage()

        correlation_id: str = getattr(record, "correlation_id", "")
        extra_parts: list[str] = []

        if correlation_id:
            extra_parts.append(f"correlation_id={correlation_id}")

        # Include any extra kwargs stored on the record
        extra_data: dict[str, object] = getattr(record, "extra_data", {})
        for key, value in extra_data.items():
            extra_parts.append(f"{key}={value}")

        suffix = f" [{', '.join(extra_parts)}]" if extra_parts else ""
        return f"{timestamp} {level} {component} {message}{suffix}"


class JsonFormatter(_logging.Formatter):
    """JSON log formatter.

    Produces a JSON object per log entry with fields: timestamp, level,
    component, message, correlation_id, and extra.
    """

    def format(self, record: _logging.LogRecord) -> str:
        """Format a log record as JSON.

        Args:
            record: The log record to format.

        Returns:
            A JSON-encoded string representing the log entry.
        """
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat()
        correlation_id: str = getattr(record, "correlation_id", "")
        extra_data: dict[str, object] = getattr(record, "extra_data", {})

        entry: dict[str, object] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "component": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id if correlation_id else None,
            "extra": extra_data if extra_data else None,
        }
        return json.dumps(entry, default=str)


class KernelLogger(Logger):
    """Structured logger wrapping a standard library Logger.

    Each instance is scoped to a component name and optionally bound
    to a correlation ID. Extra keyword arguments passed to log methods
    are attached as structured data.
    """

    def __init__(
        self,
        logger: _logging.Logger,
        *,
        correlation_id: UUID | None = None,
    ) -> None:
        """Initialize a KernelLogger.

        Args:
            logger: The underlying stdlib logger instance.
            correlation_id: Optional fixed correlation ID for this logger.
        """
        self._logger = logger
        self._correlation_id = correlation_id

        if correlation_id is not None:
            self._logger.addFilter(_CorrelationIdFilter(correlation_id))

    def debug(self, message: str, **kwargs: object) -> None:
        """Log a message at DEBUG level.

        Args:
            message: The log message.
            **kwargs: Additional structured fields to include in the log entry.
        """
        self._log(_logging.DEBUG, message, kwargs)

    def info(self, message: str, **kwargs: object) -> None:
        """Log a message at INFO level.

        Args:
            message: The log message.
            **kwargs: Additional structured fields to include in the log entry.
        """
        self._log(_logging.INFO, message, kwargs)

    def warning(self, message: str, **kwargs: object) -> None:
        """Log a message at WARNING level.

        Args:
            message: The log message.
            **kwargs: Additional structured fields to include in the log entry.
        """
        self._log(_logging.WARNING, message, kwargs)

    def error(self, message: str, **kwargs: object) -> None:
        """Log a message at ERROR level.

        Args:
            message: The log message.
            **kwargs: Additional structured fields to include in the log entry.
        """
        self._log(_logging.ERROR, message, kwargs)

    def with_correlation_id(self, correlation_id: UUID) -> Logger:
        """Return a child logger with the correlation ID attached.

        Creates a new KernelLogger instance that includes the given
        correlation ID in every log entry it produces.

        Args:
            correlation_id: The unique identifier for tracing related operations.

        Returns:
            A new Logger instance bound to the specified correlation ID.
        """
        # Create a child logger under the same component name with a unique suffix
        child_name = f"{self._logger.name}.cid-{correlation_id.hex[:8]}"
        child_stdlib_logger = _logging.getLogger(child_name)
        child_stdlib_logger.setLevel(self._logger.level)
        # Child inherits handlers from parent via propagation
        child_stdlib_logger.propagate = True
        return KernelLogger(child_stdlib_logger, correlation_id=correlation_id)

    def _log(self, level: int, message: str, extra_kwargs: dict[str, object]) -> None:
        """Emit a log record with optional extra structured data.

        Args:
            level: The numeric log level.
            message: The log message text.
            extra_kwargs: Additional structured fields.
        """
        if not self._logger.isEnabledFor(level):
            return
        # Store extra kwargs on the record for formatters to pick up
        self._logger.log(level, message, extra={"extra_data": extra_kwargs})


class _ExtraDataFilter(_logging.Filter):
    """Filter that extracts extra_data from the record's extra dict to a top-level attr."""

    def filter(self, record: _logging.LogRecord) -> bool:
        """Move extra_data to a top-level record attribute.

        Args:
            record: The log record to process.

        Returns:
            Always True.
        """
        # The logging module places extra dict values directly on the record
        if not hasattr(record, "extra_data"):
            record.extra_data = {}  # type: ignore[attr-defined]
        return True


class KernelLoggerFactory(LoggerFactory):
    """Factory creating KernelLogger instances scoped to components.

    Configures the root debcraft logger with appropriate handlers and
    formatters based on the desired output format.
    """

    def __init__(
        self,
        *,
        level: str = "INFO",
        log_format: str = "human",
        console: Console | None = None,
    ) -> None:
        """Initialize the logger factory.

        Args:
            level: The logging level (DEBUG, INFO, WARNING, ERROR).
            log_format: The output format, either "human" or "json".
            console: Optional Rich Console instance for display integration.
        """
        self._level = getattr(_logging, level.upper(), _logging.INFO)
        self._log_format = log_format
        self._console = console or Console(stderr=True)
        self._configured = False
        self._configure_root()

    def get_logger(self, component: str) -> Logger:
        """Create a logger for the named component.

        Args:
            component: The name of the component requesting a logger
                (typically the module or service name).

        Returns:
            A Logger instance configured with the component context.
        """
        stdlib_logger = _logging.getLogger(f"debcraft.{component}")
        stdlib_logger.setLevel(self._level)
        return KernelLogger(stdlib_logger)

    def _configure_root(self) -> None:
        """Configure the root debcraft logger with handlers and formatters."""
        if self._configured:
            return

        root_logger = _logging.getLogger("debcraft")
        root_logger.setLevel(self._level)
        # Remove existing handlers to avoid duplicates
        root_logger.handlers.clear()
        root_logger.propagate = False

        # Add context-based correlation ID filter
        root_logger.addFilter(_ContextCorrelationIdFilter())
        root_logger.addFilter(_ExtraDataFilter())

        if self._log_format == "json":
            handler: _logging.Handler = _logging.StreamHandler()
            handler.setFormatter(JsonFormatter())
        else:
            # Use RichHandler for human-readable output to avoid display
            # corruption when Rich live displays are active.
            handler = RichHandler(
                console=self._console,
                show_time=False,
                show_level=False,
                show_path=False,
                markup=False,
                rich_tracebacks=True,
            )
            handler.setFormatter(HumanFormatter())

        handler.setLevel(self._level)
        root_logger.addHandler(handler)
        self._configured = True
