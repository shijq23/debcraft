"""Property-based tests for cancellation producing a valid subset.

**Validates: Requirements 4.5, 13.1, 13.2, 13.3**

Property 4: Cancellation Produces Valid Subset
  When a scan is cancelled at a random point during processing, the partial
  result packages shall be a prefix of the full (uncancelled) result, and a
  diagnostic message mentioning cancellation shall be present.
"""

from __future__ import annotations

import os
import tempfile
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.domain.scanner.values import Artifact, ArtifactType
from debcraft.infrastructure.scanners.directory import DirectoryScanner
from debcraft.platform.contracts.workflow import (
    CancellationToken,
    ProgressReporter,
    WorkflowContext,
)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class RecordingProgressReporter(ProgressReporter):
    """Progress reporter that records all calls."""

    def __init__(self) -> None:
        self.reports: list[tuple[float, str]] = []

    def report(self, percentage: float, message: str = "") -> None:
        self.reports.append((percentage, message))


class DelayedCancellationToken:
    """A cancellation token that flips to cancelled after K checks.

    Each read of `is_cancelled` increments the check counter. After K
    checks have occurred, it returns True.
    """

    def __init__(self, cancel_after: int) -> None:
        self._cancel_after = cancel_after
        self._check_count = 0

    @property
    def is_cancelled(self) -> bool:
        self._check_count += 1
        return self._check_count > self._cancel_after

    def cancel(self) -> None:
        self._cancel_after = 0


def _make_context(
    cancellation_token: CancellationToken | DelayedCancellationToken,
) -> WorkflowContext:
    """Create a minimal WorkflowContext with the given cancellation token."""
    ctx = WorkflowContext.__new__(WorkflowContext)
    ctx.scope = AsyncMock()
    ctx.cancellation_token = cancellation_token
    ctx.progress = RecordingProgressReporter()
    ctx.resources = AsyncMock()
    ctx.logger = AsyncMock()
    ctx.event_bus = AsyncMock()
    return ctx


def _make_dpkg_stanza(name: str, version: str, arch: str = "amd64") -> str:
    """Create a single valid dpkg status stanza text."""
    return f"Package: {name}\nStatus: install ok installed\nVersion: {version}\nArchitecture: {arch}\n"


