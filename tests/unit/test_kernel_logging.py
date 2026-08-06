"""Unit tests for the platform kernel logging module."""

import json
import logging as _logging
from uuid import uuid4

import pytest

from debcraft.platform.kernel.logging import (
    HumanFormatter,
    JsonFormatter,
    KernelLogger,
    KernelLoggerFactory,
    _ContextCorrelationIdFilter,
    _correlation_id_var,
    _CorrelationIdFilter,
    get_correlation_id,
    set_correlation_id,
)


@pytest.fixture(autouse=True)
def _reset_correlation_id():
    """Reset the correlation ID context variable between tests."""
    token = _correlation_id_var.set(None)
    yield
    _correlation_id_var.reset(token)


@pytest.fixture(autouse=True)
def _cleanup_debcraft_loggers():
    """Clean up debcraft loggers after each test."""
    yield
    root = _logging.getLogger("debcraft")
    root.handlers.clear()
    root.filters.clear()
    for name in list(_logging.Logger.manager.loggerDict):
        if name.startswith("debcraft.test"):
            logger = _logging.getLogger(name)
            logger.handlers.clear()
            logger.filters.clear()


# --- Context variable tests ---


@pytest.mark.unit
def test_correlation_id_default_is_none():
    assert get_correlation_id() is None


@pytest.mark.unit
def test_correlation_id_set_and_get():
    cid = uuid4()
    set_correlation_id(cid)
    assert get_correlation_id() == cid


@pytest.mark.unit
def test_correlation_id_set_none_clears():
    cid = uuid4()
    set_correlation_id(cid)
    set_correlation_id(None)
    assert get_correlation_id() is None


@pytest.mark.unit
def test_correlation_id_token_resets():
    cid = uuid4()
    token = set_correlation_id(cid)
    assert get_correlation_id() == cid
    _correlation_id_var.reset(token)
    assert get_correlation_id() is None


# --- Filter tests ---


@pytest.mark.unit
def test_fixed_filter_attaches_correlation_id():
    cid = uuid4()
    f = _CorrelationIdFilter(cid)
    record = _logging.LogRecord(
        name="test", level=_logging.INFO, pathname="", lineno=0, msg="hello", args=(), exc_info=None
    )
    result = f.filter(record)
    assert result is True
    assert record.correlation_id == str(cid)  # type: ignore[attr-defined]


@pytest.mark.unit
def test_context_filter_reads_from_context():
    cid = uuid4()
    set_correlation_id(cid)
    f = _ContextCorrelationIdFilter()
    record = _logging.LogRecord(
        name="test", level=_logging.INFO, pathname="", lineno=0, msg="hello", args=(), exc_info=None
    )
    result = f.filter(record)
    assert result is True
    assert record.correlation_id == str(cid)  # type: ignore[attr-defined]


@pytest.mark.unit
def test_context_filter_empty_when_no_correlation():
    f = _ContextCorrelationIdFilter()
    record = _logging.LogRecord(
        name="test", level=_logging.INFO, pathname="", lineno=0, msg="hello", args=(), exc_info=None
    )
    result = f.filter(record)
    assert result is True
    assert record.correlation_id == ""  # type: ignore[attr-defined]


# --- HumanFormatter tests ---


@pytest.mark.unit
def test_human_formatter_basic_format():
    formatter = HumanFormatter()
    record = _logging.LogRecord(
        name="debcraft.builder",
        level=_logging.INFO,
        pathname="",
        lineno=0,
        msg="Build started",
        args=(),
        exc_info=None,
    )
    record.correlation_id = ""  # type: ignore[attr-defined]
    record.extra_data = {}  # type: ignore[attr-defined]
    output = formatter.format(record)
    assert "INFO" in output
    assert "debcraft.builder" in output
    assert "Build started" in output


@pytest.mark.unit
def test_human_formatter_includes_correlation_id():
    formatter = HumanFormatter()
    cid = uuid4()
    record = _logging.LogRecord(
        name="debcraft.builder",
        level=_logging.WARNING,
        pathname="",
        lineno=0,
        msg="Slow step",
        args=(),
        exc_info=None,
    )
    record.correlation_id = str(cid)  # type: ignore[attr-defined]
    record.extra_data = {}  # type: ignore[attr-defined]
    output = formatter.format(record)
    assert f"correlation_id={cid}" in output


