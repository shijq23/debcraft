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
from debcraft.domain.scanner.filesystem_analyzer import analyze_filesystem
from debcraft.domain.scanner.values import ScanningStrategy, ScanResult

if TYPE_CHECKING:
    from debcraft.domain.scanner.ports import (
        ContentsIndexPort,
        GuestfsInspector,
        PackageLookupPort,
    )
    from debcraft.domain.scanner.values import Artifact
    from debcraft.platform.contracts.workflow import WorkflowContext


QCOW2_MAGIC = b"QFI\xfb"


class QCOW2Scanner:
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
            context.progress.report(100.0, "Scan complete: guestfs not available")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=path,
            )

        # Step 2: Validate QCOW2 magic bytes (Req 8.4, 8.5)
        context.progress.report(5.0, "Validating QCOW2 magic bytes")
        try:
            with open(path, "rb") as f:
                header = f.read(4)
        except (OSError, PermissionError) as e:
            duration = time.perf_counter() - start_time
            diagnostics.append(f"Cannot read QCOW2 image at '{path}': {e}")
            context.progress.report(100.0, "Scan complete: file not readable")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=path,
            )

        if len(header) < 4 or header != QCOW2_MAGIC:
            duration = time.perf_counter() - start_time
            diagnostics.append(
                f"Invalid QCOW2 image at '{path}': missing QFI\\xfb magic bytes at offset 0 (got {header!r})"
            )
            context.progress.report(100.0, "Scan complete: invalid QCOW2 format")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=path,
            )

        # Step 3: Check cancellation before opening image (Req 8.7)
        if context.cancellation_token.is_cancelled:
            duration = time.perf_counter() - start_time
            diagnostics.append("Scan cancelled before opening image")
            context.progress.report(100.0, "Scan cancelled")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=path,
            )

        # Open image via guestfs
        context.progress.report(15.0, "Opening QCOW2 image via guestfs")
        try:
            self._guestfs.open_image(path, readonly=True)
        except Exception as e:
            duration = time.perf_counter() - start_time
            diagnostics.append(f"Failed to open QCOW2 image at '{path}' via guestfs: {e}")
            context.progress.report(100.0, "Scan complete: image open failed")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=path,
            )

        try:
            # Step 4: Inspect OS roots, check cancellation (Req 8.6, 8.7)
            if context.cancellation_token.is_cancelled:
                duration = time.perf_counter() - start_time
                diagnostics.append("Scan cancelled before OS inspection")
                context.progress.report(100.0, "Scan cancelled")
                return ScanResult(
                    packages=[],
                    strategy=ScanningStrategy.DPKG_METADATA.value,
                    diagnostics=diagnostics,
                    duration_seconds=duration,
                    artifact_path=path,
                )

            context.progress.report(30.0, "Inspecting OS roots")
            try:
                roots = self._guestfs.inspect_os()
            except Exception as e:
                duration = time.perf_counter() - start_time
                diagnostics.append(f"Failed to inspect OS roots in QCOW2 image: {e}")
                context.progress.report(100.0, "Scan complete: OS inspection failed")
                return ScanResult(
                    packages=[],
                    strategy=ScanningStrategy.DPKG_METADATA.value,
                    diagnostics=diagnostics,
                    duration_seconds=duration,
                    artifact_path=path,
                )

            # Req 8.6: No inspectable OS root
            if not roots:
                duration = time.perf_counter() - start_time
                diagnostics.append(
                    "No inspectable operating system root found in QCOW2 image: "
                    "unrecognized partition layout or unsupported filesystem"
                )
                context.progress.report(100.0, "Scan complete: no OS root found")
                return ScanResult(
                    packages=[],
                    strategy=ScanningStrategy.DPKG_METADATA.value,
                    diagnostics=diagnostics,
                    duration_seconds=duration,
                    artifact_path=path,
                )

            # Step 5: Mount first root read-only
            if context.cancellation_token.is_cancelled:
                duration = time.perf_counter() - start_time
                diagnostics.append("Scan cancelled before mounting filesystem")
                context.progress.report(100.0, "Scan cancelled")
                return ScanResult(
                    packages=[],
                    strategy=ScanningStrategy.DPKG_METADATA.value,
                    diagnostics=diagnostics,
                    duration_seconds=duration,
                    artifact_path=path,
                )

            context.progress.report(50.0, "Mounting root filesystem read-only")
            first_root = roots[0]
            try:
                self._guestfs.mount_readonly(first_root, "/")
            except Exception as e:
                duration = time.perf_counter() - start_time
                diagnostics.append(f"Failed to mount root '{first_root}' read-only: {e}")
                context.progress.report(100.0, "Scan complete: mount failed")
                return ScanResult(
                    packages=[],
                    strategy=ScanningStrategy.DPKG_METADATA.value,
                    diagnostics=diagnostics,
                    duration_seconds=duration,
                    artifact_path=path,
                )

            # Step 6: Read /var/lib/dpkg/status, check cancellation (Req 8.1)
            if context.cancellation_token.is_cancelled:
                duration = time.perf_counter() - start_time
                diagnostics.append("Scan cancelled before reading dpkg status")
                context.progress.report(100.0, "Scan cancelled")
                return ScanResult(
                    packages=[],
                    strategy=ScanningStrategy.DPKG_METADATA.value,
                    diagnostics=diagnostics,
                    duration_seconds=duration,
                    artifact_path=path,
                )

            context.progress.report(65.0, "Reading /var/lib/dpkg/status")
            dpkg_content: str | None = None
            try:
                raw_bytes = self._guestfs.read_file("/var/lib/dpkg/status")
                dpkg_content = raw_bytes.decode("utf-8", errors="replace")
            except Exception:
                # dpkg status not readable — will fall back
                dpkg_content = None

            # Step 7: Parse or fall back (Req 8.2, 8.3)
            if dpkg_content is not None:
                # Req 8.2: Parse dpkg status, strategy "dpkg_metadata"
                context.progress.report(75.0, "Parsing dpkg status file")
                parse_result = parse_dpkg_status(dpkg_content)
                diagnostics.extend(parse_result.diagnostics)

                # Check cancellation between package entries
                packages = []
                for pkg in parse_result.packages:
                    if context.cancellation_token.is_cancelled:
                        duration = time.perf_counter() - start_time
                        diagnostics.append(
                            f"Scan cancelled after processing {len(packages)} of {len(parse_result.packages)} packages"
                        )
                        context.progress.report(100.0, "Scan cancelled")
                        return ScanResult(
                            packages=packages,
                            strategy=ScanningStrategy.DPKG_METADATA.value,
                            diagnostics=diagnostics,
                            duration_seconds=duration,
                            artifact_path=path,
                        )
                    packages.append(pkg)

                duration = time.perf_counter() - start_time
                context.progress.report(
                    100.0,
                    f"Scan complete: identified {len(packages)} packages",
                )
                return ScanResult(
                    packages=packages,
                    strategy=ScanningStrategy.DPKG_METADATA.value,
                    diagnostics=diagnostics,
                    duration_seconds=duration,
                    artifact_path=path,
                )
            else:
                # Req 8.3: Fall back to filesystem analysis
                context.progress.report(75.0, "dpkg status not found, falling back to filesystem analysis")
                return await self._fallback_filesystem_analysis(artifact, context, path, start_time, diagnostics)
        finally:
            # Always close guestfs to release resources
            with contextlib.suppress(Exception):
                self._guestfs.close()

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

        if context.cancellation_token.is_cancelled:
            duration = time.perf_counter() - start_time
            diagnostics.append("Scan cancelled during filesystem traversal")
            context.progress.report(100.0, "Scan cancelled")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.FILESYSTEM_ANALYSIS.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=path,
            )

        # Get snapshot_id from artifact options
        snapshot_id = int(artifact.options.get("snapshot_id", "0"))

        # Run filesystem analysis
        result = await analyze_filesystem(
            file_paths=file_paths,
            contents_port=self._contents_port,
            package_port=self._package_port,
            snapshot_id=snapshot_id,
        )

        diagnostics.extend(result.diagnostics)

        # Check cancellation for the resulting packages
        packages = []
        for pkg in result.packages:
            if context.cancellation_token.is_cancelled:
                diagnostics.append(
                    f"Scan cancelled after processing {len(packages)} of {len(result.packages)} packages"
                )
                break
            packages.append(pkg)

        duration = time.perf_counter() - start_time
        context.progress.report(
            100.0,
            f"Scan complete: identified {len(packages)} packages via filesystem analysis",
        )
        return ScanResult(
            packages=packages,
            strategy=ScanningStrategy.FILESYSTEM_ANALYSIS.value,
            diagnostics=diagnostics,
            duration_seconds=duration,
            artifact_path=path,
        )
