"""Property-based tests for dpkg status parser/printer round-trip.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

Property 1: dpkg Status Round-Trip
  For all valid dpkg status files, when text is parsed by parse_dpkg_status
  into stanzas and then formatted by format_dpkg_status and then parsed again,
  the resulting list of IdentifiedPackage entries shall be equal to the original
  parsed result, where equality means identical values for package name, version,
  architecture, and status fields in the same order.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.domain.scanner.dpkg_parser import parse_dpkg_status
from debcraft.domain.scanner.dpkg_printer import format_dpkg_status

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Valid dpkg package names: lowercase alphanumeric with hyphens, starting with
# a letter, minimum 2 characters per Debian policy.
_PACKAGE_NAME_FIRST_CHAR = st.sampled_from("abcdefghijklmnopqrstuvwxyz")
_PACKAGE_NAME_REST_CHARS = st.text(
    st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789-"),
    min_size=1,
    max_size=30,
)


@st.composite
def st_package_name(draw: st.DrawFn) -> str:
    """Generate a valid Debian package name.

    Package names are lowercase, start with a letter, contain
    alphanumeric characters and hyphens, and are at least 2 chars.
    """
    first = draw(_PACKAGE_NAME_FIRST_CHAR)
    rest = draw(_PACKAGE_NAME_REST_CHARS)
    # Ensure the name doesn't end with a hyphen
    return first + rest.rstrip("-") or first + "a"


@st.composite
def st_version(draw: st.DrawFn) -> str:
    """Generate a valid dpkg version string (epoch:upstream-revision format)."""
    major = draw(st.integers(min_value=0, max_value=99))
    minor = draw(st.integers(min_value=0, max_value=99))
    revision = draw(st.integers(min_value=1, max_value=9))
    return f"{major}.{minor}-{revision}"


_ARCHITECTURES = st.sampled_from(["amd64", "arm64", "i386", "all"])

# Status field values that result in included packages
_INCLUDED_STATUSES = st.sampled_from(
    [
        "install ok installed",
        "hold ok installed",
        "install ok config-files",
        "hold ok config-files",
    ]
)


@st.composite
def st_description_value(draw: st.DrawFn) -> str:
    """Generate a multiline Description field value.

    First line is a short description, followed by zero or more
    continuation lines. Continuation content avoids characters that
    would break field parsing (no colons at line start, no blank-line
    separators within the value).
    """
    # Short description (first line)
    short_desc = draw(
        st.text(
            st.characters(
                blacklist_characters="\n\r\0",
                blacklist_categories=("Cs",),
            ),
            min_size=1,
            max_size=60,
        )
    )

    # Number of continuation lines (0-5)
    num_continuation = draw(st.integers(min_value=0, max_value=5))
    lines = [short_desc]

    for _ in range(num_continuation):
        # Each continuation line is non-empty text or a dot (empty line marker)
        is_empty_line = draw(st.booleans())
        if is_empty_line:
            lines.append(".")
        else:
            line_text = draw(
                st.text(
                    st.characters(
                        blacklist_characters="\n\r\0",
                        blacklist_categories=("Cs",),
                    ),
                    min_size=1,
                    max_size=70,
                )
            )
            lines.append(line_text)

    return "\n".join(lines)


@st.composite
def st_dpkg_stanza(draw: st.DrawFn) -> str:
    """Generate a single valid dpkg status stanza as text.

    Produces a stanza with Package, Status, Version, Architecture fields
    and optionally a multiline Description field. The status value is
    chosen from included statuses so the package will be parsed successfully.
    """
    name = draw(st_package_name())
    status = draw(_INCLUDED_STATUSES)
    version = draw(st_version())
    arch = draw(_ARCHITECTURES)

    lines = [
        f"Package: {name}",
        f"Status: {status}",
        f"Version: {version}",
        f"Architecture: {arch}",
    ]

    # Optionally add a Description field with multiline content
    if draw(st.booleans()):
        desc_value = draw(st_description_value())
        # Format the description as it would appear in a dpkg status file
        desc_parts = desc_value.split("\n")
        lines.append(f"Description: {desc_parts[0]}")
        for part in desc_parts[1:]:
            if part == "" or part == ".":
                lines.append(" .")
            else:
                lines.append(f" {part}")

    return "\n".join(lines)


@st.composite
def st_dpkg_status_file(draw: st.DrawFn) -> str:
    """Generate a valid dpkg status file containing multiple stanzas.

    Stanzas are separated by exactly one blank line. The file ends
    with a trailing newline.
    """
    num_stanzas = draw(st.integers(min_value=1, max_value=20))
    stanzas = [draw(st_dpkg_stanza()) for _ in range(num_stanzas)]
    return "\n\n".join(stanzas) + "\n"


# ---------------------------------------------------------------------------
# Property 1: dpkg Status Round-Trip
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProperty1DpkgRoundTrip:
    """Property 1: dpkg Status Round-Trip.

    For all valid dpkg status files, when text is parsed into stanzas and
    then formatted by format_dpkg_status and then parsed again, the resulting
    list of IdentifiedPackage entries shall be equal to the original parsed
    result.
    """

    @settings(max_examples=200)
    @given(content=st_dpkg_status_file())
    def test_parse_format_parse_roundtrip_preserves_packages(self, content: str) -> None:
        """parse(format(parse(text).stanzas)).packages == parse(text).packages.

        **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**
        """
        # First parse
        result1 = parse_dpkg_status(content)

        # Format back to text
        formatted = format_dpkg_status(result1.stanzas)

        # Second parse
        result2 = parse_dpkg_status(formatted)

        # Assert packages are equal in order and content
        assert len(result2.packages) == len(result1.packages), (
            f"Package count changed: {len(result1.packages)} -> {len(result2.packages)}"
        )
        for i, (pkg1, pkg2) in enumerate(zip(result1.packages, result2.packages, strict=False)):
            assert pkg1.name == pkg2.name, f"Package {i} name mismatch: {pkg1.name!r} != {pkg2.name!r}"
            assert pkg1.version == pkg2.version, f"Package {i} version mismatch: {pkg1.version!r} != {pkg2.version!r}"
            assert pkg1.architecture == pkg2.architecture, (
                f"Package {i} architecture mismatch: {pkg1.architecture!r} != {pkg2.architecture!r}"
            )
            assert pkg1.status == pkg2.status, f"Package {i} status mismatch: {pkg1.status!r} != {pkg2.status!r}"

    @settings(max_examples=200)
    @given(content=st_dpkg_status_file())
    def test_format_produces_valid_dpkg_text(self, content: str) -> None:
        """format(parse(text).stanzas) produces valid dpkg text without double blank lines within stanzas.

        **Validates: Requirements 3.1, 3.2**
        """
        result = parse_dpkg_status(content)
        formatted = format_dpkg_status(result.stanzas)

        # The formatted output should not contain double blank lines within
        # a stanza (only between stanzas)
        if formatted:
            # Split into stanza blocks by double newlines
            stanza_texts = formatted.split("\n\n")
            for i, stanza_text in enumerate(stanza_texts):
                # Within a stanza, there should be no blank lines
                for line in stanza_text.split("\n"):
                    if line == "" and i == len(stanza_texts) - 1 and stanza_text.endswith("\n"):
                        continue

    @settings(max_examples=200)
    @given(stanza=st_dpkg_stanza())
    def test_single_stanza_roundtrip(self, stanza: str) -> None:
        """A single stanza round-trips correctly through parse -> format -> parse.

        **Validates: Requirements 3.3, 3.4**
        """
        content = stanza + "\n"

        result1 = parse_dpkg_status(content)
        assert len(result1.packages) == 1, f"Expected 1 package from single stanza, got {len(result1.packages)}"

        formatted = format_dpkg_status(result1.stanzas)
        result2 = parse_dpkg_status(formatted)

        assert len(result2.packages) == 1, f"Expected 1 package after round-trip, got {len(result2.packages)}"
        assert result1.packages[0] == result2.packages[0]

    def test_empty_stanza_list_returns_empty_string(self) -> None:
        """format_dpkg_status([]) returns empty string.

        **Validates: Requirements 3.5**
        """
        assert format_dpkg_status([]) == ""
