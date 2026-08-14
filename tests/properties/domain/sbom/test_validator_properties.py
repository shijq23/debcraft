"""Property-based tests for Schema Validator error message format.

# Feature: sbom-writers, Property 10: Schema validation error message format

**Validates: Requirements 7.6**

Property 10: Schema validation error message format.
For any JSON string that fails validation against any supported schema
(SPDX 3.0, SPDX 2.3, or CycloneDX 1.5), each error message returned by the
SchemaValidator SHALL contain the JSON path of the failing element (RFC 6901
JSON Pointer), the constraint that was violated, and the actual value that
failed (truncated to 200 characters if longer).
"""

from __future__ import annotations

import json
import re

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from debcraft.domain.sbom.validator import SchemaValidator
from debcraft.domain.sbom.values import OutputFormat

# ---------------------------------------------------------------------------
# Strategies for generating invalid JSON documents
# ---------------------------------------------------------------------------

# Strategy: random dicts that are clearly not valid SBOM documents
_random_keys = st.text(
    alphabet=st.characters(categories=("L", "N")),
    min_size=1,
    max_size=20,
)

_random_values = st.one_of(
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.booleans(),
    st.text(min_size=0, max_size=50),
    st.none(),
    st.lists(st.integers(), max_size=3),
)

random_invalid_dicts: st.SearchStrategy[dict] = st.dictionaries(
    keys=_random_keys,
    values=_random_values,
    min_size=1,
    max_size=5,
)

# Strategy: documents with wrong types for known fields
_wrong_type_docs = st.one_of(
    # SPDX 2.3-like doc with wrong types
    st.fixed_dictionaries(
        {
            "spdxVersion": st.integers(),  # should be string
            "dataLicense": st.booleans(),  # should be string
            "SPDXID": st.lists(st.integers(), max_size=2),  # should be string
        }
    ),
    # CycloneDX-like doc with wrong types
    st.fixed_dictionaries(
        {
            "bomFormat": st.integers(),  # should be string "CycloneDX"
            "specVersion": st.booleans(),  # should be string
            "version": st.text(min_size=1, max_size=5),  # should be integer
        }
    ),
    # Generic with nested wrong types
    st.fixed_dictionaries(
        {
            "components": st.text(min_size=1, max_size=10),  # should be array
        }
    ),
)

# Strategy: documents with missing required fields (empty objects or partial)
_missing_fields_docs = st.one_of(
    st.just({}),  # completely empty
    st.just({"spdxVersion": "SPDX-2.3"}),  # missing most required fields
    st.just({"bomFormat": "CycloneDX"}),  # missing specVersion and version
)

# All invalid document strategies combined
invalid_json_docs = st.one_of(
    random_invalid_dicts,
    _wrong_type_docs,
    _missing_fields_docs,
)

# Strategy for supported output formats
output_formats = st.sampled_from(
    [
        OutputFormat.SPDX_3_0,
        OutputFormat.SPDX_2_3,
        OutputFormat.CYCLONEDX,
    ]
)

# Strategy for generating long values that should be truncated (>200 chars)
long_values = st.text(
    alphabet=st.characters(categories=("L", "N")),
    min_size=201,
    max_size=500,
)

# Strategy for malformed JSON strings (not valid JSON syntax)
malformed_json_strings = st.one_of(
    st.just("{invalid json"),
    st.just("{'single': 'quotes'}"),
    st.just("{missing: closing"),
    st.just("[1, 2, 3,]"),  # trailing comma
    st.just(""),  # empty string
    st.just('{"key": undefined}'),
    st.text(min_size=1, max_size=50).filter(lambda s: not _is_valid_json(s)),
)


def _is_valid_json(s: str) -> bool:
    """Check if a string is valid JSON."""
    try:
        json.loads(s)
        return True
    except (json.JSONDecodeError, ValueError):
        return False


# Regex pattern for the expected error format:
# "<json_pointer>: <constraint> (got: <truncated_value>)"
# JSON pointer starts with "/" or is empty string for root
_ERROR_FORMAT_PATTERN = re.compile(r"^(/[^:]*|): .+ \(got: .+\)$", re.DOTALL)


