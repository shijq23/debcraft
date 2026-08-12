"""Unit tests for AMI scanner."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from debcraft.domain.scanner.values import (
    Artifact,
    ArtifactType,
    IdentifiedPackage,
    ScanningStrategy,
    ScanResult,
)
from debcraft.infrastructure.scanners.ami import AMIScanner
from debcraft.infrastructure.scanners.qcow2 import QCOW2_MAGIC
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


def _make_scan_result(
    *,
    packages: list[IdentifiedPackage] | None = None,
    strategy: str = ScanningStrategy.DPKG_METADATA.value,
    diagnostics: list[str] | None = None,
    duration: float = 0.5,
    path: str = "/tmp/test.ami",
) -> ScanResult:
    """Create a ScanResult for testing delegation."""
    return ScanResult(
        packages=packages or [],
        strategy=strategy,
        diagnostics=diagnostics or [],
        duration_seconds=duration,
        artifact_path=path,
    )


class TestAMIFormatDetection:
    """Tests for AMI format detection (Req 10.1, 10.2, 10.3)."""

    @pytest.mark.asyncio
    async def test_qcow2_magic_delegates_to_qcow2_scanner(self) -> None:
        """QCOW2 magic bytes → delegate to QCOW2Scanner (Req 10.2)."""
        with tempfile.NamedTemporaryFile(suffix=".ami", delete=False) as f:
            f.write(QCOW2_MAGIC + b"\x00" * 100)
            path = f.name

        try:
            expected_result = _make_scan_result(
                packages=[
                    IdentifiedPackage(
                        name="bash",
                        version="5.2-1",
                        architecture="amd64",
                        status="installed",
                    )
                ],
                path=path,
            )
            qcow2_scanner = MagicMock()
            qcow2_scanner.scan = AsyncMock(return_value=expected_result)
            img_scanner = MagicMock()
            img_scanner.scan = AsyncMock()

            scanner = AMIScanner(
                qcow2_scanner=qcow2_scanner,
                img_scanner=img_scanner,
            )
            artifact = Artifact(type=ArtifactType.AMI, path=path)
            ctx = _make_context()

            result = await scanner.scan(artifact, ctx)

            qcow2_scanner.scan.assert_called_once_with(artifact, ctx)
            img_scanner.scan.assert_not_called()
            assert result is expected_result
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_non_qcow2_delegates_to_img_scanner(self) -> None:
        """Non-QCOW2 header → delegate to IMGScanner (Req 10.3)."""
        with tempfile.NamedTemporaryFile(suffix=".ami", delete=False) as f:
            f.write(b"\x00\x00\x00\x00" + b"\x00" * 100)
            path = f.name

        try:
            expected_result = _make_scan_result(
                packages=[
                    IdentifiedPackage(
                        name="coreutils",
                        version="9.1-1",
                        architecture="amd64",
                        status="installed",
                    )
                ],
                strategy=ScanningStrategy.DPKG_METADATA.value,
                path=path,
            )
            qcow2_scanner = MagicMock()
            qcow2_scanner.scan = AsyncMock()
            img_scanner = MagicMock()
            img_scanner.scan = AsyncMock(return_value=expected_result)

            scanner = AMIScanner(
                qcow2_scanner=qcow2_scanner,
                img_scanner=img_scanner,
            )
            artifact = Artifact(type=ArtifactType.AMI, path=path)
            ctx = _make_context()

            result = await scanner.scan(artifact, ctx)

            img_scanner.scan.assert_called_once_with(artifact, ctx)
            qcow2_scanner.scan.assert_not_called()
            assert result is expected_result
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_random_bytes_delegates_to_img_scanner(self) -> None:
        """Random non-QCOW2 bytes → delegate to IMGScanner (Req 10.3)."""
        with tempfile.NamedTemporaryFile(suffix=".ami", delete=False) as f:
            f.write(b"\xde\xad\xbe\xef" + b"\x00" * 100)
            path = f.name

        try:
            expected_result = _make_scan_result(path=path)
            qcow2_scanner = MagicMock()
            qcow2_scanner.scan = AsyncMock()
            img_scanner = MagicMock()
            img_scanner.scan = AsyncMock(return_value=expected_result)

            scanner = AMIScanner(
                qcow2_scanner=qcow2_scanner,
                img_scanner=img_scanner,
            )
            artifact = Artifact(type=ArtifactType.AMI, path=path)
            ctx = _make_context()

            result = await scanner.scan(artifact, ctx)

            img_scanner.scan.assert_called_once_with(artifact, ctx)
            qcow2_scanner.scan.assert_not_called()
            assert result is expected_result
        finally:
            os.unlink(path)


class TestAMIResultPropagation:
    """Tests for ScanResult propagation (Req 10.4)."""

    @pytest.mark.asyncio
    async def test_propagates_all_fields_unchanged(self) -> None:
        """ScanResult from delegate is returned unchanged (Req 10.4)."""
        with tempfile.NamedTemporaryFile(suffix=".ami", delete=False) as f:
            f.write(QCOW2_MAGIC + b"\x00" * 100)
            path = f.name

        try:
            packages = [
                IdentifiedPackage(name="pkg1", version="1.0", architecture="amd64", status="installed"),
                IdentifiedPackage(name="pkg2", version="2.0", architecture="arm64", status="config-files"),
            ]
            expected_result = ScanResult(
                packages=packages,
                strategy=ScanningStrategy.FILESYSTEM_ANALYSIS.value,
                diagnostics=["some warning", "another note"],
                duration_seconds=1.234,
                artifact_path=path,
            )
            qcow2_scanner = MagicMock()
            qcow2_scanner.scan = AsyncMock(return_value=expected_result)
            img_scanner = MagicMock()

            scanner = AMIScanner(
                qcow2_scanner=qcow2_scanner,
                img_scanner=img_scanner,
            )
            artifact = Artifact(type=ArtifactType.AMI, path=path)
            ctx = _make_context()

            result = await scanner.scan(artifact, ctx)

            # Verify all fields are propagated unchanged
            assert result.packages == expected_result.packages
            assert result.strategy == expected_result.strategy
            assert result.diagnostics == expected_result.diagnostics
            assert result.duration_seconds == expected_result.duration_seconds
            assert result.artifact_path == expected_result.artifact_path
            # Same object reference — no copying
            assert result is expected_result
        finally:
            os.unlink(path)


class TestAMIFileErrors:
    """Tests for file access error handling (Req 10.5)."""

    @pytest.mark.asyncio
    async def test_file_not_exists_returns_diagnostic(self) -> None:
        """Non-existent file → empty packages + diagnostic (Req 10.5)."""
        qcow2_scanner = MagicMock()
        img_scanner = MagicMock()

        scanner = AMIScanner(
            qcow2_scanner=qcow2_scanner,
            img_scanner=img_scanner,
        )
        artifact = Artifact(type=ArtifactType.AMI, path="/tmp/nonexistent_ami_file.img")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert len(result.diagnostics) == 1
        assert "/tmp/nonexistent_ami_file.img" in result.diagnostics[0]
        assert result.artifact_path == "/tmp/nonexistent_ami_file.img"

    @pytest.mark.asyncio
    async def test_file_too_small_returns_diagnostic(self) -> None:
        """File < 4 bytes → empty packages + diagnostic (Req 10.5)."""
        with tempfile.NamedTemporaryFile(suffix=".ami", delete=False) as f:
            f.write(b"\x00\x01")  # Only 2 bytes
            path = f.name

        try:
            qcow2_scanner = MagicMock()
            img_scanner = MagicMock()

            scanner = AMIScanner(
                qcow2_scanner=qcow2_scanner,
                img_scanner=img_scanner,
            )
            artifact = Artifact(type=ArtifactType.AMI, path=path)
            ctx = _make_context()

            result = await scanner.scan(artifact, ctx)

            assert result.packages == []
            assert len(result.diagnostics) == 1
            assert "got 2" in result.diagnostics[0]
            assert result.artifact_path == path
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_empty_file_returns_diagnostic(self) -> None:
        """Empty file (0 bytes) → empty packages + diagnostic (Req 10.5)."""
        with tempfile.NamedTemporaryFile(suffix=".ami", delete=False) as f:
            path = f.name
            # Write nothing — 0-byte file

        try:
            qcow2_scanner = MagicMock()
            img_scanner = MagicMock()

            scanner = AMIScanner(
                qcow2_scanner=qcow2_scanner,
                img_scanner=img_scanner,
            )
            artifact = Artifact(type=ArtifactType.AMI, path=path)
            ctx = _make_context()

            result = await scanner.scan(artifact, ctx)

            assert result.packages == []
            assert len(result.diagnostics) == 1
            assert "got 0" in result.diagnostics[0]
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_unreadable_file_returns_diagnostic(self) -> None:
        """Unreadable file → empty packages + diagnostic (Req 10.5)."""
        with tempfile.NamedTemporaryFile(suffix=".ami", delete=False) as f:
            f.write(QCOW2_MAGIC + b"\x00" * 100)
            path = f.name

        try:
            os.chmod(path, 0o000)
            qcow2_scanner = MagicMock()
            img_scanner = MagicMock()

            scanner = AMIScanner(
                qcow2_scanner=qcow2_scanner,
                img_scanner=img_scanner,
            )
            artifact = Artifact(type=ArtifactType.AMI, path=path)
            ctx = _make_context()

            result = await scanner.scan(artifact, ctx)

            assert result.packages == []
            assert len(result.diagnostics) == 1
            assert "Permission denied" in result.diagnostics[0]
        finally:
            os.chmod(path, 0o644)
            os.unlink(path)


class TestAMICancellation:
    """Tests for cancellation handling (Req 10.7)."""

    @pytest.mark.asyncio
    async def test_cancellation_before_format_detection(self) -> None:
        """Cancellation before any work → empty + diagnostic (Req 10.7)."""
        qcow2_scanner = MagicMock()
        qcow2_scanner.scan = AsyncMock()
        img_scanner = MagicMock()
        img_scanner.scan = AsyncMock()

        scanner = AMIScanner(
            qcow2_scanner=qcow2_scanner,
            img_scanner=img_scanner,
        )
        artifact = Artifact(type=ArtifactType.AMI, path="/tmp/test.ami")
        ctx = _make_context(cancelled=True)

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("cancelled" in d.lower() for d in result.diagnostics)
        qcow2_scanner.scan.assert_not_called()
        img_scanner.scan.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancellation_after_header_read(self) -> None:
        """Cancellation after reading header → empty + diagnostic (Req 10.7)."""
        with tempfile.NamedTemporaryFile(suffix=".ami", delete=False) as f:
            f.write(QCOW2_MAGIC + b"\x00" * 100)
            path = f.name

        try:
            qcow2_scanner = MagicMock()
            qcow2_scanner.scan = AsyncMock()
            img_scanner = MagicMock()
            img_scanner.scan = AsyncMock()

            scanner = AMIScanner(
                qcow2_scanner=qcow2_scanner,
                img_scanner=img_scanner,
            )
            artifact = Artifact(type=ArtifactType.AMI, path=path)

            # Create a context with a token that cancels after the first check
            progress = _MockProgressReporter()
            ctx = MagicMock(spec=WorkflowContext)
            ctx.progress = progress

            # Use a custom object that flips after first access
            class _DelayedCancel:
                def __init__(self) -> None:
                    self._count = 0

                @property
                def is_cancelled(self) -> bool:
                    self._count += 1
                    # First check passes, second check (after reading) cancels
                    return self._count > 1

            ctx.cancellation_token = _DelayedCancel()

            result = await scanner.scan(artifact, ctx)

            assert result.packages == []
            assert any("cancelled" in d.lower() for d in result.diagnostics)
            qcow2_scanner.scan.assert_not_called()
            img_scanner.scan.assert_not_called()
        finally:
            os.unlink(path)


class TestAMINoCredentials:
    """Tests for no-credentials requirement (Req 10.6)."""

    @pytest.mark.asyncio
    async def test_operates_on_local_files_only(self) -> None:
        """AMI scanner only reads from the local file path (Req 10.6)."""
        with tempfile.NamedTemporaryFile(suffix=".ami", delete=False) as f:
            f.write(b"\x00\x00\x00\x00" + b"\x00" * 100)
            path = f.name

        try:
            expected_result = _make_scan_result(path=path)
            qcow2_scanner = MagicMock()
            img_scanner = MagicMock()
            img_scanner.scan = AsyncMock(return_value=expected_result)

            scanner = AMIScanner(
                qcow2_scanner=qcow2_scanner,
                img_scanner=img_scanner,
            )
            artifact = Artifact(type=ArtifactType.AMI, path=path)
            ctx = _make_context()

            # Should succeed without any AWS env vars or network
            result = await scanner.scan(artifact, ctx)

            assert result is expected_result
        finally:
            os.unlink(path)


class TestAMIDuration:
    """Tests for timing in error cases."""

    @pytest.mark.asyncio
    async def test_error_case_has_non_negative_duration(self) -> None:
        """Error cases have a non-negative duration."""
        scanner = AMIScanner(
            qcow2_scanner=MagicMock(),
            img_scanner=MagicMock(),
        )
        artifact = Artifact(type=ArtifactType.AMI, path="/tmp/nonexistent_ami.img")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.duration_seconds >= 0.0

    @pytest.mark.asyncio
    async def test_cancellation_has_non_negative_duration(self) -> None:
        """Cancellation case has a non-negative duration."""
        scanner = AMIScanner(
            qcow2_scanner=MagicMock(),
            img_scanner=MagicMock(),
        )
        artifact = Artifact(type=ArtifactType.AMI, path="/tmp/test.ami")
        ctx = _make_context(cancelled=True)

        result = await scanner.scan(artifact, ctx)

        assert result.duration_seconds >= 0.0
