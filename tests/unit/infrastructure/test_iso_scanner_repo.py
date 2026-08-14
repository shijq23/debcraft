"""Unit tests for ISO repository scanning (NETINST ISO support)."""

from __future__ import annotations

import gzip
from unittest.mock import AsyncMock, MagicMock

import pytest

from debcraft.domain.scanner.values import Artifact, ArtifactType, ScanningStrategy
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


class _CancellingISOReader(_MockISOReader):
    """ISO reader that cancels a token after N read_file calls."""

    def __init__(self, *, token: CancellationToken, cancel_after_reads: int = 1, **kwargs) -> None:
        super().__init__(**kwargs)
        self._token = token
        self._cancel_after = cancel_after_reads
        self._read_count = 0

    def read_file(self, path: str) -> bytes:
        result = super().read_file(path)
        self._read_count += 1
        if self._read_count >= self._cancel_after:
            self._token.cancel()
        return result


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


PACKAGES_CONTENT = (
    "Package: bash\nVersion: 5.2-1\nArchitecture: amd64\n\nPackage: coreutils\nVersion: 9.1-1\nArchitecture: amd64\n"
)

DPKG_STATUS_CONTENT = "Package: bash\nVersion: 5.2-1\nArchitecture: amd64\nStatus: install ok installed\n"


class TestRepoStrategyOrderAndFallback:
    """Tests for strategy ordering and fallback logic (Reqs 1.3, 1.4, 7.1, 7.2, 7.3)."""

    @pytest.mark.asyncio
    async def test_no_squashfs_no_dists_falls_through_to_direct_rootfs(self) -> None:
        """ISO with no squashfs + no dists/ falls through to direct rootfs (Req 1.3)."""
        iso_reader = _MockISOReader(
            dirs={
                "": ["pool", "boot", "var"],
                "var": ["lib"],
                "var/lib": ["dpkg"],
                "var/lib/dpkg": ["status"],
            },
            files={
                "var/lib/dpkg/status": DPKG_STATUS_CONTENT.encode(),
            },
        )
        squashfs_reader = _MockSquashfsReader()
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/netinst.iso")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.strategy == ScanningStrategy.DPKG_METADATA.value
        assert len(result.packages) == 1
        assert result.packages[0].name == "bash"
        # No repository detection diagnostic
        assert not any("Repository structure detected" in d for d in result.diagnostics)

    @pytest.mark.asyncio
    async def test_io_error_on_root_listing_triggers_fallback(self) -> None:
        """I/O error on list_dir('') triggers fallback to direct rootfs (Req 1.4)."""
        iso_reader = _MockISOReader(
            # No "" key in dirs → list_dir("") raises FileNotFoundError
            dirs={
                "var": ["lib"],
                "var/lib": ["dpkg"],
                "var/lib/dpkg": ["status"],
            },
            files={
                "var/lib/dpkg/status": DPKG_STATUS_CONTENT.encode(),
            },
        )
        squashfs_reader = _MockSquashfsReader()
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/netinst.iso")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.strategy == ScanningStrategy.DPKG_METADATA.value
        assert len(result.packages) == 1
        assert result.packages[0].name == "bash"

    @pytest.mark.asyncio
    async def test_repository_detected_packages_found_short_circuits(self) -> None:
        """Repository detected + packages found short-circuits fallback (Req 7.2)."""
        iso_reader = _MockISOReader(
            dirs={
                "": ["dists", "pool", "var"],
                "dists": ["stable"],
                "dists/stable": ["main"],
                "dists/stable/main": ["binary-amd64"],
                "dists/stable/main/binary-amd64": ["Packages"],
                "var": ["lib"],
                "var/lib": ["dpkg"],
                "var/lib/dpkg": ["status"],
            },
            files={
                "dists/stable/main/binary-amd64/Packages": PACKAGES_CONTENT.encode(),
                "var/lib/dpkg/status": DPKG_STATUS_CONTENT.encode(),
            },
        )
        squashfs_reader = _MockSquashfsReader()
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/netinst.iso")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        # Should return from repository scanning with found packages
        assert result.strategy == ScanningStrategy.DPKG_METADATA.value
        assert len(result.packages) == 2
        assert result.packages[0].name == "bash"
        assert result.packages[0].status == "installed"
        # Verify repository detection diagnostic is present
        assert any("Repository structure detected" in d for d in result.diagnostics)

    @pytest.mark.asyncio
    async def test_repository_detected_zero_packages_falls_through(self) -> None:
        """Repository detected + 0 packages triggers fallback to direct rootfs (Req 7.3)."""
        iso_reader = _MockISOReader(
            dirs={
                "": ["dists", "var"],
                "dists": ["stable"],
                "dists/stable": ["main"],
                "dists/stable/main": ["binary-amd64"],
                "dists/stable/main/binary-amd64": ["Packages"],
                "var": ["lib"],
                "var/lib": ["dpkg"],
                "var/lib/dpkg": ["status"],
            },
            files={
                # Empty Packages file → no packages parsed
                "dists/stable/main/binary-amd64/Packages": b"",
                # Direct rootfs dpkg status should be used as fallback
                "var/lib/dpkg/status": DPKG_STATUS_CONTENT.encode(),
            },
        )
        squashfs_reader = _MockSquashfsReader()
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/netinst.iso")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        # Should fall through to direct rootfs
        assert result.strategy == ScanningStrategy.DPKG_METADATA.value
        assert len(result.packages) == 1
        assert result.packages[0].name == "bash"

    @pytest.mark.asyncio
    async def test_strategy_order_squashfs_repo_rootfs_filesystem(self) -> None:
        """Strategy order is squashfs → repo → direct rootfs → filesystem (Req 7.1)."""
        iso_reader = _MockISOReader(
            dirs={
                "": ["dists", "live"],
                "dists": ["stable"],
                "dists/stable": ["main"],
                "dists/stable/main": ["binary-amd64"],
                "dists/stable/main/binary-amd64": ["Packages"],
            },
            files={
                # Squashfs exists — this should be used
                "live/filesystem.squashfs": b"squashfs_data",
                # Repository Packages also exists — should NOT be reached
                "dists/stable/main/binary-amd64/Packages": PACKAGES_CONTENT.encode(),
            },
        )
        squashfs_reader = _MockSquashfsReader(
            files={
                "var/lib/dpkg/status": (
                    b"Package: coreutils\nVersion: 9.1-1\nArchitecture: amd64\nStatus: install ok installed\n"
                ),
            },
        )
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/netinst.iso")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        # Squashfs takes priority — should find coreutils (not bash from repo)
        assert result.strategy == ScanningStrategy.DPKG_METADATA.value
        assert len(result.packages) == 1
        assert result.packages[0].name == "coreutils"
        # Repository structure diagnostic should NOT be present
        assert not any("Repository structure detected" in d for d in result.diagnostics)


