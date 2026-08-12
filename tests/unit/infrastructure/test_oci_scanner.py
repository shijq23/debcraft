"""Unit tests for OCI image layout scanner."""

from __future__ import annotations

import gzip
import io
import json
import tarfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from debcraft.domain.scanner.values import Artifact, ArtifactType
from debcraft.infrastructure.scanners.oci import OCIScanner
from debcraft.platform.contracts.workflow import (
    CancellationToken,
    ProgressReporter,
    WorkflowContext,
)

pytestmark = [pytest.mark.unit]


class _MockProgressReporter(ProgressReporter):
    """Mock progress reporter that records calls."""

    def __init__(self) -> None:
        self.reports: list[tuple[float, str]] = []

    def report(self, percentage: float, message: str = "") -> None:
        self.reports.append((percentage, message))


def _make_context(*, cancelled: bool = False) -> WorkflowContext:
    """Create a mock WorkflowContext."""
    token = CancellationToken()
    if cancelled:
        token.cancel()
    progress = _MockProgressReporter()
    ctx = MagicMock(spec=WorkflowContext)
    ctx.cancellation_token = token
    ctx.progress = progress
    return ctx


def _create_tar_gz(files: dict[str, bytes]) -> bytes:
    """Create a gzip-compressed tar archive from a dict of path -> content."""
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as tf:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    tar_data = tar_buffer.getvalue()
    return gzip.compress(tar_data)


def _create_oci_layout(
    tmp_path: Path,
    *,
    layers: list[dict[str, bytes]] | None = None,
    layout_version: str = "1.0.0",
    include_oci_layout: bool = True,
    include_index: bool = True,
    layer_media_type: str = "application/vnd.oci.image.layer.v1.tar+gzip",
) -> Path:
    """Create a minimal OCI image layout directory for testing.

    Args:
        tmp_path: Base temporary directory.
        layers: List of dicts mapping file paths to content for each layer.
        layout_version: imageLayoutVersion to write.
        include_oci_layout: Whether to create the oci-layout file.
        include_index: Whether to create the index.json file.
        layer_media_type: Media type to use for layer descriptors.

    Returns:
        Path to the OCI layout directory.
    """
    oci_dir = tmp_path / "oci-image"
    oci_dir.mkdir(parents=True, exist_ok=True)

    if include_oci_layout:
        oci_layout = {"imageLayoutVersion": layout_version}
        (oci_dir / "oci-layout").write_text(json.dumps(oci_layout))

    if layers is None:
        layers = []

    # Create layer blobs
    blobs_dir = oci_dir / "blobs" / "sha256"
    blobs_dir.mkdir(parents=True, exist_ok=True)

    layer_descriptors = []
    for layer_files in layers:
        blob_data = _create_tar_gz(layer_files)
        # Use a simple hash stand-in (just hex of the length for testing)
        import hashlib

        digest = hashlib.sha256(blob_data).hexdigest()
        (blobs_dir / digest).write_bytes(blob_data)
        layer_descriptors.append(
            {
                "mediaType": layer_media_type,
                "digest": f"sha256:{digest}",
                "size": len(blob_data),
            }
        )

    # Create manifest blob
    manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": "sha256:deadbeef",
            "size": 0,
        },
        "layers": layer_descriptors,
    }
    manifest_bytes = json.dumps(manifest).encode()
    import hashlib

    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    (blobs_dir / manifest_digest).write_bytes(manifest_bytes)

    if include_index:
        index = {
            "schemaVersion": 2,
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": f"sha256:{manifest_digest}",
                    "size": len(manifest_bytes),
                }
            ],
        }
        (oci_dir / "index.json").write_text(json.dumps(index))

    return oci_dir


class TestOCILayoutValidation:
    """Tests for OCI layout validation (Req 6.4, 6.5)."""

    @pytest.mark.asyncio
    async def test_missing_oci_layout_file(self, tmp_path: Path) -> None:
        """Missing oci-layout file → empty packages + diagnostic."""
        oci_dir = _create_oci_layout(tmp_path, include_oci_layout=False, include_index=True)
        scanner = OCIScanner()
        artifact = Artifact(type=ArtifactType.OCI, path=str(oci_dir))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("missing oci-layout" in d for d in result.diagnostics)

    @pytest.mark.asyncio
    async def test_unsupported_layout_version(self, tmp_path: Path) -> None:
        """Unsupported imageLayoutVersion → empty packages + diagnostic."""
        oci_dir = _create_oci_layout(tmp_path, layout_version="2.0.0")
        scanner = OCIScanner()
        artifact = Artifact(type=ArtifactType.OCI, path=str(oci_dir))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("unsupported imageLayoutVersion" in d and "'2.0.0'" in d for d in result.diagnostics)

    @pytest.mark.asyncio
    async def test_valid_layout_version_accepted(self, tmp_path: Path) -> None:
        """Valid imageLayoutVersion '1.0.0' is accepted (Req 6.4)."""
        oci_dir = _create_oci_layout(tmp_path, layout_version="1.0.0")
        scanner = OCIScanner()
        artifact = Artifact(type=ArtifactType.OCI, path=str(oci_dir))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        # Should not fail on layout validation
        assert not any("oci-layout" in d for d in result.diagnostics)

    @pytest.mark.asyncio
    async def test_missing_index_json(self, tmp_path: Path) -> None:
        """Missing index.json → empty packages + diagnostic."""
        oci_dir = _create_oci_layout(tmp_path, include_index=False)
        scanner = OCIScanner()
        artifact = Artifact(type=ArtifactType.OCI, path=str(oci_dir))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("missing index.json" in d for d in result.diagnostics)


