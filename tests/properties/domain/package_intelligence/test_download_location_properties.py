"""Property-based tests for download location URL construction.

# Feature: package-intelligence, Property 11: Download Location URL Join
# Feature: package-intelligence, Property 12: Download Location NOASSERTION for Missing Inputs

**Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5**
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.domain.package_intelligence.download_location import (
    resolve_download_location,
)

# ===========================================================================
# Strategies for generating non-empty URLs and filenames (Property 11)
# ===========================================================================

# Characters valid in URL path segments (simplified but representative)
_URL_PATH_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"

# Common URL schemes for base URLs
_SCHEMES = ["https://", "http://"]

# Example domain fragments
_DOMAINS = [
    "deb.debian.org",
    "archive.ubuntu.com",
    "packages.example.com",
    "mirror.example.org",
    "repo.example.net",
]


@st.composite
def non_empty_urls(draw: st.DrawFn) -> str:
    """Generate non-empty, non-whitespace base URLs.

    Produces URLs like:
    - https://deb.debian.org/debian
    - http://archive.ubuntu.com/ubuntu/pool
    - https://mirror.example.org

    With optional trailing slashes to test slash normalization.
    """
    scheme = draw(st.sampled_from(_SCHEMES))
    domain = draw(st.sampled_from(_DOMAINS))

    # Optional path segments after the domain
    path_segments = draw(
        st.lists(
            st.text(alphabet=_URL_PATH_CHARS, min_size=1, max_size=12),
            min_size=0,
            max_size=3,
        )
    )

    url = scheme + domain
    if path_segments:
        url += "/" + "/".join(path_segments)

    # Optionally append trailing slashes (0, 1, 2, or 3)
    trailing_slashes = draw(st.integers(min_value=0, max_value=3))
    url += "/" * trailing_slashes

    return url


@st.composite
def non_empty_filenames(draw: st.DrawFn) -> str:
    """Generate non-empty, non-whitespace filenames.

    Produces filenames like:
    - pool/main/g/glibc/libc6_2.40_amd64.deb
    - some-package_1.0_all.deb

    With optional leading slashes to test slash normalization.
    """
    # Optionally prepend leading slashes (0, 1, 2, or 3)
    leading_slashes = draw(st.integers(min_value=0, max_value=3))

    # Generate path segments for the filename
    segments = draw(
        st.lists(
            st.text(alphabet=_URL_PATH_CHARS, min_size=1, max_size=15),
            min_size=1,
            max_size=5,
        )
    )

    filename = "/" * leading_slashes + "/".join(segments)
    return filename


# ===========================================================================
# Property 11: Download Location URL Join
# ===========================================================================


@pytest.mark.unit
@pytest.mark.package
class TestProperty11DownloadLocationURLJoin:
    """Property 11: Download Location URL Join.

    For any non-empty, non-whitespace base URL and non-empty, non-whitespace
    filename, the Download_Location_Resolver SHALL produce a URL where the
    base URL and filename are separated by exactly one `/` character (no
    double-slash at the join boundary).

    **Validates: Requirements 9.1, 9.2, 9.3, 9.5**
    """

    @settings(max_examples=100)
    @given(base_url=non_empty_urls(), filename=non_empty_filenames())
    def test_download_location_no_double_slash(self, base_url: str, filename: str) -> None:
        """Result has no double-slash after the protocol prefix."""
        result = resolve_download_location(base_url, filename)

        # Must not return NOASSERTION for valid inputs
        assert result != "NOASSERTION", (
            f"Expected a URL but got NOASSERTION for base_url={base_url!r}, filename={filename!r}"
        )

        # After stripping protocol prefix (e.g. "https://"), check
        # that no double-slash exists at the join boundary.
        # The "//" in "https://" is legitimate and expected.
        after_protocol = result.split("://", 1)[1] if "://" in result else result

        assert "//" not in after_protocol, (
            f"Double-slash found in URL path after protocol: {result!r}\n"
            f"  base_url={base_url!r}\n"
            f"  filename={filename!r}"
        )


# ===========================================================================
# Strategies for generating missing/invalid inputs (Property 12)
# ===========================================================================

# Strategies for whitespace-only or empty strings
_whitespace_only = st.text(
    alphabet=st.sampled_from([" ", "\t", "\n", "\r"]),
    min_size=0,
    max_size=10,
)

# Strategy for values that should trigger NOASSERTION
_missing_value = st.one_of(
    st.just(None),
    st.just(""),
    _whitespace_only,
)

# Strategy for non-missing values (used as the "valid" counterpart)
_present_value = st.text(
    alphabet=_URL_PATH_CHARS,
    min_size=1,
    max_size=30,
).map(lambda s: "https://example.com/" + s)

_present_filename = st.text(
    alphabet=_URL_PATH_CHARS,
    min_size=1,
    max_size=30,
)


# ===========================================================================
# Property 12: Download Location NOASSERTION for Missing Inputs
# ===========================================================================


@pytest.mark.unit
@pytest.mark.package
class TestProperty12DownloadLocationNOASSERTIONForMissing:
    """Property 12: Download Location NOASSERTION for Missing Inputs.

    For any input where the filename is absent, empty, or whitespace-only,
    OR the base URL is null or empty, the Download_Location_Resolver SHALL
    return the string `NOASSERTION`.

    **Validates: Requirements 9.4**
    """

    @settings(max_examples=100)
    @given(base_url=_missing_value, filename=_present_filename)
    def test_noassertion_when_base_url_missing(self, base_url: str | None, filename: str) -> None:
        """Returns NOASSERTION when base_url is None, empty, or whitespace."""
        result = resolve_download_location(base_url, filename)
        assert result == "NOASSERTION", (
            f"Expected NOASSERTION for missing base_url={base_url!r}, filename={filename!r}, got {result!r}"
        )

    @settings(max_examples=100)
    @given(base_url=_present_value, filename=_missing_value)
    def test_noassertion_when_filename_missing(self, base_url: str, filename: str | None) -> None:
        """Returns NOASSERTION when filename is None, empty, or whitespace."""
        result = resolve_download_location(base_url, filename)
        assert result == "NOASSERTION", (
            f"Expected NOASSERTION for base_url={base_url!r}, missing filename={filename!r}, got {result!r}"
        )

    @settings(max_examples=100)
    @given(base_url=_missing_value, filename=_missing_value)
    def test_noassertion_when_both_missing(self, base_url: str | None, filename: str | None) -> None:
        """Returns NOASSERTION when both inputs are missing."""
        result = resolve_download_location(base_url, filename)
        assert result == "NOASSERTION", (
            f"Expected NOASSERTION for base_url={base_url!r}, filename={filename!r}, got {result!r}"
        )
