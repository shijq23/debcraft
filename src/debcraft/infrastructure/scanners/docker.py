"""Scanner for Docker image tarballs (docker save format).

Reads manifest.json, extracts layers bottom-to-top into a virtual filesystem,
applies whiteout semantics, and locates the dpkg status file in the merged
result. Operates without Docker daemon or root privileges.
"""

from __future__ import annotations

import json
import os
import tarfile
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from debcraft.domain.scanner.ports import ContentsIndexPort, PackageLookupPort
    from debcraft.domain.scanner.values import Artifact, IdentifiedPackage, ScanResult
    from debcraft.platform.contracts.workflow import WorkflowContext

from debcraft.domain.scanner.dpkg_parser import parse_dpkg_status
from debcraft.domain.scanner.filesystem_analyzer import analyze_filesystem
from debcraft.domain.scanner.values import ScanningStrategy
from debcraft.domain.scanner.values import ScanResult as ScanResultClass

DPKG_STATUS_PATH = "var/lib/dpkg/status"
WHITEOUT_PREFIX = ".wh."
OPAQUE_WHITEOUT = ".wh..wh..opq"


class DockerScanner:
    """Scans Docker image tarballs for installed Debian packages.

    Reads manifest.json, extracts layers bottom-to-top,
    applies whiteout files, locates dpkg status.
    Operates without Docker daemon or root privileges.
    """

    def __init__(  # noqa: D107
        self,
        contents_port: ContentsIndexPort,
        package_port: PackageLookupPort,
    ) -> None:
        self._contents_port = contents_port
        self._package_port = package_port

    async def scan(self, artifact: Artifact, context: WorkflowContext) -> ScanResult:
        """Scan a Docker image tarball.

        Steps:
        1. Open tarball, read manifest.json
        2. Select first image entry from manifest
        3. For each layer (bottom to top):
           a. Check cancellation token
           b. Extract layer tar entries into virtual filesystem dict
           c. Apply whiteout files (.wh.* and .wh..wh..opq)
        4. Look for var/lib/dpkg/status in merged filesystem
        5. If found: parse, else: fall back to FilesystemAnalyzer

        Args:
            artifact: The artifact descriptor (type, path, options).
            context: Workflow context providing cancellation, progress, logging.

        Returns:
            ScanResult with identified packages and metadata.
        """
        start = time.perf_counter()
        diagnostics: list[str] = []

        # Validate tarball exists and is accessible
        if not os.path.exists(artifact.path):
            duration = time.perf_counter() - start
            return ScanResultClass(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=[f"Docker image tarball not found: {artifact.path}"],
                duration_seconds=duration,
                artifact_path=artifact.path,
            )

        if not os.path.isfile(artifact.path):
            duration = time.perf_counter() - start
            return ScanResultClass(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=[f"Path is not a file: {artifact.path}"],
                duration_seconds=duration,
                artifact_path=artifact.path,
            )

        try:
            outer_tar = tarfile.open(artifact.path, "r:*")  # noqa: SIM115
        except (tarfile.TarError, OSError) as exc:
            duration = time.perf_counter() - start
            return ScanResultClass(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=[f"Invalid Docker image tarball: {exc}"],
                duration_seconds=duration,
                artifact_path=artifact.path,
            )

        try:
            # Step 1: Read manifest.json
            try:
                manifest_member = outer_tar.getmember("manifest.json")
                manifest_file = outer_tar.extractfile(manifest_member)
                if manifest_file is None:
                    duration = time.perf_counter() - start
                    return ScanResultClass(
                        packages=[],
                        strategy=ScanningStrategy.DPKG_METADATA.value,
                        diagnostics=["manifest.json is not a regular file"],
                        duration_seconds=duration,
                        artifact_path=artifact.path,
                    )
                manifest_data = json.loads(manifest_file.read())
            except KeyError:
                duration = time.perf_counter() - start
                return ScanResultClass(
                    packages=[],
                    strategy=ScanningStrategy.DPKG_METADATA.value,
                    diagnostics=["Invalid Docker image: missing manifest.json"],
                    duration_seconds=duration,
                    artifact_path=artifact.path,
                )
            except (json.JSONDecodeError, OSError) as exc:
                duration = time.perf_counter() - start
                return ScanResultClass(
                    packages=[],
                    strategy=ScanningStrategy.DPKG_METADATA.value,
                    diagnostics=[f"Invalid Docker image: cannot parse manifest.json: {exc}"],
                    duration_seconds=duration,
                    artifact_path=artifact.path,
                )

            # Step 2: Select first image entry (Requirement 5.9)
            if not isinstance(manifest_data, list) or len(manifest_data) == 0:
                duration = time.perf_counter() - start
                return ScanResultClass(
                    packages=[],
                    strategy=ScanningStrategy.DPKG_METADATA.value,
                    diagnostics=["Invalid Docker image: manifest.json is empty or not an array"],
                    duration_seconds=duration,
                    artifact_path=artifact.path,
                )

            image_entry = manifest_data[0]
            layers = image_entry.get("Layers", [])

            if not layers:
                duration = time.perf_counter() - start
                return ScanResultClass(
                    packages=[],
                    strategy=ScanningStrategy.DPKG_METADATA.value,
                    diagnostics=["Invalid Docker image: no layers found in manifest"],
                    duration_seconds=duration,
                    artifact_path=artifact.path,
                )

            # Step 3: Extract layers bottom-to-top into virtual filesystem
            vfs: dict[str, bytes] = {}
            total_layers = len(layers)

            for layer_index, layer_path in enumerate(layers):
                # Check cancellation between layers (Requirement 5.7)
                if context.cancellation_token.is_cancelled:
                    # Return partial result from what we've parsed so far
                    packages = self._parse_vfs_dpkg(vfs)
                    duration = time.perf_counter() - start
                    strategy = (
                        ScanningStrategy.DPKG_METADATA.value
                        if packages is not None
                        else ScanningStrategy.FILESYSTEM_ANALYSIS.value
                    )
                    diagnostics.append(
                        f"Scan cancelled during layer extraction (processed {layer_index}/{total_layers} layers)"
                    )
                    return ScanResultClass(
                        packages=packages or [],
                        strategy=strategy,
                        diagnostics=diagnostics,
                        duration_seconds=duration,
                        artifact_path=artifact.path,
                    )

                # Report progress for layer extraction
                progress_pct = (layer_index / total_layers) * 80.0
                context.progress.report(
                    progress_pct,
                    f"Extracting layer {layer_index + 1}/{total_layers}",
                )

                # Extract layer tarball from outer tar
                try:
                    layer_member = outer_tar.getmember(layer_path)
                    layer_fileobj = outer_tar.extractfile(layer_member)
                    if layer_fileobj is None:
                        diagnostics.append(f"Layer '{layer_path}' is not a regular file, skipping")
                        continue

                    layer_tar = tarfile.open(fileobj=layer_fileobj, mode="r:*")  # noqa: SIM115
                    try:
                        layer_entries = self._merge_layer(vfs, layer_tar)
                        self._apply_whiteouts(vfs, layer_entries)
                    finally:
                        layer_tar.close()

                except (KeyError, tarfile.TarError, OSError) as exc:
                    diagnostics.append(f"Error extracting layer '{layer_path}': {exc}")
                    continue

            # Step 4: Look for var/lib/dpkg/status in merged filesystem
            context.progress.report(80.0, "Parsing package metadata")

            if DPKG_STATUS_PATH in vfs:
                # Requirement 5.4: dpkg status found → strategy "dpkg_metadata"
                status_content = vfs[DPKG_STATUS_PATH].decode("utf-8", errors="replace")
                parse_result = parse_dpkg_status(status_content)
                diagnostics.extend(parse_result.diagnostics)

                duration = time.perf_counter() - start
                context.progress.report(
                    100.0,
                    f"Scan complete: {len(parse_result.packages)} packages identified",
                )
                return ScanResultClass(
                    packages=parse_result.packages,
                    strategy=ScanningStrategy.DPKG_METADATA.value,
                    diagnostics=diagnostics,
                    duration_seconds=duration,
                    artifact_path=artifact.path,
                )
            else:
                # Requirement 5.5: fall back to FilesystemAnalyzer
                snapshot_id = int(artifact.options.get("snapshot_id", "0"))
                file_paths = list(vfs.keys())

                fs_result = await analyze_filesystem(
                    file_paths=file_paths,
                    contents_port=self._contents_port,
                    package_port=self._package_port,
                    snapshot_id=snapshot_id,
                )
                diagnostics.extend(fs_result.diagnostics)

                duration = time.perf_counter() - start
                context.progress.report(
                    100.0,
                    f"Scan complete: {len(fs_result.packages)} packages identified via filesystem analysis",
                )
                return ScanResultClass(
                    packages=fs_result.packages,
                    strategy=ScanningStrategy.FILESYSTEM_ANALYSIS.value,
                    diagnostics=diagnostics,
                    duration_seconds=duration,
                    artifact_path=artifact.path,
                )

        finally:
            outer_tar.close()

    def _merge_layer(self, vfs: dict[str, bytes], layer_tar: tarfile.TarFile) -> list[str]:
        """Merge a layer's files into the virtual filesystem.

        Iterates all entries in the layer tar. For regular files, reads their
        content and stores in the vfs dict keyed by normalized path. Returns
        the list of all entry names (including whiteout markers) for subsequent
        whiteout processing.

        Args:
            vfs: The virtual filesystem dict to merge into.
            layer_tar: An opened tarfile for the layer.

        Returns:
            List of all tar entry names in this layer.
        """
        entries: list[str] = []

        for member in layer_tar.getmembers():
            # Normalize the path (strip leading ./ or /)
            name = member.name.lstrip("./")
            if not name:
                continue

            entries.append(name)

            # Only store regular files in vfs (not directories, symlinks, etc.)
            if member.isfile():
                fileobj = layer_tar.extractfile(member)
                if fileobj is not None:
                    vfs[name] = fileobj.read()

        return entries

    def _apply_whiteouts(self, vfs: dict[str, bytes], layer_entries: list[str]) -> None:
        """Apply Docker whiteout semantics to the virtual filesystem.

        Processes whiteout markers from the layer entries:
        - `.wh.<filename>`: Removes the corresponding file from the vfs
        - `.wh..wh..opq`: Removes all files in that directory from lower layers
          (files added in the same layer are preserved)

        After processing, the whiteout markers themselves are removed from the vfs.

        Args:
            vfs: The virtual filesystem dict to modify.
            layer_entries: List of entry names from this layer.
        """
        # Build a set of non-whiteout entries from this layer for opaque handling
        current_layer_files: set[str] = set()
        for entry in layer_entries:
            basename = os.path.basename(entry)
            if not basename.startswith(WHITEOUT_PREFIX):
                current_layer_files.add(entry)

        for entry in layer_entries:
            basename = os.path.basename(entry)
            dirname = os.path.dirname(entry)

            if basename == OPAQUE_WHITEOUT:
                # Opaque whiteout: remove all entries under this directory
                # that came from lower layers (preserve same-layer entries)
                prefix = dirname + "/" if dirname else ""
                keys_to_remove = [k for k in vfs if k.startswith(prefix) and k not in current_layer_files]
                for key in keys_to_remove:
                    del vfs[key]
                # Remove the opaque whiteout marker itself
                vfs.pop(entry, None)

            elif basename.startswith(WHITEOUT_PREFIX):
                # Regular whiteout: remove the specific file
                target_name = basename[len(WHITEOUT_PREFIX) :]
                target_path = os.path.join(dirname, target_name) if dirname else target_name
                vfs.pop(target_path, None)
                # Remove the whiteout marker itself
                vfs.pop(entry, None)

    def _parse_vfs_dpkg(self, vfs: dict[str, bytes]) -> list[IdentifiedPackage] | None:
        """Try to parse dpkg status from vfs, returning packages or None."""
        if DPKG_STATUS_PATH in vfs:
            content = vfs[DPKG_STATUS_PATH].decode("utf-8", errors="replace")
            result = parse_dpkg_status(content)
            return result.packages
        return None
