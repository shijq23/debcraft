"""Property-based tests for mirror cache path derivation.

**Validates: Requirements 5.1, 5.2, 5.4**

Property 10: Local path derivation from base URL.
For any valid HTTP/HTTPS base URL, the derived local mirror path SHALL be
`{mirror_root}/{hostname}/{url_path}/` where hostname is the host portion
and url_path is the path portion of the URL. For any two distinct base URLs,
the derived local paths SHALL be distinct (no collisions).

Property 11: Relative path preservation.
For any file with a relative path declared in repository metadata, the local
filesystem path SHALL end with that exact relative path appended to the
repository's local root directory.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from debcraft.infrastructure.mirror.paths import (
    derive_file_path,
    derive_mirror_root,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Valid hostname segments (lowercase alphanumeric + hyphens, per DNS rules)
_hostname_label = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
    min_size=1,
    max_size=10,
)

# Valid hostnames like "mirror.example.com"
_hostname_strategy = st.builds(
    lambda parts: ".".join(parts),
    st.lists(_hostname_label, min_size=2, max_size=4),
)

# URL path segments (alphanumeric + common chars, no slashes)
_path_segment = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_.",
    min_size=1,
    max_size=15,
)

# Optional URL path (may be empty or have segments)
_url_path_strategy = st.one_of(
    st.just(""),
    st.builds(
        lambda parts: "/".join(parts),
        st.lists(_path_segment, min_size=1, max_size=4),
    ),
)

# HTTP/HTTPS scheme
_scheme_strategy = st.sampled_from(["http", "https"])

# Complete valid base URL
_base_url_strategy = st.builds(
    lambda scheme, host, path: f"{scheme}://{host}/{path}" if path else f"{scheme}://{host}",
    _scheme_strategy,
    _hostname_strategy,
    _url_path_strategy,
)

# Relative paths for files in a repository (no leading slash)
# Exclude lone dots which are normalized by Path (e.g., "a/." == "a")
_relative_path_segment = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_",
    min_size=1,
    max_size=20,
)

_relative_path_strategy = st.builds(
    lambda parts: "/".join(parts),
    st.lists(_relative_path_segment, min_size=1, max_size=5),
)

# Mirror root paths (fixed base for testing)
_MIRROR_BASE = Path("/tmp/test-cache/debcraft/mirror")


# ---------------------------------------------------------------------------
# Mock StorageEngine
# ---------------------------------------------------------------------------


class _FakeStorageEngine:
    """Minimal fake StorageEngine with get_path("mirror") returning a fixed Path."""

    def get_path(self, purpose: str, relative: str = "") -> Path:
        if purpose == "mirror":
            if relative:
                return _MIRROR_BASE / relative
            return _MIRROR_BASE
        return Path("/tmp/test-cache/debcraft") / purpose


# ---------------------------------------------------------------------------
# Property 10: Local path derivation from base URL
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty10LocalPathDerivation:
    """Property 10: Local path derivation from base URL.

    For any valid HTTP/HTTPS base URL, the derived local mirror path SHALL be
    `{mirror_root}/{hostname}/{url_path}/` where hostname is the host portion
    and url_path is the path portion of the URL. For any two distinct base URLs,
    the derived local paths SHALL be distinct (no collisions).
    """

    @given(base_url=_base_url_strategy)
    def test_derived_path_contains_hostname(self, base_url: str) -> None:
        """**Validates: Requirements 5.1**.

        The derived mirror root path contains the hostname as a directory
        component.
        """
        engine = _FakeStorageEngine()
        result = derive_mirror_root(engine, base_url)

        # Extract hostname from URL
        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        hostname = parsed.hostname

        # The hostname must appear as a path component
        assert hostname in result.parts, (
            f"Hostname '{hostname}' not found in path parts {result.parts} for URL '{base_url}'"
        )

    @given(base_url=_base_url_strategy)
    def test_derived_path_contains_url_path_segments(self, base_url: str) -> None:
        """**Validates: Requirements 5.1**.

        The derived mirror root path contains the URL path segments as
        directory components.
        """
        engine = _FakeStorageEngine()
        result = derive_mirror_root(engine, base_url)

        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        url_path = parsed.path.strip("/")

        if url_path:
            # Each path segment from the URL should appear in the result.
            # Skip '.' and '..' which are normalized away by Path resolution.
            for segment in url_path.split("/"):
                if segment and segment not in (".", ".."):
                    assert segment in result.parts, (
                        f"URL path segment '{segment}' not in path parts {result.parts} for URL '{base_url}'"
                    )

    @given(
        scheme=_scheme_strategy,
        host1=_hostname_strategy,
        path1=_url_path_strategy,
        host2=_hostname_strategy,
        path2=_url_path_strategy,
    )
    def test_different_urls_produce_different_paths(
        self,
        scheme: str,
        host1: str,
        path1: str,
        host2: str,
        path2: str,
    ) -> None:
        """**Validates: Requirements 5.4**.

        Two distinct base URLs (different hostname or path) produce
        different derived local paths, ensuring no collisions.
        """
        url1 = f"{scheme}://{host1}/{path1}" if path1 else f"{scheme}://{host1}"
        url2 = f"{scheme}://{host2}/{path2}" if path2 else f"{scheme}://{host2}"

        # Only test when URLs are meaningfully different (host or path differs)
        from urllib.parse import urlparse

        parsed1 = urlparse(url1)
        parsed2 = urlparse(url2)
        assume(parsed1.hostname != parsed2.hostname or parsed1.path.strip("/") != parsed2.path.strip("/"))

        engine = _FakeStorageEngine()
        result1 = derive_mirror_root(engine, url1)
        result2 = derive_mirror_root(engine, url2)

        assert result1 != result2, f"URLs '{url1}' and '{url2}' produced same path '{result1}'"

    @given(base_url=_base_url_strategy)
    def test_derived_path_starts_with_mirror_base(self, base_url: str) -> None:
        """**Validates: Requirements 5.1**.

        The derived path is always rooted under the mirror base directory.
        """
        engine = _FakeStorageEngine()
        result = derive_mirror_root(engine, base_url)

        assert str(result).startswith(str(_MIRROR_BASE)), (
            f"Path '{result}' does not start with mirror base '{_MIRROR_BASE}'"
        )


# ---------------------------------------------------------------------------
# Property 11: Relative path preservation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty11RelativePathPreservation:
    """Property 11: Relative path preservation.

    For any file with a relative path declared in repository metadata, the local
    filesystem path SHALL end with that exact relative path appended to the
    repository's local root directory.
    """

    @given(relative_path=_relative_path_strategy)
    def test_file_path_starts_with_mirror_root(self, relative_path: str) -> None:
        """**Validates: Requirements 5.2**.

        derive_file_path(root, relative_path) produces a path that starts
        with the given mirror root.
        """
        mirror_root = _MIRROR_BASE / "example.com" / "repo"
        result = derive_file_path(mirror_root, relative_path)

        assert str(result).startswith(str(mirror_root)), f"Path '{result}' does not start with root '{mirror_root}'"

    @given(relative_path=_relative_path_strategy)
    def test_path_suffix_matches_relative_path(self, relative_path: str) -> None:
        """**Validates: Requirements 5.2**.

        The path suffix after the mirror root exactly matches the relative
        path components.
        """
        mirror_root = _MIRROR_BASE / "example.com" / "repo"
        result = derive_file_path(mirror_root, relative_path)

        # Get the relative portion after the mirror root
        result_relative = result.relative_to(mirror_root)

        # Compare using PurePosixPath to normalize separators
        expected = PurePosixPath(relative_path)
        assert PurePosixPath(result_relative.as_posix()) == expected, (
            f"Relative portion '{result_relative}' does not match expected '{expected}' for input '{relative_path}'"
        )

    @given(relative_path=_relative_path_strategy)
    def test_leading_slashes_are_stripped(self, relative_path: str) -> None:
        """**Validates: Requirements 5.2**.

        Leading slashes in relative_path are stripped, preventing absolute
        path override issues.
        """
        mirror_root = _MIRROR_BASE / "example.com" / "repo"

        # Add leading slashes to the relative path
        path_with_slashes = "/" + relative_path
        result = derive_file_path(mirror_root, path_with_slashes)

        # Should still start with the root (not be treated as absolute)
        assert str(result).startswith(str(mirror_root)), (
            f"Path '{result}' with leading slash input '/{relative_path}' escaped the mirror root"
        )

        # Should produce the same result as without leading slash
        result_clean = derive_file_path(mirror_root, relative_path)
        assert result == result_clean, f"Leading slash produced different result: '{result}' vs '{result_clean}'"

    @given(
        relative_path1=_relative_path_strategy,
        relative_path2=_relative_path_strategy,
    )
    def test_different_relative_paths_produce_different_file_paths(
        self, relative_path1: str, relative_path2: str
    ) -> None:
        """**Validates: Requirements 5.2**.

        Different relative paths produce different file paths, preserving
        the structure uniquely.
        """
        assume(relative_path1 != relative_path2)

        mirror_root = _MIRROR_BASE / "example.com" / "repo"
        result1 = derive_file_path(mirror_root, relative_path1)
        result2 = derive_file_path(mirror_root, relative_path2)

        assert result1 != result2, (
            f"Relative paths '{relative_path1}' and '{relative_path2}' produced same file path '{result1}'"
        )
