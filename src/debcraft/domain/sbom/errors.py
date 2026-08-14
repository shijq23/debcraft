"""Domain-specific errors for the SBOM subsystem.

Defines the exception hierarchy for SBOM model validation, writer operations,
schema validation, and format registry errors. All errors derive from
PlatformError via the SBOMError base class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from debcraft.platform.kernel.errors import PlatformError

if TYPE_CHECKING:
    from pathlib import Path


class SBOMError(PlatformError):
    """Base error for all SBOM domain errors."""


class ModelValidationError(SBOMError):
    """Raised when SBOM value object construction fails validation.

    This indicates that a field value violated a stated constraint during
    construction of a frozen dataclass value object.
    """

    def __init__(self, field_name: str, constraint: str, value: object = None) -> None:
        """Initialize ModelValidationError.

        Args:
            field_name: The name of the field that failed validation.
            constraint: Description of the constraint that was violated.
            value: The invalid value that was provided (optional).
        """
        self.field_name = field_name
        self.constraint = constraint
        self.value = value
        msg = f"Validation failed for field '{field_name}': {constraint}"
        if value is not None:
            msg += f" (got {value!r})"
        super().__init__(msg)


class WriterError(SBOMError):
    """Base for writer-specific errors."""


class OutputPathError(WriterError):
    """Raised when the output path is not writable.

    This error is raised when permission is denied, the filesystem is full,
    or the path is otherwise not writable. No partial output file is left
    on disk when this error is raised.
    """

    def __init__(self, path: Path, reason: str) -> None:
        """Initialize OutputPathError.

        Args:
            path: The output path that could not be written to.
            reason: Description of why the path is not writable.
        """
        self.path = path
        self.reason = reason
        super().__init__(f"Cannot write to output path '{path}': {reason}")


class WriterCancellationError(WriterError):
    """Raised when a write operation is cancelled.

    When cancellation occurs, any partial output file at the output path
    is removed before this error is raised.
    """

    def __init__(self, output_path: Path | None = None) -> None:
        """Initialize WriterCancellationError.

        Args:
            output_path: The output path where writing was cancelled (optional).
        """
        self.output_path = output_path
        msg = "Write operation was cancelled"
        if output_path is not None:
            msg += f" (output path: '{output_path}')"
        super().__init__(msg)


class DocumentValidationError(WriterError):
    """Raised when document is None or has no root package.

    This error is raised before any file writing occurs when the document
    parameter fails pre-write validation.
    """

    def __init__(self, reason: str) -> None:
        """Initialize DocumentValidationError.

        Args:
            reason: Description of why document validation failed
                (e.g. "document is None", "document has no root package").
        """
        self.reason = reason
        super().__init__(f"Document validation failed: {reason}")


class UnsupportedFormatError(SBOMError):
    """Raised when no writer is registered for a requested format.

    Identifies the unsupported format by name and lists all currently
    registered format values.
    """

    def __init__(self, format_name: str, registered: list[str]) -> None:
        """Initialize UnsupportedFormatError.

        Args:
            format_name: The format that was requested but has no registered writer.
            registered: List of format names that are currently registered.
        """
        self.format_name = format_name
        self.registered = registered
        super().__init__(f"No writer registered for format '{format_name}'. Registered formats: {registered}")


class SchemaUnavailableError(SBOMError):
    """Raised when a schema file is missing or unparseable.

    Indicates which schema is unavailable and provides the reason for
    the failure (e.g. file not found, invalid JSON content).
    """

    def __init__(self, schema_name: str, reason: str) -> None:
        """Initialize SchemaUnavailableError.

        Args:
            schema_name: Identifier of the schema that is unavailable
                (e.g. "spdx_3_0", "cyclonedx").
            reason: Description of why the schema could not be loaded.
        """
        self.schema_name = schema_name
        self.reason = reason
        super().__init__(f"Schema '{schema_name}' is unavailable: {reason}")
