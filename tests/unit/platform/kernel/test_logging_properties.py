"""Property-based tests for KernelLoggerFactory and formatters (Properties 18-19).

**Validates: Requirements 5.1, 5.3, 5.4, 5.5**
"""

from __future__ import annotations

import json
import logging as _logging
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.platform.kernel.logging import HumanFormatter, JsonFormatter

# --- Strategies ---

_LOG_LEVELS = st.sampled_from([_logging.DEBUG, _logging.INFO, _logging.WARNING, _logging.ERROR])

_LEVEL_NAMES = {
    _logging.DEBUG: "DEBUG",
    _logging.INFO: "INFO",
    _logging.WARNING: "WARNING",
    _logging.ERROR: "ERROR",
}

# Component names: non-empty printable strings without whitespace (as they appear in log output)
_COMPONENT_NAMES = st.from_regex(r"[a-zA-Z][a-zA-Z0-9._]{0,49}", fullmatch=True)

# Messages: non-empty printable text without newlines
_MESSAGES = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S", "Z"), blacklist_characters="\n\r"),
    min_size=1,
    max_size=100,
)


def _make_log_record(
    component: str,
    level: int,
    message: str,
    correlation_id: str = "",
) -> _logging.LogRecord:
    """Create a LogRecord simulating what KernelLogger produces."""
    record = _logging.LogRecord(
        name=f"debcraft.{component}",
        level=level,
        pathname="",
        lineno=0,
        msg=message,
        args=None,
        exc_info=None,
    )
    record.correlation_id = correlation_id  # type: ignore[attr-defined]
    record.extra_data = {}  # type: ignore[attr-defined]
    return record


@pytest.mark.unit
class TestProperty18HumanLogFormatStructure:
    """Property 18: Human log format structure.

    For any log entry produced in human-readable mode, the formatted output
    SHALL contain — in order — a timestamp, the log level, the component name,
    and the message text. When a correlation_id is active, it SHALL also appear
    in the output.

    **Validates: Requirements 5.1, 5.3, 5.4**
    """

    @given(
        component=_COMPONENT_NAMES,
        level=_LOG_LEVELS,
        message=_MESSAGES,
    )
    def test_human_format_contains_fields_in_order(
        self,
        component: str,
        level: int,
        message: str,
    ) -> None:
        """Output contains timestamp, level, component, message in order.

        **Validates: Requirements 5.1, 5.3**
        """
        formatter = HumanFormatter()
        record = _make_log_record(component, level, message)
        output = formatter.format(record)

        level_name = _LEVEL_NAMES[level]
        full_component = f"debcraft.{component}"

        # Verify all parts are present
        assert level_name in output, f"Level '{level_name}' not found in output: {output}"
        assert full_component in output, f"Component '{full_component}' not found in output: {output}"
        assert message in output, f"Message '{message}' not found in output: {output}"

        # Verify ordering: timestamp < level < component < message
        # The format is: "TIMESTAMP LEVEL COMPONENT MESSAGE [extras]"
        # Use the known positions of the unique identifiers (level_name and full_component)
        level_pos = output.index(level_name)
        component_pos = output.index(full_component)
        # Find the message after the component (message text may also appear in timestamp/component)
        message_pos = output.index(message, component_pos + len(full_component))

        assert level_pos > 0, "Timestamp should precede level"
        assert level_pos < component_pos, f"Level at {level_pos} should precede component at {component_pos}"
        assert component_pos < message_pos, f"Component at {component_pos} should precede message at {message_pos}"

    @given(
        component=_COMPONENT_NAMES,
        level=_LOG_LEVELS,
        message=_MESSAGES,
        correlation_id=st.uuids(),
    )
    def test_human_format_includes_correlation_id_when_active(
        self,
        component: str,
        level: int,
        message: str,
        correlation_id: UUID,
    ) -> None:
        """Correlation_id appears in human format output when active.

        **Validates: Requirements 5.4**
        """
        formatter = HumanFormatter()
        record = _make_log_record(component, level, message, correlation_id=str(correlation_id))
        output = formatter.format(record)

        assert str(correlation_id) in output, f"Correlation ID '{correlation_id}' not found in output: {output}"
        assert "correlation_id=" in output, f"'correlation_id=' label not found in output: {output}"

    @given(
        component=_COMPONENT_NAMES,
        level=_LOG_LEVELS,
        message=_MESSAGES,
    )
    def test_human_format_omits_correlation_id_when_not_active(
        self,
        component: str,
        level: int,
        message: str,
    ) -> None:
        """Correlation_id is not present in output when not active.

        **Validates: Requirements 5.4**
        """
        formatter = HumanFormatter()
        record = _make_log_record(component, level, message, correlation_id="")
        output = formatter.format(record)

        assert "correlation_id=" not in output, f"'correlation_id=' should not appear when not active: {output}"

    @given(
        component=_COMPONENT_NAMES,
        level=_LOG_LEVELS,
        message=_MESSAGES,
    )
    def test_human_format_timestamp_is_valid_iso_prefix(
        self,
        component: str,
        level: int,
        message: str,
    ) -> None:
        """Timestamp at start of human format is valid ISO-like datetime.

        **Validates: Requirements 5.1**
        """
        formatter = HumanFormatter()
        record = _make_log_record(component, level, message)
        output = formatter.format(record)

        # Timestamp format is YYYY-MM-DDTHH:MM:SS.mmm
        # First field before the space should be a timestamp
        timestamp_str = output.split(" ")[0]
        assert "T" in timestamp_str, f"Timestamp should contain 'T' separator: {timestamp_str}"
        assert len(timestamp_str) == 23, f"Timestamp should be 23 chars (YYYY-MM-DDTHH:MM:SS.mmm): '{timestamp_str}'"


