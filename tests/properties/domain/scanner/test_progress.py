"""Property-based tests for progress monotonicity.

# Feature: m6-artifact-scanners, Property 5: Progress Monotonicity

**Validates: Requirements 13.4, 13.5**

Property 5: Progress Monotonicity
  While a scan is in progress, the progress reporter receives percentage
  values that are monotonically non-decreasing (each >= previous). When
  the scan completes successfully without cancellation, the final progress
  value is exactly 100.0.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from unittest.mock import MagicMock

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.domain.scanner.values import Artifact, ArtifactType
from debcraft.infrastructure.scanners.directory import DirectoryScanner
from debcraft.platform.contracts.workflow import (
    CancellationToken,
    ProgressReporter,
    WorkflowContext,
)

pytestmark = [pytest.mark.unit]


# ===========================================================================
# Test helpers
# ===========================================================================


class _RecordingProgressReporter(ProgressReporter):
    """Progress reporter that records all (percentage, message) calls."""

    def __init__(self) -> None:
        self.reports: list[tuple[float, str]] = []

    def report(self, percentage: float, message: str = "") -> None:
        self.reports.append((percentage, message))


def _make_context() -> tuple[WorkflowContext, _RecordingProgressReporter]:
    """Create a mock WorkflowContext with a recording progress reporter."""
    token = CancellationToken()
    progress = _RecordingProgressReporter()
    ctx = MagicMock(spec=WorkflowContext)
    ctx.cancellation_token = token
    ctx.progress = progress
    return ctx, progress


# ===========================================================================
# Strategies: dpkg status file generation
# ===========================================================================

_PACKAGE_NAME_FIRST = st.sampled_from("abcdefghijklmnopqrstuvwxyz")
_PACKAGE_NAME_REST = st.text(
    st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789-"),
    min_size=1,
    max_size=20,
)

_ARCHITECTURES = st.sampled_from(["amd64", "arm64", "i386", "all"])


@st.composite
def st_package_name(draw: st.DrawFn) -> str:
    """Generate a valid Debian package name."""
    first = draw(_PACKAGE_NAME_FIRST)
    rest = draw(_PACKAGE_NAME_REST)
    name = first + rest.rstrip("-")
    return name if len(name) >= 2 else first + "a"


@st.composite
def st_version(draw: st.DrawFn) -> str:
    """Generate a valid dpkg version string."""
    major = draw(st.integers(min_value=0, max_value=99))
    minor = draw(st.integers(min_value=0, max_value=99))
    revision = draw(st.integers(min_value=1, max_value=9))
    return f"{major}.{minor}-{revision}"


@st.composite
def st_dpkg_stanza(draw: st.DrawFn) -> str:
    """Generate a single valid dpkg status stanza that will be parsed as installed."""
    name = draw(st_package_name())
    version = draw(st_version())
    arch = draw(_ARCHITECTURES)
    return f"Package: {name}\nStatus: install ok installed\nVersion: {version}\nArchitecture: {arch}\n"


@st.composite
def st_dpkg_status_file(draw: st.DrawFn) -> str:
    """Generate a dpkg status file with 1-50 package stanzas."""
    num_packages = draw(st.integers(min_value=1, max_value=50))
    stanzas = [draw(st_dpkg_stanza()) for _ in range(num_packages)]
    return "\n".join(stanzas)


# ===========================================================================
# Property 5: Progress Monotonicity
# ===========================================================================


@pytest.mark.asyncio
class TestProperty5ProgressMonotonicity:
    """Property 5: Progress Monotonicity.

    WHILE a scan is in progress, THE Artifact_Scanner implementation SHALL
    report progress via the WorkflowContext progress reporter as a percentage
    value from 0.0 to 100.0 that is monotonically non-decreasing.

    WHEN a scan completes successfully without cancellation, THE
    Artifact_Scanner implementation SHALL report a final progress value
    of 100.0 via the WorkflowContext progress reporter.

    **Validates: Requirements 13.4, 13.5**
    """

    @given(dpkg_content=st_dpkg_status_file())
    async def test_progress_is_monotonically_non_decreasing(
        self,
        dpkg_content: str,
    ) -> None:
        """All progress percentages reported during scan are non-decreasing.

        **Validates: Requirements 13.4**
        """
        tmp_dir = tempfile.mkdtemp()
        try:
            # Create directory structure with dpkg status file
            dpkg_dir = os.path.join(tmp_dir, "var", "lib", "dpkg")
            os.makedirs(dpkg_dir)
            status_path = os.path.join(dpkg_dir, "status")
            with open(status_path, "w") as f:
                f.write(dpkg_content)

            # Create scanner with mock ports
            contents_port = MagicMock()
            package_port = MagicMock()
            scanner = DirectoryScanner(
                contents_port=contents_port,
                package_port=package_port,
            )

            # Create context with recording progress reporter
            ctx, progress = _make_context()

            # Run the scan
            artifact = Artifact(type=ArtifactType.DIRECTORY, path=tmp_dir)
            await scanner.scan(artifact, ctx)

            # Assert progress reports are monotonically non-decreasing
            percentages = [p for p, _msg in progress.reports]
            for i in range(1, len(percentages)):
                assert percentages[i] >= percentages[i - 1], (
                    f"Progress decreased from {percentages[i - 1]} to "
                    f"{percentages[i]} at report index {i}. "
                    f"All reports: {progress.reports}"
                )
        finally:
            shutil.rmtree(tmp_dir)

    @given(dpkg_content=st_dpkg_status_file())
    async def test_final_progress_is_100_on_successful_completion(
        self,
        dpkg_content: str,
    ) -> None:
        """Final progress value is exactly 100.0 on successful completion.

        **Validates: Requirements 13.5**
        """
        tmp_dir = tempfile.mkdtemp()
        try:
            # Create directory structure with dpkg status file
            dpkg_dir = os.path.join(tmp_dir, "var", "lib", "dpkg")
            os.makedirs(dpkg_dir)
            status_path = os.path.join(dpkg_dir, "status")
            with open(status_path, "w") as f:
                f.write(dpkg_content)

            # Create scanner with mock ports
            contents_port = MagicMock()
            package_port = MagicMock()
            scanner = DirectoryScanner(
                contents_port=contents_port,
                package_port=package_port,
            )

            # Create context with recording progress reporter
            ctx, progress = _make_context()

            # Run the scan (no cancellation)
            artifact = Artifact(type=ArtifactType.DIRECTORY, path=tmp_dir)
            await scanner.scan(artifact, ctx)

            # Assert at least one progress report was made
            assert len(progress.reports) > 0, "No progress reports were made during scan"

            # Assert final progress is exactly 100.0
            final_percentage = progress.reports[-1][0]
            assert final_percentage == 100.0, (
                f"Final progress was {final_percentage}, expected 100.0. All reports: {progress.reports}"
            )
        finally:
            shutil.rmtree(tmp_dir)

    @given(dpkg_content=st_dpkg_status_file())
    async def test_progress_values_within_valid_range(
        self,
        dpkg_content: str,
    ) -> None:
        """All progress percentages are within [0.0, 100.0] range.

        **Validates: Requirements 13.4**
        """
        tmp_dir = tempfile.mkdtemp()
        try:
            # Create directory structure with dpkg status file
            dpkg_dir = os.path.join(tmp_dir, "var", "lib", "dpkg")
            os.makedirs(dpkg_dir)
            status_path = os.path.join(dpkg_dir, "status")
            with open(status_path, "w") as f:
                f.write(dpkg_content)

            # Create scanner with mock ports
            contents_port = MagicMock()
            package_port = MagicMock()
            scanner = DirectoryScanner(
                contents_port=contents_port,
                package_port=package_port,
            )

            # Create context with recording progress reporter
            ctx, progress = _make_context()

            # Run the scan
            artifact = Artifact(type=ArtifactType.DIRECTORY, path=tmp_dir)
            await scanner.scan(artifact, ctx)

            # Assert all percentages are in valid range
            for percentage, msg in progress.reports:
                assert 0.0 <= percentage <= 100.0, (
                    f"Progress {percentage} out of valid range [0.0, 100.0]. Message: {msg!r}"
                )
        finally:
            shutil.rmtree(tmp_dir)
