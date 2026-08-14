"""Property-based tests for PackagesParser.

**Validates: Requirements 5.1, 5.2, 5.3, 6.1, 6.2, 6.3, 6.4, 7.1, 7.2,
8.1, 8.2, 9.1, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 11.1, 11.2, 11.3,
11.4, 11.5**

Property 5: PackagesParser Idempotency
  For any content string, calling PackagesParser.parse(content) twice
  on the same instance SHALL produce identical results.

Property 6: PackagesParser Output Validity
  For any content string, every FileEntry returned by PackagesParser.parse(content)
  SHALL have non-empty relative_path, non-empty sha256, and size_bytes >= 0.

Property 7: PackagesParser Stanza Count Bound
  For any content string, the number of FileEntry instances returned
  SHALL be less than or equal to the number of stanzas in the input.

Property 8: PackagesParser Monotonicity
  For any content string and any valid stanza, appending the valid stanza
  SHALL result in parse(combined) >= parse(content) in count.

Property 9: PackagesParser Round-Trip
  For any valid FileEntry field values, constructing a stanza and parsing it
  SHALL recover those exact values.

Property 10: PackagesParser Missing Field Rejection
  Stanzas missing any required field SHALL produce no entries.

Property 11: PackagesParser Invalid Size Rejection
  Stanzas with non-integer or negative Size SHALL produce no entries.

Property 12: PackagesParser Invalid Stanza Isolation
  Invalid stanzas SHALL not affect parsing of adjacent valid stanzas.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.domain.mirror.packages_parser import PackagesParser

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# All characters Python's str.splitlines() treats as line boundaries
_LINE_SEPARATORS = "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"

# All ASCII characters that Python's str.strip() removes
_STRIP_CHARS = "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \x85\xa0"

# Characters safe for field values: no line separators, no colons, no NUL
_SAFE_CHARS = st.characters(
    blacklist_characters=_LINE_SEPARATORS + "\0:",
    blacklist_categories=("Cs",),
)

# Characters for values that survive parser stripping and won't be treated
# as line separators by splitlines(). Excludes all whitespace categories and
# control characters to ensure generated values remain intact through parsing.
_VALUE_SAFE_CHARS = st.characters(
    blacklist_characters=_LINE_SEPARATORS + _STRIP_CHARS + "\0:",
    blacklist_categories=("Cs", "Zs", "Zl", "Zp"),
)


@st.composite
def _valid_packages_stanza(draw: st.DrawFn) -> str:
    """Generate a single valid Packages stanza with required fields.

    Produces a stanza containing Filename, SHA256, and Size fields,
    plus optional extra fields (Package, Version, Architecture).
    Values are constrained so they survive the parser's value.strip() call.
    """
    filename = draw(st.text(_VALUE_SAFE_CHARS, min_size=1, max_size=100))
    sha256 = draw(st.text(st.sampled_from("0123456789abcdef"), min_size=64, max_size=64))
    size = draw(st.integers(min_value=0, max_value=999999999))

    lines = []

    # Optionally add Package field
    if draw(st.booleans()):
        pkg_name = draw(st.text(_SAFE_CHARS, min_size=1, max_size=50))
        lines.append(f"Package: {pkg_name}")

    # Optionally add Version field
    if draw(st.booleans()):
        version = draw(st.text(_SAFE_CHARS, min_size=1, max_size=30))
        lines.append(f"Version: {version}")

    # Add required fields
    lines.append(f"Filename: {filename}")
    lines.append(f"SHA256: {sha256}")
    lines.append(f"Size: {size}")

    # Optionally add Architecture field
    if draw(st.booleans()):
        arch = draw(st.sampled_from(["amd64", "arm64", "i386", "all"]))
        lines.append(f"Architecture: {arch}")

    return "\n".join(lines)


@st.composite
def _valid_packages_content(draw: st.DrawFn) -> str:
    """Generate multi-stanza valid Packages content (1-50 stanzas)."""
    count = draw(st.integers(min_value=1, max_value=50))
    stanzas = [draw(_valid_packages_stanza()) for _ in range(count)]
    return "\n\n".join(stanzas)


def _arbitrary_content() -> st.SearchStrategy[str]:
    """Generate fully random text content."""
    return st.text(min_size=0, max_size=10000)


def _packages_content() -> st.SearchStrategy[str]:
    """Generate either valid Packages content or arbitrary text."""
    return st.one_of(_valid_packages_content(), _arbitrary_content())


@st.composite
def _valid_file_entry_fields(draw: st.DrawFn) -> tuple[str, str, int]:
    """Generate (relative_path, sha256, size_bytes) constrained to parser-safe values.

    No newlines or colons in strings, no leading/trailing whitespace
    (since the parser strips values), non-negative size.
    """
    relative_path = draw(st.text(_VALUE_SAFE_CHARS, min_size=1, max_size=100))
    sha256 = draw(st.text(_VALUE_SAFE_CHARS, min_size=1, max_size=100))
    size_bytes = draw(st.integers(min_value=0, max_value=999999999))
    return (relative_path, sha256, size_bytes)


@st.composite
def _stanza_missing_field(draw: st.DrawFn, excluded_field: str) -> str:
    """Generate a stanza with valid Key:Value lines but omitting the specified required field.

    Always includes at least one other valid Key: Value line.
    """
    fields: dict[str, str] = {}

    # Always include at least one extra field
    pkg_name = draw(st.text(_SAFE_CHARS, min_size=1, max_size=50))
    fields["Package"] = pkg_name

    # Optionally add Version
    if draw(st.booleans()):
        version = draw(st.text(_SAFE_CHARS, min_size=1, max_size=30))
        fields["Version"] = version

    # Add required fields except the excluded one
    if excluded_field != "Filename":
        filename = draw(st.text(_SAFE_CHARS, min_size=1, max_size=100))
        fields["Filename"] = filename

    if excluded_field != "SHA256":
        sha256 = draw(st.text(st.sampled_from("0123456789abcdef"), min_size=64, max_size=64))
        fields["SHA256"] = sha256

    if excluded_field != "Size":
        size = draw(st.integers(min_value=0, max_value=999999999))
        fields["Size"] = str(size)

    lines = [f"{key}: {value}" for key, value in fields.items()]
    return "\n".join(lines)


@st.composite
def _stanza_with_invalid_size(draw: st.DrawFn) -> str:
    """Generate a stanza with Filename and SHA256 but a non-parseable or negative Size."""
    filename = draw(st.text(_SAFE_CHARS, min_size=1, max_size=100))
    sha256 = draw(st.text(st.sampled_from("0123456789abcdef"), min_size=64, max_size=64))

    # Generate an invalid size value: either non-integer text or a negative number
    invalid_size = draw(
        st.one_of(
            # Non-integer strings (alphabetic, floats, special chars, empty)
            st.text(
                st.characters(blacklist_characters="\n\r\0:"),
                min_size=1,
                max_size=20,
            ).filter(lambda s: not s.strip().lstrip("-").isdigit()),
            # Negative integers
            st.integers(max_value=-1).map(str),
        )
    )

    lines = [
        f"Filename: {filename}",
        f"SHA256: {sha256}",
        f"Size: {invalid_size}",
    ]

    # Optionally add Package field
    if draw(st.booleans()):
        pkg_name = draw(st.text(_SAFE_CHARS, min_size=1, max_size=50))
        lines.insert(0, f"Package: {pkg_name}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _count_stanzas(content: str) -> int:
    """Count stanzas in content.

    A stanza is a maximal group of consecutive non-blank lines.
    A blank line is one whose stripped form is the empty string.
    """
    if not content or not content.strip():
        return 0

    count = 0
    in_stanza = False

    for line in content.splitlines():
        if line.strip():
            if not in_stanza:
                count += 1
                in_stanza = True
        else:
            in_stanza = False

    return count


# ---------------------------------------------------------------------------
# Property 5: PackagesParser Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty5ParserIdempotency:
    """Property 5: PackagesParser Idempotency.

    For any content string, calling PackagesParser.parse(content) twice
    on the same instance SHALL produce identical results (same length,
    same order, same element values).
    """

    @given(content=_arbitrary_content())
    def test_parse_idempotent_arbitrary_content(self, content: str) -> None:
        """parse(content) == parse(content) for arbitrary content.

        **Validates: Requirements 5.1, 5.3**
        """
        parser = PackagesParser()
        result1 = parser.parse(content)
        result2 = parser.parse(content)
        assert result1 == result2

    @given(content=_valid_packages_content())
    def test_parse_idempotent_valid_packages_content(self, content: str) -> None:
        """parse(content) == parse(content) for valid Packages content.

        **Validates: Requirements 5.1, 5.2, 5.3**
        """
        parser = PackagesParser()
        result1 = parser.parse(content)
        result2 = parser.parse(content)
        assert result1 == result2


# ---------------------------------------------------------------------------
# Property 6: PackagesParser Output Validity
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty6OutputValidity:
    """Property 6: PackagesParser Output Validity.

    For any content string, every FileEntry returned by PackagesParser.parse(content)
    SHALL have non-empty relative_path, non-empty sha256, and size_bytes >= 0.
    """

    @given(content=_packages_content())
    def test_all_entries_have_valid_fields(self, content: str) -> None:
        """All returned entries have non-empty relative_path, non-empty sha256, size_bytes >= 0.

        **Validates: Requirements 6.1, 6.2, 6.3, 6.4**
        """
        parser = PackagesParser()
        entries = parser.parse(content)
        for entry in entries:
            assert len(entry.relative_path) >= 1, "relative_path must be non-empty"
            assert len(entry.sha256) >= 1, "sha256 must be non-empty"
            assert entry.size_bytes >= 0, "size_bytes must be non-negative"


# ---------------------------------------------------------------------------
# Property 7: PackagesParser Stanza Count Bound
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty7StanzaCountBound:
    """Property 7: PackagesParser Stanza Count Bound.

    For any content string, the number of FileEntry instances returned
    SHALL be less than or equal to the number of stanzas in the input.
    """

    @given(content=_packages_content())
    def test_result_count_bounded_by_stanza_count(self, content: str) -> None:
        """len(parse(content)) <= stanza_count(content).

        **Validates: Requirements 7.1, 7.2**
        """
        parser = PackagesParser()
        entries = parser.parse(content)
        stanza_count = _count_stanzas(content)
        assert len(entries) <= stanza_count, f"Got {len(entries)} entries but only {stanza_count} stanzas"


# ---------------------------------------------------------------------------
# Property 8: PackagesParser Monotonicity
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty8Monotonicity:
    """Property 8: PackagesParser Monotonicity.

    Appending a valid stanza to content does not decrease result count.
    """

    @given(content=_packages_content(), stanza=_valid_packages_stanza())
    def test_appending_valid_stanza_does_not_decrease_count(self, content: str, stanza: str) -> None:
        """Appending a valid stanza does not decrease result count.

        **Validates: Requirements 8.1**
        """
        parser = PackagesParser()
        original_count = len(parser.parse(content))

        combined = content + "\n\n" + stanza if content.strip() else stanza

        new_count = len(parser.parse(combined))
        assert new_count >= original_count, (
            f"Appending valid stanza decreased count from {original_count} to {new_count}"
        )

    @given(stanza=_valid_packages_stanza())
    def test_single_valid_stanza_returns_at_least_one_entry(self, stanza: str) -> None:
        """Parsing a single valid stanza returns at least 1 entry.

        **Validates: Requirements 8.2**
        """
        parser = PackagesParser()
        entries = parser.parse(stanza)
        assert len(entries) >= 1, "A valid stanza should produce at least 1 entry"


# ---------------------------------------------------------------------------
# Property 9: PackagesParser Round-Trip
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty9RoundTrip:
    """Property 9: PackagesParser Round-Trip.

    For any valid FileEntry field values, constructing a stanza and parsing it
    SHALL recover those exact values.
    """

    @given(fields=_valid_file_entry_fields())
    def test_round_trip_recovers_original_values(self, fields: tuple[str, str, int]) -> None:
        """Constructing a stanza from known values and parsing recovers those exact values.

        **Validates: Requirements 9.1**
        """
        relative_path, sha256, size_bytes = fields
        stanza = f"Filename: {relative_path}\nSHA256: {sha256}\nSize: {size_bytes}\n"

        parser = PackagesParser()
        entries = parser.parse(stanza)

        assert len(entries) == 1, f"Expected exactly 1 entry, got {len(entries)}"
        assert entries[0].relative_path == relative_path
        assert entries[0].sha256 == sha256
        assert entries[0].size_bytes == size_bytes


# ---------------------------------------------------------------------------
# Property 10: PackagesParser Missing Field Rejection
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty10MissingFieldRejection:
    """Property 10: PackagesParser Missing Field Rejection.

    For any stanza containing at least one valid Key: Value line but missing
    one of the required fields (Filename, SHA256, or Size),
    PackagesParser.parse SHALL return an empty list.
    """

    @given(stanza=_stanza_missing_field(excluded_field="Filename"))
    def test_missing_filename_produces_no_entries(self, stanza: str) -> None:
        """Stanzas missing Filename produce no entries.

        **Validates: Requirements 10.1**
        """
        parser = PackagesParser()
        entries = parser.parse(stanza)
        assert entries == [], f"Expected no entries for stanza missing Filename, got {len(entries)}"

    @given(stanza=_stanza_missing_field(excluded_field="SHA256"))
    def test_missing_sha256_produces_no_entries(self, stanza: str) -> None:
        """Stanzas missing SHA256 produce no entries.

        **Validates: Requirements 10.2**
        """
        parser = PackagesParser()
        entries = parser.parse(stanza)
        assert entries == [], f"Expected no entries for stanza missing SHA256, got {len(entries)}"

    @given(stanza=_stanza_missing_field(excluded_field="Size"))
    def test_missing_size_produces_no_entries(self, stanza: str) -> None:
        """Stanzas missing Size produce no entries.

        **Validates: Requirements 10.3**
        """
        parser = PackagesParser()
        entries = parser.parse(stanza)
        assert entries == [], f"Expected no entries for stanza missing Size, got {len(entries)}"


# ---------------------------------------------------------------------------
# Property 11: PackagesParser Invalid Size Rejection
# ---------------------------------------------------------------------------


@st.composite
def _stanza_with_non_integer_size(draw: st.DrawFn) -> str:
    """Generate a stanza with Filename and SHA256 but a non-parseable Size value."""
    filename = draw(st.text(_SAFE_CHARS, min_size=1, max_size=100))
    sha256 = draw(st.text(st.sampled_from("0123456789abcdef"), min_size=64, max_size=64))

    # Non-integer strings (alphabetic, floats, special chars)
    invalid_size = draw(
        st.text(
            st.characters(blacklist_characters="\n\r\0:"),
            min_size=1,
            max_size=20,
        ).filter(lambda s: not s.strip().lstrip("-").isdigit())
    )

    lines = [
        f"Filename: {filename}",
        f"SHA256: {sha256}",
        f"Size: {invalid_size}",
    ]
    return "\n".join(lines)


@st.composite
def _stanza_with_negative_size(draw: st.DrawFn) -> str:
    """Generate a stanza with Filename and SHA256 but a negative integer Size."""
    filename = draw(st.text(_SAFE_CHARS, min_size=1, max_size=100))
    sha256 = draw(st.text(st.sampled_from("0123456789abcdef"), min_size=64, max_size=64))

    negative_size = draw(st.integers(max_value=-1).map(str))

    lines = [
        f"Filename: {filename}",
        f"SHA256: {sha256}",
        f"Size: {negative_size}",
    ]
    return "\n".join(lines)


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty11InvalidSizeRejection:
    """Property 11: PackagesParser Invalid Size Rejection.

    For any stanza containing Filename and SHA256 fields but with a Size value
    that is either not parseable as an integer or is a negative integer,
    PackagesParser.parse SHALL return an empty list.
    """

    @given(stanza=_stanza_with_non_integer_size())
    def test_non_integer_size_produces_no_entries(self, stanza: str) -> None:
        """Stanzas with non-integer Size produce no entries.

        **Validates: Requirements 10.4**
        """
        parser = PackagesParser()
        entries = parser.parse(stanza)
        assert entries == [], f"Expected no entries for non-integer Size, got {len(entries)}"

    @given(stanza=_stanza_with_negative_size())
    def test_negative_size_produces_no_entries(self, stanza: str) -> None:
        """Stanzas with negative Size produce no entries.

        **Validates: Requirements 10.5**
        """
        parser = PackagesParser()
        entries = parser.parse(stanza)
        assert entries == [], f"Expected no entries for negative Size, got {len(entries)}"


# ---------------------------------------------------------------------------
# Property 12: PackagesParser Invalid Stanza Isolation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty12InvalidStanzaIsolation:
    """Property 12: PackagesParser Invalid Stanza Isolation.

    For any invalid stanza (missing Filename) concatenated with a valid stanza
    (separated by a blank line), PackagesParser.parse SHALL return exactly 1
    FileEntry corresponding to the valid stanza only.
    """

    @given(
        invalid_stanza=_stanza_missing_field(excluded_field="Filename"),
        valid_stanza=_valid_packages_stanza(),
    )
    def test_invalid_stanza_does_not_affect_adjacent_valid_stanza(self, invalid_stanza: str, valid_stanza: str) -> None:
        """Invalid stanzas don't affect parsing of adjacent valid stanzas.

        **Validates: Requirements 10.6**
        """
        combined = invalid_stanza + "\n\n" + valid_stanza

        parser = PackagesParser()
        entries = parser.parse(combined)

        assert len(entries) == 1, f"Expected exactly 1 entry from valid stanza, got {len(entries)}"

        # Parse the valid stanza alone to confirm the entry matches
        valid_entries = parser.parse(valid_stanza)
        assert len(valid_entries) == 1, f"Valid stanza alone should produce 1 entry, got {len(valid_entries)}"
        assert entries[0] == valid_entries[0], "Entry from combined content should match entry from valid stanza alone"