def _make_repo_dirs_and_files(
    *,
    packages_content: bytes | None = None,
    packages_content_2: bytes | None = None,
) -> tuple[dict[str, list[str]], dict[str, bytes]]:
    """Build directory/file dicts for a repo ISO with one or two Packages files."""
    content_1 = packages_content or PACKAGES_CONTENT.encode()
    dirs = {
        "": ["dists", "pool"],
        "dists": ["stable"],
        "dists/stable": ["main", "Release"],
        "dists/stable/main": ["binary-amd64"],
        "dists/stable/main/binary-amd64": ["Packages.gz"],
    }
    files: dict[str, bytes] = {
        "dists/stable/main/binary-amd64/Packages.gz": gzip.compress(content_1),
    }

    if packages_content_2 is not None:
        # Add binary-i386 to component listing and its own dir listing
        dirs["dists/stable/main"] = ["binary-amd64", "binary-i386"]
        dirs["dists/stable/main/binary-i386"] = ["Packages.gz"]
        files["dists/stable/main/binary-i386/Packages.gz"] = gzip.compress(packages_content_2)

    return dirs, files


class TestRepoCancellation:
    """Tests for cancellation during repository scanning (Req 6.1, 6.2)."""

    @pytest.mark.asyncio
    async def test_cancellation_after_discovery_returns_cancellation_result(self) -> None:
        """Token already cancelled before scanning.

        The _check_cancellation after discovery raises ScannerError,
        which is caught by the outer except in scan() and returns a
        cancellation result with empty packages and a diagnostic.
        (Req 6.1, 6.2)
        """
        dirs = {
            "": ["dists", "pool"],
            "dists": ["stable"],
            "dists/stable": ["main", "Release"],
            "dists/stable/main": ["binary-amd64"],
            "dists/stable/main/binary-amd64": ["Packages.gz"],
        }
        files = {
            "dists/stable/main/binary-amd64/Packages.gz": gzip.compress(PACKAGES_CONTENT.encode()),
        }

        iso_reader = _MockISOReader(dirs=dirs, files=files)
        squashfs_reader = _MockSquashfsReader()
        context = _make_context(cancelled=True)

        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )

        artifact = Artifact(type=ArtifactType.ISO, path="/test.iso", options={"snapshot_id": "0"})
        result = await scanner.scan(artifact, context)

        # Cancellation returns empty packages with a diagnostic
        assert result.packages == []
        assert result.strategy == ScanningStrategy.DPKG_METADATA.value
        # Should have a cancellation diagnostic
        assert any("cancelled" in d.lower() for d in result.diagnostics)

    @pytest.mark.asyncio
    async def test_cancellation_after_parsing_one_file_returns_partial(self) -> None:
        """Token cancelled after reading the first Packages file.

        Uses a _CancellingISOReader that cancels the token after the first
        read_file call. The scanner should return packages from the first
        file only.
        (Req 6.2)
        """
        pkg_content_1 = "Package: bash\nVersion: 5.2-1\nArchitecture: amd64\n"
        pkg_content_2 = "Package: vim\nVersion: 9.0-1\nArchitecture: amd64\n"

        dirs = {
            "": ["dists", "pool"],
            "dists": ["stable"],
            "dists/stable": ["main", "Release"],
            "dists/stable/main": ["binary-amd64", "binary-i386"],
            "dists/stable/main/binary-amd64": ["Packages.gz"],
            "dists/stable/main/binary-i386": ["Packages.gz"],
        }
        files = {
            "dists/stable/main/binary-amd64/Packages.gz": gzip.compress(pkg_content_1.encode()),
            "dists/stable/main/binary-i386/Packages.gz": gzip.compress(pkg_content_2.encode()),
        }

        token = CancellationToken()
        iso_reader = _CancellingISOReader(
            token=token,
            cancel_after_reads=1,
            dirs=dirs,
            files=files,
        )
        squashfs_reader = _MockSquashfsReader()

        ctx = MagicMock(spec=WorkflowContext)
        ctx.cancellation_token = token
        ctx.progress = _MockProgressReporter()

        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )

        artifact = Artifact(type=ArtifactType.ISO, path="/test.iso", options={"snapshot_id": "0"})
        result = await scanner.scan(artifact, ctx)

        # Should have the package from the first file (bash)
        assert len(result.packages) == 1
        assert result.packages[0].name == "bash"
        # Should NOT have the package from the second file (vim)
        assert all(p.name != "vim" for p in result.packages)
        # Should have a cancellation diagnostic
        assert any("cancelled" in d.lower() for d in result.diagnostics)

    @pytest.mark.asyncio
    async def test_cancellation_result_has_dpkg_metadata_strategy(self) -> None:
        """ScanResult from cancellation has DPKG_METADATA strategy.

        (Req 6.2).
        """
        dirs = {
            "": ["dists", "pool"],
            "dists": ["stable"],
            "dists/stable": ["main", "Release"],
            "dists/stable/main": ["binary-amd64"],
            "dists/stable/main/binary-amd64": ["Packages.gz"],
        }
        files = {
            "dists/stable/main/binary-amd64/Packages.gz": gzip.compress(PACKAGES_CONTENT.encode()),
        }

        # Pre-cancelled token — triggers cancellation at first check point
        iso_reader = _MockISOReader(dirs=dirs, files=files)
        squashfs_reader = _MockSquashfsReader()
        context = _make_context(cancelled=True)

        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=squashfs_reader,
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )

        artifact = Artifact(type=ArtifactType.ISO, path="/test.iso", options={"snapshot_id": "0"})
        result = await scanner.scan(artifact, context)

        assert result.strategy == ScanningStrategy.DPKG_METADATA.value


