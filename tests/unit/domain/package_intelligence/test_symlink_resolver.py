"""Unit tests for the symlink resolver.

Tests cover relative path resolution, absolute path handling, multi-hop chains,
cycle detection, depth limits, and failure cases.
"""

from __future__ import annotations

import pytest

from debcraft.domain.package_intelligence.symlink_resolver import SymlinkResolver
from debcraft.domain.package_intelligence.values import SymlinkResolutionResult


class FakeContentsLookup:
    """Fake ContentsLookupPort for testing."""

    def __init__(
        self,
        owners: dict[str, str] | None = None,
        copyrights: dict[str, str] | None = None,
    ) -> None:
        self._owners = owners or {}
        self._copyrights = copyrights or {}

    def find_owner(self, file_path: str) -> str | None:
        return self._owners.get(file_path)

    def get_copyright_content(self, package_name: str) -> str | None:
        return self._copyrights.get(package_name)


@pytest.mark.unit
class TestSymlinkResolverRelativePaths:
    """Verify relative symlink targets are resolved correctly."""

    def test_simple_relative_path(self):
        """Relative target ../other-package/copyright resolves correctly."""
        lookup = FakeContentsLookup(
            owners={"/usr/share/doc/other-package/copyright": "other-package"},
            copyrights={"other-package": "Copyright 2024 Other Package"},
        )
        resolver = SymlinkResolver(lookup)
        result = resolver.resolve("../other-package/copyright", "/usr/share/doc/my-package")
        assert result.resolved is True
        assert result.target_path == "/usr/share/doc/other-package/copyright"
        assert result.owning_package == "other-package"
        assert result.copyright_content == "Copyright 2024 Other Package"

    def test_relative_path_with_dot_dot_normalization(self):
        """Multiple ../ components are normalized properly."""
        # From /usr/share/doc/my-pkg/subdir, going ../../base-pkg/copyright
        # resolves to /usr/share/doc/base-pkg/copyright
        lookup = FakeContentsLookup(
            owners={"/usr/share/doc/base-pkg/copyright": "base-pkg"},
            copyrights={"base-pkg": "Copyright 2024 Base"},
        )
        resolver = SymlinkResolver(lookup)
        result = resolver.resolve("../../base-pkg/copyright", "/usr/share/doc/my-pkg/subdir")
        assert result.resolved is True
        assert result.target_path == "/usr/share/doc/base-pkg/copyright"

    def test_relative_path_same_directory(self):
        """Relative target in same directory resolves correctly."""
        lookup = FakeContentsLookup(
            owners={"/usr/share/doc/pkg/license.txt": "pkg"},
            copyrights={"pkg": "MIT License"},
        )
        resolver = SymlinkResolver(lookup)
        result = resolver.resolve("license.txt", "/usr/share/doc/pkg")
        assert result.resolved is True
        assert result.target_path == "/usr/share/doc/pkg/license.txt"


@pytest.mark.unit
class TestSymlinkResolverAbsolutePaths:
    """Verify absolute symlink targets are used directly."""

    def test_absolute_target_path(self):
        """Absolute path target is used without modification."""
        lookup = FakeContentsLookup(
            owners={"/usr/share/doc/other-pkg/copyright": "other-pkg"},
            copyrights={"other-pkg": "Copyright 2024 Other"},
        )
        resolver = SymlinkResolver(lookup)
        result = resolver.resolve("/usr/share/doc/other-pkg/copyright", "/usr/share/doc/my-pkg")
        assert result.resolved is True
        assert result.target_path == "/usr/share/doc/other-pkg/copyright"
        assert result.owning_package == "other-pkg"
        assert result.copyright_content == "Copyright 2024 Other"


