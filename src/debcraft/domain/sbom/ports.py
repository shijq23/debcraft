"""Port interfaces (Protocols) for the SBOM writer domain.

These define the contracts that infrastructure writer adapters must satisfy,
allowing the domain layer to remain decoupled from concrete implementations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from debcraft.domain.sbom.values import SBOMDocument, WriterResult
    from debcraft.platform.contracts.workflow import WorkflowContext


class SBOMWriter(Protocol):
    """Protocol that all SBOM writer implementations must satisfy.

    Writers are stateless plugins that serialize an SBOMDocument into
    a specific output format (SPDX 3.0, SPDX 2.3, CycloneDX).
    Structural subtyping — no inheritance required.
    """

    async def write(self, document: SBOMDocument, output_path: Path, context: WorkflowContext) -> WriterResult:
        """Serialize an SBOM document to a file in the writer's format.

        Args:
            document: The internal SBOM document to serialize.
            output_path: Filesystem path where the output file will be written.
            context: Workflow context providing cancellation, progress, logging.

        Returns:
            WriterResult with output path, format, SHA-256 hash, file size,
            and any validation diagnostics.

        Raises:
            OutputPathError: If the output path is not writable.
            WriterCancellationError: If cancellation is requested during write.
            DocumentValidationError: If the document is None or has no root package.
        """
        ...


# Re-export WriterResult for convenience so consumers can import from ports.
def __getattr__(name: str) -> object:
    if name == "WriterResult":
        from debcraft.domain.sbom.values import WriterResult

        return WriterResult
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