# ===========================================================================
# Task 5.2: Unit tests for Packages file discovery edge cases
# ===========================================================================


class TestRepoPackagesDiscovery:
    """Tests for _discover_packages_files edge cases and ScanResult fields."""

    def test_empty_dists_returns_no_packages_files(self) -> None:
        """Empty dists/ directory returns no Packages files (Req 2.6)."""
        iso_reader = _MockISOReader(
            dirs={
                "": ["dists"],
                "dists": [],  # Empty dists/
            },
        )
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=_MockSquashfsReader(),
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        iso_reader.opened = True

        diagnostics: list[str] = []
        result = scanner._discover_packages_files(diagnostics)

        assert result == []
        assert any("No Packages files found" in d for d in diagnostics)

    def test_metadata_entries_excluded_from_components(self) -> None:
        """Metadata entries (Release, InRelease) are not treated as components (Req 2.2)."""
        iso_reader = _MockISOReader(
            dirs={
                "": ["dists"],
                "dists": ["stable"],
                "dists/stable": ["Release", "InRelease", "main"],
                "dists/stable/main": ["binary-amd64"],
                "dists/stable/main/binary-amd64": ["Packages"],
            },
            files={
                "dists/stable/main/binary-amd64/Packages": PACKAGES_CONTENT.encode(),
            },
        )
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=_MockSquashfsReader(),
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        iso_reader.opened = True

        diagnostics: list[str] = []
        result = scanner._discover_packages_files(diagnostics)

        # Only main should be treated as a component, not Release or InRelease
        assert len(result) == 1
        assert result[0] == "dists/stable/main/binary-amd64/Packages"

    def test_packages_gz_preferred_over_packages(self) -> None:
        """Packages.gz preferred over Packages when both exist (Req 2.4)."""
        iso_reader = _MockISOReader(
            dirs={
                "": ["dists"],
                "dists": ["stable"],
                "dists/stable": ["main"],
                "dists/stable/main": ["binary-amd64"],
                "dists/stable/main/binary-amd64": ["Packages", "Packages.gz"],
            },
            files={
                "dists/stable/main/binary-amd64/Packages": PACKAGES_CONTENT.encode(),
                "dists/stable/main/binary-amd64/Packages.gz": gzip.compress(PACKAGES_CONTENT.encode()),
            },
        )
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=_MockSquashfsReader(),
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        iso_reader.opened = True

        diagnostics: list[str] = []
        result = scanner._discover_packages_files(diagnostics)

        # Only Packages.gz should be in the result
        assert len(result) == 1
        assert result[0] == "dists/stable/main/binary-amd64/Packages.gz"

    def test_io_error_on_one_codename_continues_others(self) -> None:
        """I/O error on one codename doesn't abort discovery of others (Req 2.5)."""

        # "dists/stable" will raise OSError since it's not in dirs
        # but we need to raise OSError, not FileNotFoundError
        # Use a custom approach: make list_dir for "dists/stable" raise OSError
        class _ErrorOnStableISOReader(_MockISOReader):
            def list_dir(self, path: str) -> list[str]:
                if path == "dists/stable":
                    raise OSError("I/O error reading directory")
                return super().list_dir(path)

        iso_reader = _ErrorOnStableISOReader(
            dirs={
                "": ["dists"],
                "dists": ["stable", "testing"],
                "dists/testing": ["main"],
                "dists/testing/main": ["binary-amd64"],
                "dists/testing/main/binary-amd64": ["Packages"],
            },
            files={
                "dists/testing/main/binary-amd64/Packages": PACKAGES_CONTENT.encode(),
            },
        )
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=_MockSquashfsReader(),
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        iso_reader.opened = True

        diagnostics: list[str] = []
        result = scanner._discover_packages_files(diagnostics)

        # Should still find Packages from "testing" despite "stable" failing
        assert len(result) == 1
        assert result[0] == "dists/testing/main/binary-amd64/Packages"
        # Should have a diagnostic about the I/O error on stable
        assert any("dists/stable" in d for d in diagnostics)

    @pytest.mark.asyncio
    async def test_scan_result_fields_correct(self) -> None:
        """ScanResult fields (artifact_path, strategy, duration) are correct (Req 5.1, 5.2)."""
        iso_reader = _MockISOReader(
            dirs={
                "": ["dists"],
                "dists": ["stable"],
                "dists/stable": ["main"],
                "dists/stable/main": ["binary-amd64"],
                "dists/stable/main/binary-amd64": ["Packages"],
            },
            files={
                "dists/stable/main/binary-amd64/Packages": PACKAGES_CONTENT.encode(),
            },
        )
        scanner = ISOScanner(
            iso_reader=iso_reader,
            squashfs_reader=_MockSquashfsReader(),
            contents_port=_make_contents_port(),
            package_port=_make_package_port(),
        )
        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/netinst.iso")
        ctx = _make_context()

        result = await scanner.scan(artifact, ctx)

        assert result.artifact_path == "/tmp/netinst.iso"
        assert result.strategy == ScanningStrategy.DPKG_METADATA.value
        assert result.duration_seconds >= 0
        assert len(result.packages) == 2
        assert result.packages[0].name == "bash"
        assert result.packages[1].name == "coreutils"