@pytest.mark.unit
class TestSymlinkResolverMultiHop:
    """Verify multi-hop symlink chain resolution."""

    def test_two_hop_chain(self):
        """A chain of two hops resolves to the final copyright content."""
        lookup = FakeContentsLookup(
            owners={
                "/usr/share/doc/pkg-b/copyright": "pkg-b",
                "/usr/share/doc/pkg-c/copyright": "pkg-c",
            },
            copyrights={
                # pkg-b's "copyright" is itself a symlink path
                "pkg-b": "../pkg-c/copyright",
                # pkg-c has actual content
                "pkg-c": "Copyright 2024 Pkg C\nMIT License",
            },
        )
        resolver = SymlinkResolver(lookup)
        result = resolver.resolve("../pkg-b/copyright", "/usr/share/doc/pkg-a")
        assert result.resolved is True
        assert result.target_path == "/usr/share/doc/pkg-c/copyright"
        assert result.owning_package == "pkg-c"
        assert result.copyright_content == "Copyright 2024 Pkg C\nMIT License"

    def test_three_hop_chain(self):
        """A chain of three hops resolves correctly."""
        lookup = FakeContentsLookup(
            owners={
                "/usr/share/doc/pkg-b/copyright": "pkg-b",
                "/usr/share/doc/pkg-c/copyright": "pkg-c",
                "/usr/share/doc/pkg-d/copyright": "pkg-d",
            },
            copyrights={
                "pkg-b": "../pkg-c/copyright",
                "pkg-c": "../pkg-d/copyright",
                "pkg-d": "Copyright 2024 Final\nGPL-2.0",
            },
        )
        resolver = SymlinkResolver(lookup)
        result = resolver.resolve("../pkg-b/copyright", "/usr/share/doc/pkg-a")
        assert result.resolved is True
        assert result.target_path == "/usr/share/doc/pkg-d/copyright"
        assert result.owning_package == "pkg-d"


@pytest.mark.unit
class TestSymlinkResolverCycleDetection:
    """Verify cycle detection in symlink chains."""

    def test_direct_cycle(self):
        """A symlink that points back to itself is detected."""
        lookup = FakeContentsLookup(
            owners={"/usr/share/doc/pkg-a/copyright": "pkg-a"},
            copyrights={"pkg-a": "/usr/share/doc/pkg-a/copyright"},
        )
        resolver = SymlinkResolver(lookup)
        result = resolver.resolve("/usr/share/doc/pkg-a/copyright", "/usr/share/doc/pkg-a")
        assert result.resolved is False
        assert "Circular" in (result.failure_reason or "")

    def test_indirect_cycle(self):
        """A->B->A cycle is detected."""
        lookup = FakeContentsLookup(
            owners={
                "/usr/share/doc/pkg-a/copyright": "pkg-a",
                "/usr/share/doc/pkg-b/copyright": "pkg-b",
            },
            copyrights={
                "pkg-a": "../pkg-b/copyright",
                "pkg-b": "../pkg-a/copyright",
            },
        )
        resolver = SymlinkResolver(lookup)
        result = resolver.resolve("../pkg-a/copyright", "/usr/share/doc/pkg-x")
        assert result.resolved is False
        assert "Circular" in (result.failure_reason or "")


@pytest.mark.unit
class TestSymlinkResolverDepthLimit:
    """Verify maximum resolution depth is enforced."""

    def test_exceeds_max_depth(self):
        """A chain exceeding 10 hops returns failure."""
        # Create a chain of 11 packages where each points to the next
        owners = {}
        copyrights = {}
        for i in range(11):
            path = f"/usr/share/doc/pkg-{i}/copyright"
            owners[path] = f"pkg-{i}"
            copyrights[f"pkg-{i}"] = f"../pkg-{i + 1}/copyright"

        lookup = FakeContentsLookup(owners=owners, copyrights=copyrights)
        resolver = SymlinkResolver(lookup)
        result = resolver.resolve("../pkg-0/copyright", "/usr/share/doc/start")
        assert result.resolved is False
        assert "depth" in (result.failure_reason or "").lower()


@pytest.mark.unit
class TestSymlinkResolverFailureCases:
    """Verify failure results for unresolvable cases."""

    def test_no_owner_found(self):
        """Returns failure when no package owns the resolved path."""
        lookup = FakeContentsLookup()
        resolver = SymlinkResolver(lookup)
        result = resolver.resolve("../missing-pkg/copyright", "/usr/share/doc/my-pkg")
        assert result.resolved is False
        assert result.failure_reason is not None
        assert "no package owns" in result.failure_reason.lower()

    def test_owner_found_but_no_copyright_content(self):
        """Returns failure when owner has no copyright content."""
        lookup = FakeContentsLookup(
            owners={"/usr/share/doc/empty-pkg/copyright": "empty-pkg"},
            copyrights={},
        )
        resolver = SymlinkResolver(lookup)
        result = resolver.resolve("../empty-pkg/copyright", "/usr/share/doc/my-pkg")
        assert result.resolved is False
        assert "no copyright content" in (result.failure_reason or "").lower()

    def test_never_raises_exceptions(self):
        """The resolver never raises — all errors become failure results."""
        lookup = FakeContentsLookup()
        resolver = SymlinkResolver(lookup)
        # Various inputs that could cause issues
        result = resolver.resolve("", "/some/dir")
        assert isinstance(result, SymlinkResolutionResult)
        assert result.resolved is False
