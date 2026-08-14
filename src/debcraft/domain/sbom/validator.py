"""Schema validation service for SBOM output formats.

Validates serialized SBOM JSON strings against bundled specification schemas
(SPDX 3.0, SPDX 2.3, CycloneDX 1.5). Schemas are loaded from package data
via importlib.resources, requiring no network access.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from jsonschema.validators import validator_for  # type: ignore[import-untyped]

from debcraft.domain.sbom.errors import SchemaUnavailableError
from debcraft.domain.sbom.schemas import load_schema

if TYPE_CHECKING:
    import jsonschema  # type: ignore[import-untyped]

    from debcraft.domain.sbom.values import OutputFormat

#: Maximum length for actual values in error messages.
_MAX_VALUE_LENGTH = 200


def _truncate_value(value: Any) -> str:  # noqa: ANN401
    """Truncate a value representation to at most _MAX_VALUE_LENGTH characters."""
    text = repr(value)
    if len(text) > _MAX_VALUE_LENGTH:
        return text[:_MAX_VALUE_LENGTH] + "..."
    return text


def _path_to_json_pointer(path: list[str | int]) -> str:
    """Convert a path list to an RFC 6901 JSON Pointer string.

    An empty path maps to the empty string (document root).
    Each path element is escaped per RFC 6901: '~' → '~0', '/' → '~1'.
    """
    if not path:
        return ""
    escaped = []
    for part in path:
        segment = str(part)
        segment = segment.replace("~", "~0").replace("/", "~1")
        escaped.append(segment)
    return "/" + "/".join(escaped)


def _format_error(error: jsonschema.ValidationError) -> str:
    """Format a jsonschema ValidationError into the required message format.

    Format: "<json_pointer>: <constraint> (got: <truncated_value>)"
    """
    pointer = _path_to_json_pointer(list(error.absolute_path))
    constraint = error.message
    value = _truncate_value(error.instance)
    return f"{pointer}: {constraint} (got: {value})"


class SchemaValidator:
    """Validates JSON strings against SBOM format schemas.

    Loads schemas from bundled package data via importlib.resources.
    Supports SPDX 3.0, SPDX 2.3, and CycloneDX 1.5 formats.
    """

    def __init__(self) -> None:
        """Initialize SchemaValidator with an empty schema cache."""
        self._schema_cache: dict[OutputFormat, dict[str, Any]] = {}
        self._validator_cache: dict[OutputFormat, Any] = {}

    def _get_validator(self, format: OutputFormat) -> Any:  # noqa: A002, ANN401
        """Get or create a cached validator instance for the given format.

        Raises:
            SchemaUnavailableError: If the schema file is missing or corrupt.
        """
        if format in self._validator_cache:
            return self._validator_cache[format]

        try:
            schema = load_schema(format)
        except KeyError:
            raise SchemaUnavailableError(
                schema_name=format.value,
                reason="no schema file is bundled for this format",
            ) from None
        except (json.JSONDecodeError, OSError) as exc:
            raise SchemaUnavailableError(
                schema_name=format.value,
                reason=str(exc),
            ) from exc

        validator_cls = validator_for(schema)
        validator_cls.check_schema(schema)
        validator = validator_cls(schema)

        self._schema_cache[format] = schema
        self._validator_cache[format] = validator
        return validator

    def validate(self, json_string: str, format: OutputFormat) -> list[str]:  # noqa: A002
        """Validate a JSON string against the specified format schema.

        Args:
            json_string: The JSON string to validate.
            format: The SBOM output format identifying which schema to use.

        Returns:
            An empty list if the document is valid, or a list of error messages.
            Each error message has the format:
            ``"<json_pointer>: <constraint> (got: <truncated_value>)"``

        Raises:
            SchemaUnavailableError: If the schema file is missing or corrupt.
        """
        # Parse JSON first — if malformed, return parse error with location
        try:
            document = json.loads(json_string)
        except json.JSONDecodeError as exc:
            return [f"JSON parse error at line {exc.lineno}, column {exc.colno}: {exc.msg}"]

        # Load/cache the validator (may raise SchemaUnavailableError)
        validator = self._get_validator(format)

        # Validate and collect errors
        errors: list[str] = []
        for error in validator.iter_errors(document):
            errors.append(_format_error(error))

        return errors
