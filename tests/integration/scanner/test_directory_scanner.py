"""Integration tests for DirectoryScanner with real filesystem operations.

Verifies that DirectoryScanner correctly:
1. Identifies packages from a valid dpkg status file (strategy "dpkg_metadata")
2. Falls back to filesystem analysis when dpkg status is absent
3. Handles non-existent directory paths gracefully
4. Falls back when dpkg status file is unreadable (permission denied)
5. Skips symlinks that point outside the artifact root

Requirements: 4.1, 4.2, 4.3, 4.4, 4.7
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from debcraft.domain.scanner.values import Artifact, ArtifactType, ScanningStrategy
from debcraft.infrastructure.scanners.directory import DirectoryScanner
from debcraft.platform.contracts.workflow import (
    CancellationToken,
    ProgressReporter,
    WorkflowContext,
)

pytestmark = [pytest.mark.integration]


# --- Helpers ---


class _RecordingProgressReporter(ProgressReporter):
    """Progress reporter that records all calls for assertions."""

    def __init__(self) -> None:
        self.reports: list[tuple[float, str]] = []

    def report(self, percentage: float, message: str = "") -> None:
        self.reports.append((percentage, message))


def _make_context(*, cancelled: bool = False) -> WorkflowContext:
    """Create a mock WorkflowContext with a real CancellationToken and recording progress."""
    token = CancellationToken()
    if cancelled:
        token.cancel()
    progress = _RecordingProgressReporter()
    ctx = MagicMock(spec=WorkflowContext)
    ctx.cancellation_token = token
    ctx.progress = progress
    return ctx


def _make_empty_ports() -> tuple[AsyncMock, AsyncMock]:
    """Create mock ports that return empty results (no Contents index data)."""
    contents_port = AsyncMock()
    contents_port.find_owners = AsyncMock(return_value={})

    package_port = AsyncMock()
    package_port.find_by_name = AsyncMock(return_value=None)

    return contents_port, package_port


def _write_dpkg_status(root: str, content: str) -> str:
    """Write a dpkg status file at <root>/var/lib/dpkg/status.

    Returns the path to the created file.
    """
    dpkg_dir = os.path.join(root, "var", "lib", "dpkg")
    os.makedirs(dpkg_dir, exist_ok=True)
    status_path = os.path.join(dpkg_dir, "status")
    with open(status_path, "w", encoding="utf-8") as f:
        f.write(content)
    return status_path


SAMPLE_DPKG_STATUS = """\
Package: bash
Status: install ok installed
Priority: required
Section: shells
Architecture: amd64
Version: 5.2-2
Description: GNU Bourne Again SHell

Package: coreutils
Status: install ok installed
Priority: required
Section: utils
Architecture: amd64
Version: 9.1-1
Description: GNU core utilities

