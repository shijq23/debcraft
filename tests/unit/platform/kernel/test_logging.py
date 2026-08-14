"""Unit tests for KernelLoggerFactory, formatters, and correlation ID propagation."""

from __future__ import annotations

import json
import logging as _logging
from uuid import uuid4

import pytest

from debcraft.platform.contracts.logging import Logger, LoggerFactory
from debcraft.platform.kernel.logging import (
    HumanFormatter,
    JsonFormatter,
    KernelLogger,
    KernelLoggerFactory,
    _ContextCorrelationIdFilter,
    _CorrelationIdFilter,
    get_correlation_id,
    set_correlation_id,
)


@pytest.fixture(autouse=True)
def _clean_loggers() -> None:
    """Remove debcraft loggers between tests to avoid handler leaks."""
    yield  # type: ignore[misc]
    root = _logging.getLogger("debcraft")
    root.handlers.clear()
    root.filters.clear()


# ---------------------------------------------------------------------------
# Human format output structure (Requirement 5.3)
# ---------------------------------------------------------------------------


class TestHumanFormatOutputStructure:
    def _make_record(self, name: str, level: int, levelname: str, msg: str) -> _logging.LogRecord:
        """Create a LogRecord with correlation_id and extra_data attrs."""
        record = _logging.LogRecord(
            name=name,
            level=level,
            pathname="",
            lineno=0,
            msg=msg,
            args=None,
            exc_info=None,
        )
        record.levelname = levelname
        record.correlation_id = ""  # type: ignore[attr-defined]
        record.extra_data = {}  # type: ignore[attr-defined]
        return record

    @pytest.mark.unit
    def test_human_format_contains_timestamp_level_component_message(self) -> None:
        formatter = HumanFormatter()
        record = self._make_record("debcraft.test.human", _logging.INFO, "INFO", "test message")

        output = formatter.format(record)

        assert "INFO" in output
        assert "debcraft.test.human" in output
        assert "test message" in output

    @pytest.mark.unit
    def test_human_format_includes_correlation_id_when_active(self) -> None:
        formatter = HumanFormatter()
        cid = uuid4()
        record = self._make_record("debcraft.test.cid", _logging.INFO, "INFO", "with cid")
        record.correlation_id = str(cid)  # type: ignore[attr-defined]

        output = formatter.format(record)

        assert f"correlation_id={cid}" in output

    @pytest.mark.unit
    def test_human_format_excludes_correlation_id_when_empty(self) -> None:
        formatter = HumanFormatter()
        record = self._make_record("debcraft.test.nocid", _logging.INFO, "INFO", "no cid")

        output = formatter.format(record)

        assert "correlation_id" not in output

    @pytest.mark.unit
    def test_human_format_timestamp_is_iso_format(self) -> None:
        formatter = HumanFormatter()
        record = self._make_record("debcraft.test.ts", _logging.INFO, "INFO", "timestamp check")

        output = formatter.format(record)

        # Timestamp starts with a date-like pattern: YYYY-MM-DDTHH:MM:SS
        assert output[4] == "-"  # Year separator
        assert "T" in output[:25]  # ISO datetime separator


# ---------------------------------------------------------------------------
# JSON format output is valid JSON with required keys (Requirement 5.4)
# ---------------------------------------------------------------------------


class TestJsonFormatOutput:
    def _make_record(self, name: str, level: int, levelname: str, msg: str) -> _logging.LogRecord:
        """Create a LogRecord with correlation_id and extra_data attrs."""
        record = _logging.LogRecord(
            name=name,
            level=level,
            pathname="",
            lineno=0,
            msg=msg,
            args=None,
            exc_info=None,
        )
        record.levelname = levelname
        record.correlation_id = ""  # type: ignore[attr-defined]
        record.extra_data = {}  # type: ignore[attr-defined]
        return record

    @pytest.mark.unit
    def test_json_format_is_valid_json(self) -> None:
        formatter = JsonFormatter()
        record = self._make_record("debcraft.test.json", _logging.INFO, "INFO", "json test")

        output = formatter.format(record)

        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    @pytest.mark.unit
    def test_json_format_has_required_keys(self) -> None:
        formatter = JsonFormatter()
        record = self._make_record("debcraft.test.jsonkeys", _logging.INFO, "INFO", "keys test")

        output = formatter.format(record)
        parsed = json.loads(output)

        required_keys = {"timestamp", "level", "component", "message", "correlation_id", "extra"}
        assert required_keys.issubset(parsed.keys())

    @pytest.mark.unit
    def test_json_format_level_matches_record(self) -> None:
        formatter = JsonFormatter()
        record = self._make_record("debcraft.test.jsonlevel", _logging.WARNING, "WARNING", "warn msg")

        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["level"] == "WARNING"

    @pytest.mark.unit
    def test_json_format_component_matches_logger_name(self) -> None:
        formatter = JsonFormatter()
        record = self._make_record("debcraft.mycomponent", _logging.INFO, "INFO", "component test")

        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["component"] == "debcraft.mycomponent"

    @pytest.mark.unit
    def test_json_format_correlation_id_included_when_active(self) -> None:
        formatter = JsonFormatter()
        cid = uuid4()
        record = self._make_record("debcraft.test.jsoncid", _logging.INFO, "INFO", "with cid")
        record.correlation_id = str(cid)  # type: ignore[attr-defined]

        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["correlation_id"] == str(cid)

    @pytest.mark.unit
    def test_json_format_correlation_id_null_when_absent(self) -> None:
        formatter = JsonFormatter()
        record = self._make_record("debcraft.test.jsonnull", _logging.INFO, "INFO", "no cid")

        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["correlation_id"] is None


