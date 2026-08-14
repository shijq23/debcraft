"""Property-based tests for symlink resolver.

# Feature: package-intelligence, Property 15: Symlink Resolution Terminates Within Bounds
# Feature: package-intelligence, Property 16: Symlink Relative Path Resolution

**Validates: Requirements 8.2, 8.5, 8.7**
"""

from __future__ import annotations

import posixpath
import signal
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.domain.package_intelligence.symlink_resolver import SymlinkResolver
from debcraft.domain.package_intelligence.values import SymlinkResolutionResult

if TYPE_CHECKING:
    pass


# ===========================================================================
# Fake ContentsLookupPort implementation
# ===========================================================================


class FakeContentsLookup:
    """A fake ContentsLookupPort that uses a dict-based mapping.

    Supports configuring:
    - file_owners: maps file paths to owning package names
    - copyright_contents: maps package names to their copyright content
    """

    def __init__(
        self,
        file_owners: dict[str, str] | None = None,
        copyright_contents: dict[str, str] | None = None,
    ) -> None:
        """Initialize with optional mappings."""
        self._file_owners: dict[str, str] = file_owners or {}
        self._copyright_contents: dict[str, str] = copyright_contents or {}

    def find_owner(self, file_path: str) -> str | None:
        """Return the owner package for a path, or None."""
        return self._file_owners.get(file_path)

    def get_copyright_content(self, package_name: str) -> str | None:
        """Return copyright content for a package, or None."""
        return self._copyright_contents.get(package_name)


# ===========================================================================
# Strategies for generating symlink chains (Property 15)
# ===========================================================================

# Path component strategy: valid non-empty directory/file names
_path_component = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-",
    min_size=1,
    max_size=10,
)


@st.composite
def _absolute_path(draw: st.DrawFn) -> str:
    """Generate a random absolute POSIX path."""
    components = draw(st.lists(_path_component, min_size=1, max_size=5))
    return "/" + "/".join(components)


@st.composite
def _symlink_chain_no_cycle(draw: st.DrawFn) -> tuple[int, FakeContentsLookup, str, str]:
    """Generate a linear symlink chain of depth n (no cycles).

    Returns (depth, fake_lookup, initial_target, source_dir).
    The chain resolves through n hops before reaching actual copyright content.
    """
    depth = draw(st.integers(min_value=0, max_value=15))

    # Generate unique absolute paths for each hop in the chain
    paths: list[str] = []
    for _i in range(depth + 1):
        path = draw(_absolute_path().filter(lambda p, existing=paths: p not in existing))
        paths.append(path)

    # Build the lookup mappings
    file_owners: dict[str, str] = {}
    copyright_contents: dict[str, str] = {}

    # Each path is "owned" by a unique package
    for i, path in enumerate(paths):
        pkg_name = f"pkg-{i}"
        file_owners[path] = pkg_name

        if i < depth:
            # Intermediate hops: copyright content is another symlink path
            # (absolute path that looks like a symlink target)
            copyright_contents[pkg_name] = paths[i + 1]
        else:
            # Final hop: actual copyright content (multi-line to avoid _is_symlink_path)
            copyright_contents[pkg_name] = f"Copyright 2024 Test\nLicense: MIT\nPackage: {pkg_name}"

    fake_lookup = FakeContentsLookup(
        file_owners=file_owners,
        copyright_contents=copyright_contents,
    )

    # The initial target is the first path (absolute), source_dir doesn't matter for absolute
    initial_target = paths[0]
    source_dir = "/usr/share/doc/some-package"

    return depth, fake_lookup, initial_target, source_dir


@st.composite
def _symlink_chain_with_cycle(draw: st.DrawFn) -> tuple[FakeContentsLookup, str, str]:
    """Generate a symlink chain that contains a cycle.

    Returns (fake_lookup, initial_target, source_dir).
    """
    # Generate 2-5 paths that form a cycle
    cycle_length = draw(st.integers(min_value=2, max_value=5))

    paths: list[str] = []
    for _i in range(cycle_length):
        path = draw(_absolute_path().filter(lambda p, existing=paths: p not in existing))
        paths.append(path)

    # Build circular chain: each path's copyright points to the next, last points back to first
    file_owners: dict[str, str] = {}
    copyright_contents: dict[str, str] = {}

    for i, path in enumerate(paths):
        pkg_name = f"cycle-pkg-{i}"
        file_owners[path] = pkg_name
        # Point to next path in the cycle (wrapping around)
        next_path = paths[(i + 1) % cycle_length]
        copyright_contents[pkg_name] = next_path

    fake_lookup = FakeContentsLookup(
        file_owners=file_owners,
        copyright_contents=copyright_contents,
    )

    initial_target = paths[0]
    source_dir = "/usr/share/doc/cyclic-package"

    return fake_lookup, initial_target, source_dir


# ===========================================================================
# Property 15: Symlink Resolution Terminates Within Bounds
# ===========================================================================


class _TimeoutError(Exception):
    """Raised when a function call exceeds time limit."""


def _timeout_handler(signum: int, frame: object) -> None:
    """Signal handler for SIGALRM timeout."""
    raise _TimeoutError("Function did not terminate within time limit")


