"""Scanner for QCOW2 virtual machine disk images.

Scans QCOW2 disk images for installed Debian packages using guestfs
(constructor-injected) to inspect the image, mount the OS root read-only,
and extract the dpkg status file. Falls back to filesystem analysis when
dpkg metadata is unavailable. Operates without root privileges or mount
operations.
"""

from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING

from debcraft.domain.scanner.dpkg_parser import parse_dpkg_status
from debcraft.domain.scanner.values import ScanningStrategy, ScanResult
from debcraft.infrastructure.scanners._mixin import ScannerMixin

if TYPE_CHECKING:
    from debcraft.domain.scanner.ports import (
        ContentsIndexPort,
        GuestfsInspector,
        PackageLookupPort,
    )
    from debcraft.domain.scanner.values import Artifact
    from debcraft.platform.contracts.workflow import WorkflowContext


QCOW2_MAGIC = b"QFI\xfb"


class QCOW2Scanner(ScannerMixin):
    """Scans QCOW2 disk images for installed Debian packages.

    Uses guestfs (constructor-injected) to inspect the image,
    mount the OS root read-only, and extract dpkg status.
    Falls back to FilesystemAnalyzer if dpkg metadata unavailable.
    """

    def __init__(
        self,
        guestfs_inspector: GuestfsInspector | None,
        contents_port: ContentsIndexPort,
        package_port: PackageLookupPort,
    ) -> None:
        """Initialize QCOW2Scanner with required dependencies.

        Args:
            guestfs_inspector: Optional guestfs abstraction for disk inspection.
                If None, scan returns immediately with a diagnostic about
                the missing dependency.
            contents_port: Port for Contents index lookups (filesystem fallback).
            package_port: Port for package metadata lookups (filesystem fallback).
        """
        self._guestfs = guestfs_inspector
        self._contents_port = contents_port
        self._package_port = package_port

    async def scan(self, artifact: Artifact, context: WorkflowContext) -> ScanResult:
        """Scan a QCOW2 disk image for installed Debian packages.

        Steps:
        1. Check guestfs availability (return diagnostic if None)
        2. Validate QCOW2 magic bytes at offset 0
        3. Open image via guestfs, check cancellation
        4. Inspect OS roots, check cancellation
        5. Mount first root read-only
        6. Read /var/lib/dpkg/status, check cancellation
        7. Parse or fall back to filesystem analysis
        8. Report progress throughout

        Args:
            artifact: The artifact descriptor with type QCOW2.
            context: Workflow context providing cancellation, progress, logging.

        Returns:
            ScanResult with identified packages and metadata.
        """
        start_time = time.perf_counter()
        path = artifact.path
        diagnostics: list[str] = []

        # Step 1: Check guestfs availability (Req 8.10)
        if self._guestfs is None:
            duration = time.perf_counter() - start_time
            diagnostics.append(
                "guestfs library is not available: cannot inspect QCOW2 images. "
                "Install python3-guestfs or libguestfs Python bindings."
            )
            self._report_progress(context, 100.0, "Scan complete: guestfs not available")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=path,
            )

        # Step 2: Validate QCOW2 magic bytes
        early_result = self._validate_qcow2_header(context, path, start_time, diagnostics)
        if early_result is not None:
            return early_result

        # Step 3: Check cancellation and open image via guestfs
        early_result = self._open_guestfs_image(context, path, start_time, diagnostics)
        if early_result is not None:
            return early_result

        try:
            # Steps 4-5: Inspect OS roots and mount
            early_result = self._inspect_and_mount_root(context, path, start_time, diagnostics)
            if early_result is not None:
                return early_result

            # Steps 6-7: Read dpkg status and parse or fall back
            return await self._read_and_parse_dpkg(artifact, context, path, start_time, diagnostics)
        finally:
            # Always close guestfs to release resources
            with contextlib.suppress(Exception):
                self._guestfs.close()

    def _validate_qcow2_header(
        self,
        context: WorkflowContext,
        path: str,
        start_time: float,
        diagnostics: list[str],
    ) -> ScanResult | None:
        """Validate QCOW2 magic bytes at offset 0.

        Returns a ScanResult if validation fails, or None to continue.
        """
        self._report_progress(context, 5.0, "Validating QCOW2 magic bytes")
        try:
            with open(path, "rb") as f:
                header = f.read(4)
        except (OSError, PermissionError) as e:
            diagnostics.append(f"Cannot read QCOW2 image at '{path}': {e}")
            self._report_progress(context, 100.0, "Scan complete: file not readable")
            return self._build_empty_result(
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                start_time=start_time,
                artifact_path=path,
            )

        if len(header) < 4 or header != QCOW2_MAGIC:
            diagnostics.append(
                f"Invalid QCOW2 image at '{path}': missing QFI\\xfb magic bytes at offset 0 (got {header!r})"
            )
            self._report_progress(context, 100.0, "Scan complete: invalid QCOW2 format")
            return self._build_empty_result(
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                start_time=start_time,
                artifact_path=path,
            )

        return None

    def _open_guestfs_image(
        self,
        context: WorkflowContext,
        path: str,
        start_time: float,
        diagnostics: list[str],
    ) -> ScanResult | None:
        """Check cancellation and open image via guestfs.

        Returns a ScanResult if cancelled or open fails, or None to continue.
        """
        if context.cancellation_token.is_cancelled:
            self._report_progress(context, 100.0, "Scan cancelled")
            return self._build_cancellation_result(
                step="before opening image",
                start_time=start_time,
                strategy=ScanningStrategy.DPKG_METADATA.value,
                artifact_path=path,
                diagnostics=diagnostics,
            )

        self._report_progress(context, 15.0, "Opening QCOW2 image via guestfs")
        try:
            self._guestfs.open_image(path, readonly=True)  # type: ignore[union-attr]
        except (OSError, RuntimeError) as e:
            duration = time.perf_counter() - start_time
            diagnostics.append(f"Failed to open QCOW2 image at '{path}' via guestfs: {e}")
            self._report_progress(context, 100.0, "Scan complete: image open failed")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=path,
            )

        return None

    def _inspect_and_mount_root(
        self,
        context: WorkflowContext,
        path: str,
        start_time: float,
        diagnostics: list[str],
    ) -> ScanResult | None:
        """Inspect OS roots and mount the first root read-only.

        Returns a ScanResult if cancelled, inspection fails, or mount fails.
        Returns None to continue processing.
        """
        # Check cancellation before OS inspection
        if context.cancellation_token.is_cancelled:
            self._report_progress(context, 100.0, "Scan cancelled")
            return self._build_cancellation_result(
                step="before OS inspection",
                start_time=start_time,
                strategy=ScanningStrategy.DPKG_METADATA.value,
                artifact_path=path,
                diagnostics=diagnostics,
            )

        self._report_progress(context, 30.0, "Inspecting OS roots")
        try:
            roots = self._guestfs.inspect_os()  # type: ignore[union-attr]
        except (OSError, RuntimeError) as e:
            duration = time.perf_counter() - start_time
            diagnostics.append(f"Failed to inspect OS roots in QCOW2 image: {e}")
            self._report_progress(context, 100.0, "Scan complete: OS inspection failed")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=path,
            )

        # No inspectable OS root
        if not roots:
            duration = time.perf_counter() - start_time
            diagnostics.append(
                "No inspectable operating system root found in QCOW2 image: "
                "unrecognized partition layout or unsupported filesystem"
            )
            self._report_progress(context, 100.0, "Scan complete: no OS root found")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=path,
            )

        # Check cancellation before mounting
        if context.cancellation_token.is_cancelled:
            self._report_progress(context, 100.0, "Scan cancelled")
            return self._build_cancellation_result(
                step="before mounting filesystem",
                start_time=start_time,
                strategy=ScanningStrategy.DPKG_METADATA.value,
                artifact_path=path,
                diagnostics=diagnostics,
            )

        self._report_progress(context, 50.0, "Mounting root filesystem read-only")
        first_root = roots[0]
        try:
            self._guestfs.mount_readonly(first_root, "/")  # type: ignore[union-attr]
        except (OSError, RuntimeError) as e:
            duration = time.perf_counter() - start_time
            diagnostics.append(f"Failed to mount root '{first_root}' read-only: {e}")
            self._report_progress(context, 100.0, "Scan complete: mount failed")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=path,
            )

        return None

    async def _read_and_parse_dpkg(
        self,
        artifact: Artifact,
        context: WorkflowContext,
        path: str,
        start_time: float,
        diagnostics: list[str],
    ) -> ScanResult:
        """Read /var/lib/dpkg/status and parse, or fall back to filesystem analysis.

        Returns a ScanResult with packages from dpkg parsing or filesystem analysis.
        """
        # Check cancellation before reading dpkg status
        if context.cancellation_token.is_cancelled:
            self._report_progress(context, 100.0, "Scan cancelled")
            return self._build_cancellation_result(
                step="before reading dpkg status",
                start_time=start_time,
                strategy=ScanningStrategy.DPKG_METADATA.value,
                artifact_path=path,
                diagnostics=diagnostics,
            )

        self._report_progress(context, 65.0, "Reading /var/lib/dpkg/status")
        dpkg_content: str | None = None
        try:
            raw_bytes = self._guestfs.read_file("/var/lib/dpkg/status")  # type: ignore[union-attr]
            dpkg_content = raw_bytes.decode("utf-8", errors="replace")
        except (OSError, RuntimeError):
            # dpkg status not readable — will fall back
            dpkg_content = None

        if dpkg_content is not None:
            return self._parse_dpkg_packages(context, path, start_time, diagnostics, dpkg_content)

        # Fall back to filesystem analysis
        self._report_progress(context, 75.0, "dpkg status not found, falling back to filesystem analysis")
        return await self._fallback_filesystem_analysis(artifact, context, path, start_time, diagnostics)

    def _parse_dpkg_packages(
        self,
        context: WorkflowContext,
        path: str,
        start_time: float,
        diagnostics: list[str],
        dpkg_content: str,
    ) -> ScanResult:
        """Parse dpkg status content into packages with cancellation checks.

        Returns a ScanResult with the parsed packages.
        """
        self._report_progress(context, 75.0, "Parsing dpkg status file")
        parse_result = parse_dpkg_status(dpkg_content)
        diagnostics.extend(parse_result.diagnostics)

        # Check cancellation between package entries
        result = self._iterate_packages_with_cancellation(
            parse_result.packages,
            context,
            start_time,
            ScanningStrategy.DPKG_METADATA.value,
            artifact_path=path,
            diagnostics=diagnostics,
        )

        self._report_progress(
            context,
            100.0,
            f"Scan complete: identified {len(result.packages)} packages",
        )
        return result

    async def _fallback_filesystem_analysis(
        self,
        artifact: Artifact,
        context: WorkflowContext,
        path: str,
        start_time: float,
        diagnostics: list[str],
    ) -> ScanResult:
        """Fall back to filesystem analysis when dpkg metadata unavailable.

        Lists files from the mounted guestfs filesystem and queries the
        Contents index for file-to-package mappings.

        Args:
            artifact: The artifact descriptor.
            context: Workflow context for cancellation and progress.
            path: The artifact path.
            start_time: perf_counter value at scan start.
            diagnostics: Accumulated diagnostics list.

        Returns:
            ScanResult with filesystem_analysis strategy.
        """
        # Collect file paths from the guestfs mounted filesystem
        file_paths: list[str] = []
        with contextlib.suppress(Exception):
            entries = self._guestfs.ls("/")  # type: ignore[union-attr]
            for entry in entries:
                file_paths.append("/" + entry)

        # Run filesystem analysis with pre-cancellation check and cancellation iteration
        return await self._analyze_and_build_filesystem_result(
            file_paths=file_paths,
            context=context,
            contents_port=self._contents_port,
            package_port=self._package_port,
            artifact=artifact,
            start_time=start_time,
            artifact_path=path,
            diagnostics=diagnostics,
            use_cancellation_iteration=True,
            pre_cancellation_step="filesystem traversal",
        )
