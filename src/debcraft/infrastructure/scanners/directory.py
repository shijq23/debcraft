"""Scanner for local directory (rootfs) artifacts.

Scans local directories for installed Debian packages by looking for
the dpkg status file at <path>/var/lib/dpkg/status. Falls back to
filesystem analysis via the Contents index when dpkg metadata is
unavailable. Applies symlink containment to prevent path traversal.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from debcraft.domain.scanner.dpkg_parser import parse_dpkg_status
from debcraft.domain.scanner.values import ScanningStrategy, ScanResult
from debcraft.infrastructure.scanners._mixin import ScannerMixin

if TYPE_CHECKING:
    from debcraft.domain.scanner.ports import ContentsIndexPort, PackageLookupPort
    from debcraft.domain.scanner.values import Artifact
    from debcraft.platform.contracts.workflow import WorkflowContext


class DirectoryScanner(ScannerMixin):
    """Scans local directories for installed Debian packages.

    Looks for /var/lib/dpkg/status within the directory root.
    Falls back to FilesystemAnalyzer if dpkg metadata unavailable.
    Does not follow symlinks that escape the artifact root.
    """

    def __init__(
        self,
        contents_port: ContentsIndexPort,
        package_port: PackageLookupPort,
    ) -> None:
        """Initialize DirectoryScanner with required ports.

        Args:
            contents_port: Port for Contents index lookups (filesystem fallback).
            package_port: Port for package metadata lookups (filesystem fallback).
        """
        self._contents_port = contents_port
        self._package_port = package_port

    async def scan(self, artifact: Artifact, context: WorkflowContext) -> ScanResult:
        """Scan a directory artifact for installed Debian packages.

        Steps:
        1. Validate directory exists and is accessible
        2. Check for <path>/var/lib/dpkg/status
        3. If found and readable: parse with parse_dpkg_status
        4. If not found or unreadable: fall back to FilesystemAnalyzer
        5. Check cancellation between package entries
        6. Report progress on completion

        Args:
            artifact: The artifact descriptor with type DIRECTORY.
            context: Workflow context providing cancellation, progress, logging.

        Returns:
            ScanResult with identified packages and metadata.
        """
        start_time = time.perf_counter()
        root = artifact.path
        diagnostics: list[str] = []

        # Step 1: Validate directory exists and is accessible
        if not os.path.isdir(root):
            duration = time.perf_counter() - start_time
            reason = "path does not exist" if not os.path.exists(root) else "path is not a directory"
            diagnostics.append(f"Directory not accessible at '{root}': {reason}")
            self._report_progress(context, 100.0, "Scan complete: directory not accessible")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=root,
            )

        # Step 2: Check for dpkg status file
        dpkg_status_path = os.path.join(root, "var", "lib", "dpkg", "status")

        # Apply symlink containment check
        if not self._is_safe_path(root, dpkg_status_path):
            # Symlink escapes root — treat as not found, fall back
            return await self._fallback_filesystem_analysis(artifact, context, root, start_time, diagnostics)

        # Step 3: Try to read dpkg status
        if os.path.isfile(dpkg_status_path):
            try:
                with open(dpkg_status_path, encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except (OSError, PermissionError):
                # Step 4 (Req 4.8): If dpkg status exists but not readable,
                # fall back to FilesystemAnalyzer
                return await self._fallback_filesystem_analysis(artifact, context, root, start_time, diagnostics)

            # Parse dpkg status file
            parse_result = parse_dpkg_status(content)
            diagnostics.extend(parse_result.diagnostics)

            # Step 5: Check cancellation between package entries
            result = self._iterate_packages_with_cancellation(
                parse_result.packages,
                context,
                start_time,
                ScanningStrategy.DPKG_METADATA.value,
                artifact_path=root,
                diagnostics=diagnostics,
            )

            # Step 6: Report 100% progress
            self._report_progress(
                context,
                100.0,
                f"Scan complete: identified {len(result.packages)} packages",
            )
            return result

        # Step 4 (Req 4.3): dpkg status file not found — fall back
        return await self._fallback_filesystem_analysis(artifact, context, root, start_time, diagnostics)

    async def _fallback_filesystem_analysis(
        self,
        artifact: Artifact,
        context: WorkflowContext,
        root: str,
        start_time: float,
        diagnostics: list[str],
    ) -> ScanResult:
        """Fall back to filesystem analysis when dpkg metadata unavailable.

        Walks the directory tree (respecting symlink containment) and
        queries the Contents index for file-to-package mappings.

        Args:
            artifact: The artifact descriptor.
            context: Workflow context for cancellation and progress.
            root: The artifact root path.
            start_time: perf_counter value at scan start.
            diagnostics: Accumulated diagnostics list.

        Returns:
            ScanResult with filesystem_analysis strategy.
        """
        # Collect file paths from the directory tree
        file_paths: list[str] = []
        for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
            if context.cancellation_token.is_cancelled:
                break
            for filename in filenames:
                full_path = os.path.join(dirpath, filename)
                if not self._is_safe_path(root, full_path):
                    continue
                # Convert to relative path for Contents index lookup
                rel_path = os.path.relpath(full_path, root)
                # Normalize to forward-slash paths starting with /
                file_paths.append("/" + rel_path.replace(os.sep, "/"))

        # Run filesystem analysis with pre-cancellation check and cancellation iteration
        return await self._analyze_and_build_filesystem_result(
            file_paths=file_paths,
            contents_port=self._contents_port,
            package_port=self._package_port,
            artifact=artifact,
            context=context,
            start_time=start_time,
            artifact_path=root,
            diagnostics=diagnostics,
            use_cancellation_iteration=True,
            pre_cancellation_step="filesystem traversal",
        )

    def _is_safe_path(self, root: str, target: str) -> bool:
        """Check that resolved target stays within root (symlink safety).

        Uses os.path.realpath to resolve symlinks and verifies the
        resolved path is under the artifact root directory.

        Args:
            root: The artifact root directory path.
            target: The target path to validate.

        Returns:
            True if the resolved target is within root, False otherwise.
        """
        real_root = os.path.realpath(root)
        real_target = os.path.realpath(target)
        # Ensure target is within root (or is root itself)
        return real_target == real_root or real_target.startswith(real_root + os.sep)
