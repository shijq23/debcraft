"""Domain-level errors for the indexer bounded context.

Defines exception classes that are raised by domain logic (e.g., parsing,
indexing orchestration) and are independent of infrastructure concerns.
"""

from __future__ import annotations

from debcraft.platform.kernel.errors import PlatformError


class ReleaseParseError(PlatformError):
    """Raised when a Release file cannot be parsed for repository identity.

    Indicates that the Release file is missing both Suite and Codename fields,
    making it impossible to determine the repository identity.
    """

    def __init__(
        self,
        message: str,
        cause: Exception | None = None,
    ) -> None:
        """Initialize ReleaseParseError.

        Args:
            message: Description of the parse failure.
            cause: The underlying exception that triggered this error, if any.
        """
        self.cause = cause
        self.__cause__ = cause
        super().__init__(f"Failed to parse Release file: {message}")


class IndexingError(PlatformError):
    """Raised when a general indexing failure occurs.

    Covers failures such as file read errors, database write errors,
    or other issues encountered during the indexing workflow.
    """

    def __init__(
        self,
        message: str,
        cause: Exception | None = None,
    ) -> None:
        """Initialize IndexingError.

        Args:
            message: Description of the indexing failure.
            cause: The underlying exception that triggered this error, if any.
        """
        self.cause = cause
        self.__cause__ = cause
        super().__init__(f"Indexing failed: {message}")
