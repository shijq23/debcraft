"""Scanner for OCI image layout directories.

Reads index.json and oci-layout, extracts layers from blobs/,
supports tar+gzip and tar+zstd media types. Operates without
container runtimes or root privileges.
"""

from __future__ import annotations

import gzip
import io
import json
import tarfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from debcraft.platform.contracts.workflow import WorkflowContext

from debcraft.domain.scanner.dpkg_parser import parse_dpkg_status
from debcraft.domain.scanner.values import (
    Artifact,
    ScanningStrategy,
    ScanResult,
)


class OCIScanner:
    """Scans OCI image layout directories for installed Debian packages.

    Reads index.json and oci-layout, extracts layers from blobs/,
    supports tar+gzip and tar+zstd media types. Applies layers
    bottom-to-top with whiteout handling to reconstruct a merged
    virtual filesystem, then locates and parses the dpkg status file.

    Operates without requiring container runtimes or root privileges.
    """

    SUPPORTED_MEDIA_TYPES = frozenset(
        {
            "application/vnd.oci.image.layer.v1.tar+gzip",
            "application/vnd.oci.image.layer.v1.tar+zstd",
        }
    )

    async def scan(self, artifact: Artifact, context: WorkflowContext) -> ScanResult:
        """Scan an OCI image layout directory.

        Steps:
        1. Validate oci-layout file (imageLayoutVersion == "1.0.0")
        2. Read index.json for manifest descriptors
        3. Read image manifest for layer descriptors
        4. For each layer blob (bottom to top):
           a. Check cancellation token
           b. Decompress (gzip or zstd) and extract tar
           c. Merge into virtual filesystem with whiteout handling
        5. Look for var/lib/dpkg/status
        6. Parse if found; return empty + diagnostic if not

        Args:
            artifact: The artifact descriptor (type, path, options).
            context: Workflow context providing cancellation, progress, logging.

        Returns:
            ScanResult with identified packages and metadata.
        """
        start = time.perf_counter()
        diagnostics: list[str] = []
        layout_dir = Path(artifact.path)

        # Step 1: Validate oci-layout file
        oci_layout_path = layout_dir / "oci-layout"
        if not oci_layout_path.exists():
            duration = time.perf_counter() - start
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=["Invalid OCI layout: missing oci-layout file"],
                duration_seconds=duration,
                artifact_path=artifact.path,
            )

        try:
            oci_layout_content = oci_layout_path.read_text(encoding="utf-8")
            oci_layout = json.loads(oci_layout_content)
        except (OSError, json.JSONDecodeError) as e:
            duration = time.perf_counter() - start
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=[f"Invalid OCI layout: cannot read oci-layout file: {e}"],
                duration_seconds=duration,
                artifact_path=artifact.path,
            )

        layout_version = oci_layout.get("imageLayoutVersion")
        if layout_version != "1.0.0":
            duration = time.perf_counter() - start
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=[
                    f"Invalid OCI layout: unsupported imageLayoutVersion '{layout_version}' (expected '1.0.0')"
                ],
                duration_seconds=duration,
                artifact_path=artifact.path,
            )

        # Step 2: Read index.json
        index_path = layout_dir / "index.json"
        if not index_path.exists():
            duration = time.perf_counter() - start
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=["Invalid OCI layout: missing index.json"],
                duration_seconds=duration,
                artifact_path=artifact.path,
            )

        try:
            index_content = index_path.read_text(encoding="utf-8")
            index_data = json.loads(index_content)
        except (OSError, json.JSONDecodeError) as e:
            duration = time.perf_counter() - start
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=[f"Invalid OCI layout: cannot read index.json: {e}"],
                duration_seconds=duration,
                artifact_path=artifact.path,
            )

        manifests = index_data.get("manifests", [])
        if not manifests:
            duration = time.perf_counter() - start
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=["Invalid OCI layout: index.json contains no manifests"],
                duration_seconds=duration,
                artifact_path=artifact.path,
            )

        # Step 3: Read the first manifest
        manifest_descriptor = manifests[0]
        manifest_digest = manifest_descriptor.get("digest", "")

        manifest_blob_path = self._digest_to_blob_path(layout_dir, manifest_digest)
        if manifest_blob_path is None or not manifest_blob_path.exists():
            duration = time.perf_counter() - start
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=[f"Invalid OCI layout: manifest blob not found for digest '{manifest_digest}'"],
                duration_seconds=duration,
                artifact_path=artifact.path,
            )

        try:
            manifest_content = manifest_blob_path.read_text(encoding="utf-8")
            manifest_data = json.loads(manifest_content)
        except (OSError, json.JSONDecodeError) as e:
            duration = time.perf_counter() - start
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=[f"Invalid OCI layout: cannot read manifest blob: {e}"],
                duration_seconds=duration,
                artifact_path=artifact.path,
            )

        layers = manifest_data.get("layers", [])

        # Step 4: Extract layers bottom-to-top into virtual filesystem
        vfs: dict[str, bytes] = {}
        total_layers = len(layers)

        for layer_index, layer_descriptor in enumerate(layers):
            # Check cancellation between layers
            if context.cancellation_token.is_cancelled:
                duration = time.perf_counter() - start
                return ScanResult(
                    packages=[],
                    strategy=ScanningStrategy.DPKG_METADATA.value,
                    diagnostics=["Scan cancelled during layer extraction"],
                    duration_seconds=duration,
                    artifact_path=artifact.path,
                )

            media_type = layer_descriptor.get("mediaType", "")
            layer_digest = layer_descriptor.get("digest", "")

            # Check for unsupported media types
            if media_type not in self.SUPPORTED_MEDIA_TYPES:
                diagnostics.append(f"Skipping layer {layer_index}: unsupported media type '{media_type}'")
                continue

            layer_blob_path = self._digest_to_blob_path(layout_dir, layer_digest)
            if layer_blob_path is None or not layer_blob_path.exists():
                diagnostics.append(f"Skipping layer {layer_index}: blob not found for digest '{layer_digest}'")
                continue

            # Decompress and extract
            try:
                layer_data = layer_blob_path.read_bytes()
                tar_data = self._decompress_layer(layer_data, media_type, diagnostics)
                if tar_data is not None:
                    self._merge_layer(vfs, tar_data)
            except (OSError, tarfile.TarError) as e:
                diagnostics.append(f"Error extracting layer {layer_index}: {e}")
                continue

            # Report progress
            if total_layers > 0:
                progress_pct = ((layer_index + 1) / total_layers) * 80.0
                context.progress.report(
                    progress_pct,
                    f"Extracted layer {layer_index + 1}/{total_layers}",
                )

        # Step 5: Look for var/lib/dpkg/status in merged filesystem
        dpkg_status_path = "var/lib/dpkg/status"
        if dpkg_status_path in vfs:
            # Step 6: Parse dpkg status
            try:
                status_content = vfs[dpkg_status_path].decode("utf-8")
            except UnicodeDecodeError as e:
                duration = time.perf_counter() - start
                diagnostics.append(f"Cannot decode dpkg status file: {e}")
                return ScanResult(
                    packages=[],
                    strategy=ScanningStrategy.DPKG_METADATA.value,
                    diagnostics=diagnostics,
                    duration_seconds=duration,
                    artifact_path=artifact.path,
                )

            parse_result = parse_dpkg_status(status_content)
            diagnostics.extend(parse_result.diagnostics)

            context.progress.report(
                100.0,
                f"Scan complete: {len(parse_result.packages)} packages identified",
            )

            duration = time.perf_counter() - start
            return ScanResult(
                packages=parse_result.packages,
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=diagnostics,
                duration_seconds=duration,
                artifact_path=artifact.path,
            )

        # Step 6: No dpkg status found
        diagnostics.append("dpkg status file not found at var/lib/dpkg/status in merged layer filesystem")

        context.progress.report(100.0, "Scan complete: no packages identified")

        duration = time.perf_counter() - start
        return ScanResult(
            packages=[],
            strategy=ScanningStrategy.DPKG_METADATA.value,
            diagnostics=diagnostics,
            duration_seconds=duration,
            artifact_path=artifact.path,
        )

    def _digest_to_blob_path(self, layout_dir: Path, digest: str) -> Path | None:
        """Convert an OCI content-addressable digest to a blob file path.

        Args:
            layout_dir: Root of the OCI layout directory.
            digest: Digest string in format "algorithm:hex" (e.g. "sha256:abc123").

        Returns:
            Path to the blob file, or None if the digest format is invalid.
        """
        if ":" not in digest:
            return None
        algorithm, hex_digest = digest.split(":", 1)
        return layout_dir / "blobs" / algorithm / hex_digest

    def _decompress_layer(
        self,
        data: bytes,
        media_type: str,
        diagnostics: list[str],
    ) -> bytes | None:
        """Decompress a layer blob based on its media type.

        Args:
            data: Raw compressed layer data.
            media_type: OCI media type indicating compression format.
            diagnostics: List to append diagnostic messages to.

        Returns:
            Decompressed tar data, or None if decompression fails.
        """
        if media_type == "application/vnd.oci.image.layer.v1.tar+gzip":
            return gzip.decompress(data)

        if media_type == "application/vnd.oci.image.layer.v1.tar+zstd":
            try:
                import zstandard

                dctx = zstandard.ZstdDecompressor()
                return dctx.decompress(data)
            except ImportError:
                diagnostics.append(
                    "Cannot decompress zstd layer: 'zstandard' package "
                    "is not installed. Install with: pip install zstandard"
                )
                return None

        return None

    def _merge_layer(self, vfs: dict[str, bytes], tar_data: bytes) -> None:
        """Merge a decompressed layer tar into the virtual filesystem.

        Applies OCI whiteout semantics:
        - `.wh.<filename>` markers delete the corresponding file
        - `.wh..wh..opq` markers clear all entries from lower layers
          in the containing directory

        Args:
            vfs: The virtual filesystem dict (path -> content bytes).
            tar_data: Decompressed tar archive data for this layer.
        """
        tar_buffer = io.BytesIO(tar_data)
        layer_entries: list[str] = []

        with tarfile.open(fileobj=tar_buffer, mode="r:") as tf:
            for member in tf:
                name = member.name
                # Normalize: strip leading ./ or /
                if name.startswith("./"):
                    name = name[2:]
                elif name.startswith("/"):
                    name = name[1:]

                if not name:
                    continue

                layer_entries.append(name)

                # Extract regular file contents
                if member.isfile():
                    f = tf.extractfile(member)
                    if f is not None:
                        vfs[name] = f.read()

        # Apply whiteout semantics after extracting all entries
        self._apply_whiteouts(vfs, layer_entries)

    def _apply_whiteouts(self, vfs: dict[str, bytes], layer_entries: list[str]) -> None:
        """Apply OCI whiteout semantics to the virtual filesystem.

        Processes whiteout markers found in the layer entries:
        - `.wh.<filename>`: Remove the file named `<filename>` in the same directory
        - `.wh..wh..opq`: Remove all entries from lower layers in that directory

        Args:
            vfs: The virtual filesystem dict to modify in-place.
            layer_entries: List of entry paths from this layer.
        """
        for entry in layer_entries:
            basename = entry.rsplit("/", 1)[-1] if "/" in entry else entry
            parent_dir = entry.rsplit("/", 1)[0] if "/" in entry else ""

            if basename == ".wh..wh..opq":
                # Opaque whiteout: remove all entries in this directory
                # from lower layers (entries NOT in current layer_entries)
                prefix = parent_dir + "/" if parent_dir else ""
                keys_to_remove = [k for k in vfs if k.startswith(prefix) and k != entry and k not in layer_entries]
                for key in keys_to_remove:
                    del vfs[key]
                # Remove the opaque whiteout marker itself
                vfs.pop(entry, None)

            elif basename.startswith(".wh."):
                # Single-file whiteout: remove the target file
                target_name = basename[4:]  # Strip ".wh." prefix
                target_path = f"{parent_dir}/{target_name}" if parent_dir else target_name
                vfs.pop(target_path, None)
                # Remove the whiteout marker itself
                vfs.pop(entry, None)
