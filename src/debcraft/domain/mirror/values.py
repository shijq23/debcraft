"""Value objects for the mirror domain layer.

Immutable dataclasses representing files, sync decisions,
and download outcomes. These carry no behavior beyond
field access and are used throughout the mirror pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class FileEntry:
    """A file listed in Release or Packages metadata.

    Attributes:
        relative_path: Path relative to the repository root.
        sha256: Hex-encoded SHA256 digest of the file content.
        size_bytes: Declared file size in bytes.
    """

    relative_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class SyncDecision:
    """Result of comparing remote vs local file state.

    Attributes:
        file_entry: The remote file entry being evaluated.
        action: One of "download", "skip", or "verify".
        reason: Human-readable explanation for the decision.
    """

    file_entry: FileEntry
    action: Literal["download", "skip", "verify"]
    reason: str


@dataclass(frozen=True)
class DownloadResult:
    """Outcome of a single file download attempt.

    Attributes:
        url: The URL that was downloaded.
        success: Whether the download completed successfully.
        sha256_verified: Whether SHA256 verification passed.
        bytes_transferred: Number of bytes actually transferred.
        error: Error message if the download failed, None otherwise.
        retry_count: Number of retry attempts made before this result.
        status_code: HTTP status code from the final response, if available.
        response_headers: Selected HTTP response headers (ETag, Last-Modified).
    """

    url: str
    success: bool
    sha256_verified: bool
    bytes_transferred: int
    error: str | None = None
    retry_count: int = 0
    status_code: int | None = None
    response_headers: dict[str, str] | None = None