class TestOCILayerExtraction:
    """Tests for layer extraction and dpkg status parsing (Req 6.1, 6.2)."""

    @pytest.mark.asyncio
    async def test_single_layer_with_dpkg_status(self, tmp_path: Path) -> None:
        """Single gzip layer with dpkg status → packages identified."""
        dpkg_content = "Package: bash\nVersion: 5.2-1\nArchitecture: amd64\nStatus: install ok installed\n"
        layer_files = {
            "var/lib/dpkg/status": dpkg_content.encode(),
            "usr/bin/bash": b"#!/bin/bash",
        }
        oci_dir = _create_oci_layout(tmp_path, layers=[layer_files])
        scanner = OCIScanner()
        artifact = Artifact(type=ArtifactType.OCI, path=str(oci_dir))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert len(result.packages) == 1
        assert result.packages[0].name == "bash"
        assert result.packages[0].version == "5.2-1"
        assert result.packages[0].architecture == "amd64"
        assert result.packages[0].status == "installed"
        assert result.strategy == "dpkg_metadata"

    @pytest.mark.asyncio
    async def test_multi_layer_merge(self, tmp_path: Path) -> None:
        """Multiple layers merge correctly with upper layers overriding."""
        # Layer 1: base dpkg status
        dpkg_base = "Package: base-pkg\nVersion: 1.0\nArchitecture: amd64\nStatus: install ok installed\n"
        layer1_files = {
            "var/lib/dpkg/status": dpkg_base.encode(),
            "usr/bin/old-tool": b"old",
        }
        # Layer 2: updated dpkg status
        dpkg_updated = (
            "Package: base-pkg\n"
            "Version: 1.0\n"
            "Architecture: amd64\n"
            "Status: install ok installed\n"
            "\n"
            "Package: new-pkg\n"
            "Version: 2.0\n"
            "Architecture: amd64\n"
            "Status: install ok installed\n"
        )
        layer2_files = {
            "var/lib/dpkg/status": dpkg_updated.encode(),
            "usr/bin/new-tool": b"new",
        }
        oci_dir = _create_oci_layout(tmp_path, layers=[layer1_files, layer2_files])
        scanner = OCIScanner()
        artifact = Artifact(type=ArtifactType.OCI, path=str(oci_dir))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert len(result.packages) == 2
        names = {p.name for p in result.packages}
        assert "base-pkg" in names
        assert "new-pkg" in names


class TestOCIWhiteoutHandling:
    """Tests for whiteout file handling (Req 6.6)."""

    @pytest.mark.asyncio
    async def test_single_file_whiteout(self, tmp_path: Path) -> None:
        """Whiteout .wh.<filename> removes the file from lower layers."""
        layer1_files = {
            "usr/bin/tool": b"content",
            "var/lib/dpkg/status": b"Package: pkg\nVersion: 1.0\nArchitecture: amd64\nStatus: install ok installed\n",
        }
        # Layer 2 whiteout removes usr/bin/tool
        layer2_files = {
            "usr/bin/.wh.tool": b"",
        }
        oci_dir = _create_oci_layout(tmp_path, layers=[layer1_files, layer2_files])
        scanner = OCIScanner()
        artifact = Artifact(type=ArtifactType.OCI, path=str(oci_dir))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        # The dpkg status should still be found (not whited-out)
        assert len(result.packages) == 1

    @pytest.mark.asyncio
    async def test_opaque_whiteout(self, tmp_path: Path) -> None:
        """Opaque whiteout .wh..wh..opq clears directory from lower layers."""
        layer1_files = {
            "etc/config/file1.conf": b"old1",
            "etc/config/file2.conf": b"old2",
            "var/lib/dpkg/status": b"Package: pkg\nVersion: 1.0\nArchitecture: amd64\nStatus: install ok installed\n",
        }
        # Layer 2 opaque whiteout clears etc/config/ then adds new file
        layer2_files = {
            "etc/config/.wh..wh..opq": b"",
            "etc/config/new.conf": b"new",
        }
        oci_dir = _create_oci_layout(tmp_path, layers=[layer1_files, layer2_files])
        scanner = OCIScanner()
        artifact = Artifact(type=ArtifactType.OCI, path=str(oci_dir))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        # dpkg status should still be found
        assert len(result.packages) == 1


