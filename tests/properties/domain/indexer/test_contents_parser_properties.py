"""Property-based tests for ContentsParser.

# Feature: repository-indexer, Property 7: Contents parsing correctness
# Feature: repository-indexer, Property 8: Contents header invariance

**Validates: Requirements 3.1, 3.2, 3.4**
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.domain.indexer.contents_parser import ContentsParser
from debcraft.domain.indexer.values import FileOwnership

# ===========================================================================
# Strategies for generating valid Contents file lines
# ===========================================================================

# Path segments: lowercase alphanumerics, dots, dashes, underscores
_PATH_SEGMENT_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789._-"

# Section names used in qualified package names
_SECTIONS = [
    "libs",
    "utils",
    "admin",
    "devel",
    "doc",
    "net",
    "web",
    "python",
    "perl",
    "java",
    "misc",
    "text",
    "editors",
    "graphics",
    "sound",
    "video",
]

# Debian package name characters
_PKG_NAME_START = "abcdefghijklmnopqrstuvwxyz0123456789"
_PKG_NAME_CHARS = _PKG_NAME_START + "+-."


def _path_segment() -> st.SearchStrategy[str]:
    """Generate a single path segment (no slashes, no whitespace)."""
    return st.text(
        alphabet=_PATH_SEGMENT_CHARS,
        min_size=1,
        max_size=20,
    )


def _file_path() -> st.SearchStrategy[str]:
    """Generate a valid filesystem path without whitespace.

    Produces paths like: usr/bin/foo, usr/lib/libfoo.so.1, etc/foo.conf
    """
    return st.builds(
        lambda segments: "/".join(segments),
        st.lists(_path_segment(), min_size=2, max_size=5),
    ).filter(lambda p: len(p) > 0 and "  " not in p)


def _package_name() -> st.SearchStrategy[str]:
    """Generate a valid Debian package name."""
    return st.builds(
        lambda start, rest: start + rest,
        st.text(alphabet=_PKG_NAME_START, min_size=1, max_size=1),
        st.text(alphabet=_PKG_NAME_CHARS, min_size=1, max_size=20),
    ).filter(lambda s: not s.endswith("+") and not s.endswith("-") and not s.endswith("."))


def _qualified_package_name() -> st.SearchStrategy[str]:
    """Generate a qualified package name in section/name format."""
    return st.builds(
        lambda section, name: f"{section}/{name}",
        st.sampled_from(_SECTIONS),
        _package_name(),
    )


@st.composite
def _contents_line_data(draw: st.DrawFn) -> tuple[str, list[str]]:
    """Generate a (path, [qualified_package_names]) tuple.

    Returns the path and a list of 1-3 qualified package names that
    should appear on a single Contents file line.
    """
    path = draw(_file_path())
    packages = draw(st.lists(_qualified_package_name(), min_size=1, max_size=3))
    return (path, packages)


def _build_contents_line(path: str, packages: list[str]) -> str:
    """Build a Contents file line from a path and package list.

    Uses multiple spaces between path and packages (as in real Contents files).
    """
    packages_field = ",".join(packages)
    return f"{path}  {packages_field}"


# ===========================================================================
# Property 7: Contents parsing correctness
# ===========================================================================


@pytest.mark.unit
class TestProperty7ContentsParsingCorrectness:
    """Property 7: Contents parsing correctness.

    For any valid Contents file line consisting of a path followed by
    whitespace and one or more comma-separated qualified package names,
    parsing SHALL produce exactly one FileOwnership record per package,
    each with the correct path and qualified package name.

    **Validates: Requirements 3.1, 3.2**
    """

    @settings(max_examples=100)
    @given(data=_contents_line_data())
    def test_single_line_produces_one_ownership_per_package(self, data: tuple[str, list[str]]) -> None:
        """Each package in a Contents line produces one FileOwnership."""
        path, packages = data
        line = _build_contents_line(path, packages)

        parser = ContentsParser()
        results = parser.parse(line)

        assert len(results) == len(packages), (
            f"Expected {len(packages)} FileOwnership records, got {len(results)}.\nLine: {line!r}"
        )

    @settings(max_examples=100)
    @given(data=_contents_line_data())
    def test_each_ownership_has_correct_path(self, data: tuple[str, list[str]]) -> None:
        """Each FileOwnership has the correct filesystem path."""
        path, packages = data
        line = _build_contents_line(path, packages)

        parser = ContentsParser()
        results = parser.parse(line)

        for ownership in results:
            assert ownership.path == path, f"Expected path={path!r}, got {ownership.path!r}.\nLine: {line!r}"

    @settings(max_examples=100)
    @given(data=_contents_line_data())
    def test_each_ownership_has_correct_qualified_name(self, data: tuple[str, list[str]]) -> None:
        """Each FileOwnership has the correct qualified package name."""
        path, packages = data
        line = _build_contents_line(path, packages)

        parser = ContentsParser()
        results = parser.parse(line)

        result_names = [r.qualified_package_name for r in results]
        assert result_names == packages, f"Expected package names {packages}, got {result_names}.\nLine: {line!r}"

    @settings(max_examples=100)
    @given(lines_data=st.lists(_contents_line_data(), min_size=1, max_size=5))
    def test_multiple_lines_produce_correct_total_ownerships(self, lines_data: list[tuple[str, list[str]]]) -> None:
        """Multiple Contents lines produce the correct total FileOwnership count."""
        content = "\n".join(_build_contents_line(path, pkgs) for path, pkgs in lines_data)
        expected_total = sum(len(pkgs) for _, pkgs in lines_data)

        parser = ContentsParser()
        results = parser.parse(content)

        assert len(results) == expected_total, (
            f"Expected {expected_total} total FileOwnership records, got {len(results)}.\nContent:\n{content}"
        )

    @settings(max_examples=100)
    @given(data=_contents_line_data())
    def test_ownership_is_file_ownership_instance(self, data: tuple[str, list[str]]) -> None:
        """Each result is a FileOwnership value object."""
        path, packages = data
        line = _build_contents_line(path, packages)

        parser = ContentsParser()
        results = parser.parse(line)

        for ownership in results:
            assert isinstance(ownership, FileOwnership)


# ===========================================================================
# Property 8: Contents header invariance (strategies)
# ===========================================================================


def _header_line_single_word() -> st.SearchStrategy[str]:
    """Generate a single-word header line (no whitespace, so rsplit yields 1 part).

    These are guaranteed to be skipped by the parser since rsplit(None, 1)
    will produce only one element for a single token.
    """
    return st.text(
        alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-.",
        min_size=1,
        max_size=30,
    )


def _header_line() -> st.SearchStrategy[str]:
    """Generate a header line that won't be parsed as a valid Contents data line.

    The safest headers are single-word lines (no internal whitespace) which
    rsplit(None, 1) will produce only 1 element, causing the parser to skip.
    """
    return st.one_of(
        # Single word: no whitespace at all
        _header_line_single_word(),
        # Empty string (will be stripped and skipped)
        st.just(""),
        # Whitespace-only (will be stripped and skipped)
        st.text(alphabet=" \t", min_size=1, max_size=10),
    )


@st.composite
def _contents_body_with_headers(
    draw: st.DrawFn,
) -> tuple[str, list[str]]:
    """Generate a valid Contents body and a list of header lines to prepend.

    Returns (body_content, header_lines) where body_content is a valid
    Contents file body and header_lines are lines that don't match the
    data format.
    """
    # Generate 1-5 valid data lines
    lines_data = draw(st.lists(_contents_line_data(), min_size=1, max_size=5))
    body_lines = [_build_contents_line(path, pkgs) for path, pkgs in lines_data]
    body_content = "\n".join(body_lines)

    # Generate 1-5 header lines
    headers = draw(st.lists(_header_line(), min_size=1, max_size=5))

    return (body_content, headers)


# ===========================================================================
# Property 8: Contents header invariance
# ===========================================================================


@pytest.mark.unit
class TestProperty8ContentsHeaderInvariance:
    """Property 8: Contents header invariance.

    For any valid Contents file body, prepending an arbitrary header
    section (lines that don't match the path/packages format) SHALL not
    change the set of FileOwnership records produced by parsing.

    **Validates: Requirements 3.4**
    """

    # Feature: repository-indexer, Property 8: Contents header invariance

    @settings(max_examples=100)
    @given(data=_contents_body_with_headers())
    def test_prepending_headers_does_not_change_results(self, data: tuple[str, list[str]]) -> None:
        """Prepending non-data header lines produces the same FileOwnership set."""
        body_content, headers = data
        parser = ContentsParser()

        # Parse the body alone to get baseline
        baseline = parser.parse(body_content)
        baseline_set = frozenset((fo.path, fo.qualified_package_name) for fo in baseline)

        # Prepend headers and parse the combined content
        header_section = "\n".join(headers)
        combined = header_section + "\n" + body_content
        combined_results = parser.parse(combined)
        combined_set = frozenset((fo.path, fo.qualified_package_name) for fo in combined_results)

        assert combined_set == baseline_set, (
            f"Header lines changed the results!\n"
            f"Headers: {headers!r}\n"
            f"Baseline set ({len(baseline_set)} items): {baseline_set}\n"
            f"Combined set ({len(combined_set)} items): {combined_set}\n"
            f"Extra in combined: {combined_set - baseline_set}\n"
            f"Missing from combined: {baseline_set - combined_set}"
        )