def _make_packages_content(*stanzas: str) -> bytes:
    """Build a Packages file content from stanza strings separated by blank lines."""
    return "\n\n".join(stanzas).encode("utf-8")


class TestRepoPackagesParsing:
    """Tests for Packages file parsing edge cases (Req 3.5, 4.2, 4.3, 4.4, 8.2, 8.3)."""

    def test_gzip_decompression_failure_records_diagnostic(self) -> None:
        """Gzip decompression failure records diagnostic and returns empty list (Req 3.5)."""
        iso_reader = _MockISOReader(
            files={
                "dists/stable/main/binary-amd64/Packages.gz": b"not-valid-gzip-data",
            },
            dirs={},
        )
        scanner = ISOScanner(iso_reader, _MockSquashfsReader(), _make_contents_port(), _make_package_port())

        diagnostics: list[str] = []
        result = scanner._parse_packages_file("dists/stable/main/binary-amd64/Packages.gz", diagnostics)

        assert result == []
        assert len(diagnostics) == 1
        assert "Failed to decompress" in diagnostics[0]
        assert "dists/stable/main/binary-amd64/Packages.gz" in diagnostics[0]

    def test_stanza_missing_package_field_skipped(self) -> None:
        """Stanza with Version but no Package is skipped with diagnostic (Req 8.2)."""
        content = _make_packages_content("Version: 1.0-1\nArchitecture: amd64\nSection: utils")
        iso_reader = _MockISOReader(
            files={"dists/stable/main/binary-amd64/Packages": content},
            dirs={},
        )
        scanner = ISOScanner(iso_reader, _MockSquashfsReader(), _make_contents_port(), _make_package_port())

        diagnostics: list[str] = []
        result = scanner._parse_packages_file("dists/stable/main/binary-amd64/Packages", diagnostics)

        assert result == []
        assert len(diagnostics) == 1
        assert "Stanza 1 in dists/stable/main/binary-amd64/Packages: skipped, missing field: Package" in diagnostics[0]

    def test_stanza_missing_version_field_skipped(self) -> None:
        """Stanza with Package but no Version is skipped with diagnostic (Req 8.2)."""
        content = _make_packages_content("Package: hello\nArchitecture: amd64\nSection: utils")
        iso_reader = _MockISOReader(
            files={"dists/stable/main/binary-amd64/Packages": content},
            dirs={},
        )
        scanner = ISOScanner(iso_reader, _MockSquashfsReader(), _make_contents_port(), _make_package_port())

        diagnostics: list[str] = []
        result = scanner._parse_packages_file("dists/stable/main/binary-amd64/Packages", diagnostics)

        assert result == []
        assert len(diagnostics) == 1
        assert "Stanza 1 in dists/stable/main/binary-amd64/Packages: skipped, missing field: Version" in diagnostics[0]

    def test_udeb_packages_included_with_installed_status(self) -> None:
        """Stanza with Section: debian-installer produces IdentifiedPackage with status 'installed' (Req 8.3)."""
        content = _make_packages_content(
            "Package: netcfg\nVersion: 1.200\nArchitecture: amd64\nSection: debian-installer"
        )
        iso_reader = _MockISOReader(
            files={"dists/stable/main/binary-amd64/Packages": content},
            dirs={},
        )
        scanner = ISOScanner(iso_reader, _MockSquashfsReader(), _make_contents_port(), _make_package_port())

        diagnostics: list[str] = []
        result = scanner._parse_packages_file("dists/stable/main/binary-amd64/Packages", diagnostics)

        assert len(result) == 1
        assert result[0].name == "netcfg"
        assert result[0].version == "1.200"
        assert result[0].architecture == "amd64"
        assert result[0].status == "installed"
        assert diagnostics == []

    @pytest.mark.asyncio
    async def test_duplicate_packages_across_files_deduplicated(self) -> None:
        """Two Packages files with same package are deduplicated in full scan (Req 4.2)."""
        stanza = "Package: bash\nVersion: 5.2-1\nArchitecture: amd64"
        content = _make_packages_content(stanza)
        gz_content = gzip.compress(content)

        iso_reader = _MockISOReader(
            dirs={
                "": ["dists"],
                "dists": ["stable"],
                "dists/stable": ["main", "contrib"],
                "dists/stable/main": ["binary-amd64"],
                "dists/stable/main/binary-amd64": ["Packages.gz"],
                "dists/stable/contrib": ["binary-amd64"],
                "dists/stable/contrib/binary-amd64": ["Packages.gz"],
            },
            files={
                "dists/stable/main/binary-amd64/Packages.gz": gz_content,
                "dists/stable/contrib/binary-amd64/Packages.gz": gz_content,
            },
        )
        scanner = ISOScanner(iso_reader, _MockSquashfsReader(), _make_contents_port(), _make_package_port())

        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/test.iso", options={"snapshot_id": "0"})
        context = _make_context()
        result = await scanner.scan(artifact, context)

        # Only 1 unique package despite appearing in 2 files
        assert len(result.packages) == 1
        assert result.packages[0].name == "bash"
        assert result.packages[0].version == "5.2-1"
        assert result.packages[0].architecture == "amd64"

    @pytest.mark.asyncio
    async def test_partial_file_failure_still_yields_packages(self) -> None:
        """One Packages file fails (invalid gzip), another succeeds. Successful packages present (Req 4.3)."""
        valid_stanza = "Package: coreutils\nVersion: 9.1-1\nArchitecture: amd64"
        valid_content = gzip.compress(_make_packages_content(valid_stanza))

        iso_reader = _MockISOReader(
            dirs={
                "": ["dists"],
                "dists": ["stable"],
                "dists/stable": ["main", "contrib"],
                "dists/stable/main": ["binary-amd64"],
                "dists/stable/main/binary-amd64": ["Packages.gz"],
                "dists/stable/contrib": ["binary-amd64"],
                "dists/stable/contrib/binary-amd64": ["Packages.gz"],
            },
            files={
                "dists/stable/main/binary-amd64/Packages.gz": b"invalid-gzip",
                "dists/stable/contrib/binary-amd64/Packages.gz": valid_content,
            },
        )
        scanner = ISOScanner(iso_reader, _MockSquashfsReader(), _make_contents_port(), _make_package_port())

        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/test.iso", options={"snapshot_id": "0"})
        context = _make_context()
        result = await scanner.scan(artifact, context)

        # Package from successful file is present
        assert len(result.packages) == 1
        assert result.packages[0].name == "coreutils"
        # Diagnostic about decompression failure recorded
        decompress_diags = [d for d in result.diagnostics if "Failed to decompress" in d]
        assert len(decompress_diags) == 1

    @pytest.mark.asyncio
    async def test_summary_diagnostic_includes_count(self) -> None:
        """After full scan, diagnostics contain summary with count info (Req 4.4)."""
        content = _make_packages_content(
            "Package: bash\nVersion: 5.2-1\nArchitecture: amd64",
            "Package: coreutils\nVersion: 9.1-1\nArchitecture: amd64",
        )
        gz_content = gzip.compress(content)

        iso_reader = _MockISOReader(
            dirs={
                "": ["dists"],
                "dists": ["stable"],
                "dists/stable": ["main"],
                "dists/stable/main": ["binary-amd64"],
                "dists/stable/main/binary-amd64": ["Packages.gz"],
            },
            files={
                "dists/stable/main/binary-amd64/Packages.gz": gz_content,
            },
        )
        scanner = ISOScanner(iso_reader, _MockSquashfsReader(), _make_contents_port(), _make_package_port())

        artifact = Artifact(type=ArtifactType.ISO, path="/tmp/test.iso", options={"snapshot_id": "0"})
        context = _make_context()
        result = await scanner.scan(artifact, context)

        # Summary diagnostic matches expected pattern
        summary_diags = [
            d
            for d in result.diagnostics
            if "Repository scan:" in d and "unique packages from" in d and "Packages file(s)" in d
        ]
        assert len(summary_diags) == 1
        assert "2 unique packages from 1 Packages file(s)" in summary_diags[0]
