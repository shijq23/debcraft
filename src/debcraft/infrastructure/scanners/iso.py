"""Scanner for ISO 9660 image files.

Scans ISO 9660 images for installed Debian packages by searching for
squashfs filesystem images at known paths, decompressing them to access
the rootfs, and parsing the dpkg status file. Falls back to direct rootfs
structure within the ISO or filesystem analysis when dpkg metadata is
unavailable. Operates without mount operations or root privileges.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Protocol

from debcraft.domain.scanner.dpkg_parser import parse_dpkg_status
from debcraft.domain.scanner.filesystem_analyzer import analyze_filesystem
from debcraft.domain.scanner.values import ScanningStrategy, ScanResult

if TYPE_CHECKING:
    from debcraft.domain.scanner.ports import ContentsIndexPort, PackageLookupPort
    from debcraft.domain.scanner.values import Artifact
    from debcraft.platform.contracts.workflow import WorkflowContext


class ISOReader(Protocol):
    """Abstraction over ISO 9660 reading library.

    Provides methods to open an ISO image, list directory contents,
    and read files from the ISO filesystem without requiring mount
    operations or root privileges.
    """

    def open(self, path: str) -> None:
        """Open an ISO 9660 image for reading.

        Args:
            path: Filesystem path to the ISO image file.

        Raises:
            OSError: If the file cannot be opened or is not valid ISO 9660.
        """
        ...

    def list_dir(self, path: str) -> list[str]:
        """List entries in a directory within the ISO filesystem.

        Args:
            path: Path within the ISO filesystem to list.

        Returns:
            List of entry names in the directory.

        Raises:
            FileNotFoundError: If the path does not exist in the ISO.
        """
        ...

    def read_file(self, path: str) -> bytes:
        """Read a file's contents from the ISO filesystem.

        Args:
            path: Path to the file within the ISO filesystem.

        Returns:
            Raw bytes content of the file.

        Raises:
            FileNotFoundError: If the file does not exist in the ISO.
        """
        ...

    def close(self) -> None:
        """Close the ISO image and release resources."""
        ...


class SquashfsReader(Protocol):
    """Abstraction over squashfs reading library.

    Provides methods to open a squashfs image from raw bytes,
    read files, and list directories within the squashfs filesystem.
    Operates without mount operations or root privileges.
    """

    def open(self, data: bytes) -> None:
        """Open a squashfs image from raw bytes.

        Args:
            data: Raw bytes of the squashfs image.

        Raises:
            OSError: If the data is not a valid squashfs image.
        """
        ...

    def read_file(self, path: str) -> bytes:
        """Read a file's contents from the squashfs filesystem.

        Args:
            path: Path to the file within the squashfs filesystem.

        Returns:
            Raw bytes content of the file.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        ...

    def list_dir(self, path: str) -> list[str]:
        """List entries in a directory within the squashfs filesystem.

        Args:
            path: Path within the squashfs filesystem to list.

        Returns:
            List of entry names in the directory.

        Raises:
            FileNotFoundError: If the path does not exist.
        """
        ...

    def close(self) -> None:
        """Close the squashfs image and release resources."""
        ...


SQUASHFS_SEARCH_PATHS = [
    "live/filesystem.squashfs",
    "casper/filesystem.squashfs",
    "install/filesystem.squashfs",
]
"""Known paths where squashfs filesystem images are stored in ISO media."""


