"""Domain-level errors for the mirror bounded context.

Defines exception classes that are raised by domain logic (e.g., parsing)
and are independent of infrastructure concerns.
"""

from __future__ import annotations

from debcraft.platform.kernel.errors import PlatformError


class ReleaseParseError(PlatformError):
    """Raised when a Release file has malformed content.

    Indicates that the Release file could not be parsed due to missing
    SHA256Sums section, encoding errors, or other format issues.
    """

    def __init__(
        self,
        url: str,
        message: str,
        cause: Exception | None = None,
    ) -> None:
        """Initialize ReleaseParseError.

        Args:
            url: The URL of the Release file that could not be parsed.
            message: Description of the parse failure.
            cause: The underlying exception that triggered this error, if any.
        """
        self.url = url
        self.cause = cause
        self.__cause__ = cause
        super().__init__(f"Failed to parse Release file at '{url}': {message}")
