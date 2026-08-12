"""Unit tests for the filesystem analyzer module.

Tests the analyze_filesystem async function covering:
- Path truncation and diagnostic generation
- Batch querying via ContentsIndexPort
- Deduplication by package name
- Unresolved package handling
- All results have status "inferred"
- Empty results diagnostic
"""

from __future__ import annotations

import pytest

from debcraft.domain.scanner.filesystem_analyzer import (
    FilesystemAnalysisResult,
    analyze_filesystem,
)
from debcraft.domain.scanner.values import IdentifiedPackage


class FakeContentsPort:
    """Fake ContentsIndexPort for testing."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping
        self.called_with: tuple[list[str], int] | None = None

    async def find_owners(self, file_paths: list[str], snapshot_id: int) -> dict[str, str]:
        self.called_with = (file_paths, snapshot_id)
        return {p: pkg for p, pkg in self._mapping.items() if p in file_paths}


class FakePackagePort:
    """Fake PackageLookupPort for testing."""

    def __init__(self, packages: dict[str, tuple[str, str, str] | None]) -> None:
        self._packages = packages
        self.queried_names: list[str] = []

    async def find_by_name(self, package_name: str, snapshot_id: int) -> tuple[str, str, str] | None:
        _ = snapshot_id  # satisfy protocol signature
        self.queried_names.append(package_name)
        return self._packages.get(package_name)


@pytest.mark.unit
class TestFilesystemAnalysisResult:
    """Verify FilesystemAnalysisResult frozen dataclass."""

    def test_construction_with_defaults(self):
        result = FilesystemAnalysisResult()
        assert result.packages == []
        assert result.diagnostics == []

    def test_construction_with_values(self):
        pkg = IdentifiedPackage(name="bash", version="5.2", architecture="amd64", status="inferred")
        result = FilesystemAnalysisResult(packages=[pkg], diagnostics=["some warning"])
        assert result.packages == [pkg]
        assert result.diagnostics == ["some warning"]

    def test_frozen(self):
        import dataclasses

        result = FilesystemAnalysisResult()
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.packages = []  # type: ignore[misc]


@pytest.mark.unit
class TestAnalyzeFilesystem:
    """Tests for the analyze_filesystem async function."""

    @pytest.mark.asyncio
    async def test_empty_file_paths(self):
        """Empty input returns empty result with no-data diagnostic."""
        contents_port = FakeContentsPort({})
        package_port = FakePackagePort({})

        result = await analyze_filesystem(
            file_paths=[],
            contents_port=contents_port,
            package_port=package_port,
            snapshot_id=1,
        )

        assert result.packages == []
        assert any("No contents data" in d for d in result.diagnostics)

    @pytest.mark.asyncio
    async def test_basic_package_identification(self):
        """Paths matching contents index produce identified packages."""
        contents_port = FakeContentsPort(
            {
                "/usr/bin/bash": "bash",
                "/usr/lib/libc.so.6": "libc6",
            }
        )
        package_port = FakePackagePort(
            {
                "bash": ("5.2-2", "amd64", "installed"),
                "libc6": ("2.36-9", "amd64", "installed"),
            }
        )

        result = await analyze_filesystem(
            file_paths=["/usr/bin/bash", "/usr/lib/libc.so.6"],
            contents_port=contents_port,
            package_port=package_port,
            snapshot_id=42,
        )

        assert len(result.packages) == 2
        names = {p.name for p in result.packages}
        assert names == {"bash", "libc6"}
        # All must have status "inferred"
        for pkg in result.packages:
            assert pkg.status == "inferred"

    @pytest.mark.asyncio
    async def test_deduplication_by_package_name(self):
        """Multiple paths mapping to same package produce single entry."""
        contents_port = FakeContentsPort(
            {
                "/usr/bin/bash": "bash",
                "/usr/share/doc/bash/README": "bash",
                "/usr/share/man/man1/bash.1.gz": "bash",
            }
        )
        package_port = FakePackagePort(
            {
                "bash": ("5.2-2", "amd64", "installed"),
            }
        )

        result = await analyze_filesystem(
            file_paths=[
                "/usr/bin/bash",
                "/usr/share/doc/bash/README",
                "/usr/share/man/man1/bash.1.gz",
            ],
            contents_port=contents_port,
            package_port=package_port,
            snapshot_id=1,
        )

        assert len(result.packages) == 1
        assert result.packages[0].name == "bash"

    @pytest.mark.asyncio
    async def test_path_truncation(self):
        """Paths exceeding max_paths are truncated with diagnostic."""
        # Create 10 paths but set max_paths=3
        all_paths = [f"/path/{i}" for i in range(10)]
        contents_port = FakeContentsPort(
            {
                "/path/0": "pkg-a",
                "/path/1": "pkg-b",
                "/path/2": "pkg-c",
            }
        )
        package_port = FakePackagePort(
            {
                "pkg-a": ("1.0", "amd64", "installed"),
                "pkg-b": ("2.0", "amd64", "installed"),
                "pkg-c": ("3.0", "amd64", "installed"),
            }
        )

        result = await analyze_filesystem(
            file_paths=all_paths,
            contents_port=contents_port,
            package_port=package_port,
            snapshot_id=1,
            max_paths=3,
        )

        # Should have truncation diagnostic
        assert any("Path limit reached" in d for d in result.diagnostics)
        assert any("processed 3 of 10" in d for d in result.diagnostics)
        assert any("7 skipped" in d for d in result.diagnostics)

    @pytest.mark.asyncio
    async def test_truncation_only_processes_first_n_paths(self):
        """Only the first max_paths entries are sent to contents_port."""
        all_paths = [f"/path/{i}" for i in range(5)]
        contents_port = FakeContentsPort(
            {
                "/path/0": "pkg-a",
                "/path/1": "pkg-b",
                "/path/3": "pkg-c",  # index 3 is beyond max_paths=2
            }
        )
        package_port = FakePackagePort(
            {
                "pkg-a": ("1.0", "amd64", "installed"),
                "pkg-b": ("2.0", "amd64", "installed"),
                "pkg-c": ("3.0", "amd64", "installed"),
            }
        )

        result = await analyze_filesystem(
            file_paths=all_paths,
            contents_port=contents_port,
            package_port=package_port,
            snapshot_id=1,
            max_paths=2,
        )

        # Only first 2 paths were sent
        assert contents_port.called_with is not None
        assert contents_port.called_with[0] == ["/path/0", "/path/1"]
        # Only pkg-a and pkg-b should be found
        names = {p.name for p in result.packages}
        assert "pkg-c" not in names

    @pytest.mark.asyncio
    async def test_unresolved_package_skipped_with_diagnostic(self):
        """Packages not found in PackageLookupPort are skipped with diagnostic."""
        contents_port = FakeContentsPort(
            {
                "/usr/bin/foo": "foo-pkg",
                "/usr/bin/bar": "bar-pkg",
            }
        )
        package_port = FakePackagePort(
            {
                "foo-pkg": ("1.0", "amd64", "installed"),
                "bar-pkg": None,  # Not found
            }
        )

        result = await analyze_filesystem(
            file_paths=["/usr/bin/foo", "/usr/bin/bar"],
            contents_port=contents_port,
            package_port=package_port,
            snapshot_id=1,
        )

        assert len(result.packages) == 1
        assert result.packages[0].name == "foo-pkg"
        assert any("bar-pkg" in d and "no metadata available" in d for d in result.diagnostics)

    @pytest.mark.asyncio
    async def test_all_packages_have_inferred_status(self):
        """Every returned package has status 'inferred'."""
        contents_port = FakeContentsPort(
            {
                "/a": "pkg1",
                "/b": "pkg2",
                "/c": "pkg3",
            }
        )
        package_port = FakePackagePort(
            {
                "pkg1": ("1.0", "amd64", "installed"),
                "pkg2": ("2.0", "arm64", "installed"),
                "pkg3": ("3.0", "all", "installed"),
            }
        )

        result = await analyze_filesystem(
            file_paths=["/a", "/b", "/c"],
            contents_port=contents_port,
            package_port=package_port,
            snapshot_id=5,
        )

        assert len(result.packages) == 3
        for pkg in result.packages:
            assert pkg.status == "inferred"

    @pytest.mark.asyncio
    async def test_no_matches_returns_empty_with_diagnostic(self):
        """When no paths match, result is empty with no-data diagnostic."""
        contents_port = FakeContentsPort({})  # Nothing matches
        package_port = FakePackagePort({})

        result = await analyze_filesystem(
            file_paths=["/usr/bin/unknown", "/opt/something"],
            contents_port=contents_port,
            package_port=package_port,
            snapshot_id=1,
        )

        assert result.packages == []
        assert any("No contents data" in d for d in result.diagnostics)

    @pytest.mark.asyncio
    async def test_version_and_architecture_from_port(self):
        """Version and architecture come from PackageLookupPort."""
        contents_port = FakeContentsPort({"/usr/bin/vim": "vim"})
        package_port = FakePackagePort(
            {
                "vim": ("9.0.1000-1", "arm64", "installed"),
            }
        )

        result = await analyze_filesystem(
            file_paths=["/usr/bin/vim"],
            contents_port=contents_port,
            package_port=package_port,
            snapshot_id=99,
        )

        assert len(result.packages) == 1
        pkg = result.packages[0]
        assert pkg.name == "vim"
        assert pkg.version == "9.0.1000-1"
        assert pkg.architecture == "arm64"
        assert pkg.status == "inferred"

    @pytest.mark.asyncio
    async def test_snapshot_id_passed_to_ports(self):
        """The snapshot_id is forwarded to both ports."""
        contents_port = FakeContentsPort({"/a": "pkg"})
        package_port = FakePackagePort({"pkg": ("1.0", "amd64", "installed")})

        await analyze_filesystem(
            file_paths=["/a"],
            contents_port=contents_port,
            package_port=package_port,
            snapshot_id=777,
        )

        assert contents_port.called_with is not None
        assert contents_port.called_with[1] == 777