@pytest.mark.unit
class TestProperty19JsonLogFormatStructure:
    """Property 19: JSON log format structure.

    For any log entry produced in JSON mode, the output SHALL be valid JSON
    containing the keys `timestamp`, `level`, `component`, `message`, and
    `correlation_id`.

    **Validates: Requirements 5.5**
    """

    @given(
        component=_COMPONENT_NAMES,
        level=_LOG_LEVELS,
        message=_MESSAGES,
    )
    def test_json_format_produces_valid_json(
        self,
        component: str,
        level: int,
        message: str,
    ) -> None:
        """JSON format output is parseable as valid JSON.

        **Validates: Requirements 5.5**
        """
        formatter = JsonFormatter()
        record = _make_log_record(component, level, message)
        output = formatter.format(record)

        # Must not raise
        parsed = json.loads(output)
        assert isinstance(parsed, dict), f"Parsed JSON should be a dict, got {type(parsed)}"

    @given(
        component=_COMPONENT_NAMES,
        level=_LOG_LEVELS,
        message=_MESSAGES,
    )
    def test_json_format_contains_required_keys(
        self,
        component: str,
        level: int,
        message: str,
    ) -> None:
        """JSON output contains all required keys: timestamp, level, component, message, correlation_id.

        **Validates: Requirements 5.5**
        """
        formatter = JsonFormatter()
        record = _make_log_record(component, level, message)
        output = formatter.format(record)
        parsed = json.loads(output)

        required_keys = {"timestamp", "level", "component", "message", "correlation_id"}
        missing = required_keys - set(parsed.keys())
        assert not missing, f"Missing required keys: {missing}. Got keys: {set(parsed.keys())}"

    @given(
        component=_COMPONENT_NAMES,
        level=_LOG_LEVELS,
        message=_MESSAGES,
    )
    def test_json_format_values_match_input(
        self,
        component: str,
        level: int,
        message: str,
    ) -> None:
        """JSON output field values match the input parameters.

        **Validates: Requirements 5.5**
        """
        formatter = JsonFormatter()
        record = _make_log_record(component, level, message)
        output = formatter.format(record)
        parsed = json.loads(output)

        level_name = _LEVEL_NAMES[level]
        full_component = f"debcraft.{component}"

        assert parsed["level"] == level_name, f"Expected level '{level_name}', got '{parsed['level']}'"
        assert parsed["component"] == full_component, (
            f"Expected component '{full_component}', got '{parsed['component']}'"
        )
        assert parsed["message"] == message, f"Expected message '{message}', got '{parsed['message']}'"

    @given(
        component=_COMPONENT_NAMES,
        level=_LOG_LEVELS,
        message=_MESSAGES,
        correlation_id=st.uuids(),
    )
    def test_json_format_includes_correlation_id_when_active(
        self,
        component: str,
        level: int,
        message: str,
        correlation_id: UUID,
    ) -> None:
        """JSON output includes non-null correlation_id when active.

        **Validates: Requirements 5.5**
        """
        formatter = JsonFormatter()
        record = _make_log_record(component, level, message, correlation_id=str(correlation_id))
        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["correlation_id"] == str(correlation_id), (
            f"Expected correlation_id '{correlation_id}', got '{parsed['correlation_id']}'"
        )

    @given(
        component=_COMPONENT_NAMES,
        level=_LOG_LEVELS,
        message=_MESSAGES,
    )
    def test_json_format_correlation_id_null_when_not_active(
        self,
        component: str,
        level: int,
        message: str,
    ) -> None:
        """JSON output has null correlation_id when not active.

        **Validates: Requirements 5.5**
        """
        formatter = JsonFormatter()
        record = _make_log_record(component, level, message, correlation_id="")
        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["correlation_id"] is None, (
            f"Expected null correlation_id when not active, got '{parsed['correlation_id']}'"
        )
