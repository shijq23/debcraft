"""Unit tests for SchemaValidator.

Tests cover:
- Valid documents produce empty error list
- Invalid documents produce error messages in required format
- Malformed JSON returns parse error with line/column
- SchemaUnavailableError raised for missing/corrupt schemas
- Value truncation for long values
- JSON pointer format (RFC 6901)
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from debcraft.domain.sbom.errors import SchemaUnavailableError
from debcraft.domain.sbom.validator import SchemaValidator, _path_to_json_pointer, _truncate_value
from debcraft.domain.sbom.values import OutputFormat

pytestmark = [pytest.mark.unit]


class TestValidDocuments:
    """Valid documents return empty error list."""

    def test_valid_cyclonedx_minimal(self) -> None:
        """A minimal valid CycloneDX document produces no errors."""
        doc = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "version": 1,
            "components": [],
        }
        validator = SchemaValidator()
        errors = validator.validate(json.dumps(doc), OutputFormat.CYCLONEDX)
        assert errors == []

    def test_valid_document_returns_empty_list(self) -> None:
        """Empty error list signals valid document (AC 7.1)."""
        doc = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "version": 1,
            "components": [],
        }
        validator = SchemaValidator()
        result = validator.validate(json.dumps(doc), OutputFormat.CYCLONEDX)
        assert isinstance(result, list)
        assert len(result) == 0


class TestInvalidDocuments:
    """Invalid documents produce error messages in required format."""

    def test_missing_required_fields_cyclonedx(self) -> None:
        """Missing required fields produce errors."""
        validator = SchemaValidator()
        errors = validator.validate("{}", OutputFormat.CYCLONEDX)
        assert len(errors) > 0

    def test_missing_required_fields_spdx23(self) -> None:
        """Missing required fields for SPDX 2.3 produce errors."""
        validator = SchemaValidator()
        errors = validator.validate("{}", OutputFormat.SPDX_2_3)
        assert len(errors) > 0

    def test_missing_required_fields_spdx30(self) -> None:
        """Missing required fields for SPDX 3.0 produce errors."""
        validator = SchemaValidator()
        errors = validator.validate("{}", OutputFormat.SPDX_3_0)
        assert len(errors) > 0

    def test_wrong_type_produces_error(self) -> None:
        """Wrong type in a field produces an error with the field path."""
        doc = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "version": 1,
            "components": [{"type": "library", "name": 123}],
        }
        validator = SchemaValidator()
        errors = validator.validate(json.dumps(doc), OutputFormat.CYCLONEDX)
        # Should have error for name being integer instead of string
        name_errors = [e for e in errors if "/components/0/name" in e]
        assert len(name_errors) > 0


class TestErrorMessageFormat:
    """Error messages follow the format: '<json_pointer>: <constraint> (got: <value>)'."""

    def test_root_level_error_has_empty_pointer(self) -> None:
        """Root-level errors use empty string as JSON pointer (AC 7.6)."""
        validator = SchemaValidator()
        errors = validator.validate("{}", OutputFormat.CYCLONEDX)
        # Root errors should have empty pointer (start with ": ")
        root_errors = [e for e in errors if e.startswith(": ")]
        assert len(root_errors) > 0

    def test_nested_error_has_json_pointer(self) -> None:
        """Nested errors have RFC 6901 JSON pointer (AC 7.6)."""
        doc = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "version": 1,
            "components": [{"type": "library", "name": 123}],
        }
        validator = SchemaValidator()
        errors = validator.validate(json.dumps(doc), OutputFormat.CYCLONEDX)
        # Find errors with JSON pointers
        nested = [e for e in errors if e.startswith("/")]
        assert len(nested) > 0

    def test_error_contains_constraint_description(self) -> None:
        """Each error contains a constraint description."""
        validator = SchemaValidator()
        errors = validator.validate("{}", OutputFormat.CYCLONEDX)
        for error in errors:
            # After the pointer and ": " separator, before "(got: "
            parts = error.split(": ", 1)
            assert len(parts) == 2
            rest = parts[1]
            assert "(got: " in rest

    def test_error_contains_got_value(self) -> None:
        """Each error ends with (got: <value>)."""
        validator = SchemaValidator()
        errors = validator.validate("{}", OutputFormat.CYCLONEDX)
        for error in errors:
            assert "(got: " in error
            assert error.endswith(")")


class TestMalformedJson:
    """Malformed JSON returns parse error with line/column (AC 7.8)."""

    def test_malformed_json_single_error(self) -> None:
        """Malformed JSON produces exactly one error message."""
        validator = SchemaValidator()
        errors = validator.validate("{bad json}", OutputFormat.SPDX_2_3)
        assert len(errors) == 1

    def test_malformed_json_includes_line(self) -> None:
        """Parse error includes line number."""
        validator = SchemaValidator()
        errors = validator.validate("{bad json}", OutputFormat.SPDX_2_3)
        assert "line" in errors[0].lower()

    def test_malformed_json_includes_column(self) -> None:
        """Parse error includes column number."""
        validator = SchemaValidator()
        errors = validator.validate("{bad json}", OutputFormat.SPDX_2_3)
        assert "column" in errors[0].lower()

    def test_empty_string_is_malformed(self) -> None:
        """Empty string is treated as malformed JSON."""
        validator = SchemaValidator()
        errors = validator.validate("", OutputFormat.CYCLONEDX)
        assert len(errors) == 1
        assert "line" in errors[0].lower()

    def test_multiline_malformed_reports_correct_line(self) -> None:
        """Multi-line malformed JSON reports the correct line number."""
        validator = SchemaValidator()
        # Error is on line 3
        bad_json = '{\n  "key": "value",\n  "bad": undefined\n}'
        errors = validator.validate(bad_json, OutputFormat.CYCLONEDX)
        assert len(errors) == 1
        assert "line 3" in errors[0]


class TestSchemaUnavailableError:
    """SchemaUnavailableError raised for missing/corrupt schemas (AC 7.7)."""

    def test_missing_schema_raises_error(self) -> None:
        """Missing schema file raises SchemaUnavailableError."""
        validator = SchemaValidator()
        with (
            patch(
                "debcraft.domain.sbom.validator.load_schema",
                side_effect=KeyError("test"),
            ),
            pytest.raises(SchemaUnavailableError) as exc_info,
        ):
            validator.validate("{}", OutputFormat.CYCLONEDX)
        assert exc_info.value.schema_name == "cyclonedx"

    def test_corrupt_schema_raises_error(self) -> None:
        """Corrupt (unparseable) schema file raises SchemaUnavailableError."""
        validator = SchemaValidator()
        with (
            patch(
                "debcraft.domain.sbom.validator.load_schema",
                side_effect=json.JSONDecodeError("corrupt", "", 0),
            ),
            pytest.raises(SchemaUnavailableError) as exc_info,
        ):
            validator.validate("{}", OutputFormat.CYCLONEDX)
        assert exc_info.value.schema_name == "cyclonedx"
        assert "corrupt" in exc_info.value.reason

    def test_io_error_raises_schema_unavailable(self) -> None:
        """OSError (file read failure) raises SchemaUnavailableError."""
        validator = SchemaValidator()
        with (
            patch(
                "debcraft.domain.sbom.validator.load_schema",
                side_effect=OSError("permission denied"),
            ),
            pytest.raises(SchemaUnavailableError) as exc_info,
        ):
            validator.validate("{}", OutputFormat.CYCLONEDX)
        assert "permission denied" in exc_info.value.reason


class TestValueTruncation:
    """Values longer than 200 characters are truncated (AC 7.6)."""

    def test_short_value_not_truncated(self) -> None:
        """Values under 200 chars are not truncated."""
        result = _truncate_value("short")
        assert "..." not in result

    def test_long_value_truncated(self) -> None:
        """Values over 200 chars are truncated with '...' suffix."""
        long_val = "x" * 300
        result = _truncate_value(long_val)
        assert result.endswith("...")
        assert len(result) == 203  # 200 + "..."

    def test_exactly_200_chars_not_truncated(self) -> None:
        """Value of exactly 200 chars (in repr form) is not truncated."""
        # repr('x' * 198) = "'xxx...xxx'" which is 200 chars
        val = "x" * 198
        result = _truncate_value(val)
        assert len(result) == 200
        assert "..." not in result

    def test_truncation_in_validation_error(self) -> None:
        """Long values in validation errors are truncated."""
        long_val = "a" * 300
        doc = {"bomFormat": long_val, "specVersion": "1.5", "version": 1}
        validator = SchemaValidator()
        errors = validator.validate(json.dumps(doc), OutputFormat.CYCLONEDX)
        # Find errors that reference the long value
        for error in errors:
            if "(got: " in error:
                got_part = error.split("(got: ", 1)[1].rstrip(")")
                assert len(got_part) <= 203  # 200 + "..."


class TestJsonPointerConversion:
    """JSON pointer conversion follows RFC 6901."""

    def test_empty_path_is_empty_string(self) -> None:
        """Empty path produces empty string (document root)."""
        assert _path_to_json_pointer([]) == ""

    def test_single_element(self) -> None:
        """Single path element."""
        assert _path_to_json_pointer(["foo"]) == "/foo"

    def test_nested_path(self) -> None:
        """Nested path with multiple elements."""
        assert _path_to_json_pointer(["a", "b", "c"]) == "/a/b/c"

    def test_integer_index(self) -> None:
        """Integer array index is converted to string."""
        assert _path_to_json_pointer(["items", 0, "name"]) == "/items/0/name"

    def test_tilde_escaped(self) -> None:
        """Tilde is escaped as ~0 per RFC 6901."""
        assert _path_to_json_pointer(["a~b"]) == "/a~0b"

    def test_slash_escaped(self) -> None:
        """Forward slash is escaped as ~1 per RFC 6901."""
        assert _path_to_json_pointer(["a/b"]) == "/a~1b"

    def test_tilde_before_slash_escaping(self) -> None:
        """Tilde is escaped before slash (order matters for ~1 in source)."""
        # "~/" should become "~0~1" not "~10"
        assert _path_to_json_pointer(["~/"]) == "/~0~1"


class TestCaching:
    """Validator caches schema and validator instances for performance."""

    def test_second_call_uses_cache(self) -> None:
        """Second validate call for same format uses cached validator."""
        validator = SchemaValidator()
        # First call loads and caches
        validator.validate("{}", OutputFormat.CYCLONEDX)
        assert OutputFormat.CYCLONEDX in validator._validator_cache

        # Second call should use cache (no load_schema call)
        with patch("debcraft.domain.sbom.validator.load_schema") as mock_load:
            validator.validate("{}", OutputFormat.CYCLONEDX)
            mock_load.assert_not_called()

    def test_different_formats_cached_separately(self) -> None:
        """Different formats get separate cache entries."""
        validator = SchemaValidator()
        validator.validate("{}", OutputFormat.CYCLONEDX)
        validator.validate("{}", OutputFormat.SPDX_2_3)
        assert OutputFormat.CYCLONEDX in validator._validator_cache
        assert OutputFormat.SPDX_2_3 in validator._validator_cache
