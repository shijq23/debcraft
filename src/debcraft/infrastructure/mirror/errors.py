"""Mirror-specific error hierarchy.

Defines all exception classes for the repository mirroring infrastructure.
All mirror errors derive from MirrorError, which extends M1's PlatformError.
The original cause is preserved via __cause__ for debugging.
"""

from __future__ import annotations

from debcraft.domain.mirror.errors import ReleaseParseError
from debcraft.platform.kernel.errors import PlatformError

__all__ = [
    "ChecksumMismatchError",
    "DiskSpaceError",
    "DownloadError",
    "HttpClientError",
    "HttpRateLimitError",
    "HttpServerError",
    "MirrorConfigurationError",
    "MirrorError",
    "NetworkError",
    "RateLimitTimeoutError",
    "ReleaseParseError",
    "SizeMismatchError",
]


class MirrorError(PlatformError):
    """Base exception for all mirror-specific errors.

    All mirror exceptions inherit from this class, allowing callers
    to catch any mirror error with a single except clause.
    """

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        """Initialize MirrorError.

        Args:
            message: Human-readable description of the error.
            cause: The underlying exception that triggered this error, if any.
        """
        super().__init__(message)
        self.cause = cause
        self.__cause__ = cause


class MirrorConfigurationError(MirrorError):
    """Raised when mirrors.toml content is invalid.

    Named MirrorConfigurationError to avoid conflict with the platform's
    ConfigurationError in debcraft.platform.kernel.errors.
    """

    def __init__(
        self,
        message: str,
        line_number: int | None = None,
        cause: Exception | None = None,
    ) -> None:
        """Initialize MirrorConfigurationError.

        Args:
            message: Description of the configuration error.
            line_number: TOML parse error location, if available.
            cause: The underlying exception that triggered this error, if any.
        """
        self.line_number = line_number
        if line_number is not None:
            message = f"{message} (line {line_number})"
        super().__init__(message, cause)


class DownloadError(MirrorError):
    """Base exception for download failures.

    Carries the URL that failed and the number of retries attempted.
    """

    def __init__(
        self,
        url: str,
        message: str,
        retry_count: int = 0,
        cause: Exception | None = None,
    ) -> None:
        """Initialize DownloadError.

        Args:
            url: The URL that failed to download.
            message: Description of the download failure.
            retry_count: Number of retry attempts made before this error.
            cause: The underlying exception that triggered this error, if any.
        """
        self.url = url
        self.retry_count = retry_count
        super().__init__(f"Download failed for '{url}': {message}", cause)


class HttpClientError(DownloadError):
    """Raised when an HTTP 4xx response is received.

    Client errors are non-retriable — the request itself is invalid.
    """

    def __init__(
        self,
        url: str,
        status_code: int,
        retry_count: int = 0,
        cause: Exception | None = None,
    ) -> None:
        """Initialize HttpClientError.

        Args:
            url: The URL that returned a 4xx status.
            status_code: The HTTP status code received (400-499).
            retry_count: Number of retry attempts made before this error.
            cause: The underlying exception that triggered this error, if any.
        """
        self.status_code = status_code
        super().__init__(
            url,
            f"HTTP {status_code} client error",
            retry_count,
            cause,
        )


class HttpServerError(DownloadError):
    """Raised when an HTTP 5xx response is received.

    Server errors are retriable — the server may recover on retry.
    """

    def __init__(
        self,
        url: str,
        status_code: int,
        retry_count: int = 0,
        cause: Exception | None = None,
    ) -> None:
        """Initialize HttpServerError.

        Args:
            url: The URL that returned a 5xx status.
            status_code: The HTTP status code received (500-599).
            retry_count: Number of retry attempts made before this error.
            cause: The underlying exception that triggered this error, if any.
        """
        self.status_code = status_code
        super().__init__(
            url,
            f"HTTP {status_code} server error",
            retry_count,
            cause,
        )


