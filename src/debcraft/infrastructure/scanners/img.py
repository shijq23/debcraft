"""Scanner for raw disk image files.

Scans raw disk images for installed Debian packages by using guestfs
to inspect partitions, mount filesystems read-only, and extract the
dpkg status file. Supports multi-partition images, using the first
partition (in table order) where /var/lib/dpkg/status is found.
Falls back to FilesystemAnalyzer when dpkg metadata is unavailable.
Operates without root privileges or mount operations.
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


class IMGScanner(ScannerMixin):
    """Scans raw disk images for installed Debian packages.

    Uses guestfs to inspect partitions, supports multi-partition images.
    Checks partitions in table order, uses first with dpkg status.
    Operates without root privileges by using libguestfs bindings.
    """

    def __init__(
        self,
        guestfs_inspector: GuestfsInspector | None,
        contents_port: ContentsIndexPort,
        package_port: PackageLookupPort,
    ) -> None:
        """Initialize IMGScanner with required dependencies.

        Args:
            guestfs_inspector: Guestfs abstraction for disk inspection,
                or None if guestfs is not available.
            contents_port: Port for Contents index lookups (filesystem fallback).
            package_port: Port for package metadata lookups (filesystem fallback).
        """
        self._guestfs = guestfs_inspector
        self._contents_port = contents_port
        self._package_port = package_port

    async def scan(self, artifact: Artifact, context: WorkflowContext) -> ScanResult:
        """Scan a raw disk image for installed Debian packages.

        Steps:
        1. Check guestfs availability (return diagnostic if None)
        2. Open image, enumerate partitions via inspect_os()
        3. For each partition (in table order):
           a. Check cancellation token
           b. Mount read-only, check for /var/lib/dpkg/status
           c. If found: parse and break
        4. If no dpkg status on any partition: fall back to FilesystemAnalyzer

        Args:
            artifact: The artifact descriptor with type IMG.
            context: Workflow context providing cancellation, progress, logging.

        Returns:
            ScanResult with identified packages and metadata.
        """
        start_time = time.perf_counter()
        image_path = artifact.path
        diagnostics: list[str] = []

        # Step 1: Check guestfs availability (Req 9.9)
        if self._guestfs is None:
            duration = time.perf_counter() - start_time
            diagnostics.append("guestfs library is not available: cannot inspect raw disk image without libguestfs")
            self._report_progress(context, 100.0, "Scan complete: guestfs unavailable")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=image_path,
            )

        # Step 2: Open image and enumerate partitions
        try:
            self._guestfs.open_image(image_path, readonly=True)
        except (OSError, RuntimeError) as exc:
            # Req 9.5: Path doesn't exist or not readable
            duration = time.perf_counter() - start_time
            diagnostics.append(f"Failed to open disk image at '{image_path}': {exc}")
            self._report_progress(context, 100.0, "Scan complete: image not accessible")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=image_path,
            )

        # At this point self._guestfs is guaranteed non-None (early return above)
        assert self._guestfs is not None  # noqa: S101
        try:
            return await self._inspect_partitions(artifact, context, image_path, start_time, diagnostics)
        finally:
            self._guestfs.close()

    async def _inspect_partitions(
        self,
        artifact: Artifact,
        context: WorkflowContext,
        image_path: str,
        start_time: float,
        diagnostics: list[str],
    ) -> ScanResult:
        """Enumerate partitions and search for dpkg status.

        Args:
            artifact: The artifact descriptor.
            context: Workflow context for cancellation and progress.
            image_path: Path to the raw disk image.
            start_time: perf_counter value at scan start.
            diagnostics: Accumulated diagnostics list.

        Returns:
            ScanResult with identified packages.
        """
        assert self._guestfs is not None  # noqa: S101
        # Check cancellation before partition enumeration (Req 9.7)
        if context.cancellation_token.is_cancelled:
            self._report_progress(context, 100.0, "Scan cancelled")
            return self._build_cancellation_result(
                step="before partition enumeration",
                start_time=start_time,
                strategy=ScanningStrategy.DPKG_METADATA.value,
                artifact_path=image_path,
                diagnostics=diagnostics,
            )

        # Enumerate partitions via inspect_os (Req 9.2)
        try:
            roots = self._guestfs.inspect_os()
        except (OSError, RuntimeError) as exc:
            # Req 9.6: Unrecognized partition table or filesystem
            duration = time.perf_counter() - start_time
            diagnostics.append(f"Failed to inspect disk image partitions: {exc}")
            self._report_progress(context, 100.0, "Scan complete: inspection failed")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=image_path,
            )

        if not roots:
            # Req 9.6: No partitions found
            duration = time.perf_counter() - start_time
            diagnostics.append("No OS partitions found in disk image: unrecognized partition table or filesystem")
            self._report_progress(context, 100.0, "Scan complete: no partitions found")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=image_path,
            )

        # Step 3: For each partition, mount and check for dpkg status (Req 9.2)
        dpkg_content: str | None = None
        for index, root_device in enumerate(roots):
            # Check cancellation between partitions (Req 9.7)
            if context.cancellation_token.is_cancelled:
                self._report_progress(context, 100.0, "Scan cancelled")
                return self._build_cancellation_result(
                    step=f"inspecting partitions ({index} of {len(roots)} inspected)",
                    start_time=start_time,
                    strategy=ScanningStrategy.DPKG_METADATA.value,
                    artifact_path=image_path,
                    diagnostics=diagnostics,
                )

            # Mount partition read-only (Req 9.8)
            try:
                self._guestfs.mount_readonly(root_device, "/")
            except (OSError, RuntimeError):
                # This partition's filesystem is unsupported, skip it
                continue

            # Check for /var/lib/dpkg/status
            try:
                content_bytes = self._guestfs.read_file("/var/lib/dpkg/status")
                dpkg_content = content_bytes.decode("utf-8", errors="replace")
                break  # Req 9.2: Use first partition with dpkg status
            except (OSError, RuntimeError):
                # dpkg status not found on this partition, continue
                continue

        # Step 4: Parse or fall back (Req 9.3, 9.4)
        if dpkg_content is not None:
            # Req 9.3: dpkg status found, parse it
            parse_result = parse_dpkg_status(dpkg_content)
            return self._build_dpkg_success_result(
                parse_result=parse_result,
                context=context,
                start_time=start_time,
                artifact_path=image_path,
                diagnostics=diagnostics,
            )

        # Req 9.4: No dpkg status on any partition, fall back to FilesystemAnalyzer
        return await self._fallback_filesystem_analysis(artifact, context, image_path, start_time, diagnostics)

    async def _fallback_filesystem_analysis(
        self,
        artifact: Artifact,
        context: WorkflowContext,
        image_path: str,
        start_time: float,
        diagnostics: list[str],
    ) -> ScanResult:
        """Fall back to filesystem analysis when dpkg metadata unavailable.

        Uses guestfs to list files from the mounted filesystem and
        queries the Contents index for file-to-package mappings.

        Args:
            artifact: The artifact descriptor.
            context: Workflow context for cancellation and progress.
            image_path: Path to the raw disk image.
            start_time: perf_counter value at scan start.
            diagnostics: Accumulated diagnostics list.

        Returns:
            ScanResult with filesystem_analysis strategy.
        """
        # Attempt to collect file paths from the image using guestfs
        file_paths: list[str] = []
        with contextlib.suppress(Exception):
            # Try to list files from the root filesystem
            file_paths = self._collect_file_paths("/")

        # Run filesystem analysis with pre-cancellation check and build result
        return await self._analyze_and_build_filesystem_result(
            file_paths=file_paths,
            artifact=artifact,
            contents_port=self._contents_port,
            package_port=self._package_port,
            context=context,
            artifact_path=image_path,
            start_time=start_time,
            diagnostics=diagnostics,
            use_cancellation_iteration=False,
            pre_cancellation_step="filesystem analysis",
        )

    def _collect_file_paths(self, directory: str) -> list[str]:
        """Recursively collect file paths from the mounted filesystem.

        Uses guestfs ls to enumerate directory contents. Only collects
        paths up to a reasonable limit to avoid excessive memory usage.

        Args:
            directory: The directory path to enumerate.

        Returns:
            List of absolute file paths found in the filesystem.
        """
        assert self._guestfs is not None  # noqa: S101
        paths: list[str] = []
        max_paths = 100_000

        dirs_to_visit = [directory]
        while dirs_to_visit and len(paths) < max_paths:
            current_dir = dirs_to_visit.pop(0)
            try:
                entries = self._guestfs.ls(current_dir)
            except (OSError, RuntimeError):
                continue

            for entry in entries:
                if len(paths) >= max_paths:
                    break
                full_path = f"{current_dir}/{entry}" if current_dir != "/" else f"/{entry}"
                paths.append(full_path)
                # Attempt to list as directory (if it fails, it's a file)
                dirs_to_visit.append(full_path)

        return paths