class TestOCIUnsupportedMediaType:
    """Tests for unsupported media type handling (Req 6.11)."""

    @pytest.mark.asyncio
    async def test_unsupported_media_type_skipped_with_diagnostic(self, tmp_path: Path) -> None:
        """Layer with unsupported media type is skipped with diagnostic."""
        oci_dir = _create_oci_layout(
            tmp_path,
            layers=[{"some/file": b"data"}],
            layer_media_type="application/vnd.oci.image.layer.v1.tar",
        )
        scanner = OCIScanner()
        artifact = Artifact(type=ArtifactType.OCI, path=str(oci_dir))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("unsupported media type" in d for d in result.diagnostics)
        assert any("application/vnd.oci.image.layer.v1.tar" in d for d in result.diagnostics)


class TestOCINoDpkgStatus:
    """Tests for missing dpkg status (Req 6.10)."""

    @pytest.mark.asyncio
    async def test_no_dpkg_status_returns_empty_with_diagnostic(self, tmp_path: Path) -> None:
        """No dpkg status in merged filesystem → empty packages + diagnostic."""
        layer_files = {
            "usr/bin/something": b"binary data",
            "etc/config": b"config data",
        }
        oci_dir = _create_oci_layout(tmp_path, layers=[layer_files])
        scanner = OCIScanner()
        artifact = Artifact(type=ArtifactType.OCI, path=str(oci_dir))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("dpkg status file not found" in d for d in result.diagnostics)


class TestOCICancellation:
    """Tests for cancellation handling (Req 6.7, 6.8)."""

    @pytest.mark.asyncio
    async def test_cancellation_before_layer_extraction(self, tmp_path: Path) -> None:
        """Cancellation before layer extraction → empty packages + diagnostic."""
        layer_files = {
            "var/lib/dpkg/status": b"Package: pkg\nVersion: 1.0\nArchitecture: amd64\nStatus: install ok installed\n",
        }
        oci_dir = _create_oci_layout(tmp_path, layers=[layer_files])
        scanner = OCIScanner()
        artifact = Artifact(type=ArtifactType.OCI, path=str(oci_dir))
        ctx = _make_context(cancelled=True)

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("cancelled" in d.lower() for d in result.diagnostics)


class TestOCIZstdSupport:
    """Tests for zstd layer decompression (Req 6.3)."""

    @pytest.mark.asyncio
    async def test_zstd_layer_decompression(self, tmp_path: Path) -> None:
        """zstd-compressed layer is properly decompressed and extracted."""
        import zstandard

        dpkg_content = "Package: zstd-pkg\nVersion: 1.5.0\nArchitecture: amd64\nStatus: install ok installed\n"
        # Create a tar archive manually
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w") as tf:
            data = dpkg_content.encode()
            info = tarfile.TarInfo(name="var/lib/dpkg/status")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        tar_data = tar_buffer.getvalue()

        # Compress with zstd
        cctx = zstandard.ZstdCompressor()
        zstd_data = cctx.compress(tar_data)

        # Create OCI layout with zstd layer
        oci_dir = tmp_path / "oci-image"
        oci_dir.mkdir(parents=True)

        (oci_dir / "oci-layout").write_text(json.dumps({"imageLayoutVersion": "1.0.0"}))

        blobs_dir = oci_dir / "blobs" / "sha256"
        blobs_dir.mkdir(parents=True)

        import hashlib

        digest = hashlib.sha256(zstd_data).hexdigest()
        (blobs_dir / digest).write_bytes(zstd_data)

        manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": "sha256:deadbeef",
                "size": 0,
            },
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar+zstd",
                    "digest": f"sha256:{digest}",
                    "size": len(zstd_data),
                }
            ],
        }
        manifest_bytes = json.dumps(manifest).encode()
        manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
        (blobs_dir / manifest_digest).write_bytes(manifest_bytes)

        index = {
            "schemaVersion": 2,
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": f"sha256:{manifest_digest}",
                    "size": len(manifest_bytes),
                }
            ],
        }
        (oci_dir / "index.json").write_text(json.dumps(index))

        scanner = OCIScanner()
        artifact = Artifact(type=ArtifactType.OCI, path=str(oci_dir))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert len(result.packages) == 1
        assert result.packages[0].name == "zstd-pkg"
        assert result.packages[0].version == "1.5.0"


class TestOCIDuration:
    """Tests for duration measurement."""

    @pytest.mark.asyncio
    async def test_duration_is_non_negative(self, tmp_path: Path) -> None:
        """Scan duration is always non-negative."""
        oci_dir = _create_oci_layout(tmp_path, layers=[])
        scanner = OCIScanner()
        artifact = Artifact(type=ArtifactType.OCI, path=str(oci_dir))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.duration_seconds >= 0.0

    @pytest.mark.asyncio
    async def test_artifact_path_preserved(self, tmp_path: Path) -> None:
        """ScanResult artifact_path matches the input artifact path."""
        oci_dir = _create_oci_layout(tmp_path, layers=[])
        scanner = OCIScanner()
        artifact = Artifact(type=ArtifactType.OCI, path=str(oci_dir))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.artifact_path == str(oci_dir)
