"""Shared write-with-cancellation utility for SBOM writers.

Encapsulates the common pre-write cancellation check, disk write via
write_sbom_output, post-write cancellation check with file unlink, and
WriterResult construction sequence used by all SBOM writer implementations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from debcraft.domain.sbom.errors import WriterCancellationError
from debcraft.domain.sbom.values import OutputFormat, WriterResult
from debcraft.infrastructure.sbom_writers._output import write_sbom_output

if TYPE_CHECKING:
    from pathlib import Path

    from debcraft.platform.contracts.workflow import CancellationToken


async def write_with_cancellation(
    *,
    output_bytes: bytes,
    output_path: Path,
    cancellation_token: CancellationToken,
    output_format: OutputFormat,
    diagnostics: list[str],
) -> WriterResult:
    """Perform the standard write-with-cancellation sequence.

    Sequence:
    1. Pre-write cancellation check → raises WriterCancellationError
    2. Write to disk via write_sbom_output → returns (sha256, file_size)
    3. Post-write cancellation check → unlinks file, raises WriterCancellationError
    4. Construct and return WriterResult

    Args:
        output_bytes: The serialized SBOM content to write.
        output_path: Filesystem path where the output file will be written.
        cancellation_token: Cooperative cancellation signal to check.
        output_format: The SBOM output format for the WriterResult.
        diagnostics: Validation diagnostics to include in the result.

    Returns:
        WriterResult with output path, format, SHA-256 hash, file size,
        and diagnostics.

    Raises:
        WriterCancellationError: If cancellation is signalled before or after write.
        OutputPathError: If an OS error occurs during directory creation or file writing.
    """
    # 1. Pre-write cancellation check
    if cancellation_token.is_cancelled:
        raise WriterCancellationError(output_path)

    # 2. Write to disk
    sha256, file_size = write_sbom_output(output_bytes, output_path)

    # 3. Post-write cancellation check with file unlink
    if cancellation_token.is_cancelled:
        output_path.unlink(missing_ok=True)
        raise WriterCancellationError(output_path)

    # 4. Construct and return WriterResult
    return WriterResult(
        output_path=output_path,
        format=output_format,
        sha256=sha256,
        file_size=file_size,
        diagnostics=diagnostics,
    )