class NetworkError(DownloadError):
    """Raised when a network connection is refused or times out.

    Network errors are retriable — the connection may succeed on retry.
    """

    def __init__(
        self,
        url: str,
        message: str = "Connection refused or timed out",
        retry_count: int = 0,
        cause: Exception | None = None,
    ) -> None:
        """Initialize NetworkError.

        Args:
            url: The URL that could not be reached.
            message: Description of the network failure.
            retry_count: Number of retry attempts made before this error.
            cause: The underlying exception that triggered this error, if any.
        """
        super().__init__(url, message, retry_count, cause)


class ChecksumMismatchError(DownloadError):
    """Raised when SHA256 verification of a downloaded file fails.

    Indicates data corruption during transfer — retriable.
    """

    def __init__(
        self,
        url: str,
        expected: str,
        actual: str,
        retry_count: int = 0,
        cause: Exception | None = None,
    ) -> None:
        """Initialize ChecksumMismatchError.

        Args:
            url: The URL of the file with a checksum mismatch.
            expected: The expected SHA256 hex digest.
            actual: The actual SHA256 hex digest computed from the downloaded file.
            retry_count: Number of retry attempts made before this error.
            cause: The underlying exception that triggered this error, if any.
        """
        self.expected = expected
        self.actual = actual
        super().__init__(
            url,
            f"SHA256 mismatch: expected {expected}, got {actual}",
            retry_count,
            cause,
        )


class SizeMismatchError(DownloadError):
    """Raised when a downloaded file's size doesn't match repository metadata.

    Indicates incomplete or corrupted transfer — retriable.
    """

    def __init__(
        self,
        url: str,
        expected_bytes: int,
        actual_bytes: int,
        retry_count: int = 0,
        cause: Exception | None = None,
    ) -> None:
        """Initialize SizeMismatchError.

        Args:
            url: The URL of the file with a size mismatch.
            expected_bytes: The expected file size in bytes from metadata.
            actual_bytes: The actual file size in bytes on disk.
            retry_count: Number of retry attempts made before this error.
            cause: The underlying exception that triggered this error, if any.
        """
        self.expected_bytes = expected_bytes
        self.actual_bytes = actual_bytes
        super().__init__(
            url,
            f"Size mismatch: expected {expected_bytes} bytes, got {actual_bytes} bytes",
            retry_count,
            cause,
        )


class HttpRateLimitError(DownloadError):
    """Raised when an HTTP 429 response indicates rate limiting.

    Rate-limit errors are retriable with extended backoff.
    """

    def __init__(
        self,
        url: str,
        status_code: int = 429,
        retry_count: int = 0,
        cause: Exception | None = None,
    ) -> None:
        """Initialize HttpRateLimitError.

        Args:
            url: The URL that returned a rate-limit response.
            status_code: The HTTP status code received (typically 429).
            retry_count: Number of retry attempts made before this error.
            cause: The underlying exception that triggered this error, if any.
        """
        self.status_code = status_code
        super().__init__(url, f"HTTP {status_code} rate limit", retry_count, cause)


class RateLimitTimeoutError(MirrorError):
    """Raised when a token cannot be acquired within the timeout period."""

    def __init__(self, timeout: float, cause: Exception | None = None) -> None:
        """Initialize RateLimitTimeoutError.

        Args:
            timeout: The timeout duration in seconds that was exceeded.
            cause: The underlying exception that triggered this error, if any.
        """
        self.timeout = timeout
        super().__init__(
            f"Rate limiter token not acquired within {timeout}s timeout",
            cause,
        )


class DiskSpaceError(MirrorError):
    """Raised when insufficient disk space prevents downloads.

    Non-retriable — requires manual intervention to free space.
    """

    def __init__(
        self,
        required_bytes: int,
        available_bytes: int,
        cause: Exception | None = None,
    ) -> None:
        """Initialize DiskSpaceError.

        Args:
            required_bytes: The estimated space needed in bytes.
            available_bytes: The currently available space in bytes.
            cause: The underlying exception that triggered this error, if any.
        """
        self.required_bytes = required_bytes
        self.available_bytes = available_bytes
        super().__init__(
            f"Insufficient disk space: need {required_bytes} bytes, only {available_bytes} bytes available",
            cause,
        )
