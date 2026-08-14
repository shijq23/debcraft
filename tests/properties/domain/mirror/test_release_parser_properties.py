"""Property-based tests for Release file parser.

**Validates: Requirements 1.2, 1.7**

Property 1: Release file parsing round-trip.
For any valid Release file content containing SHA256Sums entries,
parsing the content SHALL produce a list of FileEntry objects where
each entry's sha256, size_bytes, and relative_path exactly match the
corresponding line in the SHA256Sums section, and the count of parsed
entries equals the count of lines in the SHA256Sums section.

Property 2: Malformed Release content is always rejected.
For any string that does not contain a well-formed `SHA256:` or
`SHA256Sums:` section header followed by indented hash entries,
the ReleaseParser SHALL raise a ReleaseParseError and produce no
FileEntry output.
"""

from __future__ import annotations

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from debcraft.domain.mirror.errors import ReleaseParseError
from debcraft.domain.mirror.release_parser import ReleaseMetadata, ReleaseParser

# ---------------------------------------------------------------------------
# Strategies for generating valid SHA256 section components
# ---------------------------------------------------------------------------

# Valid SHA256 hash: exactly 64 lowercase hex characters
_HEX_CHARS = "0123456789abcdef"


def _valid_sha256_hash() -> st.SearchStrategy[str]:
    """Generate a valid SHA256 hash: exactly 64 hex characters."""
    return st.text(
        alphabet=_HEX_CHARS,
        min_size=64,
        max_size=64,
    )


def _valid_size() -> st.SearchStrategy[int]:
    """Generate a valid file size: non-negative integer."""
    return st.integers(min_value=0, max_value=10**12)


# Valid relative paths: no spaces, at least one character
_PATH_CHARS = st.characters(
    whitelist_categories=("L", "N"),
    whitelist_characters="/-_.",
)


def _valid_relative_path() -> st.SearchStrategy[str]:
    """Generate a valid relative path with no spaces."""
    # Build path segments and join with /
    segment = st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N"),
            whitelist_characters="-_.",
        ),
        min_size=1,
        max_size=20,
    ).filter(lambda s: s[0] != "." and s[-1] != ".")
    return st.lists(segment, min_size=1, max_size=4).map(lambda parts: "/".join(parts))


@st.composite
def _valid_sha256_entry(draw: st.DrawFn) -> tuple[str, int, str]:
    """Generate a single valid SHA256 entry as (hash, size, path)."""
    sha256 = draw(_valid_sha256_hash())
    size = draw(_valid_size())
    path = draw(_valid_relative_path())
    return (sha256, size, path)


@st.composite
def _valid_release_content(
    draw: st.DrawFn,
) -> tuple[str, list[tuple[str, int, str]]]:
    """Generate valid Release file content with SHA256 section.

    Returns a tuple of (content_string, list_of_entries) where each
    entry is (hash, size, path).
    """
    # Choose section header
    header = draw(st.sampled_from(["SHA256:", "SHA256Sums:"]))

    # Generate 1-20 entries
    entries = draw(st.lists(_valid_sha256_entry(), min_size=1, max_size=20))

    # Build the Release file content
    lines: list[str] = []

    # Optional header fields
    if draw(st.booleans()):
        lines.append("Origin: Debian")
    if draw(st.booleans()):
        lines.append("Codename: bookworm")
    if draw(st.booleans()):
        lines.append("Date: Sat, 01 Jan 2024 00:00:00 UTC")

    # SHA256 section
    lines.append(header)
    for sha256, size, path in entries:
        # Standard format: " {hash} {size} {path}" (leading space)
        lines.append(f" {sha256} {size} {path}")

    content = "\n".join(lines)
    return (content, entries)


# ---------------------------------------------------------------------------
# Strategies for generating malformed content (no valid SHA256 section)
# ---------------------------------------------------------------------------


def _string_without_sha256_header() -> st.SearchStrategy[str]:
    """Generate strings that don't contain SHA256: or SHA256Sums: at line start."""
    return st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N", "P", "Z"),
            blacklist_characters="\x00",
        ),
        min_size=1,
        max_size=200,
    ).filter(lambda s: not any(line.rstrip() in ("SHA256:", "SHA256Sums:") for line in s.splitlines()))


def _empty_sha256_section() -> st.SearchStrategy[str]:
    """Generate content with SHA256 header but no valid indented entries."""
    header = st.sampled_from(["SHA256:", "SHA256Sums:"])
    # After the header, add lines that are either empty or non-indented
    trailing = st.sampled_from(
        [
            "",
            "\n",
            "\nSomething: else",
            "\nAnother-Field: value",
        ]
    )
    return st.builds(lambda h, t: f"Origin: Test\n{h}{t}", header, trailing)


def _whitespace_only() -> st.SearchStrategy[str]:
    """Generate whitespace-only strings."""
    return st.text(
        alphabet=st.characters(whitelist_categories=("Z",)),
        min_size=0,
        max_size=50,
    )


