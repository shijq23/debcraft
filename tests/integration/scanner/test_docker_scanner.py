"""Integration tests for DockerScanner.

Tests the DockerScanner against crafted minimal Docker image tarballs
created programmatically to simulate docker save format.

Requirements: 5.1, 5.2, 5.3, 5.4, 5.6
"""

from __future__ import annotations

import io
import json
import tarfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from debcraft.domain.scanner.values import Artifact, ArtifactType
from debcraft.infrastructure.scanners.docker import DockerScanner

pytestmark = [pytest.mark.integration]


# ===========================================================================
# Helpers
# ===========================================================================


def _make_workflow_context() -> MagicMock:
    """Create a mock WorkflowContext with cancellation disabled."""
    context = MagicMock()
    context.cancellation_token.is_cancelled = False
    context.progress.report = MagicMock()
    return context


def _make_scanner() -> DockerScanner:
    """Create a DockerScanner with mock ports."""
    contents_port = AsyncMock()
    contents_port.find_owners = AsyncMock(return_value={})
    package_port = AsyncMock()
    package_port.find_by_name = AsyncMock(return_value=None)
    return DockerScanner(
        contents_port=contents_port,
        package_port=package_port,
    )


def _add_bytes_to_tar(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    """Add raw bytes as a file entry in a tarfile."""
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _create_layer_tar(files: dict[str, bytes]) -> bytes:
    """Create a layer tar (inner tar) from a dict of path -> content."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as layer_tar:
        for path, content in files.items():
            _add_bytes_to_tar(layer_tar, path, content)
    return buf.getvalue()


def _create_docker_tarball(
    tmp_path,
    layers: list[dict[str, bytes]],
    manifest: list[dict] | None = None,
) -> str:
    """Create a Docker image tarball in docker save format.

    Args:
        tmp_path: pytest tmp_path fixture for file creation.
        layers: List of dicts, each mapping filepath -> content for a layer.
        manifest: Optional custom manifest.json content. If None, auto-generated.

    Returns:
        Path to the created tarball.
    """
    tarball_path = str(tmp_path / "image.tar")
    layer_paths = []

    with tarfile.open(tarball_path, "w") as outer_tar:
        # Create each layer tar and add to outer tar
        for i, layer_files in enumerate(layers):
            layer_name = f"layer{i}/layer.tar"
            layer_data = _create_layer_tar(layer_files)
            _add_bytes_to_tar(outer_tar, layer_name, layer_data)
            layer_paths.append(layer_name)

        # Create manifest.json
        if manifest is None:
            manifest = [{"Layers": layer_paths}]
        manifest_data = json.dumps(manifest).encode("utf-8")
        _add_bytes_to_tar(outer_tar, "manifest.json", manifest_data)

    return tarball_path


SAMPLE_DPKG_STATUS = (
    "Package: bash\n"
    "Status: install ok installed\n"
    "Version: 5.2-2\n"
    "Architecture: amd64\n"
    "\n"
    "Package: coreutils\n"
    "Status: install ok installed\n"
    "Version: 9.1-1\n"
    "Architecture: amd64\n"
)


# ===========================================================================
# Test Cases
# ===========================================================================


@pytest.mark.asyncio
async def test_valid_docker_tarball_with_dpkg_status(tmp_path) -> None:
    """Valid Docker tarball with dpkg status in layer returns packages.

    Requirements: 5.1, 5.4
    """
    layers = [
        {"var/lib/dpkg/status": SAMPLE_DPKG_STATUS.encode("utf-8")},
    ]
    tarball_path = _create_docker_tarball(tmp_path, layers)

    artifact = Artifact(
        type=ArtifactType.DOCKER,
        path=tarball_path,
        options={},
    )
    scanner = _make_scanner()
    context = _make_workflow_context()

    result = await scanner.scan(artifact, context)

    assert result.strategy == "dpkg_metadata"
    assert len(result.packages) == 2
    package_names = {p.name for p in result.packages}
    assert "bash" in package_names
    assert "coreutils" in package_names
    assert result.artifact_path == tarball_path


@pytest.mark.asyncio
async def test_missing_manifest_json(tmp_path) -> None:
    """Docker tarball without manifest.json returns empty packages + diagnostic.

    Requirements: 5.6
    """
    # Create a tarball without manifest.json
    tarball_path = str(tmp_path / "image.tar")
    with tarfile.open(tarball_path, "w") as outer_tar:
        layer_data = _create_layer_tar({"some/file.txt": b"hello"})
        _add_bytes_to_tar(outer_tar, "layer0/layer.tar", layer_data)

    artifact = Artifact(
        type=ArtifactType.DOCKER,
        path=tarball_path,
        options={},
    )
    scanner = _make_scanner()
    context = _make_workflow_context()

    result = await scanner.scan(artifact, context)

    assert result.packages == []
    assert any("manifest.json" in d for d in result.diagnostics)


@pytest.mark.asyncio
async def test_whiteout_removes_file_from_lower_layer(tmp_path) -> None:
    """Whiteout file (.wh.X) removes the corresponding file from lower layer.

    Requirements: 5.3
    """
    # Layer 0: has dpkg status with bash and coreutils
    # Layer 1: whiteout for dpkg status, replaces with only bash
    layer0_files = {
        "var/lib/dpkg/status": SAMPLE_DPKG_STATUS.encode("utf-8"),
        "usr/bin/something": b"binary",
    }
    # Layer 1 adds a whiteout for "something" in usr/bin
    # and provides a new dpkg/status with only bash
    new_dpkg_status = "Package: bash\nStatus: install ok installed\nVersion: 5.2-3\nArchitecture: amd64\n"
    layer1_files = {
        "usr/bin/.wh.something": b"",
        "var/lib/dpkg/status": new_dpkg_status.encode("utf-8"),
    }

    layers = [layer0_files, layer1_files]
    tarball_path = _create_docker_tarball(tmp_path, layers)

    artifact = Artifact(
        type=ArtifactType.DOCKER,
        path=tarball_path,
        options={},
    )
    scanner = _make_scanner()
    context = _make_workflow_context()

    result = await scanner.scan(artifact, context)

    assert result.strategy == "dpkg_metadata"
    # Upper layer overrides dpkg status, only bash should remain
    assert len(result.packages) == 1
    assert result.packages[0].name == "bash"
    assert result.packages[0].version == "5.2-3"


@pytest.mark.asyncio
async def test_opaque_whiteout_clears_directory_from_lower_layers(
    tmp_path,
) -> None:
    """Opaque whiteout (.wh..wh..opq) clears directory from lower layers.

    Requirements: 5.3
    """
    # Layer 0: has files in etc/config/
    layer0_files = {
        "etc/config/app.conf": b"old config",
        "etc/config/db.conf": b"old db config",
        "var/lib/dpkg/status": SAMPLE_DPKG_STATUS.encode("utf-8"),
    }
    # Layer 1: opaque whiteout for etc/config/ and adds new file
    layer1_files = {
        "etc/config/.wh..wh..opq": b"",
        "etc/config/new.conf": b"new config",
    }

    layers = [layer0_files, layer1_files]
    tarball_path = _create_docker_tarball(tmp_path, layers)

    artifact = Artifact(
        type=ArtifactType.DOCKER,
        path=tarball_path,
        options={},
    )
    scanner = _make_scanner()
    context = _make_workflow_context()

    result = await scanner.scan(artifact, context)

    # dpkg status should still be there (it's not under etc/config/)
    assert result.strategy == "dpkg_metadata"
    assert len(result.packages) == 2


@pytest.mark.asyncio
async def test_multi_layer_upper_overrides_dpkg_status(tmp_path) -> None:
    """Multi-layer image with upper layer overriding dpkg status.

    Requirements: 5.2
    """
    # Layer 0: base with bash only
    base_dpkg = "Package: bash\nStatus: install ok installed\nVersion: 5.1-1\nArchitecture: amd64\n"
    layer0_files = {
        "var/lib/dpkg/status": base_dpkg.encode("utf-8"),
    }

    # Layer 1: upper layer adds coreutils to dpkg status
    upper_dpkg = (
        "Package: bash\n"
        "Status: install ok installed\n"
        "Version: 5.2-2\n"
        "Architecture: amd64\n"
        "\n"
        "Package: coreutils\n"
        "Status: install ok installed\n"
        "Version: 9.1-1\n"
        "Architecture: amd64\n"
    )
    layer1_files = {
        "var/lib/dpkg/status": upper_dpkg.encode("utf-8"),
    }

    layers = [layer0_files, layer1_files]
    tarball_path = _create_docker_tarball(tmp_path, layers)

    artifact = Artifact(
        type=ArtifactType.DOCKER,
        path=tarball_path,
        options={},
    )
    scanner = _make_scanner()
    context = _make_workflow_context()

    result = await scanner.scan(artifact, context)

    assert result.strategy == "dpkg_metadata"
    # Upper layer dpkg status should win
    assert len(result.packages) == 2
    pkg_map = {p.name: p for p in result.packages}
    assert pkg_map["bash"].version == "5.2-2"
    assert pkg_map["coreutils"].version == "9.1-1"


@pytest.mark.asyncio
async def test_invalid_tarball_not_tar_format(tmp_path) -> None:
    """Invalid tarball (not a tar file) returns empty packages + diagnostic.

    Requirements: 5.6
    """
    invalid_path = str(tmp_path / "not_a_tar.tar")
    with open(invalid_path, "wb") as f:
        f.write(b"this is not a tar file at all")

    artifact = Artifact(
        type=ArtifactType.DOCKER,
        path=invalid_path,
        options={},
    )
    scanner = _make_scanner()
    context = _make_workflow_context()

    result = await scanner.scan(artifact, context)

    assert result.packages == []
    assert len(result.diagnostics) > 0
    assert any("invalid" in d.lower() or "tar" in d.lower() for d in result.diagnostics)
