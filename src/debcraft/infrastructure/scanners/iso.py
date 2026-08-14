"""Scanner for ISO 9660 image files.

Scans ISO 9660 images for installed Debian packages by searching for
squashfs filesystem images at known paths, decompressing them to access
the rootfs, and parsing the dpkg status file. Falls back to direct rootfs
structure within the ISO or filesystem analysis when dpkg metadata is
unavailable. Operates without mount operations or root privileges.
"""

from __future__ import annotations

import gzip
import time
from typing import TYPE_CHECKING, Protocol

from debcraft.domain._stanza_parser import parse_stanza_fields_ordered, split_stanzas
from debcraft.domain.scanner.dpkg_parser import parse_dpkg_status
from debcraft.domain.scanner.errors import ScannerError
from debcraft.domain.scanner.values import IdentifiedPackage, ScanningStrategy, ScanResult
from debcraft.infrastructure.scanners._mixin import ScannerMixin

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


class ISOScanner(ScannerMixin):
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
        4. If no squashfs: try repository structure (NETINST ISOs)
        5. If no repository packages: check for direct rootfs with dpkg status
        6. Parse dpkg status or fall back to FilesystemAnalyzer
        7. Check cancellation after each major step

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
        except (OSError, RuntimeError) as exc:
            duration = time.perf_counter() - start_time
            diagnostics.append(f"Invalid ISO image at '{iso_path}': {exc}")
            self._report_progress(context, 100.0, "Scan complete: invalid ISO image")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=iso_path,
            )

        try:
            # Check cancellation after opening
            self._check_cancellation(context, iso_path, "opening ISO")

            # Step 2: Search for squashfs at known paths
            squashfs_data = self._find_squashfs()

            # Check cancellation after locating squashfs
            self._check_cancellation(context, iso_path, "locating squashfs")

            # Step 3: If squashfs found, extract and read rootfs
            if squashfs_data is not None:
                return await self._scan_squashfs(squashfs_data, artifact, context, start_time, diagnostics)

            # Step 4: No squashfs — try repository structure (NETINST ISOs)
            repo_result = self._scan_repository(artifact, context, start_time, diagnostics)
            if repo_result is not None:
                return repo_result

            # Step 5: No repository packages — check for direct rootfs with dpkg status
            return await self._scan_direct_rootfs(artifact, context, start_time, diagnostics)
        except ScannerError:
            return self._build_cancellation_result(
                step="scan",
                start_time=start_time,
                strategy=ScanningStrategy.DPKG_METADATA.value,
                artifact_path=iso_path,
                diagnostics=diagnostics,
            )
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

    def _has_repository_structure(self, diagnostics: list[str]) -> bool:
        """Check if ISO root contains a dists/ directory.

        Lists the ISO root directory and checks for a "dists" entry,
        indicating that the ISO is structured as a Debian package
        repository (e.g., NETINST images).

        Args:
            diagnostics: Accumulated diagnostics list to append messages to.

        Returns:
            True if "dists" is found at the ISO root, False otherwise.
            Returns False on any I/O error (graceful degradation).
        """
        try:
            root_entries = self._iso_reader.list_dir("")
        except (FileNotFoundError, OSError):
            return False
        if "dists" in root_entries:
            diagnostics.append("Repository structure detected: dists/ directory found at ISO root")
            return True
        return False

    def _scan_repository(
        self,
        artifact: Artifact,
        context: WorkflowContext,
        start_time: float,
        diagnostics: list[str],
    ) -> ScanResult | None:
        """Orchestrate repository-based scanning.

        Checks for repository structure, discovers Packages files, parses
        each one, deduplicates results, and returns a ScanResult. Returns
        None if no packages are found (signals fallback to caller).

        On cancellation during the parsing loop, returns partial results
        (all packages parsed before the cancellation point) with a
        cancellation diagnostic. If cancellation occurs before any parsing
        (during discovery check), lets ScannerError propagate.

        Args:
            artifact: The artifact descriptor with type ISO.
            context: Workflow context providing cancellation, progress, logging.
            start_time: perf_counter value at scan start.
            diagnostics: Accumulated diagnostics list to append messages to.

        Returns:
            ScanResult with strategy DPKG_METADATA if packages found,
            None if no packages found (triggers fallback).

        Raises:
            ScannerError: If cancellation occurs before any parsing starts.
        """
        # Step 1: Check for repository structure
        if not self._has_repository_structure(diagnostics):
            return None

        # Step 2: Discover Packages files
        packages_files = self._discover_packages_files(diagnostics)

        # Step 3: Check cancellation after discovery
        self._check_cancellation(context, artifact.path, "discovering Packages files")

        # Step 4: If no packages files found, return None (triggers fallback per Req 7.3)
        if not packages_files:
            return None

        # Step 5: Parse each Packages file with cancellation checks
        all_packages: list[IdentifiedPackage] = []
        try:
            for path in packages_files:
                self._check_cancellation(context, artifact.path, "parsing Packages file")
                parsed = self._parse_packages_file(path, diagnostics)
                all_packages.extend(parsed)
        except ScannerError:
            # Cancellation during parsing loop — return partial results (Req 6.2)
            deduplicated = self._deduplicate_packages(all_packages, len(packages_files), diagnostics)
            if not deduplicated:
                return None
            diagnostics.append("Repository scan cancelled during parsing Packages file")
            self._report_progress(
                context, 100.0, f"Scan cancelled: identified {len(deduplicated)} packages from repository"
            )
            return self._build_success_result(
                packages=deduplicated,
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                start_time=start_time,
                artifact_path=artifact.path,
            )

        # Step 6: Deduplicate
        deduplicated = self._deduplicate_packages(all_packages, len(packages_files), diagnostics)

        # Step 7: If no packages after deduplication, return None (triggers fallback per Req 7.3)
        if not deduplicated:
            return None

        # Step 8: Return successful result
        self._report_progress(context, 100.0, f"Scan complete: identified {len(deduplicated)} packages from repository")
        return self._build_success_result(
            packages=deduplicated,
            strategy=ScanningStrategy.DPKG_METADATA.value,
            diagnostics=diagnostics,
            start_time=start_time,
            artifact_path=artifact.path,
        )

    def _discover_packages_files(self, diagnostics: list[str]) -> list[str]:
        """Discover Packages files in the repository structure.

        Walks dists/<codename>/<component>/binary-<arch>/ looking for Packages files.
        For each architecture directory:
        - If Packages.gz exists, add it to the list
        - Else if Packages exists, add it
        Records diagnostics for I/O errors on directory listings.

        Args:
            diagnostics: Accumulated diagnostics list to append messages to.

        Returns:
            List of discovered Packages file paths within the ISO.
        """
        metadata_entries = {"Release", "InRelease"}
        packages_files: list[str] = []

        # Enumerate codenames under dists/
        try:
            codenames = self._iso_reader.list_dir("dists")
        except (FileNotFoundError, OSError) as exc:
            diagnostics.append(f"Failed to list directory: dists: {exc}")
            return packages_files

        for codename in codenames:
            # Enumerate components under dists/<codename>/
            codename_path = f"dists/{codename}"
            try:
                components = self._iso_reader.list_dir(codename_path)
            except (FileNotFoundError, OSError) as exc:
                diagnostics.append(f"Failed to list directory: {codename_path}: {exc}")
                continue

            for component in components:
                # Exclude known metadata entries
                if component in metadata_entries:
                    continue

                # Enumerate architecture directories under component
                component_path = f"{codename_path}/{component}"
                try:
                    arch_entries = self._iso_reader.list_dir(component_path)
                except (FileNotFoundError, OSError) as exc:
                    diagnostics.append(f"Failed to list directory: {component_path}: {exc}")
                    continue

                for arch_entry in arch_entries:
                    # Only match binary-<arch> directories
                    if not arch_entry.startswith("binary-"):
                        continue

                    arch_path = f"{component_path}/{arch_entry}"
                    try:
                        arch_contents = self._iso_reader.list_dir(arch_path)
                    except (FileNotFoundError, OSError) as exc:
                        diagnostics.append(f"Failed to list directory: {arch_path}: {exc}")
                        continue

                    # Prefer Packages.gz over Packages
                    if "Packages.gz" in arch_contents:
                        packages_files.append(f"{arch_path}/Packages.gz")
                    elif "Packages" in arch_contents:
                        packages_files.append(f"{arch_path}/Packages")

        if not packages_files:
            diagnostics.append("No Packages files found in repository structure")

        return packages_files

    def _parse_packages_file(self, path: str, diagnostics: list[str]) -> list[IdentifiedPackage]:
        """Parse a single Packages file into IdentifiedPackage entries.

        1. Read bytes via ISOReader.read_file()
        2. If path ends with .gz, decompress with gzip.decompress()
        3. Decode as UTF-8
        4. Split into stanzas using split_stanzas()
        5. Parse each stanza with parse_stanza_fields_ordered()
        6. For stanzas with Package and Version fields, create IdentifiedPackage(status="installed")
        7. Record diagnostics for stanzas missing required fields

        Args:
            path: Path to the Packages file within the ISO.
            diagnostics: Accumulated diagnostics list to append messages to.

        Returns:
            List of IdentifiedPackage entries parsed from the file.
            Returns empty list on I/O or decompression failure.
        """
        # Step 1: Read raw bytes
        try:
            raw_bytes = self._iso_reader.read_file(path)
        except (FileNotFoundError, OSError) as exc:
            diagnostics.append(f"Failed to read {path}: {exc}")
            return []

        # Step 2: Decompress if gzipped
        if path.endswith(".gz"):
            try:
                raw_bytes = gzip.decompress(raw_bytes)
            except (OSError, gzip.BadGzipFile) as exc:
                diagnostics.append(f"Failed to decompress {path}: {exc}")
                return []

        # Step 3: Decode as UTF-8
        content = raw_bytes.decode("utf-8", errors="replace")

        # Step 4: Split into stanzas
        stanzas = split_stanzas(content)

        # Step 5-7: Parse each stanza and create IdentifiedPackage entries
        packages: list[IdentifiedPackage] = []
        for n, stanza_text in enumerate(stanzas, start=1):
            fields_list = parse_stanza_fields_ordered(stanza_text)
            fields = dict(fields_list)

            package_name = fields.get("Package")
            version = fields.get("Version")

            if not package_name:
                diagnostics.append(f"Stanza {n} in {path}: skipped, missing field: Package")
                continue

            if not version:
                diagnostics.append(f"Stanza {n} in {path}: skipped, missing field: Version")
                continue

            architecture = fields.get("Architecture", "")

            packages.append(
                IdentifiedPackage(
                    name=package_name,
                    version=version,
                    architecture=architecture,
                    status="installed",
                )
            )

        return packages

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
        except (OSError, RuntimeError) as exc:
            duration = time.perf_counter() - start_time
            diagnostics.append(f"Squashfs extraction failure: {exc}")
            self._report_progress(context, 100.0, "Scan complete: squashfs extraction failed")
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=iso_path,
            )

        try:
            # Check cancellation after extracting squashfs
            self._check_cancellation(context, iso_path, "extracting squashfs")

            # Try to read dpkg status from squashfs rootfs
            try:
                dpkg_content = self._squashfs_reader.read_file("var/lib/dpkg/status")
            except (FileNotFoundError, OSError):
                dpkg_content = None

            if dpkg_content is not None:
                # Check cancellation before parsing
                self._check_cancellation(context, iso_path, "parsing dpkg status")

                content_str = dpkg_content.decode("utf-8", errors="replace")
                parse_result = parse_dpkg_status(content_str)
                return self._build_dpkg_success_result(
                    parse_result=parse_result,
                    context=context,
                    start_time=start_time,
                    artifact_path=iso_path,
                    diagnostics=diagnostics,
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
            self._check_cancellation(context, iso_path, "parsing dpkg status")

            content_str = dpkg_content.decode("utf-8", errors="replace")
            parse_result = parse_dpkg_status(content_str)
            return self._build_dpkg_success_result(
                parse_result=parse_result,
                context=context,
                start_time=start_time,
                artifact_path=iso_path,
                diagnostics=diagnostics,
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

        self._check_cancellation(context, iso_path, "collecting filesystem paths")

        snapshot_id = int(artifact.options.get("snapshot_id", "0"))

        packages, analysis_diagnostics = await self._run_filesystem_analysis(
            file_paths=file_paths,
            contents_port=self._contents_port,
            package_port=self._package_port,
            snapshot_id=snapshot_id,
            context=context,
        )

        diagnostics.extend(analysis_diagnostics)
        self._report_progress(
            context,
            100.0,
            f"Scan complete: identified {len(packages)} packages via filesystem analysis",
        )
        return self._build_success_result(
            packages=packages,
            strategy=ScanningStrategy.FILESYSTEM_ANALYSIS.value,
            diagnostics=diagnostics,
            start_time=start_time,
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

        self._check_cancellation(context, iso_path, "collecting filesystem paths")

        snapshot_id = int(artifact.options.get("snapshot_id", "0"))

        packages, analysis_diagnostics = await self._run_filesystem_analysis(
            file_paths=file_paths,
            contents_port=self._contents_port,
            package_port=self._package_port,
            snapshot_id=snapshot_id,
            context=context,
        )

        diagnostics.extend(analysis_diagnostics)
        self._report_progress(
            context,
            100.0,
            f"Scan complete: identified {len(packages)} packages via filesystem analysis",
        )
        return self._build_success_result(
            packages=packages,
            strategy=ScanningStrategy.FILESYSTEM_ANALYSIS.value,
            diagnostics=diagnostics,
            start_time=start_time,
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

    def _deduplicate_packages(
        self,
        packages: list[IdentifiedPackage],
        num_files: int,
        diagnostics: list[str],
    ) -> list[IdentifiedPackage]:
        """Deduplicate packages by (name, version, architecture) tuple.

        Retains first occurrence, preserving discovery order.
        Records a summary diagnostic with total count and files processed.

        Args:
            packages: List of packages potentially containing duplicates.
            num_files: Number of Packages files that were processed.
            diagnostics: Accumulator list for diagnostic messages.

        Returns:
            Deduplicated list preserving first-occurrence order.
        """
        seen: set[tuple[str, str, str]] = set()
        result: list[IdentifiedPackage] = []
        for pkg in packages:
            key = (pkg.name, pkg.version, pkg.architecture)
            if key not in seen:
                seen.add(key)
                result.append(pkg)
        diagnostics.append(
            f"Repository scan: {len(result)} unique packages from {num_files} "
            f"Packages file(s) ({len(packages) - len(result)} duplicates removed)"
        )
        return result