@pytest.mark.unit
def test_human_formatter_includes_extra_data():
    formatter = HumanFormatter()
    record = _logging.LogRecord(
        name="debcraft.builder",
        level=_logging.INFO,
        pathname="",
        lineno=0,
        msg="Step done",
        args=(),
        exc_info=None,
    )
    record.correlation_id = ""  # type: ignore[attr-defined]
    record.extra_data = {"duration": 1.5, "step": "compile"}  # type: ignore[attr-defined]
    output = formatter.format(record)
    assert "duration=1.5" in output
    assert "step=compile" in output


@pytest.mark.unit
def test_human_formatter_order():
    formatter = HumanFormatter()
    record = _logging.LogRecord(
        name="debcraft.workflow",
        level=_logging.ERROR,
        pathname="",
        lineno=0,
        msg="Failed",
        args=(),
        exc_info=None,
    )
    record.correlation_id = ""  # type: ignore[attr-defined]
    record.extra_data = {}  # type: ignore[attr-defined]
    output = formatter.format(record)
    # Verify order: timestamp, level, component, message
    parts = output.split()
    assert "T" in parts[0]  # timestamp
    assert parts[1] == "ERROR"
    assert parts[2] == "debcraft.workflow"
    assert parts[3] == "Failed"


# --- JsonFormatter tests ---


@pytest.mark.unit
def test_json_formatter_basic():
    formatter = JsonFormatter()
    record = _logging.LogRecord(
        name="debcraft.config",
        level=_logging.INFO,
        pathname="",
        lineno=0,
        msg="Loaded config",
        args=(),
        exc_info=None,
    )
    record.correlation_id = ""  # type: ignore[attr-defined]
    record.extra_data = {}  # type: ignore[attr-defined]
    output = formatter.format(record)
    data = json.loads(output)
    assert data["level"] == "INFO"
    assert data["component"] == "debcraft.config"
    assert data["message"] == "Loaded config"
    assert "timestamp" in data
    assert data["correlation_id"] is None
    assert data["extra"] is None


@pytest.mark.unit
def test_json_formatter_with_correlation_id():
    formatter = JsonFormatter()
    cid = uuid4()
    record = _logging.LogRecord(
        name="debcraft.events",
        level=_logging.DEBUG,
        pathname="",
        lineno=0,
        msg="Event published",
        args=(),
        exc_info=None,
    )
    record.correlation_id = str(cid)  # type: ignore[attr-defined]
    record.extra_data = {}  # type: ignore[attr-defined]
    output = formatter.format(record)
    data = json.loads(output)
    assert data["correlation_id"] == str(cid)


@pytest.mark.unit
def test_json_formatter_with_extra_data():
    formatter = JsonFormatter()
    record = _logging.LogRecord(
        name="debcraft.workflow",
        level=_logging.INFO,
        pathname="",
        lineno=0,
        msg="Step complete",
        args=(),
        exc_info=None,
    )
    record.correlation_id = ""  # type: ignore[attr-defined]
    record.extra_data = {"step_name": "build", "duration_ms": 250}  # type: ignore[attr-defined]
    output = formatter.format(record)
    data = json.loads(output)
    assert data["extra"]["step_name"] == "build"
    assert data["extra"]["duration_ms"] == 250


@pytest.mark.unit
def test_json_formatter_produces_valid_json():
    formatter = JsonFormatter()
    cid = uuid4()
    record = _logging.LogRecord(
        name="debcraft.test",
        level=_logging.WARNING,
        pathname="",
        lineno=0,
        msg="Message with 'quotes' and \"double quotes\"",
        args=(),
        exc_info=None,
    )
    record.correlation_id = str(cid)  # type: ignore[attr-defined]
    record.extra_data = {"key": "value with spaces"}  # type: ignore[attr-defined]
    output = formatter.format(record)
    parsed = json.loads(output)
    assert parsed["message"] == "Message with 'quotes' and \"double quotes\""


# --- KernelLogger tests ---


def _make_capturing_logger(name: str):
    """Create a stdlib logger with a handler that captures records."""
    records: list[_logging.LogRecord] = []

    class _CapturingHandler(_logging.Handler):
        def emit(self, record):
            records.append(record)

    stdlib_logger = _logging.getLogger(name)
    stdlib_logger.handlers.clear()
    stdlib_logger.filters.clear()
    stdlib_logger.setLevel(_logging.DEBUG)
    stdlib_logger.propagate = False
    stdlib_logger.addHandler(_CapturingHandler())
    return KernelLogger(stdlib_logger), records


@pytest.mark.unit
def test_kernel_logger_debug_level():
    logger, records = _make_capturing_logger("debcraft.test.debug")
    logger.debug("test message")
    assert len(records) == 1
    assert records[0].levelno == _logging.DEBUG
    assert records[0].getMessage() == "test message"