# ---------------------------------------------------------------------------
# Property 1: Release file parsing round-trip
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty1ReleaseParsingRoundTrip:
    """Property 1: Release file parsing round-trip.

    For any valid Release file content containing SHA256Sums entries,
    parsing the content SHALL produce a list of FileEntry objects where
    each entry's sha256, size_bytes, and relative_path exactly match
    the corresponding line in the SHA256Sums section, and the count of
    parsed entries equals the count of lines in the SHA256Sums section.
    """

    @given(data=_valid_release_content())
    def test_parsed_entry_count_matches_input_lines(self, data: tuple[str, list[tuple[str, int, str]]]) -> None:
        """Number of parsed FileEntry objects equals input SHA256 line count."""
        content, entries = data
        parser = ReleaseParser()
        result = parser.parse(content, url="http://test/Release")

        assert len(result.files) == len(entries), f"Expected {len(entries)} entries, got {len(result.files)}"

    @given(data=_valid_release_content())
    def test_parsed_sha256_matches_input(self, data: tuple[str, list[tuple[str, int, str]]]) -> None:
        """Each parsed entry's sha256 matches the corresponding input hash."""
        content, entries = data
        parser = ReleaseParser()
        result = parser.parse(content, url="http://test/Release")

        for i, (expected_hash, _, _) in enumerate(entries):
            assert result.files[i].sha256 == expected_hash, (
                f"Entry {i}: expected hash '{expected_hash}', got '{result.files[i].sha256}'"
            )

    @given(data=_valid_release_content())
    def test_parsed_size_bytes_matches_input(self, data: tuple[str, list[tuple[str, int, str]]]) -> None:
        """Each parsed entry's size_bytes matches the corresponding input size."""
        content, entries = data
        parser = ReleaseParser()
        result = parser.parse(content, url="http://test/Release")

        for i, (_, expected_size, _) in enumerate(entries):
            assert result.files[i].size_bytes == expected_size, (
                f"Entry {i}: expected size {expected_size}, got {result.files[i].size_bytes}"
            )

    @given(data=_valid_release_content())
    def test_parsed_relative_path_matches_input(self, data: tuple[str, list[tuple[str, int, str]]]) -> None:
        """Each parsed entry's relative_path matches the corresponding input path."""
        content, entries = data
        parser = ReleaseParser()
        result = parser.parse(content, url="http://test/Release")

        for i, (_, _, expected_path) in enumerate(entries):
            assert result.files[i].relative_path == expected_path, (
                f"Entry {i}: expected path '{expected_path}', got '{result.files[i].relative_path}'"
            )

    @given(data=_valid_release_content())
    def test_result_is_release_metadata(self, data: tuple[str, list[tuple[str, int, str]]]) -> None:
        """Parsing valid content always returns a ReleaseMetadata instance."""
        content, _ = data
        parser = ReleaseParser()
        result = parser.parse(content, url="http://test/Release")

        assert isinstance(result, ReleaseMetadata)


# ---------------------------------------------------------------------------
# Property 2: Malformed Release content is always rejected
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty2MalformedContentRejected:
    """Property 2: Malformed Release content is always rejected.

    For any string that does not contain a well-formed `SHA256:` or
    `SHA256Sums:` section header followed by indented hash entries,
    the ReleaseParser SHALL raise a ReleaseParseError and produce no
    FileEntry output.
    """

    @given(content=_string_without_sha256_header())
    def test_no_sha256_header_raises_error(self, content: str) -> None:
        """Content without SHA256:/SHA256Sums: header raises ReleaseParseError."""
        parser = ReleaseParser()
        with pytest.raises(ReleaseParseError):
            parser.parse(content, url="http://test/Release")

    @given(content=_empty_sha256_section())
    def test_empty_sha256_section_raises_error(self, content: str) -> None:
        """SHA256 header with no valid indented entries raises ReleaseParseError."""
        parser = ReleaseParser()
        with pytest.raises(ReleaseParseError):
            parser.parse(content, url="http://test/Release")

    @given(content=_whitespace_only())
    def test_whitespace_only_raises_error(self, content: str) -> None:
        """Whitespace-only or empty content raises ReleaseParseError."""
        parser = ReleaseParser()
        with pytest.raises(ReleaseParseError):
            parser.parse(content, url="http://test/Release")

    @given(content=st.just(""))
    def test_empty_string_raises_error(self, content: str) -> None:
        """Empty string raises ReleaseParseError."""
        parser = ReleaseParser()
        with pytest.raises(ReleaseParseError):
            parser.parse(content, url="http://test/Release")

    @given(
        header=st.sampled_from(["SHA256:", "SHA256Sums:"]),
        bad_entries=st.lists(
            st.text(
                alphabet=st.characters(
                    whitelist_categories=("L", "N"),
                    whitelist_characters=" -_./",
                ),
                min_size=1,
                max_size=50,
            ),
            min_size=1,
            max_size=5,
        ),
    )
    def test_malformed_entries_after_header_raises_error(self, header: str, bad_entries: list[str]) -> None:
        """SHA256 header followed by malformed entries raises ReleaseParseError.

        Entries must have exactly 3 fields with a 64-char hex hash and
        non-negative integer size. Random strings won't satisfy this.
        """
        # Build content with indented but malformed entries
        lines = [header]
        for entry in bad_entries:
            # Ensure it's indented (starts with space) but not a valid entry
            lines.append(f" {entry}")

        content = "\n".join(lines)

        # Filter out cases where we accidentally generated valid entries
        # A valid entry has exactly 3 whitespace-separated fields where
        # field 1 is 64 hex chars and field 2 is a non-negative int
        def _is_valid_entry(line: str) -> bool:
            stripped = line.strip()
            if not stripped:
                return False
            parts = stripped.split()
            if len(parts) != 3:
                return False
            sha256, size_str, _ = parts
            if len(sha256) != 64:
                return False
            try:
                int(sha256, 16)
            except ValueError:
                return False
            try:
                size = int(size_str)
                return size >= 0
            except ValueError:
                return False

        # If any entry accidentally ended up valid, skip this test case
        has_valid_entry = any(_is_valid_entry(line) for line in lines[1:])
        assume(not has_valid_entry)

        parser = ReleaseParser()
        with pytest.raises(ReleaseParseError):
            parser.parse(content, url="http://test/Release")