class ISOScanner:
    """Scans ISO 9660 images for installed Debian packages.

    Searches known squashfs paths within the ISO, decompresses the squashfs
    to access the rootfs, and parses the dpkg status file. Falls back to
    direct rootfs structure if no squashfs is found, and ultimately to
    filesystem analysis if no dpkg metadata is available anywhere.

    No mount operations or root privileges are required.
    """

    def __init__(
        self,
        iso_reader: ISOReader,
        squashfs_reader: SquashfsReader,
        contents_port: ContentsIndexPort,
        package_port: PackageLookupPort,
    ) -> None:
        """Initialize ISOScanner with required dependencies.

        Args:
            iso_reader: Reader for ISO 9660 filesystem access.
            squashfs_reader: Reader for squashfs decompression.
            contents_port: Port for Contents index lookups (filesystem fallback).
            package_port: Port for package metadata lookups (filesystem fallback).
        """
        self._iso_reader = iso_reader
        self._squashfs_reader = squashfs_reader
        self._contents_port = contents_port
        self._package_port = package_port

    async def scan(self, artifact: Artifact, context: WorkflowContext) -> ScanResult:
        """Scan an ISO 9660 image for installed Debian packages.

        Steps:
        1. Open ISO, check cancellation
        2. Search for squashfs at known paths
        3. If squashfs found: extract, check cancellation, read rootfs
        4. If no squashfs: look for direct var/lib/dpkg/status
        5. Parse dpkg status or fall back to FilesystemAnalyzer
        6. Check cancellation after each major step

        Args:
            artifact: The artifact descriptor with type ISO.
            context: Workflow context providing cancellation, progress, logging.

        Returns:
            ScanResult with identified packages and metadata.
        """
        start_time = time.perf_counter()
        iso_path = artifact.path
        diagnostics: list[str] = []

        # Step 1: Open ISO image
        try:
            self._iso_reader.open(iso_path)
        except (OSError, Exception) as exc:
            duration = time.perf_counter() - start_time
            diagnostics.append(f"Invalid ISO image at '{iso_path}': {exc}")
            context.progress.report(100.0, "Scan complete: invalid ISO image")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=iso_path,
            )

        try:
            # Check cancellation after opening
            if context.cancellation_token.is_cancelled:
                return self._cancelled_result(iso_path, start_time, diagnostics, "opening ISO")

            # Step 2: Search for squashfs at known paths
            squashfs_data = self._find_squashfs()

            # Check cancellation after locating squashfs
            if context.cancellation_token.is_cancelled:
                return self._cancelled_result(iso_path, start_time, diagnostics, "locating squashfs")

            # Step 3: If squashfs found, extract and read rootfs
            if squashfs_data is not None:
                return await self._scan_squashfs(squashfs_data, artifact, context, start_time, diagnostics)

            # Step 4: No squashfs — check for direct rootfs with dpkg status
            return await self._scan_direct_rootfs(artifact, context, start_time, diagnostics)
        finally:
            self._iso_reader.close()

    def _find_squashfs(self) -> bytes | None:
        """Search for squashfs at known ISO paths.

        Tries each path in SQUASHFS_SEARCH_PATHS and returns the first
        squashfs image data found.

        Returns:
            Raw bytes of the squashfs image, or None if not found.
        """
        for squashfs_path in SQUASHFS_SEARCH_PATHS:
            try:
                return self._iso_reader.read_file(squashfs_path)
            except (FileNotFoundError, OSError):
                continue
        return None

    async def _scan_squashfs(
        self,
        squashfs_data: bytes,
        artifact: Artifact,
        context: WorkflowContext,
        start_time: float,
        diagnostics: list[str],
    ) -> ScanResult:
        """Scan the squashfs filesystem for dpkg status.

        Args:
            squashfs_data: Raw bytes of the squashfs image.
            artifact: The artifact descriptor.
            context: Workflow context for cancellation and progress.
            start_time: perf_counter value at scan start.
            diagnostics: Accumulated diagnostics list.

        Returns:
            ScanResult from the squashfs contents.
        """
        iso_path = artifact.path

        # Decompress squashfs
        try:
            self._squashfs_reader.open(squashfs_data)
        except (OSError, Exception) as exc:
            duration = time.perf_counter() - start_time
            diagnostics.append(f"Squashfs extraction failure: {exc}")
            context.progress.report(100.0, "Scan complete: squashfs extraction failed")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=iso_path,
            )

        try:
            # Check cancellation after extracting squashfs
            if context.cancellation_token.is_cancelled:
                return self._cancelled_result(iso_path, start_time, diagnostics, "extracting squashfs")

            # Try to read dpkg status from squashfs rootfs
            try:
                dpkg_content = self._squashfs_reader.read_file("var/lib/dpkg/status")
            except (FileNotFoundError, OSError):
                dpkg_content = None

            if dpkg_content is not None:
                # Check cancellation before parsing
                if context.cancellation_token.is_cancelled:
                    return self._cancelled_result(iso_path, start_time, diagnostics, "parsing dpkg status")

                content_str = dpkg_content.decode("utf-8", errors="replace")
                parse_result = parse_dpkg_status(content_str)
                diagnostics.extend(parse_result.diagnostics)

                duration = time.perf_counter() - start_time
                context.progress.report(
                    100.0,
                    f"Scan complete: identified {len(parse_result.packages)} packages",
                )
                return ScanResult(
                    packages=parse_result.packages,
                    strategy=ScanningStrategy.DPKG_METADATA.value,
                    diagnostics=diagnostics,
                    duration_seconds=duration,
                    artifact_path=iso_path,
                )

            # No dpkg status in squashfs — fall back to filesystem analysis
            return await self._fallback_squashfs_filesystem(artifact, context, start_time, diagnostics)
        finally:
            self._squashfs_reader.close()

    async def _scan_direct_rootfs(
        self,
        artifact: Artifact,
        context: WorkflowContext,
        start_time: float,
        diagnostics: list[str],
    ) -> ScanResult:
        """Scan for dpkg status directly in the ISO filesystem.

        Checks if the ISO contains a direct rootfs structure with
        var/lib/dpkg/status at the top level.

        Args:
            artifact: The artifact descriptor.
            context: Workflow context for cancellation and progress.
            start_time: perf_counter value at scan start.
            diagnostics: Accumulated diagnostics list.

        Returns:
            ScanResult from the direct rootfs or filesystem fallback.
        """
        iso_path = artifact.path

        # Try direct rootfs dpkg status
        try:
            dpkg_content = self._iso_reader.read_file("var/lib/dpkg/status")
        except (FileNotFoundError, OSError):
            dpkg_content = None

        if dpkg_content is not None:
            # Check cancellation before parsing
            if context.cancellation_token.is_cancelled:
                return self._cancelled_result(iso_path, start_time, diagnostics, "parsing dpkg status")

            content_str = dpkg_content.decode("utf-8", errors="replace")
            parse_result = parse_dpkg_status(content_str)
            diagnostics.extend(parse_result.diagnostics)

            duration = time.perf_counter() - start_time
            context.progress.report(
                100.0,
                f"Scan complete: identified {len(parse_result.packages)} packages",
            )
            return ScanResult(
                packages=parse_result.packages,
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=iso_path,
            )

        # No dpkg status anywhere — fall back to filesystem analysis
        return await self._fallback_iso_filesystem(artifact, context, start_time, diagnostics)

    async def _fallback_squashfs_filesystem(
        self,
        artifact: Artifact,
        context: WorkflowContext,
        start_time: float,
        diagnostics: list[str],
    ) -> ScanResult:
        """Fall back to filesystem analysis using squashfs file listing.

        Collects file paths from the squashfs filesystem and queries
        the Contents index for file-to-package mappings.

        Args:
            artifact: The artifact descriptor.
            context: Workflow context for cancellation and progress.
            start_time: perf_counter value at scan start.
            diagnostics: Accumulated diagnostics list.

        Returns:
            ScanResult with filesystem_analysis strategy.
        """
        iso_path = artifact.path

        # Collect file paths from squashfs
        file_paths = self._collect_squashfs_paths()

        if context.cancellation_token.is_cancelled:
            return self._cancelled_result(iso_path, start_time, diagnostics, "collecting filesystem paths")

        snapshot_id = int(artifact.options.get("snapshot_id", "0"))

        result = await analyze_filesystem(
            file_paths=file_paths,
            contents_port=self._contents_port,
            package_port=self._package_port,
            snapshot_id=snapshot_id,
        )

        diagnostics.extend(result.diagnostics)
        duration = time.perf_counter() - start_time
        context.progress.report(
            100.0,
            f"Scan complete: identified {len(result.packages)} packages via filesystem analysis",
        )
        return ScanResult(
            packages=result.packages,
            strategy=ScanningStrategy.FILESYSTEM_ANALYSIS.value,
            diagnostics=diagnostics,
            duration_seconds=duration,
            artifact_path=iso_path,
        )

    async def _fallback_iso_filesystem(
        self,
        artifact: Artifact,
        context: WorkflowContext,
        start_time: float,
        diagnostics: list[str],
    ) -> ScanResult:
        """Fall back to filesystem analysis using ISO file listing.

        Collects file paths from the ISO filesystem and queries
        the Contents index for file-to-package mappings.

        Args:
            artifact: The artifact descriptor.
            context: Workflow context for cancellation and progress.
            start_time: perf_counter value at scan start.
            diagnostics: Accumulated diagnostics list.

        Returns:
            ScanResult with filesystem_analysis strategy.
        """
        iso_path = artifact.path

        # Collect file paths from ISO
        file_paths = self._collect_iso_paths()

        if context.cancellation_token.is_cancelled:
            return self._cancelled_result(iso_path, start_time, diagnostics, "collecting filesystem paths")

        snapshot_id = int(artifact.options.get("snapshot_id", "0"))

        result = await analyze_filesystem(
            file_paths=file_paths,
            contents_port=self._contents_port,
            package_port=self._package_port,
            snapshot_id=snapshot_id,
        )

        diagnostics.extend(result.diagnostics)
        duration = time.perf_counter() - start_time
        context.progress.report(
            100.0,
            f"Scan complete: identified {len(result.packages)} packages via filesystem analysis",
        )
        return ScanResult(
            packages=result.packages,
            strategy=ScanningStrategy.FILESYSTEM_ANALYSIS.value,
            diagnostics=diagnostics,
            duration_seconds=duration,
            artifact_path=iso_path,
        )

    def _collect_squashfs_paths(self) -> list[str]:
        """Collect all file paths from the squashfs filesystem.

        Recursively walks the squashfs directory tree and returns
        normalized paths suitable for Contents index lookup.

        Returns:
            List of absolute-style paths from the squashfs filesystem.
        """
        file_paths: list[str] = []
        self._walk_squashfs("", file_paths)
        return file_paths

    def _walk_squashfs(self, directory: str, file_paths: list[str]) -> None:
        """Recursively walk the squashfs filesystem.

        Args:
            directory: Current directory path within squashfs.
            file_paths: Accumulator list for discovered file paths.
        """
        try:
            entries = self._squashfs_reader.list_dir(directory)
        except (FileNotFoundError, OSError):
            return

        for entry in entries:
            entry_path = f"{directory}/{entry}" if directory else entry
            # Try to list as directory; if it fails, treat as file
            try:
                self._squashfs_reader.list_dir(entry_path)
                # It's a directory — recurse
                self._walk_squashfs(entry_path, file_paths)
            except (FileNotFoundError, OSError):
                # It's a file — add to paths
                file_paths.append(f"/{entry_path}")

    def _collect_iso_paths(self) -> list[str]:
        """Collect all file paths from the ISO filesystem.

        Recursively walks the ISO directory tree and returns
        normalized paths suitable for Contents index lookup.

        Returns:
            List of absolute-style paths from the ISO filesystem.
        """
        file_paths: list[str] = []
        self._walk_iso("", file_paths)
        return file_paths

    def _walk_iso(self, directory: str, file_paths: list[str]) -> None:
        """Recursively walk the ISO filesystem.

        Args:
            directory: Current directory path within the ISO.
            file_paths: Accumulator list for discovered file paths.
        """
        try:
            entries = self._iso_reader.list_dir(directory)
        except (FileNotFoundError, OSError):
            return

        for entry in entries:
            entry_path = f"{directory}/{entry}" if directory else entry
            # Try to list as directory; if it fails, treat as file
            try:
                self._iso_reader.list_dir(entry_path)
                # It's a directory — recurse
                self._walk_iso(entry_path, file_paths)
            except (FileNotFoundError, OSError):
                # It's a file — add to paths
                file_paths.append(f"/{entry_path}")

    def _cancelled_result(
        self,
        artifact_path: str,
        start_time: float,
        diagnostics: list[str],
        step: str,
    ) -> ScanResult:
        """Create a ScanResult indicating cancellation.

        Args:
            artifact_path: Path to the artifact being scanned.
            start_time: perf_counter value at scan start.
            diagnostics: Accumulated diagnostics list.
            step: Description of the step at which cancellation occurred.

        Returns:
            ScanResult with empty packages and cancellation diagnostic.
        """
        duration = time.perf_counter() - start_time
        diagnostics.append(f"Scan cancelled during {step}")
        return ScanResult(
            packages=[],
            strategy=ScanningStrategy.DPKG_METADATA.value,
            diagnostics=diagnostics,
            duration_seconds=duration,
            artifact_path=artifact_path,
        )
