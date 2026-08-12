"""Scanner for AWS AMI images (raw or QCOW2 format).

Scans AMI disk images by detecting the underlying format (QCOW2 or raw)
via magic bytes at offset 0, then delegating to the appropriate scanner.
Operates entirely on local image files without requiring AWS credentials
or network access.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from debcraft.domain.scanner.values import ScanningStrategy, ScanResult
from debcraft.infrastructure.scanners.qcow2 import QCOW2_MAGIC

if TYPE_CHECKING:
    from debcraft.domain.scanner.values import Artifact
    from debcraft.infrastructure.scanners.img import IMGScanner
    from debcraft.infrastructure.scanners.qcow2 import QCOW2Scanner
    from debcraft.platform.contracts.workflow import WorkflowContext


class AMIScanner:
    """Scans AMI disk images by detecting format and delegating.

    Detects QCOW2 vs raw format via magic bytes at offset 0, then
    delegates to QCOW2Scanner or IMGScanner. No AWS credentials required.
    """

    def __init__(
        self,
        qcow2_scanner: QCOW2Scanner,
        img_scanner: IMGScanner,
    ) -> None:
        """Initialize AMIScanner with delegate scanners.

        Args:
            qcow2_scanner: Scanner to delegate to for QCOW2 format images.
            img_scanner: Scanner to delegate to for raw format images.
        """
        self._qcow2_scanner = qcow2_scanner
        self._img_scanner = img_scanner

    async def scan(self, artifact: Artifact, context: WorkflowContext) -> ScanResult:
        """Scan an AMI image by detecting format and delegating.

        Steps:
        1. Check cancellation token
        2. Read first 4 bytes of the file
        3. If QCOW2 magic: delegate to QCOW2Scanner
        4. Otherwise: delegate to IMGScanner
        5. Propagate ScanResult from delegate unchanged

        Args:
            artifact: The artifact descriptor with type AMI.
            context: Workflow context providing cancellation, progress, logging.

        Returns:
            ScanResult from the delegated scanner, propagated unchanged.
        """
        start_time = time.perf_counter()
        path = artifact.path
        diagnostics: list[str] = []

        # Step 1: Check cancellation before format detection (Req 10.7)
        if context.cancellation_token.is_cancelled:
            duration = time.perf_counter() - start_time
            diagnostics.append("Scan cancelled before format detection")
            context.progress.report(100.0, "Scan cancelled")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=path,
            )

        # Step 2: Read first 4 bytes for format detection (Req 10.1, 10.5)
        context.progress.report(5.0, "Detecting AMI image format")
        try:
            with open(path, "rb") as f:
                header = f.read(4)
        except (OSError, PermissionError) as e:
            duration = time.perf_counter() - start_time
            diagnostics.append(f"Cannot read AMI image at '{path}': {e}")
            context.progress.report(100.0, "Scan complete: file not readable")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=path,
            )

        # Req 10.5: File too small for format detection
        if len(header) < 4:
            duration = time.perf_counter() - start_time
            diagnostics.append(
                f"AMI image at '{path}' is too small for format detection: expected at least 4 bytes, got {len(header)}"
            )
            context.progress.report(100.0, "Scan complete: file too small")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=path,
            )

        # Check cancellation before delegating (Req 10.7)
        if context.cancellation_token.is_cancelled:
            duration = time.perf_counter() - start_time
            diagnostics.append("Scan cancelled before delegating to sub-scanner")
            context.progress.report(100.0, "Scan cancelled")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=path,
            )

        # Step 3/4: Delegate based on magic bytes (Req 10.2, 10.3)
        if header == QCOW2_MAGIC:
            # QCOW2 format detected
            context.progress.report(10.0, "QCOW2 format detected, delegating")
            return await self._qcow2_scanner.scan(artifact, context)

        # Raw format (not QCOW2)
        context.progress.report(10.0, "Raw format detected, delegating to IMG scanner")
        return await self._img_scanner.scan(artifact, context)
