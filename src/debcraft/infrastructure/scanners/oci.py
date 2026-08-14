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
from typing import TYPE_CHECKING, Any

from debcraft.domain.scanner.dpkg_parser import parse_dpkg_status
from debcraft.domain.scanner.values import (
    Artifact,
    IdentifiedPackage,
    ScanningStrategy,
    ScanResult,
)
from debcraft.infrastructure.scanners._mixin import ScannerMixin

if TYPE_CHECKING:
    from debcraft.platform.contracts.workflow import WorkflowContext


class OCIScanner(ScannerMixin):
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

        # Steps 1-3: Validate layout and read manifest layers
        validation = self._validate_oci_artifact(layout_dir, diagnostics)
        if validation and isinstance(validation[0], str):
            # validation is a list of diagnostics indicating failure
            duration = time.perf_counter() - start
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=[str(d) for d in validation],
                duration_seconds=duration,
                artifact_path=artifact.path,
            )

        layers: list[dict[str, Any]] = validation  # type: ignore[assignment]

        # Step 4: Extract layers into virtual filesystem
        vfs = self._extract_oci_layers(layers, layout_dir, context, diagnostics)
        if vfs is None:
            # Cancelled during extraction
            duration = time.perf_counter() - start
            return ScanResult(
                packages=[],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                diagnostics=["Scan cancelled during layer extraction"],
                duration_seconds=duration,
                artifact_path=artifact.path,
            )

        # Steps 5-6: Parse dpkg status from merged filesystem
        packages = self._parse_dpkg_status_from_layers(vfs, context, diagnostics)

        duration = time.perf_counter() - start
        return ScanResult(
            packages=packages,
            strategy=ScanningStrategy.DPKG_METADATA.value,
            diagnostics=diagnostics,
            duration_seconds=duration,
            artifact_path=artifact.path,
        )

    def _validate_oci_artifact(
        self,
        layout_dir: Path,
        _diagnostics: list[str],
    ) -> list[dict[str, Any]] | list[str]:
        """Validate OCI layout structure and return layer descriptors.

        Checks oci-layout file existence and version, reads index.json,
        and reads the first manifest to extract layer descriptors.

        Args:
            layout_dir: Root of the OCI layout directory.
            diagnostics: Diagnostic list (unused here but kept for API consistency).

        Returns:
            On success: list of layer descriptor dicts from the manifest.
            On failure: list of diagnostic strings (single-element) describing the error.
        """
        # Step 1: Validate oci-layout file
        layout_error = self._check_oci_layout(layout_dir)
        if layout_error is not None:
            return [layout_error]

        # Step 2: Read index.json and get manifests
        index_result = self._read_oci_index(layout_dir)
        if isinstance(index_result, str):
            return [index_result]

        # Step 3: Read the first manifest and return layers
        layers_result = self._read_manifest_layers(layout_dir, index_result[0])
        if isinstance(layers_result, str):
            return [layers_result]

        return layers_result

    def _check_oci_layout(self, layout_dir: Path) -> str | None:
        """Validate the oci-layout file exists and has correct version.

        Args:
            layout_dir: Root of the OCI layout directory.

        Returns:
            None on success, or a diagnostic string describing the error.
        """
        oci_layout_path = layout_dir / "oci-layout"
        if not oci_layout_path.exists():
            return "Invalid OCI layout: missing oci-layout file"

        try:
            oci_layout_content = oci_layout_path.read_text(encoding="utf-8")
            oci_layout = json.loads(oci_layout_content)
        except (OSError, json.JSONDecodeError) as e:
            return f"Invalid OCI layout: cannot read oci-layout file: {e}"

        layout_version = oci_layout.get("imageLayoutVersion")
        if layout_version != "1.0.0":
            return f"Invalid OCI layout: unsupported imageLayoutVersion '{layout_version}' (expected '1.0.0')"

        return None

    def _read_oci_index(self, layout_dir: Path) -> list[dict[str, Any]] | str:
        """Read and validate index.json, returning manifest descriptors.

        Args:
            layout_dir: Root of the OCI layout directory.

        Returns:
            On success: list of manifest descriptors from index.json.
            On failure: a diagnostic string describing the error.
        """
        index_path = layout_dir / "index.json"
        if not index_path.exists():
            return "Invalid OCI layout: missing index.json"

        try:
            index_content = index_path.read_text(encoding="utf-8")
            index_data = json.loads(index_content)
        except (OSError, json.JSONDecodeError) as e:
            return f"Invalid OCI layout: cannot read index.json: {e}"

        manifests = index_data.get("manifests", [])
        if not manifests:
            return "Invalid OCI layout: index.json contains no manifests"

        result: list[dict[str, Any]] = manifests
        return result

    def _read_manifest_layers(
        self, layout_dir: Path, manifest_descriptor: dict[str, Any]
    ) -> list[dict[str, Any]] | str:
        """Read a manifest blob and extract layer descriptors.

        Args:
            layout_dir: Root of the OCI layout directory.
            manifest_descriptor: The manifest descriptor dict from index.json.

        Returns:
            On success: list of layer descriptor dicts from the manifest.
            On failure: a diagnostic string describing the error.
        """
        manifest_digest = manifest_descriptor.get("digest", "")

        manifest_blob_path = self._digest_to_blob_path(layout_dir, manifest_digest)
        if manifest_blob_path is None or not manifest_blob_path.exists():
            return f"Invalid OCI layout: manifest blob not found for digest '{manifest_digest}'"

        try:
            manifest_content = manifest_blob_path.read_text(encoding="utf-8")
            manifest_data = json.loads(manifest_content)
        except (OSError, json.JSONDecodeError) as e:
            return f"Invalid OCI layout: cannot read manifest blob: {e}"

        layers: list[dict[str, Any]] = manifest_data.get("layers", [])
        return layers

    def _extract_oci_layers(
        self,
        layers: list[dict[str, Any]],
        layout_dir: Path,
        context: WorkflowContext,
        diagnostics: list[str],
    ) -> dict[str, bytes] | None:
        """Extract OCI layers bottom-to-top into a virtual filesystem.

        Iterates over layer descriptors, decompresses each blob, and
        merges into the VFS with whiteout handling. Checks cancellation
        between layers.

        Args:
            layers: List of layer descriptor dicts from the manifest.
            layout_dir: Root of the OCI layout directory.
            context: Workflow context for cancellation and progress.
            diagnostics: List to append diagnostic messages to.

        Returns:
            Merged virtual filesystem dict, or None if cancelled.
        """
        vfs: dict[str, bytes] = {}
        total_layers = len(layers)

        for layer_index, layer_descriptor in enumerate(layers):
            # Check cancellation between layers
            if context.cancellation_token.is_cancelled:
                return None

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
                self._report_progress(
                    context,
                    progress_pct,
                    f"Extracted layer {layer_index + 1}/{total_layers}",
                )

        return vfs

    def _parse_dpkg_status_from_layers(
        self,
        vfs: dict[str, bytes],
        context: WorkflowContext,
        diagnostics: list[str],
    ) -> list[IdentifiedPackage]:
        """Parse dpkg status file from the merged virtual filesystem.

        Looks for var/lib/dpkg/status in the VFS, decodes and parses it.

        Args:
            vfs: Merged virtual filesystem dict (path -> content bytes).
            context: Workflow context for progress reporting.
            diagnostics: List to append diagnostic messages to.

        Returns:
            List of identified packages (may be empty).
        """
        dpkg_status_path = "var/lib/dpkg/status"
        if dpkg_status_path not in vfs:
            diagnostics.append("dpkg status file not found at var/lib/dpkg/status in merged layer filesystem")
            self._report_progress(context, 100.0, "Scan complete: no packages identified")
            return []

        try:
            status_content = vfs[dpkg_status_path].decode("utf-8")
        except UnicodeDecodeError as e:
            diagnostics.append(f"Cannot decode dpkg status file: {e}")
            return []

        parse_result = parse_dpkg_status(status_content)
        diagnostics.extend(parse_result.diagnostics)

        self._report_progress(
            context,
            100.0,
            f"Scan complete: {len(parse_result.packages)} packages identified",
        )

        return parse_result.packages

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
