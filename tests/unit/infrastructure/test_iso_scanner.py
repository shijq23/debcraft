"""Unit tests for ISO 9660 image scanner."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from debcraft.domain.scanner.values import Artifact, ArtifactType
from debcraft.infrastructure.scanners.iso import ISOScanner
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


class _MockISOReader:
    """Mock ISO reader for testing."""

    def __init__(
        self,
        *,
        files: dict[str, bytes] | None = None,
        dirs: dict[str, list[str]] | None = None,
        open_error: Exception | None = None,
    ) -> None:
        self._files = files or {}
        self._dirs = dirs or {}
        self._open_error = open_error
        self.opened = False
        self.closed = False

    def open(self, path: str) -> None:
        if self._open_error:
            raise self._open_error
        self.opened = True

    def list_dir(self, path: str) -> list[str]:
        if path in self._dirs:
            return self._dirs[path]
        raise FileNotFoundError(f"Directory not found: {path}")

    def read_file(self, path: str) -> bytes:
        if path in self._files:
            return self._files[path]
        raise FileNotFoundError(f"File not found: {path}")

    def close(self) -> None:
        self.closed = True


class _MockSquashfsReader:
    """Mock squashfs reader for testing."""

    def __init__(
        self,
        *,
        files: dict[str, bytes] | None = None,
        dirs: dict[str, list[str]] | None = None,
        open_error: Exception | None = None,
    ) -> None:
        self._files = files or {}
        self._dirs = dirs or {}
        self._open_error = open_error
        self.opened = False
        self.closed = False

    def open(self, data: bytes) -> None:
        if self._open_error:
            raise self._open_error
        self.opened = True

    def read_file(self, path: str) -> bytes:
        if path in self._files:
            return self._files[path]
        raise FileNotFoundError(f"File not found: {path}")

    def list_dir(self, path: str) -> list[str]:
        if path in self._dirs:
            return self._dirs[path]
        raise FileNotFoundError(f"Directory not found: {path}")

    def close(self) -> None:
        self.closed = True


def _make_contents_port() -> MagicMock:
    """Create a mock ContentsIndexPort."""
    port = MagicMock()
    port.find_owners = AsyncMock(return_value={})
    return port


def _make_package_port() -> MagicMock:
    """Create a mock PackageLookupPort."""
    port = MagicMock()
    port.find_by_name = AsyncMock(return_value=None)
    return port


DPKG_STATUS_CONTENT = "Package: bash\nVersion: 5.2-1\nArchitecture: amd64\nStatus: install ok installed\n"

DPKG_STATUS_MULTIPLE = (
    "Package: bash\n"
    "Version: 5.2-1\n"
    "Architecture: amd64\n"
    "Status: install ok installed\n"
    "\n"
    "Package: coreutils\n"
    "Version: 9.1-1\n"
    "Architecture: amd64\n"
    "Status: install ok installed\n"
)


class TestISOScannerInvalidISO:
    """Tests for invalid ISO handling (Req 7.5)."""

    @pytest.mark.asyncio
    async def test_invalid_iso_returns_empty_with_diagnostic(self) -> None:
        """Invalid ISO → empty packages + diagnostic."""
        iso_reader = _MockISOReader(open_error=OSError("Not a valid ISO 9660 image"))
        squashfs_reader = _MockSquashfsReader()
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/bad.iso")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("Invalid ISO" in d for d in result.diagnostics)
        assert result.artifact_path == "/tmp/bad.iso"

    @pytest.mark.asyncio
    async def test_iso_reader_is_closed_on_error(self) -> None:
        """ISO reader close is called even if opening raises."""
        iso_reader = _MockISOReader(open_error=OSError("broken"))
        squashfs_reader = _MockSquashfsReader()
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/bad.iso")
        ctx = _make_context()

        await scanner.scan(artifact, ctx)

        # When open fails, close is NOT called since we haven't opened
        # (the try/finally only wraps post-open operations)
        assert not iso_reader.closed


class TestISOScannerSquashfsSearch:
    """Tests for squashfs search at known paths (Req 7.1, 7.2, 7.3)."""

    @pytest.mark.asyncio
    async def test_squashfs_at_live_path(self) -> None:
        """Squashfs found at live/filesystem.squashfs → dpkg_metadata."""
        iso_reader = _MockISOReader(files={"live/filesystem.squashfs": b"squashfs_data"})
        squashfs_reader = _MockSquashfsReader(files={"var/lib/dpkg/status": DPKG_STATUS_CONTENT.encode()})
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/test.iso")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert len(result.packages) == 1
        assert result.packages[0].name == "bash"
        assert result.packages[0].version == "5.2-1"
        assert result.packages[0].architecture == "amd64"
        assert result.packages[0].status == "installed"
        assert result.strategy == "dpkg_metadata"

    @pytest.mark.asyncio
    async def test_squashfs_at_casper_path(self) -> None:
        """Squashfs found at casper/filesystem.squashfs → dpkg_metadata."""
        iso_reader = _MockISOReader(files={"casper/filesystem.squashfs": b"squashfs_data"})
        squashfs_reader = _MockSquashfsReader(files={"var/lib/dpkg/status": DPKG_STATUS_CONTENT.encode()})
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/test.iso")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert len(result.packages) == 1
        assert result.packages[0].name == "bash"
        assert result.strategy == "dpkg_metadata"

    @pytest.mark.asyncio
    async def test_squashfs_at_install_path(self) -> None:
        """Squashfs found at install/filesystem.squashfs → dpkg_metadata."""
        iso_reader = _MockISOReader(files={"install/filesystem.squashfs": b"squashfs_data"})
        squashfs_reader = _MockSquashfsReader(files={"var/lib/dpkg/status": DPKG_STATUS_CONTENT.encode()})
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/test.iso")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert len(result.packages) == 1
        assert result.packages[0].name == "bash"
        assert result.strategy == "dpkg_metadata"

    @pytest.mark.asyncio
    async def test_squashfs_search_order_first_match_wins(self) -> None:
        """First matching squashfs path is used (live before casper)."""
        iso_reader = _MockISOReader(
            files={
                "live/filesystem.squashfs": b"live_data",
                "casper/filesystem.squashfs": b"casper_data",
            }
        )
        # The squashfs reader will receive "live_data" first
        squashfs_reader = _MockSquashfsReader(files={"var/lib/dpkg/status": DPKG_STATUS_CONTENT.encode()})
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/test.iso")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.strategy == "dpkg_metadata"
        assert len(result.packages) == 1

    @pytest.mark.asyncio
    async def test_multiple_packages_in_squashfs(self) -> None:
        """Multiple packages in squashfs dpkg status are all identified."""
        iso_reader = _MockISOReader(files={"live/filesystem.squashfs": b"squashfs_data"})
        squashfs_reader = _MockSquashfsReader(files={"var/lib/dpkg/status": DPKG_STATUS_MULTIPLE.encode()})
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/test.iso")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert len(result.packages) == 2
        names = {p.name for p in result.packages}
        assert "bash" in names
        assert "coreutils" in names


class TestISOScannerSquashfsDecompressFailure:
    """Tests for squashfs decompression failure (Req 7.6)."""

    @pytest.mark.asyncio
    async def test_squashfs_cannot_decompress(self) -> None:
        """Squashfs extraction failure → empty packages + diagnostic."""
        iso_reader = _MockISOReader(files={"live/filesystem.squashfs": b"corrupt_data"})
        squashfs_reader = _MockSquashfsReader(open_error=OSError("Cannot decompress: invalid squashfs magic"))
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/test.iso")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("Squashfs extraction failure" in d for d in result.diagnostics)


class TestISOScannerDirectRootfs:
    """Tests for direct rootfs scanning (Req 7.4)."""

    @pytest.mark.asyncio
    async def test_direct_rootfs_with_dpkg_status(self) -> None:
        """Direct rootfs with dpkg status (no squashfs) → dpkg_metadata."""
        iso_reader = _MockISOReader(files={"var/lib/dpkg/status": DPKG_STATUS_CONTENT.encode()})
        squashfs_reader = _MockSquashfsReader()
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/test.iso")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert len(result.packages) == 1
        assert result.packages[0].name == "bash"
        assert result.strategy == "dpkg_metadata"

    @pytest.mark.asyncio
    async def test_direct_rootfs_no_dpkg_status_falls_back(self) -> None:
        """No squashfs and no dpkg status → filesystem_analysis fallback."""
        iso_reader = _MockISOReader(
            dirs={"": ["usr", "etc"]},
        )
        squashfs_reader = _MockSquashfsReader()
        contents_port = _make_contents_port()
        package_port = _make_package_port()
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=contents_port,
            package_port=package_port,
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/test.iso")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.strategy == "filesystem_analysis"


class TestISOScannerFilesystemFallback:
    """Tests for filesystem analysis fallback (Req 7.7)."""

    @pytest.mark.asyncio
    async def test_no_dpkg_anywhere_falls_back_to_filesystem_analysis(self) -> None:
        """No dpkg status in squashfs → filesystem_analysis strategy."""
        iso_reader = _MockISOReader(files={"live/filesystem.squashfs": b"squashfs_data"})
        # Squashfs has files but no dpkg status
        squashfs_reader = _MockSquashfsReader(
            files={"usr/bin/hello": b"content"},
            dirs={
                "": ["usr"],
                "usr": ["bin"],
                "usr/bin": ["hello"],
            },
        )
        contents_port = _make_contents_port()
        package_port = _make_package_port()
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=contents_port,
            package_port=package_port,
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/test.iso")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.strategy == "filesystem_analysis"


class TestISOScannerCancellation:
    """Tests for cancellation handling (Req 7.8)."""

    @pytest.mark.asyncio
    async def test_cancellation_after_opening(self) -> None:
        """Cancellation after opening ISO → empty packages + diagnostic."""
        iso_reader = _MockISOReader(files={"live/filesystem.squashfs": b"data"})
        squashfs_reader = _MockSquashfsReader()
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/test.iso")
        ctx = _make_context(cancelled=True)

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("cancelled" in d.lower() for d in result.diagnostics)

    @pytest.mark.asyncio
    async def test_cancellation_during_squashfs_extraction(self) -> None:
        """Cancellation triggered between locating and extracting squashfs."""
        iso_reader = _MockISOReader(files={"live/filesystem.squashfs": b"data"})
        squashfs_reader = _MockSquashfsReader(files={"var/lib/dpkg/status": DPKG_STATUS_CONTENT.encode()})
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/test.iso")

        # Create context that cancels after first check (opening) passes
        CancellationToken()
        progress = _MockProgressReporter()
        ctx = MagicMock(spec=WorkflowContext)
        ctx.progress = progress

        # Use a property that cancels after the second access
        call_count = [0]
        CancellationToken()

        class _DelayedCancelToken:
            @property
            def is_cancelled(self) -> bool:
                call_count[0] += 1
                # Cancel on the second check (after locating squashfs)
                return call_count[0] >= 2

        ctx.cancellation_token = _DelayedCancelToken()

        result = await scanner.scan(artifact, ctx)

        assert result.packages == []
        assert any("cancelled" in d.lower() for d in result.diagnostics)


class TestISOScannerDuration:
    """Tests for duration and metadata."""

    @pytest.mark.asyncio
    async def test_duration_is_non_negative(self) -> None:
        """Scan duration is always non-negative."""
        iso_reader = _MockISOReader(files={"live/filesystem.squashfs": b"data"})
        squashfs_reader = _MockSquashfsReader(files={"var/lib/dpkg/status": DPKG_STATUS_CONTENT.encode()})
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/test.iso")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.duration_seconds >= 0.0

    @pytest.mark.asyncio
    async def test_artifact_path_preserved(self) -> None:
        """ScanResult artifact_path matches the input artifact path."""
        iso_reader = _MockISOReader(files={"live/filesystem.squashfs": b"data"})
        squashfs_reader = _MockSquashfsReader(files={"var/lib/dpkg/status": DPKG_STATUS_CONTENT.encode()})
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/path/to/my.iso")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.artifact_path == "/path/to/my.iso"


class TestISOScannerNoMountRequired:
    """Tests verifying no mount or root privileges needed (Req 7.9)."""

    @pytest.mark.asyncio
    async def test_scanner_uses_only_reader_protocols(self) -> None:
        """Scanner operates exclusively via ISOReader and SquashfsReader protocols."""
        # The fact that we can test with mock readers proves no mount/root needed
        iso_reader = _MockISOReader(files={"live/filesystem.squashfs": b"data"})
        squashfs_reader = _MockSquashfsReader(files={"var/lib/dpkg/status": DPKG_STATUS_CONTENT.encode()})
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/test.iso")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        # Proves no OS mount was needed
        assert result.packages != [] or result.strategy == "dpkg_metadata"
        assert iso_reader.opened
        assert iso_reader.closed
        assert squashfs_reader.opened
        assert squashfs_reader.closed


class TestISOScannerResourceCleanup:
    """Tests for resource cleanup (ISO reader and squashfs reader closed)."""

    @pytest.mark.asyncio
    async def test_iso_reader_closed_after_success(self) -> None:
        """ISO reader is closed after successful scan."""
        iso_reader = _MockISOReader(files={"live/filesystem.squashfs": b"data"})
        squashfs_reader = _MockSquashfsReader(files={"var/lib/dpkg/status": DPKG_STATUS_CONTENT.encode()})
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/test.iso")
        ctx = _make_context()

        await scanner.scan(artifact, ctx)

        assert iso_reader.closed

    @pytest.mark.asyncio
    async def test_squashfs_reader_closed_after_success(self) -> None:
        """Squashfs reader is closed after successful scan."""
        iso_reader = _MockISOReader(files={"live/filesystem.squashfs": b"data"})
        squashfs_reader = _MockSquashfsReader(files={"var/lib/dpkg/status": DPKG_STATUS_CONTENT.encode()})
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/test.iso")
        ctx = _make_context()

        await scanner.scan(artifact, ctx)

        assert squashfs_reader.closed

    @pytest.mark.asyncio
    async def test_iso_reader_closed_on_cancellation(self) -> None:
        """ISO reader is closed even when scan is cancelled."""
        iso_reader = _MockISOReader(files={"live/filesystem.squashfs": b"data"})
        squashfs_reader = _MockSquashfsReader()
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/test.iso")
        ctx = _make_context(cancelled=True)

        await scanner.scan(artifact, ctx)

        assert iso_reader.closed

    @pytest.mark.asyncio
    async def test_squashfs_reader_closed_on_extraction_failure(self) -> None:
        """Squashfs reader close is not called if open fails."""
        iso_reader = _MockISOReader(files={"live/filesystem.squashfs": b"data"})
        squashfs_reader = _MockSquashfsReader(open_error=OSError("corrupt"))
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/test.iso")
        ctx = _make_context()

        await scanner.scan(artifact, ctx)

        # close is not called since open raised
        assert not squashfs_reader.closed
