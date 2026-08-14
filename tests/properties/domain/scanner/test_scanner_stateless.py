"""Property-based tests for scanner statelessness.

**Validates: Requirements 1.7**

Property 3: Scanner Statelessness
  THE Artifact_Scanner protocol SHALL be stateless such that calling `scan`
  multiple times with the same Artifact and WorkflowContext produces ScanResult
  values with identical Identified_Package lists, identical Scanning_Strategy,
  and identical artifact path fields.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.domain.scanner.values import Artifact, ArtifactType
from debcraft.infrastructure.scanners.directory import DirectoryScanner

pytestmark = [pytest.mark.unit]


# ===========================================================================
# Strategies
# ===========================================================================

_PACKAGE_NAME_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789-"


@st.composite
def st_package_name(draw: st.DrawFn) -> str:
    """Generate a valid Debian package name."""
    first = draw(st.sampled_from("abcdefghijklmnopqrstuvwxyz"))
    rest = draw(st.text(alphabet=_PACKAGE_NAME_CHARS, min_size=1, max_size=20))
    return first + rest.rstrip("-") or first + "a"


@st.composite
def st_version(draw: st.DrawFn) -> str:
    """Generate a valid dpkg version string."""
    major = draw(st.integers(min_value=0, max_value=99))
    minor = draw(st.integers(min_value=0, max_value=99))
    revision = draw(st.integers(min_value=1, max_value=9))
    return f"{major}.{minor}-{revision}"


_ARCHITECTURES = st.sampled_from(["amd64", "arm64", "i386", "all"])

_INCLUDED_STATUSES = st.sampled_from(
    [
        "install ok installed",
        "hold ok installed",
        "install ok config-files",
        "hold ok config-files",
    ]
)


@st.composite
def st_dpkg_stanza(draw: st.DrawFn) -> str:
    """Generate a single valid dpkg status stanza as text."""
    name = draw(st_package_name())
    status = draw(_INCLUDED_STATUSES)
    version = draw(st_version())
    arch = draw(_ARCHITECTURES)
    return f"Package: {name}\nStatus: {status}\nVersion: {version}\nArchitecture: {arch}\n"


@st.composite
def st_dpkg_status_content(draw: st.DrawFn) -> str:
    """Generate dpkg status file content with a varying number of packages."""
    num_packages = draw(st.integers(min_value=1, max_value=15))
    stanzas = [draw(st_dpkg_stanza()) for _ in range(num_packages)]
    return "\n".join(stanzas)


# ===========================================================================
# Helpers
# ===========================================================================


def _make_workflow_context() -> MagicMock:
    """Create a mock WorkflowContext with cancellation disabled."""
    context = MagicMock()
    context.cancellation_token.is_cancelled = False
    context.progress.report = MagicMock()
    return context


def _make_scanner() -> DirectoryScanner:
    """Create a DirectoryScanner with mock ports."""
    contents_port = AsyncMock()
    contents_port.find_owners = AsyncMock(return_value={})
    package_port = AsyncMock()
    package_port.find_by_name = AsyncMock(return_value=None)
    return DirectoryScanner(
        contents_port=contents_port,
        package_port=package_port,
    )


# ===========================================================================
# Property 3: Scanner Statelessness
# ===========================================================================


@pytest.mark.unit
class TestProperty3ScannerStatelessness:
    """Property 3: Scanner Statelessness.

    Calling scan multiple times with the same Artifact and WorkflowContext
    (without cancellation) produces ScanResult values with identical
    packages, strategy, and artifact_path.

    **Validates: Requirements 1.7**
    """

    @given(content=st_dpkg_status_content())
    @pytest.mark.asyncio
    async def test_scan_twice_produces_identical_packages(self, content: str) -> None:
        """Scanning the same artifact twice yields identical package lists.

        **Validates: Requirements 1.7**
        """
        tmp_dir = tempfile.mkdtemp()
        try:
            # Create dpkg status file in temp directory
            dpkg_dir = os.path.join(tmp_dir, "var", "lib", "dpkg")
            os.makedirs(dpkg_dir)
            status_path = os.path.join(dpkg_dir, "status")
            with open(status_path, "w", encoding="utf-8") as f:
                f.write(content)

            artifact = Artifact(
                type=ArtifactType.DIRECTORY,
                path=tmp_dir,
                options={},
            )
            scanner = _make_scanner()
            context1 = _make_workflow_context()
            context2 = _make_workflow_context()

            result1 = await scanner.scan(artifact, context1)
            result2 = await scanner.scan(artifact, context2)

            assert result1.packages == result2.packages
        finally:
            shutil.rmtree(tmp_dir)

    @given(content=st_dpkg_status_content())
    @pytest.mark.asyncio
    async def test_scan_twice_produces_identical_strategy(self, content: str) -> None:
        """Scanning the same artifact twice yields identical strategy.

        **Validates: Requirements 1.7**
        """
        tmp_dir = tempfile.mkdtemp()
        try:
            dpkg_dir = os.path.join(tmp_dir, "var", "lib", "dpkg")
            os.makedirs(dpkg_dir)
            status_path = os.path.join(dpkg_dir, "status")
            with open(status_path, "w", encoding="utf-8") as f:
                f.write(content)

            artifact = Artifact(
                type=ArtifactType.DIRECTORY,
                path=tmp_dir,
                options={},
            )
            scanner = _make_scanner()
            context1 = _make_workflow_context()
            context2 = _make_workflow_context()

            result1 = await scanner.scan(artifact, context1)
            result2 = await scanner.scan(artifact, context2)

            assert result1.strategy == result2.strategy
        finally:
            shutil.rmtree(tmp_dir)

    @given(content=st_dpkg_status_content())
    @pytest.mark.asyncio
    async def test_scan_twice_produces_identical_artifact_path(self, content: str) -> None:
        """Scanning the same artifact twice yields identical artifact_path.

        **Validates: Requirements 1.7**
        """
        tmp_dir = tempfile.mkdtemp()
        try:
            dpkg_dir = os.path.join(tmp_dir, "var", "lib", "dpkg")
            os.makedirs(dpkg_dir)
            status_path = os.path.join(dpkg_dir, "status")
            with open(status_path, "w", encoding="utf-8") as f:
                f.write(content)

            artifact = Artifact(
                type=ArtifactType.DIRECTORY,
                path=tmp_dir,
                options={},
            )
            scanner = _make_scanner()
            context1 = _make_workflow_context()
            context2 = _make_workflow_context()

            result1 = await scanner.scan(artifact, context1)
            result2 = await scanner.scan(artifact, context2)

            assert result1.artifact_path == result2.artifact_path
        finally:
            shutil.rmtree(tmp_dir)