def _make_dpkg_status_content(num_packages: int) -> str:
    """Generate a dpkg status file with N unique packages."""
    stanzas = []
    for i in range(num_packages):
        stanzas.append(
            _make_dpkg_stanza(
                name=f"pkg-{i:04d}",
                version=f"1.{i}-1",
                arch="amd64",
            )
        )
    return "\n".join(stanzas)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Number of packages in the dpkg status file (at least 2 so cancellation
# can occur mid-way)
st_num_packages = st.integers(min_value=2, max_value=50)


@st.composite
def st_cancellation_point(draw: st.DrawFn) -> tuple[int, int]:
    """Generate (num_packages, cancel_after_k) where 1 <= K < N.

    Returns a tuple of the total number of packages and the point
    after which cancellation occurs (K packages will be processed).
    """
    num_packages = draw(st.integers(min_value=2, max_value=50))
    # Cancel after K checks — K is between 1 and num_packages - 1
    # so that we get a partial result (not empty, not full)
    cancel_after_k = draw(st.integers(min_value=1, max_value=num_packages - 1))
    return (num_packages, cancel_after_k)


# ---------------------------------------------------------------------------
# Property 4: Cancellation Produces Valid Subset
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestProperty4CancellationProducesValidSubset:
    """Property 4: Cancellation Produces Valid Subset.

    When a directory scan is cancelled at a random point during dpkg status
    parsing, the partial result is a prefix of the full (uncancelled) result,
    and a diagnostic mentioning cancellation is present.

    **Validates: Requirements 4.5, 13.1, 13.2, 13.3**
    """

    @settings(deadline=None)
    @given(data=st_cancellation_point())
    async def test_cancelled_result_is_prefix_of_full_result(self, data: tuple[int, int]) -> None:
        """Partial result from cancellation is a prefix of the full result.

        **Validates: Requirements 4.5, 13.1, 13.2, 13.3**
        """
        num_packages, cancel_after_k = data

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the dpkg status file in the temp directory
            dpkg_dir = os.path.join(tmpdir, "var", "lib", "dpkg")
            os.makedirs(dpkg_dir)
            status_content = _make_dpkg_status_content(num_packages)
            status_path = os.path.join(dpkg_dir, "status")
            with open(status_path, "w", encoding="utf-8") as f:
                f.write(status_content)

            # Create scanner (ports not needed for dpkg metadata path)
            scanner = DirectoryScanner(
                contents_port=AsyncMock(),
                package_port=AsyncMock(),
            )

            # First scan WITHOUT cancellation to get the full result
            no_cancel_token = CancellationToken()
            full_context = _make_context(no_cancel_token)
            artifact = Artifact(type=ArtifactType.DIRECTORY, path=tmpdir)
            full_result = await scanner.scan(artifact, full_context)

            # Verify we got all packages in the full result
            assert len(full_result.packages) == num_packages

            # Second scan WITH cancellation after K packages
            cancel_token = DelayedCancellationToken(cancel_after=cancel_after_k)
            cancel_context = _make_context(cancel_token)
            cancelled_result = await scanner.scan(artifact, cancel_context)

            # Assert: cancelled result packages is a PREFIX of the full result
            partial_count = len(cancelled_result.packages)
            assert partial_count <= num_packages, (
                f"Cancelled result has {partial_count} packages, but full result has {num_packages}"
            )
            assert partial_count <= cancel_after_k, (
                f"Cancelled result has {partial_count} packages, "
                f"but should have at most {cancel_after_k} "
                f"(cancel_after_k)"
            )

            # The partial packages must be a prefix of the full packages
            for i in range(partial_count):
                assert cancelled_result.packages[i] == full_result.packages[i], (
                    f"Package at index {i} mismatch: "
                    f"cancelled={cancelled_result.packages[i]} != "
                    f"full={full_result.packages[i]}"
                )

    @settings(deadline=None)
    @given(data=st_cancellation_point())
    async def test_cancelled_result_has_cancellation_diagnostic(self, data: tuple[int, int]) -> None:
        """A cancelled scan includes a diagnostic mentioning cancellation.

        **Validates: Requirements 13.2, 13.3**
        """
        num_packages, cancel_after_k = data

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the dpkg status file
            dpkg_dir = os.path.join(tmpdir, "var", "lib", "dpkg")
            os.makedirs(dpkg_dir)
            status_content = _make_dpkg_status_content(num_packages)
            status_path = os.path.join(dpkg_dir, "status")
            with open(status_path, "w", encoding="utf-8") as f:
                f.write(status_content)

            scanner = DirectoryScanner(
                contents_port=AsyncMock(),
                package_port=AsyncMock(),
            )
            artifact = Artifact(type=ArtifactType.DIRECTORY, path=tmpdir)

            # Scan with cancellation
            cancel_token = DelayedCancellationToken(cancel_after=cancel_after_k)
            cancel_context = _make_context(cancel_token)
            cancelled_result = await scanner.scan(artifact, cancel_context)

            # Assert: a diagnostic mentions cancellation
            cancel_diagnostics = [
                d for d in cancelled_result.diagnostics if "cancel" in d.lower() or "cancelled" in d.lower()
            ]
            assert len(cancel_diagnostics) >= 1, (
                f"Expected at least one diagnostic mentioning cancellation, "
                f"got diagnostics: {cancelled_result.diagnostics}"
            )

    @settings(deadline=None)
    @given(num_packages=st.integers(min_value=1, max_value=50))
    async def test_no_cancellation_returns_all_packages(self, num_packages: int) -> None:
        """Without cancellation, the full scan returns all packages.

        **Validates: Requirements 4.5**
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the dpkg status file
            dpkg_dir = os.path.join(tmpdir, "var", "lib", "dpkg")
            os.makedirs(dpkg_dir)
            status_content = _make_dpkg_status_content(num_packages)
            status_path = os.path.join(dpkg_dir, "status")
            with open(status_path, "w", encoding="utf-8") as f:
                f.write(status_content)

            scanner = DirectoryScanner(
                contents_port=AsyncMock(),
                package_port=AsyncMock(),
            )
            artifact = Artifact(type=ArtifactType.DIRECTORY, path=tmpdir)

            # Scan without cancellation
            no_cancel_token = CancellationToken()
            full_context = _make_context(no_cancel_token)
            result = await scanner.scan(artifact, full_context)

            # All packages should be present
            assert len(result.packages) == num_packages

            # No cancellation diagnostic should be present
            cancel_diagnostics = [d for d in result.diagnostics if "cancel" in d.lower() or "cancelled" in d.lower()]
            assert len(cancel_diagnostics) == 0, (
                f"Unexpected cancellation diagnostic in full scan: {cancel_diagnostics}"
            )
