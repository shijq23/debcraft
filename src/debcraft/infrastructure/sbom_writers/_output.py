"""Shared SBOM output helper for writing serialized bytes to disk.

Encapsulates parent directory creation, file writing with partial-file
cleanup on failure, SHA-256 computation, and file size calculation.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from debcraft.domain.sbom.errors import OutputPathError

if TYPE_CHECKING:
    from pathlib import Path


def write_sbom_output(output_bytes: bytes, output_path: Path) -> tuple[str, int]:
    """Write SBOM bytes to disk with cleanup and hash computation.

    Creates parent directories, writes bytes atomically (with partial-file
    cleanup on error), and computes the SHA-256 digest of the written content.

    Args:
        output_bytes: The serialized SBOM content to write.
        output_path: Filesystem path where the output file will be written.

    Returns:
        A tuple of (sha256_hex, file_size) where sha256_hex is the lowercase
        hex-encoded SHA-256 digest and file_size is the byte length of the output.

    Raises:
        OutputPathError: If an OS error occurs during directory creation or
            file writing. Any partial file is removed before raising.
    """
    # Create parent directories
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputPathError(output_path, str(exc)) from exc

    # Write to disk
    try:
        output_path.write_bytes(output_bytes)
    except OSError as exc:
        # Clean up partial file if it exists
        if output_path.exists():
            output_path.unlink(missing_ok=True)
        raise OutputPathError(output_path, str(exc)) from exc

    # Compute SHA-256 and file size
    sha256 = hashlib.sha256(output_bytes).hexdigest()
    file_size = len(output_bytes)

    return sha256, file_size
