"""Property-based tests for filesystem analyzer output invariants.

# Feature: m6-artifact-scanners, Property 9: Filesystem Analyzer Output Invariants

**Validates: Requirements 11.3, 11.4**

Property 9: Filesystem Analyzer Output Invariants
  For all random file path lists and ContentsIndexPort mappings (including
  many-to-one duplicates), the analyze_filesystem function SHALL:
  1. Produce no duplicate package names in its output list.
  2. Set all output package statuses to "inferred".
"""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.domain.scanner.filesystem_analyzer import analyze_filesystem

# ===========================================================================
# Strategies
# ===========================================================================

# Valid Debian package name characters
_PKG_NAME_START = "abcdefghijklmnopqrstuvwxyz"
_PKG_NAME_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789-"


@st.composite
def st_package_name(draw: st.DrawFn) -> str:
    """Generate a valid Debian package name."""
    first = draw(st.sampled_from(list(_PKG_NAME_START)))
    rest = draw(st.text(alphabet=_PKG_NAME_CHARS, min_size=1, max_size=20))
    # Ensure name doesn't end with a hyphen
    name = first + rest.rstrip("-")
    return name if len(name) >= 2 else first + "a"


@st.composite
def st_version(draw: st.DrawFn) -> str:
    """Generate a valid Debian version string."""
    major = draw(st.integers(min_value=0, max_value=99))
    minor = draw(st.integers(min_value=0, max_value=99))
    revision = draw(st.integers(min_value=1, max_value=9))
    return f"{major}.{minor}-{revision}"


_ARCHITECTURES = st.sampled_from(["amd64", "arm64", "i386", "all", "armhf"])


@st.composite
def st_file_path(draw: st.DrawFn) -> str:
    """Generate a random Unix filesystem path."""
    depth = draw(st.integers(min_value=1, max_value=5))
    segments = [
        draw(
            st.text(
                alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-.",
                min_size=1,
                max_size=15,
            )
        )
        for _ in range(depth)
    ]
    return "/" + "/".join(segments)


@st.composite
def st_fs_analyzer_input(
    draw: st.DrawFn,
) -> tuple[
    list[str],
    dict[str, str],
    dict[str, tuple[str, str, str]],
]:
    """Generate filesystem analyzer inputs with intentional duplicates.

    Returns:
        Tuple of (file_paths, path_to_package_mapping, package_metadata).
        The path_to_package_mapping maps multiple paths to the same package
        name to exercise the deduplication logic.
    """
    # Generate a pool of unique package names (1–10 packages)
    num_packages = draw(st.integers(min_value=1, max_value=10))
    package_names = draw(
        st.lists(
            st_package_name(),
            min_size=num_packages,
            max_size=num_packages,
            unique=True,
        )
    )

    # Generate file paths (more paths than packages to ensure many-to-one mapping)
    num_paths = draw(st.integers(min_value=num_packages, max_value=num_packages * 5))
    file_paths = draw(st.lists(st_file_path(), min_size=num_paths, max_size=num_paths, unique=True))

    # Map each path to a package name (many paths can map to same package)
    path_to_package: dict[str, str] = {}
    for path in file_paths:
        pkg_name = draw(st.sampled_from(package_names))
        path_to_package[path] = pkg_name

    # Generate metadata for each package
    package_metadata: dict[str, tuple[str, str, str]] = {}
    for name in package_names:
        version = draw(st_version())
        arch = draw(_ARCHITECTURES)
        package_metadata[name] = (version, arch, "installed")

    return file_paths, path_to_package, package_metadata


# ===========================================================================
# Fake Ports
# ===========================================================================


class FakeContentsIndexPort:
    """Fake ContentsIndexPort that returns a pre-configured mapping."""

    def __init__(self, path_to_package: dict[str, str]) -> None:
        self._mapping = path_to_package

    async def find_owners(self, file_paths: list[str], snapshot_id: int) -> dict[str, str]:
        """Return subset of mapping for provided paths."""
        return {p: self._mapping[p] for p in file_paths if p in self._mapping}


class FakePackageLookupPort:
    """Fake PackageLookupPort that returns pre-configured metadata."""

    def __init__(self, metadata: dict[str, tuple[str, str, str]]) -> None:
        self._metadata = metadata

    async def find_by_name(self, package_name: str, snapshot_id: int) -> tuple[str, str, str] | None:
        """Return metadata tuple or None if not configured."""
        return self._metadata.get(package_name)


# ===========================================================================
# Property 9: Filesystem Analyzer Output Invariants
# ===========================================================================


@pytest.mark.unit
class TestProperty9FilesystemAnalyzerOutputInvariants:
    """Property 9: Filesystem Analyzer Output Invariants.

    For all random file path lists and ContentsIndexPort mappings (including
    many-to-one duplicates), the analyze_filesystem function SHALL:
    1. Produce no duplicate package names in output.
    2. Set all output package statuses to "inferred".

    **Validates: Requirements 11.3, 11.4**
    """

    @given(data=st_fs_analyzer_input())
    def test_no_duplicate_package_names_in_output(
        self,
        data: tuple[list[str], dict[str, str], dict[str, tuple[str, str, str]]],
    ) -> None:
        """Output contains no duplicate package names even when many paths map to same package.

        **Validates: Requirements 11.3**
        """
        file_paths, path_to_package, package_metadata = data

        contents_port = FakeContentsIndexPort(path_to_package)
        package_port = FakePackageLookupPort(package_metadata)

        result = asyncio.run(
            analyze_filesystem(
                file_paths=file_paths,
                contents_port=contents_port,
                package_port=package_port,
                snapshot_id=1,
            )
        )

        # Extract package names from result
        output_names = [pkg.name for pkg in result.packages]

        # Assert no duplicates
        assert len(output_names) == len(set(output_names)), f"Duplicate package names found in output: {output_names}"

    @given(data=st_fs_analyzer_input())
    def test_all_statuses_are_inferred(
        self,
        data: tuple[list[str], dict[str, str], dict[str, tuple[str, str, str]]],
    ) -> None:
        """All output packages have status == "inferred".

        **Validates: Requirements 11.4**
        """
        file_paths, path_to_package, package_metadata = data

        contents_port = FakeContentsIndexPort(path_to_package)
        package_port = FakePackageLookupPort(package_metadata)

        result = asyncio.run(
            analyze_filesystem(
                file_paths=file_paths,
                contents_port=contents_port,
                package_port=package_port,
                snapshot_id=1,
            )
        )

        # Assert all statuses are "inferred"
        for pkg in result.packages:
            assert pkg.status == "inferred", f"Package '{pkg.name}' has status '{pkg.status}', expected 'inferred'"

    @given(data=st_fs_analyzer_input())
    def test_output_count_at_most_unique_packages(
        self,
        data: tuple[list[str], dict[str, str], dict[str, tuple[str, str, str]]],
    ) -> None:
        """Output package count is at most the number of unique package names in the mapping.

        **Validates: Requirements 11.3**
        """
        file_paths, path_to_package, package_metadata = data

        contents_port = FakeContentsIndexPort(path_to_package)
        package_port = FakePackageLookupPort(package_metadata)

        result = asyncio.run(
            analyze_filesystem(
                file_paths=file_paths,
                contents_port=contents_port,
                package_port=package_port,
                snapshot_id=1,
            )
        )

        # The number of unique package names in the mapping
        unique_names_in_mapping = set(path_to_package.values())

        # Output should have at most that many packages
        assert len(result.packages) <= len(unique_names_in_mapping), (
            f"Got {len(result.packages)} packages but only {len(unique_names_in_mapping)} unique names in mapping"
        )
