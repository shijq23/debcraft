"""Unit tests for QCOW2 disk image scanner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from debcraft.domain.scanner.values import Artifact, ArtifactType
from debcraft.infrastructure.scanners.qcow2 import QCOW2_MAGIC, QCOW2Scanner
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


def _make_mock_guestfs(
    *,
    roots: list[str] | None = None,
    dpkg_content: bytes | None = None,
    inspect_os_error: Exception | None = None,
    mount_error: Exception | None = None,
    read_file_error: Exception | None = None,
    open_error: Exception | None = None,
    ls_result: list[str] | None = None,
) -> MagicMock:
    """Create a mock GuestfsInspector."""
    gfs = MagicMock()

    if open_error:
        gfs.open_image.side_effect = open_error
    else:
        gfs.open_image.return_value = None

    if inspect_os_error:
        gfs.inspect_os.side_effect = inspect_os_error
    else:
        gfs.inspect_os.return_value = roots if roots is not None else []

    if mount_error:
        gfs.mount_readonly.side_effect = mount_error
    else:
        gfs.mount_readonly.return_value = None

    if read_file_error:
        gfs.read_file.side_effect = read_file_error
    elif dpkg_content is not None:
        gfs.read_file.return_value = dpkg_content
    else:
        gfs.read_file.side_effect = FileNotFoundError("not found")

    gfs.ls.return_value = ls_result if ls_result is not None else []
    gfs.close.return_value = None

    return gfs


def _write_qcow2_file(path: Path, *, valid: bool = True) -> Path:
    """Write a file with or without QCOW2 magic bytes."""
    file_path = path / "test.qcow2"
    if valid:
        # Write QCOW2 magic followed by some dummy data
        file_path.write_bytes(QCOW2_MAGIC + b"\x00" * 100)
    else:
        file_path.write_bytes(b"\x00\x00\x00\x00" + b"\x00" * 100)
    return file_path


class TestQCOW2GuestfsAvailability:
    """Tests for guestfs availability check (Req 8.10)."""

    @pytest.mark.asyncio
    async def test_no_guestfs_returns_diagnostic(self, tmp_path: Path) -> None:
        """When guestfs_inspector is None, return empty + diagnostic."""
        contents_port = MagicMock()
        package_port = MagicMock()
        scanner = QCOW2Scanner(
            guestfs_inspector=None,
            contents_port=contents_port,
            package_port=package_port,
        )
        file_path = _write_qcow2_file(tmp_path)
        artifact = Artifact(type=ArtifactType.QCOW2, path=str(file_path))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("guestfs" in d and "not available" in d for d in result.diagnostics)
        assert result.duration_seconds >= 0.0
        assert result.artifact_path == str(file_path)

    @pytest.mark.asyncio
    async def test_no_guestfs_reports_progress(self, tmp_path: Path) -> None:
        """When guestfs not available, reports 100% progress."""
        scanner = QCOW2Scanner(
            guestfs_inspector=None,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        file_path = _write_qcow2_file(tmp_path)
        artifact = Artifact(type=ArtifactType.QCOW2, path=str(file_path))
        ctx = _make_context()

        await scanner.scan(artifact, ctx)

        assert any(pct == 100.0 for pct, _ in ctx.progress.reports)


class TestQCOW2MagicValidation:
    """Tests for QCOW2 magic bytes validation (Req 8.4)."""

    @pytest.mark.asyncio
    async def test_invalid_magic_returns_diagnostic(self, tmp_path: Path) -> None:
        r"""File without QFI\\xfb magic → empty packages + diagnostic."""
        gfs = _make_mock_guestfs()
        scanner = QCOW2Scanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        file_path = _write_qcow2_file(tmp_path, valid=False)
        artifact = Artifact(type=ArtifactType.QCOW2, path=str(file_path))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("Invalid QCOW2" in d and "magic" in d for d in result.diagnostics)
        # guestfs should never be called since magic check failed
        gfs.open_image.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_magic_proceeds_to_open(self, tmp_path: Path) -> None:
        r"""File with valid QFI\\xfb magic proceeds to guestfs open."""
        gfs = _make_mock_guestfs(roots=["/dev/sda1"])
        contents_port = AsyncMock()
        contents_port.find_owners.return_value = {}
        scanner = QCOW2Scanner(
            guestfs_inspector=gfs,
            contents_port=contents_port,
            package_port=AsyncMock(),
        )
        file_path = _write_qcow2_file(tmp_path, valid=True)
        artifact = Artifact(type=ArtifactType.QCOW2, path=str(file_path))
        ctx = _make_context()

        await scanner.scan(artifact, ctx)

        gfs.open_image.assert_called_once_with(str(file_path), readonly=True)

    @pytest.mark.asyncio
    async def test_short_file_returns_diagnostic(self, tmp_path: Path) -> None:
        """File with fewer than 4 bytes → invalid magic diagnostic."""
        gfs = _make_mock_guestfs()
        scanner = QCOW2Scanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        file_path = tmp_path / "tiny.qcow2"
        file_path.write_bytes(b"QF")  # Only 2 bytes
        artifact = Artifact(type=ArtifactType.QCOW2, path=str(file_path))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("Invalid QCOW2" in d for d in result.diagnostics)


class TestQCOW2FileAccess:
    """Tests for file access errors (Req 8.5)."""

    @pytest.mark.asyncio
    async def test_nonexistent_file_returns_diagnostic(self, tmp_path: Path) -> None:
        """Non-existent file → empty packages + diagnostic."""
        gfs = _make_mock_guestfs()
        scanner = QCOW2Scanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        artifact = Artifact(type=ArtifactType.QCOW2, path=str(tmp_path / "nonexistent.qcow2"))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("Cannot read" in d for d in result.diagnostics)


class TestQCOW2OSInspection:
    """Tests for OS root inspection (Req 8.6)."""

    @pytest.mark.asyncio
    async def test_no_os_roots_returns_diagnostic(self, tmp_path: Path) -> None:
        """No inspectable OS root → empty packages + diagnostic."""
        gfs = _make_mock_guestfs(roots=[])
        scanner = QCOW2Scanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        file_path = _write_qcow2_file(tmp_path)
        artifact = Artifact(type=ArtifactType.QCOW2, path=str(file_path))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("No inspectable operating system root" in d for d in result.diagnostics)

    @pytest.mark.asyncio
    async def test_inspect_os_error_returns_diagnostic(self, tmp_path: Path) -> None:
        """inspect_os raises error → empty packages + diagnostic."""
        gfs = _make_mock_guestfs(inspect_os_error=RuntimeError("inspection failed"))
        scanner = QCOW2Scanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        file_path = _write_qcow2_file(tmp_path)
        artifact = Artifact(type=ArtifactType.QCOW2, path=str(file_path))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("Failed to inspect OS roots" in d for d in result.diagnostics)


class TestQCOW2DpkgParsing:
    """Tests for dpkg status parsing (Req 8.1, 8.2)."""

    @pytest.mark.asyncio
    async def test_dpkg_status_found_and_parsed(self, tmp_path: Path) -> None:
        """Dpkg status found → packages identified with dpkg_metadata strategy."""
        dpkg_content = b"Package: bash\nVersion: 5.2-1\nArchitecture: amd64\nStatus: install ok installed\n"
        gfs = _make_mock_guestfs(
            roots=["/dev/sda1"],
            dpkg_content=dpkg_content,
        )
        scanner = QCOW2Scanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        file_path = _write_qcow2_file(tmp_path)
        artifact = Artifact(type=ArtifactType.QCOW2, path=str(file_path))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert len(result.packages) == 1
        assert result.packages[0].name == "bash"
        assert result.packages[0].version == "5.2-1"
        assert result.packages[0].architecture == "amd64"
        assert result.packages[0].status == "installed"
        assert result.strategy == "dpkg_metadata"

    @pytest.mark.asyncio
    async def test_multiple_packages_parsed(self, tmp_path: Path) -> None:
        """Multiple packages in dpkg status are all identified."""
        dpkg_content = (
            b"Package: bash\n"
            b"Version: 5.2-1\n"
            b"Architecture: amd64\n"
            b"Status: install ok installed\n"
            b"\n"
            b"Package: coreutils\n"
            b"Version: 9.1-1\n"
            b"Architecture: amd64\n"
            b"Status: install ok installed\n"
        )
        gfs = _make_mock_guestfs(
            roots=["/dev/sda1"],
            dpkg_content=dpkg_content,
        )
        scanner = QCOW2Scanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        file_path = _write_qcow2_file(tmp_path)
        artifact = Artifact(type=ArtifactType.QCOW2, path=str(file_path))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert len(result.packages) == 2
        names = {p.name for p in result.packages}
        assert "bash" in names
        assert "coreutils" in names

    @pytest.mark.asyncio
    async def test_uses_first_os_root(self, tmp_path: Path) -> None:
        """When multiple OS roots exist, first one is used."""
        dpkg_content = b"Package: pkg1\nVersion: 1.0\nArchitecture: amd64\nStatus: install ok installed\n"
        gfs = _make_mock_guestfs(
            roots=["/dev/sda1", "/dev/sda2"],
            dpkg_content=dpkg_content,
        )
        scanner = QCOW2Scanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        file_path = _write_qcow2_file(tmp_path)
        artifact = Artifact(type=ArtifactType.QCOW2, path=str(file_path))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        # Should mount the first root
        gfs.mount_readonly.assert_called_once_with("/dev/sda1", "/")
        assert len(result.packages) == 1


class TestQCOW2FilesystemFallback:
    """Tests for filesystem analysis fallback (Req 8.3)."""

    @pytest.mark.asyncio
    async def test_no_dpkg_status_falls_back(self, tmp_path: Path) -> None:
        """No dpkg status → fall back to filesystem analysis."""
        gfs = _make_mock_guestfs(
            roots=["/dev/sda1"],
            read_file_error=FileNotFoundError("/var/lib/dpkg/status not found"),
            ls_result=["usr", "etc"],
        )
        contents_port = AsyncMock()
        contents_port.find_owners.return_value = {}
        package_port = AsyncMock()
        scanner = QCOW2Scanner(
            guestfs_inspector=gfs,
            contents_port=contents_port,
            package_port=package_port,
        )
        file_path = _write_qcow2_file(tmp_path)
        artifact = Artifact(type=ArtifactType.QCOW2, path=str(file_path))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.strategy == "filesystem_analysis"


class TestQCOW2Cancellation:
    """Tests for cancellation handling (Req 8.7)."""

    @pytest.mark.asyncio
    async def test_cancellation_before_open(self, tmp_path: Path) -> None:
        """Cancellation before opening image → empty packages + diagnostic."""
        gfs = _make_mock_guestfs(roots=["/dev/sda1"])
        scanner = QCOW2Scanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        file_path = _write_qcow2_file(tmp_path)
        artifact = Artifact(type=ArtifactType.QCOW2, path=str(file_path))
        ctx = _make_context(cancelled=True)

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("cancelled" in d.lower() for d in result.diagnostics)
        # guestfs should not be opened
        gfs.open_image.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancellation_during_package_processing(self, tmp_path: Path) -> None:
        """Cancellation during package iteration → partial result."""
        dpkg_content = (
            b"Package: pkg1\n"
            b"Version: 1.0\n"
            b"Architecture: amd64\n"
            b"Status: install ok installed\n"
            b"\n"
            b"Package: pkg2\n"
            b"Version: 2.0\n"
            b"Architecture: amd64\n"
            b"Status: install ok installed\n"
        )
        gfs = _make_mock_guestfs(
            roots=["/dev/sda1"],
            dpkg_content=dpkg_content,
        )
        scanner = QCOW2Scanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        file_path = _write_qcow2_file(tmp_path)
        artifact = Artifact(type=ArtifactType.QCOW2, path=str(file_path))

        # Create context with a custom cancellation token
        progress = _MockProgressReporter()
        ctx = MagicMock(spec=WorkflowContext)
        ctx.progress = progress

        # Use a custom mock that cancels after the first is_cancelled
        # check returns False during package iteration.
        # The scan checks cancellation at multiple boundaries before
        # reaching package iteration. We track calls via a wrapper.

        class _CancelAfterNChecks:
            """Token that cancels after N is_cancelled checks."""

            def __init__(self, cancel_after: int) -> None:
                self._cancel_after = cancel_after
                self._check_count = 0

            @property
            def is_cancelled(self) -> bool:
                self._check_count += 1
                return self._check_count > self._cancel_after

        # 5 checks happen before package iteration:
        # 1. After magic validation
        # 2. After open_image
        # 3. Before inspect_os
        # 4. Before mount
        # 5. Before read_file
        # Then during package iteration, first package passes (check 6 -> False),
        # second package check (check 7 -> True) triggers cancellation
        ctx.cancellation_token = _CancelAfterNChecks(cancel_after=5)

        result = await scanner.scan(artifact, ctx)

        # Should have partial results (at most 1 package)
        assert len(result.packages) <= 1
        assert any("cancelled" in d.lower() for d in result.diagnostics)


class TestQCOW2Progress:
    """Tests for progress reporting (Req 8.8)."""

    @pytest.mark.asyncio
    async def test_progress_reported_on_success(self, tmp_path: Path) -> None:
        """Successful scan reports progress at multiple stages."""
        dpkg_content = b"Package: pkg\nVersion: 1.0\nArchitecture: amd64\nStatus: install ok installed\n"
        gfs = _make_mock_guestfs(
            roots=["/dev/sda1"],
            dpkg_content=dpkg_content,
        )
        scanner = QCOW2Scanner(
            guestfs_inspector=gfs,
            contents_port=AsyncMock(),
            package_port=AsyncMock(),
        )
        file_path = _write_qcow2_file(tmp_path)
        artifact = Artifact(type=ArtifactType.QCOW2, path=str(file_path))
        ctx = _make_context()

        await scanner.scan(artifact, ctx)

        # Should have multiple progress reports (Req 8.8)
        assert len(ctx.progress.reports) >= 2
        # Final report should be 100%
        assert ctx.progress.reports[-1][0] == 100.0

    @pytest.mark.asyncio
    async def test_progress_monotonically_increasing(self, tmp_path: Path) -> None:
        """Progress percentages are monotonically non-decreasing."""
        dpkg_content = b"Package: pkg\nVersion: 1.0\nArchitecture: amd64\nStatus: install ok installed\n"
        gfs = _make_mock_guestfs(
            roots=["/dev/sda1"],
            dpkg_content=dpkg_content,
        )
        scanner = QCOW2Scanner(
            guestfs_inspector=gfs,
            contents_port=AsyncMock(),
            package_port=AsyncMock(),
        )
        file_path = _write_qcow2_file(tmp_path)
        artifact = Artifact(type=ArtifactType.QCOW2, path=str(file_path))
        ctx = _make_context()

        await scanner.scan(artifact, ctx)

        percentages = [pct for pct, _ in ctx.progress.reports]
        for i in range(1, len(percentages)):
            assert percentages[i] >= percentages[i - 1]


class TestQCOW2GuestfsClose:
    """Tests for guestfs resource cleanup."""

    @pytest.mark.asyncio
    async def test_guestfs_closed_on_success(self, tmp_path: Path) -> None:
        """Guestfs is closed after successful scan."""
        dpkg_content = b"Package: pkg\nVersion: 1.0\nArchitecture: amd64\nStatus: install ok installed\n"
        gfs = _make_mock_guestfs(
            roots=["/dev/sda1"],
            dpkg_content=dpkg_content,
        )
        scanner = QCOW2Scanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        file_path = _write_qcow2_file(tmp_path)
        artifact = Artifact(type=ArtifactType.QCOW2, path=str(file_path))
        ctx = _make_context()

        await scanner.scan(artifact, ctx)

        gfs.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_guestfs_closed_on_error(self, tmp_path: Path) -> None:
        """Guestfs is closed even when inspection fails."""
        gfs = _make_mock_guestfs(inspect_os_error=RuntimeError("inspection failed"))
        scanner = QCOW2Scanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        file_path = _write_qcow2_file(tmp_path)
        artifact = Artifact(type=ArtifactType.QCOW2, path=str(file_path))
        ctx = _make_context()

        await scanner.scan(artifact, ctx)

        gfs.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_guestfs_closed_on_mount_error(self, tmp_path: Path) -> None:
        """Guestfs is closed when mount fails."""
        gfs = _make_mock_guestfs(
            roots=["/dev/sda1"],
            mount_error=RuntimeError("mount failed"),
        )
        scanner = QCOW2Scanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        file_path = _write_qcow2_file(tmp_path)
        artifact = Artifact(type=ArtifactType.QCOW2, path=str(file_path))
        ctx = _make_context()

        await scanner.scan(artifact, ctx)

        gfs.close.assert_called_once()


class TestQCOW2MagicConstant:
    """Tests for QCOW2_MAGIC constant."""

    def test_qcow2_magic_value(self) -> None:
        """QCOW2_MAGIC is the correct 4-byte sequence."""
        assert QCOW2_MAGIC == b"QFI\xfb"
        assert len(QCOW2_MAGIC) == 4


class TestQCOW2Duration:
    """Tests for timing measurement."""

    @pytest.mark.asyncio
    async def test_duration_non_negative(self, tmp_path: Path) -> None:
        """Scan duration is always non-negative."""
        gfs = _make_mock_guestfs(roots=["/dev/sda1"])
        contents_port = AsyncMock()
        contents_port.find_owners.return_value = {}
        scanner = QCOW2Scanner(
            guestfs_inspector=gfs,
            contents_port=contents_port,
            package_port=AsyncMock(),
        )
        file_path = _write_qcow2_file(tmp_path)
        artifact = Artifact(type=ArtifactType.QCOW2, path=str(file_path))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.duration_seconds >= 0.0

    @pytest.mark.asyncio
    async def test_artifact_path_preserved(self, tmp_path: Path) -> None:
        """ScanResult artifact_path matches the input artifact path."""
        gfs = _make_mock_guestfs(roots=["/dev/sda1"])
        contents_port = AsyncMock()
        contents_port.find_owners.return_value = {}
        scanner = QCOW2Scanner(
            guestfs_inspector=gfs,
            contents_port=contents_port,
            package_port=AsyncMock(),
        )
        file_path = _write_qcow2_file(tmp_path)
        artifact = Artifact(type=ArtifactType.QCOW2, path=str(file_path))
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.artifact_path == str(file_path)
