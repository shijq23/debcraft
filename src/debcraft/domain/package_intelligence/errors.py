"""Domain-level errors for the package intelligence bounded context.

Defines exception classes raised during .deb parsing, DEP-5 license parsing,
SPDX expression handling, PURL generation, and dependency parsing.
"""

from __future__ import annotations

from debcraft.platform.kernel.errors import PlatformError


class DebParseError(PlatformError):
    """Raised when a .deb archive cannot be parsed."""

    def __init__(
        self,
        file_path: str,
        reason: str,
        cause: Exception | None = None,
    ) -> None:
        """Initialize DebParseError.

        Args:
            file_path: Path to the .deb file that failed to parse.
            reason: Description of the parse failure.
            cause: The underlying exception that triggered this error, if any.
        """
        self.file_path = file_path
        self.reason = reason
        self.cause = cause
        self.__cause__ = cause
        super().__init__(f"Failed to parse .deb archive '{file_path}': {reason}")


class DEP5ParseError(PlatformError):
    """Raised when a DEP-5 document cannot be parsed."""

    def __init__(
        self,
        message: str,
        paragraph_index: int | None = None,
    ) -> None:
        """Initialize DEP5ParseError.

        Args:
            message: Description of the parse failure.
            paragraph_index: Zero-based index of the paragraph where parsing
                failed, if applicable.
        """
        self.paragraph_index = paragraph_index
        if paragraph_index is not None:
            full_message = f"Failed to parse DEP-5 document at paragraph {paragraph_index}: {message}"
        else:
            full_message = f"Failed to parse DEP-5 document: {message}"
        super().__init__(full_message)


class SPDXTokenizeError(PlatformError):
    """Raised when SPDX expression tokenization fails."""

    def __init__(self, message: str, offset: int) -> None:
        """Initialize SPDXTokenizeError.

        Args:
            message: Description of the tokenization failure.
            offset: Character offset in the expression where tokenization failed.
        """
        self.offset = offset
        super().__init__(f"SPDX tokenization failed at offset {offset}: {message}")


class SPDXParseError(PlatformError):
    """Raised when SPDX expression parsing fails."""

    def __init__(self, message: str, token_position: int) -> None:
        """Initialize SPDXParseError.

        Args:
            message: Description of the parse failure.
            token_position: Position of the token where parsing failed.
        """
        self.token_position = token_position
        super().__init__(f"SPDX parse failed at token position {token_position}: {message}")


class PURLGenerationError(PlatformError):
    """Raised when PURL generation fails due to missing fields."""

    def __init__(self, missing_field: str) -> None:
        """Initialize PURLGenerationError.

        Args:
            missing_field: Name of the required field that is missing.
        """
        self.missing_field = missing_field
        super().__init__(f"Cannot generate PURL: missing required field '{missing_field}'")


class DependencyParseError(PlatformError):
    """Raised when a dependency field cannot be parsed."""

    def __init__(
        self,
        package_name: str,
        field_name: str,
        reason: str,
    ) -> None:
        """Initialize DependencyParseError.

        Args:
            package_name: Name of the package whose dependency field failed.
            field_name: Name of the dependency field (e.g. 'Depends', 'Pre-Depends').
            reason: Description of the parse failure.
        """
        self.package_name = package_name
        self.field_name = field_name
        self.reason = reason
        super().__init__(f"Failed to parse '{field_name}' for package '{package_name}': {reason}")