# ---------------------------------------------------------------------------
# Property 10: Schema validation error message format
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.spdx
class TestProperty10SchemaValidationErrorMessageFormat:
    """Property 10: Schema validation error message format.

    For any JSON string that fails validation against any supported schema,
    each error message SHALL contain the JSON path (RFC 6901 JSON Pointer),
    the constraint that was violated, and the actual value that failed
    (truncated to 200 characters if longer).
    """

    @settings(max_examples=100, deadline=None)
    @given(doc=invalid_json_docs, fmt=output_formats)
    def test_error_messages_contain_json_pointer(self, doc: dict, fmt: OutputFormat) -> None:
        """Each error message contains a JSON pointer (starts with '/' or is empty for root)."""
        json_string = json.dumps(doc)
        validator = SchemaValidator()
        errors = validator.validate(json_string, fmt)

        # These documents should produce at least one error
        assume(len(errors) > 0)

        for error in errors:
            # Error should contain a JSON pointer - either "/" prefix or empty for root
            # The format is: "<json_pointer>: <constraint> (got: <value>)"
            assert ": " in error, (
                f"Error message must contain ': ' separator between JSON pointer and constraint. Got: {error!r}"
            )
            json_pointer = error.split(": ", 1)[0]
            assert json_pointer == "" or json_pointer.startswith("/"), (
                f"JSON pointer must be empty (root) or start with '/'. "
                f"Got pointer: {json_pointer!r} in error: {error!r}"
            )

    @settings(max_examples=100, deadline=None)
    @given(doc=invalid_json_docs, fmt=output_formats)
    def test_error_messages_contain_constraint_description(self, doc: dict, fmt: OutputFormat) -> None:
        """Each error message contains a constraint description."""
        json_string = json.dumps(doc)
        validator = SchemaValidator()
        errors = validator.validate(json_string, fmt)

        assume(len(errors) > 0)

        for error in errors:
            # Split on first ": " to get the constraint + value part
            parts = error.split(": ", 1)
            assert len(parts) == 2, (
                f"Error message must have format '<pointer>: <constraint> (got: <value>)'. Got: {error!r}"
            )
            constraint_and_value = parts[1]
            # The constraint part is everything before "(got: ...)"
            assert "(got: " in constraint_and_value, (
                f"Error message must contain '(got: ' to indicate actual value. Got: {error!r}"
            )
            constraint = constraint_and_value.split(" (got: ", 1)[0]
            assert len(constraint) > 0, f"Constraint description must be non-empty. Got: {error!r}"

    @settings(max_examples=100, deadline=None)
    @given(doc=invalid_json_docs, fmt=output_formats)
    def test_error_messages_contain_actual_value(self, doc: dict, fmt: OutputFormat) -> None:
        """Each error message contains the actual value indication (got: ...)."""
        json_string = json.dumps(doc)
        validator = SchemaValidator()
        errors = validator.validate(json_string, fmt)

        assume(len(errors) > 0)

        for error in errors:
            assert "(got: " in error and error.endswith(")"), (
                f"Error message must end with '(got: <value>)' pattern. Got: {error!r}"
            )

    @settings(max_examples=100, deadline=None)
    @given(doc=invalid_json_docs, fmt=output_formats)
    def test_error_messages_match_full_format_pattern(self, doc: dict, fmt: OutputFormat) -> None:
        """Each error message matches the format: '<json_pointer>: <constraint> (got: <value>)'."""
        json_string = json.dumps(doc)
        validator = SchemaValidator()
        errors = validator.validate(json_string, fmt)

        assume(len(errors) > 0)

        for error in errors:
            assert _ERROR_FORMAT_PATTERN.match(error), (
                f"Error message must match pattern '<json_pointer>: <constraint> (got: <value>)'. Got: {error!r}"
            )

    @settings(max_examples=100, deadline=None)
    @given(fmt=output_formats)
    def test_truncation_of_long_values(self, fmt: OutputFormat) -> None:
        """Values longer than 200 characters are truncated in error messages."""
        # Create a document with a very long string value in a field that will
        # fail schema validation
        long_value = "x" * 300
        doc = {"invalid_field_name_that_wont_match_schema": long_value}
        json_string = json.dumps(doc)

        validator = SchemaValidator()
        errors = validator.validate(json_string, fmt)

        # The document should produce errors since it's invalid
        assume(len(errors) > 0)

        for error in errors:
            # Extract the "(got: ...)" portion
            if "(got: " in error:
                got_part = error.split("(got: ", 1)[1]
                # Remove the trailing ")" from the error format
                if got_part.endswith(")"):
                    got_part = got_part[:-1]
                # The value representation should not contain the full 300-char string.
                # The truncation limit is 200 chars for the repr, potentially + "..."
                # So the got_part should be at most ~203 chars (200 + "...")
                assert len(got_part) <= 210, (
                    f"Value in error message should be truncated. Got {len(got_part)} chars: {got_part[:50]}..."
                )

    @settings(max_examples=100, deadline=None)
    @given(
        key=st.text(
            alphabet=st.characters(categories=("L", "N")),
            min_size=1,
            max_size=10,
        ),
        long_val=long_values,
        fmt=output_formats,
    )
    def test_truncation_with_generated_long_values(self, key: str, long_val: str, fmt: OutputFormat) -> None:
        """Generated long values (>200 chars) are truncated in error messages."""
        doc = {key: long_val}
        json_string = json.dumps(doc)

        validator = SchemaValidator()
        errors = validator.validate(json_string, fmt)

        assume(len(errors) > 0)

        for error in errors:
            if "(got: " in error:
                got_part = error.split("(got: ", 1)[1]
                if got_part.endswith(")"):
                    got_part = got_part[:-1]
                # Truncation produces at most 200 chars + "..." suffix = 203 chars
                # Allow small margin for repr formatting (quotes etc)
                assert len(got_part) <= 210, (
                    f"Value in error message should be truncated to ~200 chars. "
                    f"Got {len(got_part)} chars in error: {error[:100]}..."
                )

    @settings(max_examples=100, deadline=None)
    @given(malformed=malformed_json_strings)
    def test_malformed_json_returns_parse_error(self, malformed: str) -> None:
        """Malformed JSON (not valid JSON syntax) returns a parse error with line/column info."""
        # Verify the string is actually not valid JSON
        assume(not _is_valid_json(malformed))

        validator = SchemaValidator()
        # Use any format — the malformed JSON should be caught before schema validation
        errors = validator.validate(malformed, OutputFormat.SPDX_2_3)

        assert len(errors) == 1, (
            f"Malformed JSON should produce exactly 1 parse error. Got {len(errors)} errors: {errors}"
        )

        error = errors[0]
        # Parse error should mention line/column
        assert "line" in error.lower() or "column" in error.lower(), (
            f"Parse error should include line/column information. Got: {error!r}"
        )

    @settings(max_examples=100, deadline=None)
    @given(fmt=output_formats)
    def test_valid_empty_errors_for_valid_documents_not_produced_for_invalid(self, fmt: OutputFormat) -> None:
        """Invalid documents (empty dict) always produce at least one error."""
        # An empty dict {} is invalid for all supported schemas
        json_string = json.dumps({})
        validator = SchemaValidator()
        errors = validator.validate(json_string, fmt)

        assert len(errors) > 0, f"Empty document {{}} should fail validation against {fmt.value} schema"