@pytest.mark.unit
def test_kernel_logger_info_level():
    logger, records = _make_capturing_logger("debcraft.test.info")
    logger.info("info msg")
    assert len(records) == 1
    assert records[0].levelno == _logging.INFO


@pytest.mark.unit
def test_kernel_logger_warning_level():
    logger, records = _make_capturing_logger("debcraft.test.warning")
    logger.warning("warn msg")
    assert len(records) == 1
    assert records[0].levelno == _logging.WARNING


@pytest.mark.unit
def test_kernel_logger_error_level():
    logger, records = _make_capturing_logger("debcraft.test.error")
    logger.error("error msg")
    assert len(records) == 1
    assert records[0].levelno == _logging.ERROR


@pytest.mark.unit
def test_kernel_logger_extra_kwargs_stored():
    logger, records = _make_capturing_logger("debcraft.test.extra")
    logger.info("event", action="build", count=3)
    assert len(records) == 1
    assert records[0].extra_data == {"action": "build", "count": 3}  # type: ignore[attr-defined]


@pytest.mark.unit
def test_kernel_logger_with_correlation_id_returns_new_logger():
    logger, _ = _make_capturing_logger("debcraft.test.cidnew")
    cid = uuid4()
    child = logger.with_correlation_id(cid)
    assert child is not logger
    assert isinstance(child, KernelLogger)


@pytest.mark.unit
def test_kernel_logger_with_correlation_id_attaches_id():
    cid = uuid4()

    # Set up a child logger that captures records
    child_records: list[_logging.LogRecord] = []

    class _CapturingHandler(_logging.Handler):
        def emit(self, record):
            child_records.append(record)

    parent_logger = _logging.getLogger("debcraft.test.cidparent")
    parent_logger.handlers.clear()
    parent_logger.filters.clear()
    parent_logger.setLevel(_logging.DEBUG)
    parent_logger.propagate = False

    kernel_logger = KernelLogger(parent_logger)
    child = kernel_logger.with_correlation_id(cid)

    # Attach handler directly on child's internal logger
    assert isinstance(child, KernelLogger)
    child._logger.propagate = False
    child._logger.addHandler(_CapturingHandler())

    child.info("correlated message")
    assert len(child_records) == 1
    assert child_records[0].correlation_id == str(cid)  # type: ignore[attr-defined]


# --- KernelLoggerFactory tests ---


@pytest.mark.unit
def test_factory_get_logger_returns_kernel_logger():
    factory = KernelLoggerFactory(level="DEBUG", log_format="human")
    logger = factory.get_logger("my_component")
    assert isinstance(logger, KernelLogger)


@pytest.mark.unit
def test_factory_get_logger_component_name():
    factory = KernelLoggerFactory(level="INFO", log_format="human")
    logger = factory.get_logger("workflow_engine")
    assert isinstance(logger, KernelLogger)
    assert logger._logger.name == "debcraft.workflow_engine"


@pytest.mark.unit
def test_factory_configures_root_logger():
    KernelLoggerFactory(level="DEBUG", log_format="human")
    root = _logging.getLogger("debcraft")
    assert root.level == _logging.DEBUG
    assert len(root.handlers) == 1
    assert root.propagate is False


@pytest.mark.unit
def test_factory_json_format():
    KernelLoggerFactory(level="INFO", log_format="json")
    root = _logging.getLogger("debcraft")
    assert len(root.handlers) == 1
    handler = root.handlers[0]
    assert isinstance(handler.formatter, JsonFormatter)


@pytest.mark.unit
def test_factory_human_format_uses_rich_handler():
    from rich.logging import RichHandler

    KernelLoggerFactory(level="INFO", log_format="human")
    root = _logging.getLogger("debcraft")
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0], RichHandler)


@pytest.mark.unit
def test_factory_level_respected():
    factory = KernelLoggerFactory(level="WARNING", log_format="human")
    logger = factory.get_logger("test_comp")
    assert logger._logger.level == _logging.WARNING


@pytest.mark.unit
def test_factory_multiple_get_logger_calls():
    factory = KernelLoggerFactory(level="INFO", log_format="human")
    logger1 = factory.get_logger("component_a")
    logger2 = factory.get_logger("component_b")
    assert logger1._logger.name != logger2._logger.name
    assert logger1._logger.name == "debcraft.component_a"
    assert logger2._logger.name == "debcraft.component_b"