Package: removed-pkg
Status: deinstall ok config-files
Priority: optional
Section: misc
Architecture: amd64
Version: 1.0-1
Description: A removed package
"""


# --- Test Cases ---


@pytest.mark.asyncio
async def test_valid_dpkg_status_present(tmp_path) -> None:
    """Directory with a valid dpkg status file identifies packages via dpkg_metadata strategy.

    Requirements: 4.1, 4.2
    """
    root = str(tmp_path)
    _write_dpkg_status(root, SAMPLE_DPKG_STATUS)

    contents_port, package_port = _make_empty_ports()
    scanner = DirectoryScanner(contents_port, package_port)
    artifact = Artifact(type=ArtifactType.DIRECTORY, path=root)
    context = _make_context()

    result = await scanner.scan(artifact, context)

    # Strategy should be dpkg_metadata
    assert result.strategy == ScanningStrategy.DPKG_METADATA.value

    # Should find bash and coreutils (installed), not removed-pkg (deinstall)
    pkg_names = [p.name for p in result.packages]
    assert "bash" in pkg_names
    assert "coreutils" in pkg_names
    assert "removed-pkg" not in pkg_names

    # Verify package details
    bash_pkg = next(p for p in result.packages if p.name == "bash")
    assert bash_pkg.version == "5.2-2"
    assert bash_pkg.architecture == "amd64"
    assert bash_pkg.status == "installed"

    # Duration should be positive
    assert result.duration_seconds >= 0

    # Artifact path should match
    assert result.artifact_path == root

    # Progress should have been reported at 100%
    progress = context.progress
    assert any(pct == 100.0 for pct, _ in progress.reports)


@pytest.mark.asyncio
async def test_dpkg_status_absent_falls_back_to_filesystem(tmp_path) -> None:
    """Directory without dpkg status falls back to filesystem analysis.

    Requirements: 4.3
    """
    root = str(tmp_path)
    # Create some files but no dpkg status
    os.makedirs(os.path.join(root, "usr", "bin"), exist_ok=True)
    with open(os.path.join(root, "usr", "bin", "hello"), "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\necho hello\n")

    contents_port, package_port = _make_empty_ports()
    scanner = DirectoryScanner(contents_port, package_port)
    artifact = Artifact(type=ArtifactType.DIRECTORY, path=root, options={"snapshot_id": "0"})
    context = _make_context()

    result = await scanner.scan(artifact, context)

    # Strategy should be filesystem_analysis (fallback)
    assert result.strategy == ScanningStrategy.FILESYSTEM_ANALYSIS.value

    # With empty ports, no packages should be found
    assert result.packages == []

    # Should still complete without error
    assert result.duration_seconds >= 0
    assert result.artifact_path == root


@pytest.mark.asyncio
async def test_nonexistent_directory(tmp_path) -> None:
    """Non-existent directory path returns empty packages with diagnostic.

    Requirements: 4.4
    """
    nonexistent = str(tmp_path / "does_not_exist")

    contents_port, package_port = _make_empty_ports()
    scanner = DirectoryScanner(contents_port, package_port)
    artifact = Artifact(type=ArtifactType.DIRECTORY, path=nonexistent)
    context = _make_context()

    result = await scanner.scan(artifact, context)

    # Should return empty packages
    assert result.packages == []

    # Should have a diagnostic about access failure
    assert len(result.diagnostics) > 0
    diag_text = " ".join(result.diagnostics)
    assert "not accessible" in diag_text.lower() or "does not exist" in diag_text.lower()

    # Duration should be non-negative
    assert result.duration_seconds >= 0


@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="chmod 000 does not restrict access on Windows",
)
@pytest.mark.skipif(
    os.getuid() == 0 if hasattr(os, "getuid") else False,
    reason="Root user bypasses file permissions",
)
async def test_dpkg_status_unreadable_falls_back(tmp_path) -> None:
    """Dpkg status file that exists but is unreadable triggers filesystem fallback.

    Requirements: 4.4 (implied by 4.8 in design — unreadable status falls back)
    """
    root = str(tmp_path)
    status_path = _write_dpkg_status(root, SAMPLE_DPKG_STATUS)

    # Make the status file unreadable
    os.chmod(status_path, 0o000)
    try:
        contents_port, package_port = _make_empty_ports()
        scanner = DirectoryScanner(contents_port, package_port)
        artifact = Artifact(type=ArtifactType.DIRECTORY, path=root, options={"snapshot_id": "0"})
        context = _make_context()

        result = await scanner.scan(artifact, context)

        # Should fall back to filesystem analysis
        assert result.strategy == ScanningStrategy.FILESYSTEM_ANALYSIS.value
        assert result.duration_seconds >= 0
        assert result.artifact_path == root
    finally:
        # Restore permissions for cleanup
        os.chmod(status_path, 0o644)


@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Symlink behavior differs on Windows",
)
async def test_symlinks_outside_root_are_skipped(tmp_path) -> None:
    """Symlinks pointing outside the artifact root are skipped during scanning.

    Requirements: 4.7
    """
    # Create artifact root and an external directory
    root = tmp_path / "artifact_root"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()

    # Create an external file that should NOT be accessed
    external_file = external / "secret.txt"
    external_file.write_text("sensitive data")

    # Create a dpkg status dir with a symlink to the external directory
    dpkg_dir = root / "var" / "lib" / "dpkg"
    dpkg_dir.mkdir(parents=True)

    # Make var/lib/dpkg/status a symlink pointing outside root
    status_symlink = dpkg_dir / "status"
    status_symlink.symlink_to(str(external_file))

    contents_port, package_port = _make_empty_ports()
    scanner = DirectoryScanner(contents_port, package_port)
    artifact = Artifact(
        type=ArtifactType.DIRECTORY,
        path=str(root),
        options={"snapshot_id": "0"},
    )
    context = _make_context()

    result = await scanner.scan(artifact, context)

    # The scanner should detect the symlink escape and fall back
    # to filesystem analysis (not parse the external file as dpkg status)
    assert result.strategy == ScanningStrategy.FILESYSTEM_ANALYSIS.value
    # Should complete without error
    assert result.duration_seconds >= 0


@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Symlink behavior differs on Windows",
)
async def test_symlinks_in_tree_outside_root_skipped_during_walk(tmp_path) -> None:
    """Symlinks within the directory tree pointing outside root are skipped in fallback walk.

    Requirements: 4.7
    """
    # Create artifact root with valid dpkg missing (trigger fallback walk)
    root = tmp_path / "artifact_root"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()

    # Create some normal files in the root
    usr_bin = root / "usr" / "bin"
    usr_bin.mkdir(parents=True)
    (usr_bin / "ls").write_text("#!/bin/sh\n")

    # Create a symlink in the tree pointing outside root
    escape_link = usr_bin / "escape"
    escape_link.symlink_to(str(external))

    # Also create a normal file that should be picked up
    (usr_bin / "cat").write_text("#!/bin/sh\n")

    contents_port, package_port = _make_empty_ports()
    scanner = DirectoryScanner(contents_port, package_port)
    artifact = Artifact(
        type=ArtifactType.DIRECTORY,
        path=str(root),
        options={"snapshot_id": "0"},
    )
    context = _make_context()

    result = await scanner.scan(artifact, context)

    # Falls back to filesystem analysis (no dpkg status)
    assert result.strategy == ScanningStrategy.FILESYSTEM_ANALYSIS.value

    # The scanner should have walked the tree without following the external symlink.
    # Since os.walk with followlinks=False won't follow it and _is_safe_path also checks,
    # the scanner should complete successfully without error.
    assert result.duration_seconds >= 0
    assert result.artifact_path == str(root)

    # Verify that the contents port was called (filesystem analysis was attempted)
    # with paths that don't include anything from the external directory
    if contents_port.find_owners.called:
        call_args = contents_port.find_owners.call_args[0][0]
        for path in call_args:
            # No path should reference the external directory
            assert "external" not in path
