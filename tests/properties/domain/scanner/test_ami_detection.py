r"""Property-based tests for AMI format detection correctness.

**Validates: Requirements 10.1, 10.2, 10.3, 10.4**

Property 8: AMI Format Detection Correctness
  For any file whose first 4 bytes are the QCOW2 magic QFI\\xfb, the AMI
  scanner SHALL delegate to the QCOW2 scanner. For any file whose first 4
  bytes are NOT the QCOW2 magic, the AMI scanner SHALL delegate to the IMG
  scanner. The resulting ScanResult SHALL be the unmodified result from
  the delegate.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.domain.scanner.values import (
    Artifact,
    ArtifactType,
    IdentifiedPackage,
    ScanningStrategy,
    ScanResult,
)
from debcraft.infrastructure.scanners.ami import QCOW2_MAGIC, AMIScanner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workflow_context(*, cancelled: bool = False) -> MagicMock:
    """Create a mock WorkflowContext with configurable cancellation."""
    context = MagicMock()
    context.cancellation_token.is_cancelled = cancelled
    context.progress.report = MagicMock()
    return context


@dataclass
class DelegationTracker:
    """Tracks which scanner was called and with what arguments."""

    called: bool = False
    artifact: Artifact | None = None
    context: object | None = None
    result: ScanResult | None = None


def _make_scan_result(
    packages: list[IdentifiedPackage] | None = None,
    strategy: str = ScanningStrategy.DPKG_METADATA.value,
    diagnostics: list[str] | None = None,
    duration: float = 1.23,
    artifact_path: str = "/tmp/test.img",
) -> ScanResult:
    """Create a ScanResult with configurable fields."""
    return ScanResult(
        packages=packages or [],
        strategy=strategy,
        diagnostics=diagnostics or [],
        duration_seconds=duration,
        artifact_path=artifact_path,
    )


def _make_ami_scanner_with_trackers(
    qcow2_result: ScanResult,
    img_result: ScanResult,
) -> tuple[AMIScanner, DelegationTracker, DelegationTracker]:
    """Create an AMIScanner with mock sub-scanners that track calls.

    Returns the scanner and trackers for both sub-scanners.
    """
    qcow2_tracker = DelegationTracker(result=qcow2_result)
    img_tracker = DelegationTracker(result=img_result)

    async def qcow2_scan(artifact: Artifact, context: object) -> ScanResult:
        qcow2_tracker.called = True
        qcow2_tracker.artifact = artifact
        qcow2_tracker.context = context
        assert qcow2_tracker.result is not None
        return qcow2_tracker.result

    async def img_scan(artifact: Artifact, context: object) -> ScanResult:
        img_tracker.called = True
        img_tracker.artifact = artifact
        img_tracker.context = context
        assert img_tracker.result is not None
        return img_tracker.result

    qcow2_scanner = MagicMock()
    qcow2_scanner.scan = qcow2_scan

    img_scanner = MagicMock()
    img_scanner.scan = img_scan

    scanner = AMIScanner(
        qcow2_scanner=qcow2_scanner,  # type: ignore[arg-type]
        img_scanner=img_scanner,  # type: ignore[arg-type]
    )

    return scanner, qcow2_tracker, img_tracker


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


@st.composite
def st_non_qcow2_header(draw: st.DrawFn) -> bytes:
    """Generate a 4+ byte header that is NOT the QCOW2 magic.

    Generates arbitrary binary data of 4-64 bytes, filtering out
    any that happen to start with the QCOW2 magic bytes.
    """
    data = draw(st.binary(min_size=4, max_size=64))
    # Ensure the first 4 bytes are NOT QCOW2 magic
    if data[:4] == QCOW2_MAGIC:
        # Flip the first byte to guarantee it differs
        flipped = bytes([data[0] ^ 0xFF]) + data[1:]
        return flipped
    return data


@st.composite
def st_qcow2_header(draw: st.DrawFn) -> bytes:
    """Generate a file header starting with QCOW2 magic bytes.

    Appends 0-60 random bytes after the magic to simulate a real header.
    """
    suffix = draw(st.binary(min_size=0, max_size=60))
    return QCOW2_MAGIC + suffix


@st.composite
def st_scan_result(draw: st.DrawFn) -> ScanResult:
    """Generate a random but valid ScanResult for delegation testing."""
    num_packages = draw(st.integers(min_value=0, max_value=5))
    packages = []
    for _ in range(num_packages):
        pkg = IdentifiedPackage(
            name=draw(
                st.text(
                    st.characters(whitelist_categories=("Ll", "Nd")),
                    min_size=1,
                    max_size=20,
                )
            ),
            version=draw(st.from_regex(r"[0-9]+\.[0-9]+", fullmatch=True)),
            architecture=draw(st.sampled_from(["amd64", "arm64", "i386", "all"])),
            status=draw(st.sampled_from(["installed", "config-files", "inferred"])),
        )
        packages.append(pkg)

    strategy = draw(
        st.sampled_from(
            [
                ScanningStrategy.DPKG_METADATA.value,
                ScanningStrategy.FILESYSTEM_ANALYSIS.value,
            ]
        )
    )

    num_diagnostics = draw(st.integers(min_value=0, max_value=3))
    diagnostics = [draw(st.text(min_size=1, max_size=50)) for _ in range(num_diagnostics)]

    duration = draw(st.floats(min_value=0.0, max_value=100.0, allow_nan=False))
    artifact_path = draw(st.text(min_size=1, max_size=100))

    return ScanResult(
        packages=packages,
        strategy=strategy,
        diagnostics=diagnostics,
        duration_seconds=duration,
        artifact_path=artifact_path,
    )


# ---------------------------------------------------------------------------
# Property 8: AMI Format Detection Correctness
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProperty8AMIFormatDetection:
    """Property 8: AMI Format Detection Correctness.

    Tests that the AMIScanner correctly detects QCOW2 vs raw format
    and delegates to the appropriate sub-scanner, propagating the
    ScanResult unchanged.
    """

    @given(
        header=st_qcow2_header(),
        qcow2_result=st_scan_result(),
        img_result=st_scan_result(),
    )
    @pytest.mark.asyncio
    async def test_qcow2_magic_delegates_to_qcow2_scanner(
        self,
        header: bytes,
        qcow2_result: ScanResult,
        img_result: ScanResult,
    ) -> None:
        """Files starting with QCOW2 magic delegate to QCOW2Scanner.

        **Validates: Requirements 10.1, 10.2**
        """
        scanner, qcow2_tracker, img_tracker = _make_ami_scanner_with_trackers(
            qcow2_result=qcow2_result,
            img_result=img_result,
        )

        # Create a temp file with the QCOW2 header
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ami") as f:
            f.write(header)
            temp_path = f.name

        try:
            artifact = Artifact(
                type=ArtifactType.AMI,
                path=temp_path,
                options={},
            )
            context = _make_workflow_context()

            result = await scanner.scan(artifact, context)

            # QCOW2 scanner should have been called
            assert qcow2_tracker.called, "QCOW2Scanner should be called for files with QCOW2 magic header"
            # IMG scanner should NOT have been called
            assert not img_tracker.called, "IMGScanner should NOT be called for files with QCOW2 magic header"
            # Result should be the QCOW2 scanner's result, unchanged
            assert result is qcow2_result, "ScanResult should be propagated unchanged from QCOW2Scanner"
        finally:
            os.unlink(temp_path)

    @given(
        header=st_non_qcow2_header(),
        qcow2_result=st_scan_result(),
        img_result=st_scan_result(),
    )
    @pytest.mark.asyncio
    async def test_non_qcow2_magic_delegates_to_img_scanner(
        self,
        header: bytes,
        qcow2_result: ScanResult,
        img_result: ScanResult,
    ) -> None:
        """Files NOT starting with QCOW2 magic delegate to IMGScanner.

        **Validates: Requirements 10.1, 10.3**
        """
        scanner, qcow2_tracker, img_tracker = _make_ami_scanner_with_trackers(
            qcow2_result=qcow2_result,
            img_result=img_result,
        )

        # Create a temp file with the non-QCOW2 header
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ami") as f:
            f.write(header)
            temp_path = f.name

        try:
            artifact = Artifact(
                type=ArtifactType.AMI,
                path=temp_path,
                options={},
            )
            context = _make_workflow_context()

            result = await scanner.scan(artifact, context)

            # IMG scanner should have been called
            assert img_tracker.called, "IMGScanner should be called for files without QCOW2 magic header"
            # QCOW2 scanner should NOT have been called
            assert not qcow2_tracker.called, "QCOW2Scanner should NOT be called for files without QCOW2 magic"
            # Result should be the IMG scanner's result, unchanged
            assert result is img_result, "ScanResult should be propagated unchanged from IMGScanner"
        finally:
            os.unlink(temp_path)

    @given(
        header=st_qcow2_header(),
        expected_result=st_scan_result(),
    )
    @pytest.mark.asyncio
    async def test_scan_result_propagated_unmodified_qcow2(
        self,
        header: bytes,
        expected_result: ScanResult,
    ) -> None:
        """ScanResult from QCOW2 delegate is propagated with all fields intact.

        **Validates: Requirements 10.4**
        """
        scanner, _qcow2_tracker, _ = _make_ami_scanner_with_trackers(
            qcow2_result=expected_result,
            img_result=_make_scan_result(),
        )

        with tempfile.NamedTemporaryFile(delete=False, suffix=".ami") as f:
            f.write(header)
            temp_path = f.name

        try:
            artifact = Artifact(
                type=ArtifactType.AMI,
                path=temp_path,
                options={},
            )
            context = _make_workflow_context()

            result = await scanner.scan(artifact, context)

            # Verify all fields are propagated unchanged
            assert result.packages == expected_result.packages
            assert result.strategy == expected_result.strategy
            assert result.diagnostics == expected_result.diagnostics
            assert result.duration_seconds == expected_result.duration_seconds
            assert result.artifact_path == expected_result.artifact_path
        finally:
            os.unlink(temp_path)

    @given(
        header=st_non_qcow2_header(),
        expected_result=st_scan_result(),
    )
    @pytest.mark.asyncio
    async def test_scan_result_propagated_unmodified_img(
        self,
        header: bytes,
        expected_result: ScanResult,
    ) -> None:
        """ScanResult from IMG delegate is propagated with all fields intact.

        **Validates: Requirements 10.4**
        """
        scanner, _, _img_tracker = _make_ami_scanner_with_trackers(
            qcow2_result=_make_scan_result(),
            img_result=expected_result,
        )

        with tempfile.NamedTemporaryFile(delete=False, suffix=".ami") as f:
            f.write(header)
            temp_path = f.name

        try:
            artifact = Artifact(
                type=ArtifactType.AMI,
                path=temp_path,
                options={},
            )
            context = _make_workflow_context()

            result = await scanner.scan(artifact, context)

            # Verify all fields are propagated unchanged
            assert result.packages == expected_result.packages
            assert result.strategy == expected_result.strategy
            assert result.diagnostics == expected_result.diagnostics
            assert result.duration_seconds == expected_result.duration_seconds
            assert result.artifact_path == expected_result.artifact_path
        finally:
            os.unlink(temp_path)