@pytest.mark.unit
@pytest.mark.package
class TestProperty15SymlinkResolutionTerminatesWithinBounds:
    """Property 15: Symlink Resolution Terminates Within Bounds.

    For any symlink chain of depth n, the Symlink_Resolver SHALL either
    successfully resolve the target (when n <= 10 and no cycles exist) or
    return a failure result (when n > 10 or a cycle is detected), and SHALL
    never enter an infinite loop.

    **Validates: Requirements 8.5, 8.7**
    """

    @given(data=_symlink_chain_no_cycle())
    def test_linear_chain_terminates(
        self,
        data: tuple[int, FakeContentsLookup, str, str],
    ) -> None:
        """Linear symlink chains always terminate and return appropriate result."""
        depth, fake_lookup, initial_target, source_dir = data
        resolver = SymlinkResolver(fake_lookup)

        # Use a timeout to ensure the function always returns
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(5)  # 5 second timeout
        try:
            result = resolver.resolve(initial_target, source_dir)
        except _TimeoutError:
            pytest.fail(f"SymlinkResolver.resolve() did not terminate for chain depth {depth}")
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

        # Verify result type
        assert isinstance(result, SymlinkResolutionResult)

        if depth < SymlinkResolver.MAX_RESOLUTION_DEPTH:
            # Within max depth and no cycles: should resolve successfully.
            # The loop runs MAX_RESOLUTION_DEPTH iterations, and a chain of
            # depth n requires n+1 iterations (n hops + 1 final resolution),
            # so chains of depth < MAX_RESOLUTION_DEPTH can fully resolve.
            assert result.resolved is True, (
                f"Expected resolved=True for chain depth {depth} < {SymlinkResolver.MAX_RESOLUTION_DEPTH}, "
                f"got failure_reason: {result.failure_reason}"
            )
            assert result.copyright_content is not None
            assert result.owning_package is not None
            assert result.target_path is not None
        else:
            # Exceeds max resolution depth: should fail
            assert result.resolved is False, (
                f"Expected resolved=False for chain depth {depth} >= {SymlinkResolver.MAX_RESOLUTION_DEPTH}"
            )
            assert result.failure_reason is not None

    @given(data=_symlink_chain_with_cycle())
    def test_cyclic_chain_terminates(
        self,
        data: tuple[FakeContentsLookup, str, str],
    ) -> None:
        """Cyclic symlink chains always terminate with a failure result."""
        fake_lookup, initial_target, source_dir = data
        resolver = SymlinkResolver(fake_lookup)

        # Use a timeout to ensure the function always returns
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(5)  # 5 second timeout
        try:
            result = resolver.resolve(initial_target, source_dir)
        except _TimeoutError:
            pytest.fail("SymlinkResolver.resolve() did not terminate for cyclic chain")
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

        # Cyclic chains must always fail
        assert isinstance(result, SymlinkResolutionResult)
        assert result.resolved is False, "Expected resolved=False for cyclic chain"
        assert result.failure_reason is not None


# ===========================================================================
# Strategies for generating relative paths (Property 16)
# ===========================================================================


@st.composite
def _relative_symlink_scenario(draw: st.DrawFn) -> tuple[str, str, str]:
    """Generate a (source_dir, relative_target, expected_resolved_path) triple.

    The expected_resolved_path is posixpath.normpath(posixpath.join(source_dir, relative_target)).
    """
    # Generate a source directory (absolute path)
    source_components = draw(st.lists(_path_component, min_size=1, max_size=5))
    source_dir = "/" + "/".join(source_components)

    # Generate a relative target, possibly with ../ components
    # Choose how many ../ to prepend (0-3)
    num_dotdot = draw(st.integers(min_value=0, max_value=min(3, len(source_components))))
    target_components = draw(st.lists(_path_component, min_size=1, max_size=4))

    relative_parts: list[str] = [".."] * num_dotdot + target_components
    relative_target = "/".join(relative_parts)

    # Compute expected resolved path
    expected_resolved = posixpath.normpath(posixpath.join(source_dir, relative_target))

    return source_dir, relative_target, expected_resolved


# ===========================================================================
# Property 16: Symlink Relative Path Resolution
# ===========================================================================


@pytest.mark.unit
@pytest.mark.package
class TestProperty16SymlinkRelativePathResolution:
    """Property 16: Symlink Relative Path Resolution.

    For any relative symlink target and source directory, the Symlink_Resolver
    SHALL resolve the target to an absolute path that is equivalent to joining
    the source directory with the relative target and normalizing the result
    (resolving `..` components).

    **Validates: Requirements 8.2**
    """

    @given(data=_relative_symlink_scenario())
    def test_relative_path_resolution(
        self,
        data: tuple[str, str, str],
    ) -> None:
        """Relative paths resolve to normpath(join(source_dir, target))."""
        source_dir, relative_target, expected_resolved = data

        # Create a fake lookup that maps the expected resolved path to a package
        # with actual copyright content
        owner_pkg = "target-pkg"
        copyright_text = "Copyright 2024 Test Package\nLicense: MIT\nAll rights reserved."

        fake_lookup = FakeContentsLookup(
            file_owners={expected_resolved: owner_pkg},
            copyright_contents={owner_pkg: copyright_text},
        )

        resolver = SymlinkResolver(fake_lookup)
        result = resolver.resolve(relative_target, source_dir)

        # The resolver should successfully resolve, and the target_path
        # should match our expected normpath(join(source_dir, relative_target))
        assert result.resolved is True, (
            f"Expected resolved=True for relative target '{relative_target}' "
            f"from source_dir '{source_dir}'. "
            f"Expected resolved path: '{expected_resolved}'. "
            f"Failure reason: {result.failure_reason}"
        )
        assert result.target_path == expected_resolved, (
            f"Expected target_path='{expected_resolved}', "
            f"got '{result.target_path}' for relative_target='{relative_target}', "
            f"source_dir='{source_dir}'"
        )
        assert result.owning_package == owner_pkg
        assert result.copyright_content == copyright_text
