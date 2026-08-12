"""Unit tests for raw disk image scanner."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from debcraft.domain.scanner.values import Artifact, ArtifactType
from debcraft.infrastructure.scanners.img import IMGScanner
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


class TestIMGGuestfsAvailability:
    """Tests for guestfs availability check (Req 9.9)."""

    @pytest.mark.asyncio
    async def test_no_guestfs_returns_diagnostic(self) -> None:
        """When guestfs_inspector is None, return empty + diagnostic."""
        scanner = IMGScanner(
            guestfs_inspector=None,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        artifact = Artifact(type=ArtifactType.IMG, path="/tmp/test.img")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("guestfs" in d and "not available" in d for d in result.diagnostics)
        assert result.duration_seconds >= 0.0
        assert result.artifact_path == "/tmp/test.img"

    @pytest.mark.asyncio
    async def test_no_guestfs_reports_progress(self) -> None:
        """When guestfs not available, reports 100% progress."""
        scanner = IMGScanner(
            guestfs_inspector=None,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        artifact = Artifact(type=ArtifactType.IMG, path="/tmp/test.img")
        ctx = _make_context()

        await scanner.scan(artifact, ctx)

        assert any(pct == 100.0 for pct, _ in ctx.progress.reports)


class TestIMGFileAccess:
    """Tests for file access errors (Req 9.5)."""

    @pytest.mark.asyncio
    async def test_open_image_failure_returns_diagnostic(self) -> None:
        """If guestfs cannot open the image, return empty + diagnostic."""
        gfs = _make_mock_guestfs(open_error=OSError("No such file or directory"))
        scanner = IMGScanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        artifact = Artifact(type=ArtifactType.IMG, path="/tmp/nonexistent.img")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("Failed to open" in d for d in result.diagnostics)
        assert result.artifact_path == "/tmp/nonexistent.img"


class TestIMGPartitionInspection:
    """Tests for partition inspection (Req 9.2, 9.6)."""

    @pytest.mark.asyncio
    async def test_no_partitions_returns_diagnostic(self) -> None:
        """No OS partitions found → empty packages + diagnostic."""
        gfs = _make_mock_guestfs(roots=[])
        scanner = IMGScanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        artifact = Artifact(type=ArtifactType.IMG, path="/tmp/test.img")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("No OS partitions" in d for d in result.diagnostics)

    @pytest.mark.asyncio
    async def test_inspect_os_error_returns_diagnostic(self) -> None:
        """inspect_os raises error → empty packages + diagnostic."""
        gfs = _make_mock_guestfs(inspect_os_error=RuntimeError("partition table unrecognized"))
        scanner = IMGScanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        artifact = Artifact(type=ArtifactType.IMG, path="/tmp/test.img")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("Failed to inspect" in d for d in result.diagnostics)

    @pytest.mark.asyncio
    async def test_uses_first_partition_with_dpkg(self) -> None:
        """Multiple partitions → uses first one with dpkg status (Req 9.2)."""
        dpkg_content = b"Package: bash\nVersion: 5.2-1\nArchitecture: amd64\nStatus: install ok installed\n"
        gfs = _make_mock_guestfs(
            roots=["/dev/sda1", "/dev/sda2", "/dev/sda3"],
            dpkg_content=dpkg_content,
        )
        scanner = IMGScanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        artifact = Artifact(type=ArtifactType.IMG, path="/tmp/test.img")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        # Should find packages from first partition with dpkg status
        assert len(result.packages) == 1
        assert result.packages[0].name == "bash"
        assert result.strategy == "dpkg_metadata"
        # Should mount at least the first partition
        gfs.mount_readonly.assert_called()

    @pytest.mark.asyncio
    async def test_skips_unmountable_partitions(self) -> None:
        """Partitions that fail to mount are skipped (Req 9.6)."""
        dpkg_content = b"Package: coreutils\nVersion: 9.1-1\nArchitecture: amd64\nStatus: install ok installed\n"
        gfs = MagicMock()
        gfs.open_image.return_value = None
        gfs.inspect_os.return_value = ["/dev/sda1", "/dev/sda2"]
        gfs.close.return_value = None

        # First partition fails to mount, second succeeds
        mount_call_count = [0]

        def mock_mount(device: str, mountpoint: str) -> None:
            mount_call_count[0] += 1
            if device == "/dev/sda1":
                raise RuntimeError("unsupported filesystem")

        gfs.mount_readonly.side_effect = mock_mount

        # read_file only works after second partition is mounted
        def mock_read_file(path: str) -> bytes:
            if mount_call_count[0] >= 2:
                return dpkg_content
            raise FileNotFoundError("not found")

        gfs.read_file.side_effect = mock_read_file
        gfs.ls.return_value = []

        scanner = IMGScanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        artifact = Artifact(type=ArtifactType.IMG, path="/tmp/test.img")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        # Should find packages from second partition
        assert len(result.packages) == 1
        assert result.packages[0].name == "coreutils"
        assert result.strategy == "dpkg_metadata"


class TestIMGDpkgParsing:
    """Tests for dpkg status parsing (Req 9.1, 9.3)."""

    @pytest.mark.asyncio
    async def test_dpkg_status_found_and_parsed(self) -> None:
        """Dpkg status found → packages identified with dpkg_metadata strategy."""
        dpkg_content = b"Package: bash\nVersion: 5.2-1\nArchitecture: amd64\nStatus: install ok installed\n"
        gfs = _make_mock_guestfs(
            roots=["/dev/sda1"],
            dpkg_content=dpkg_content,
        )
        scanner = IMGScanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        artifact = Artifact(type=ArtifactType.IMG, path="/tmp/test.img")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert len(result.packages) == 1
        assert result.packages[0].name == "bash"
        assert result.packages[0].version == "5.2-1"
        assert result.packages[0].architecture == "amd64"
        assert result.packages[0].status == "installed"
        assert result.strategy == "dpkg_metadata"

    @pytest.mark.asyncio
    async def test_multiple_packages_parsed(self) -> None:
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
        scanner = IMGScanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        artifact = Artifact(type=ArtifactType.IMG, path="/tmp/test.img")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert len(result.packages) == 2
        names = {p.name for p in result.packages}
        assert "bash" in names
        assert "coreutils" in names


class TestIMGFilesystemFallback:
    """Tests for filesystem analysis fallback (Req 9.4)."""

    @pytest.mark.asyncio
    async def test_no_dpkg_status_falls_back(self) -> None:
        """No dpkg status on any partition → fall back to filesystem analysis."""
        gfs = _make_mock_guestfs(
            roots=["/dev/sda1"],
            read_file_error=FileNotFoundError("/var/lib/dpkg/status not found"),
            ls_result=["usr", "etc"],
        )
        contents_port = MagicMock()

        async def mock_find_owners(file_paths: list[str], snapshot_id: int) -> dict[str, str]:
            return {}

        contents_port.find_owners = mock_find_owners
        package_port = MagicMock()
        scanner = IMGScanner(
            guestfs_inspector=gfs,
            contents_port=contents_port,
            package_port=package_port,
        )
        artifact = Artifact(type=ArtifactType.IMG, path="/tmp/test.img")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.strategy == "filesystem_analysis"


class TestIMGCancellation:
    """Tests for cancellation handling (Req 9.7)."""

    @pytest.mark.asyncio
    async def test_cancellation_before_partition_enumeration(self) -> None:
        """Cancellation before inspecting → empty packages + diagnostic."""
        gfs = _make_mock_guestfs(roots=["/dev/sda1"])
        scanner = IMGScanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        artifact = Artifact(type=ArtifactType.IMG, path="/tmp/test.img")
        ctx = _make_context(cancelled=True)

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("cancelled" in d.lower() for d in result.diagnostics)

    @pytest.mark.asyncio
    async def test_cancellation_between_partitions(self) -> None:
        """Cancellation between partitions → partial result + diagnostic."""
        gfs = MagicMock()
        gfs.open_image.return_value = None
        gfs.inspect_os.return_value = ["/dev/sda1", "/dev/sda2"]
        gfs.close.return_value = None
        gfs.mount_readonly.return_value = None
        gfs.read_file.side_effect = FileNotFoundError("not found")
        gfs.ls.return_value = []

        scanner = IMGScanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        artifact = Artifact(type=ArtifactType.IMG, path="/tmp/test.img")
        ctx = _make_context()

        # Use a mock cancellation token that cancels after first check
        mock_token = MagicMock()
        call_count = [0]

        def _is_cancelled_side_effect() -> bool:
            call_count[0] += 1
            # First check (before enumerate) passes, second check (in loop) cancels
            return call_count[0] > 1

        type(mock_token).is_cancelled = property(lambda self: _is_cancelled_side_effect())
        ctx.cancellation_token = mock_token

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("cancelled" in d.lower() for d in result.diagnostics)


class TestIMGGuestfsClose:
    """Tests for guestfs resource cleanup."""

    @pytest.mark.asyncio
    async def test_guestfs_closed_on_success(self) -> None:
        """Guestfs is closed after successful scan."""
        dpkg_content = b"Package: pkg\nVersion: 1.0\nArchitecture: amd64\nStatus: install ok installed\n"
        gfs = _make_mock_guestfs(
            roots=["/dev/sda1"],
            dpkg_content=dpkg_content,
        )
        scanner = IMGScanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        artifact = Artifact(type=ArtifactType.IMG, path="/tmp/test.img")
        ctx = _make_context()

        await scanner.scan(artifact, ctx)

        gfs.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_guestfs_closed_on_error(self) -> None:
        """Guestfs is closed even when inspection fails."""
        gfs = _make_mock_guestfs(inspect_os_error=RuntimeError("inspection failed"))
        scanner = IMGScanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        artifact = Artifact(type=ArtifactType.IMG, path="/tmp/test.img")
        ctx = _make_context()

        await scanner.scan(artifact, ctx)

        gfs.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_guestfs_closed_on_mount_error(self) -> None:
        """Guestfs is closed when all mounts fail."""
        gfs = _make_mock_guestfs(
            roots=["/dev/sda1"],
            mount_error=RuntimeError("mount failed"),
        )
        contents_port = MagicMock()

        async def mock_find_owners(file_paths: list[str], snapshot_id: int) -> dict[str, str]:
            return {}

        contents_port.find_owners = mock_find_owners
        package_port = MagicMock()

        scanner = IMGScanner(
            guestfs_inspector=gfs,
            contents_port=contents_port,
            package_port=package_port,
        )
        artifact = Artifact(type=ArtifactType.IMG, path="/tmp/test.img")
        ctx = _make_context()

        await scanner.scan(artifact, ctx)

        gfs.close.assert_called_once()


class TestIMGDuration:
    """Tests for timing measurement."""

    @pytest.mark.asyncio
    async def test_duration_non_negative(self) -> None:
        """Scan duration is always non-negative."""
        dpkg_content = b"Package: pkg\nVersion: 1.0\nArchitecture: amd64\nStatus: install ok installed\n"
        gfs = _make_mock_guestfs(
            roots=["/dev/sda1"],
            dpkg_content=dpkg_content,
        )
        scanner = IMGScanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        artifact = Artifact(type=ArtifactType.IMG, path="/tmp/test.img")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.duration_seconds >= 0.0

    @pytest.mark.asyncio
    async def test_artifact_path_preserved(self) -> None:
        """ScanResult artifact_path matches the input artifact path."""
        dpkg_content = b"Package: pkg\nVersion: 1.0\nArchitecture: amd64\nStatus: install ok installed\n"
        gfs = _make_mock_guestfs(
            roots=["/dev/sda1"],
            dpkg_content=dpkg_content,
        )
        scanner = IMGScanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        artifact = Artifact(type=ArtifactType.IMG, path="/tmp/my-disk.img")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.artifact_path == "/tmp/my-disk.img"


class TestIMGNoRootPrivileges:
    """Tests for no-root-privilege requirement (Req 9.8)."""

    @pytest.mark.asyncio
    async def test_uses_guestfs_not_mount_commands(self) -> None:
        """Scanner uses guestfs APIs, not OS-level mount operations."""
        dpkg_content = b"Package: pkg\nVersion: 1.0\nArchitecture: amd64\nStatus: install ok installed\n"
        gfs = _make_mock_guestfs(
            roots=["/dev/sda1"],
            dpkg_content=dpkg_content,
        )
        scanner = IMGScanner(
            guestfs_inspector=gfs,
            contents_port=MagicMock(),
            package_port=MagicMock(),
        )
        artifact = Artifact(type=ArtifactType.IMG, path="/tmp/test.img")
        ctx = _make_context()

        await scanner.scan(artifact, ctx)

        # Verify guestfs APIs are used
        gfs.open_image.assert_called_once_with("/tmp/test.img", readonly=True)
        gfs.inspect_os.assert_called_once()
        gfs.mount_readonly.assert_called_once_with("/dev/sda1", "/")
        gfs.read_file.assert_called_once_with("/var/lib/dpkg/status")