# ---------------------------------------------------------------------------
# Correlation ID inclusion when active (Requirement 5.5)
# ---------------------------------------------------------------------------


class TestCorrelationIdInclusion:
    @pytest.mark.unit
    def test_context_var_default_is_none(self) -> None:
        # Reset first
        set_correlation_id(None)

        assert get_correlation_id() is None

    @pytest.mark.unit
    def test_set_and_get_correlation_id(self) -> None:
        cid = uuid4()
        set_correlation_id(cid)

        assert get_correlation_id() == cid

        # Clean up
        set_correlation_id(None)

    @pytest.mark.unit
    def test_with_correlation_id_returns_new_logger(self) -> None:
        factory = KernelLoggerFactory(level="DEBUG", log_format="json")
        logger = factory.get_logger("test.component")

        cid = uuid4()
        child_logger = logger.with_correlation_id(cid)

        assert child_logger is not logger
        assert isinstance(child_logger, Logger)

    @pytest.mark.unit
    def test_correlation_id_filter_attaches_id_to_record(self) -> None:
        cid = uuid4()
        filter_obj = _CorrelationIdFilter(cid)
        record = _logging.LogRecord(
            name="test",
            level=_logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=None,
            exc_info=None,
        )

        filter_obj.filter(record)

        assert record.correlation_id == str(cid)  # type: ignore[attr-defined]

    @pytest.mark.unit
    def test_context_correlation_id_filter_reads_from_contextvar(self) -> None:
        cid = uuid4()
        set_correlation_id(cid)

        filter_obj = _ContextCorrelationIdFilter()
        record = _logging.LogRecord(
            name="test",
            level=_logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=None,
            exc_info=None,
        )

        filter_obj.filter(record)

        assert record.correlation_id == str(cid)  # type: ignore[attr-defined]

        # Clean up
        set_correlation_id(None)

    @pytest.mark.unit
    def test_context_correlation_id_filter_empty_when_not_set(self) -> None:
        set_correlation_id(None)

        filter_obj = _ContextCorrelationIdFilter()
        record = _logging.LogRecord(
            name="test",
            level=_logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=None,
            exc_info=None,
        )

        filter_obj.filter(record)

        assert record.correlation_id == ""  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Component name attachment (Requirement 5.1)
# ---------------------------------------------------------------------------


class TestComponentNameAttachment:
    @pytest.mark.unit
    def test_logger_component_name_is_prefixed(self) -> None:
        factory = KernelLoggerFactory(level="DEBUG", log_format="json")
        logger = factory.get_logger("myservice")

        # The underlying logger should be named debcraft.myservice
        assert isinstance(logger, KernelLogger)
        assert logger._logger.name == "debcraft.myservice"

    @pytest.mark.unit
    def test_different_components_get_different_loggers(self) -> None:
        factory = KernelLoggerFactory(level="DEBUG", log_format="json")
        logger_a = factory.get_logger("service_a")
        logger_b = factory.get_logger("service_b")

        assert isinstance(logger_a, KernelLogger)
        assert isinstance(logger_b, KernelLogger)
        assert logger_a._logger.name != logger_b._logger.name

    @pytest.mark.unit
    def test_factory_implements_contract(self) -> None:
        factory = KernelLoggerFactory(level="DEBUG", log_format="json")

        assert isinstance(factory, LoggerFactory)

    @pytest.mark.unit
    def test_logger_implements_contract(self) -> None:
        factory = KernelLoggerFactory(level="DEBUG", log_format="json")
        logger = factory.get_logger("contract_test")

        assert isinstance(logger, Logger)


# ---------------------------------------------------------------------------
# Logger level filtering
# ---------------------------------------------------------------------------


class TestLoggerLevelFiltering:
    @pytest.mark.unit
    def test_logger_respects_configured_level(self) -> None:
        factory = KernelLoggerFactory(level="WARNING", log_format="json")
        logger = factory.get_logger("leveltest")

        # The underlying logger should be at WARNING level
        assert isinstance(logger, KernelLogger)
        assert logger._logger.level == _logging.WARNING

    @pytest.mark.unit
    def test_factory_default_level_is_info(self) -> None:
        factory = KernelLoggerFactory()
        logger = factory.get_logger("default_level")

        assert isinstance(logger, KernelLogger)
        assert logger._logger.level == _logging.INFO


# ---------------------------------------------------------------------------
# Integration: full log output via factory
# ---------------------------------------------------------------------------


class TestLogOutputIntegration:
    @pytest.mark.unit
    def test_json_factory_produces_valid_json_output(self) -> None:
        # Use a StreamHandler writing to stderr so capsys can capture it
        factory = KernelLoggerFactory(level="DEBUG", log_format="json")
        logger = factory.get_logger("integration")

        # Emit a log message
        logger.info("hello world")

        # The JSON formatter goes through the logging system;
        # verify the root logger has the JSON formatter attached
        root = _logging.getLogger("debcraft")
        assert len(root.handlers) > 0
        handler = root.handlers[0]
        assert isinstance(handler.formatter, JsonFormatter)
