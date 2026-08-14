"""Integration test for ISOScanner end-to-end pipeline.

Scans fixtures/images/test.iso through ISOScanner with production readers
(PyCdlibISOReader and PySquashfsImageReader) and verifies the full pipeline
returns at least 1 package (base-files).

The test.iso fixture contains var/lib/dpkg/status with a base-files entry
directly in the ISO (no squashfs layer), so the scanner finds it via the
"direct rootfs" path.

Requirements: 7.3
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from debcraft.domain.scanner.values import Artifact, ArtifactType
from debcraft.infrastructure.scanners.iso import ISOScanner
from debcraft.infrastructure.scanners.iso_reader_pycdlib import PyCdlibISOReader
from debcraft.infrastructure.scanners.squashfs_reader_pysquashfsimage import PySquashfsImageReader

pytestmark = [pytest.mark.integration, pytest.mark.iso]

# Path to the test ISO fixture
FIXTURE_ISO_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "..",
    "fixtures",
    "images",
    "test.iso",
)


def _make_workflow_context() -> MagicMock:
    """Create a mock WorkflowContext with cancellation disabled."""
    ctx = MagicMock()
    ctx.cancellation_token.is_cancelled = False
    ctx.progress.report = MagicMock()
    return ctx


def _make_no_op_ports() -> tuple[AsyncMock, AsyncMock]:
    """Create no-op ports for ContentsIndexPort and PackageLookupPort."""
    contents_port = AsyncMock()
    contents_port.find_owners = AsyncMock(return_value={})

    package_port = AsyncMock()
    package_port.find_by_name = AsyncMock(return_value=None)

    return contents_port, package_port


@pytest.mark.asyncio
async def test_iso_scanner_full_pipeline_finds_packages() -> None:
    """Scanning test.iso with production readers finds at least 1 package (base-files).

    Requirements: 7.3
    """
    iso_path = os.path.abspath(FIXTURE_ISO_PATH)
    assert os.path.isfile(iso_path), f"Fixture ISO not found at {iso_path}"

    iso_reader = PyCdlibISOReader()
    squashfs_reader = PySquashfsImageReader()
    contents_port, package_port = _make_no_op_ports()

    scanner = ISOScanner(
        iso_reader=iso_reader,
        squashfs_reader=squashfs_reader,
        contents_port=contents_port,
        package_port=package_port,
    )

    artifact = Artifact(
        type=ArtifactType.ISO,
        path=iso_path,
        options={},
    )
    context = _make_workflow_context()

    result = await scanner.scan(artifact, context)

    # The full pipeline should find at least 1 package
    assert len(result.packages) >= 1, (
        f"Expected at least 1 package but got {len(result.packages)}. Diagnostics: {result.diagnostics}"
    )

    # Specifically, base-files should be present
    package_names = {p.name for p in result.packages}
    assert "base-files" in package_names, f"Expected 'base-files' in packages, got: {package_names}"

    # Result metadata should be valid
    assert result.artifact_path == iso_path
    assert result.duration_seconds >= 0
    assert result.strategy == "dpkg_metadata"
