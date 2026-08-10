"""Property-based tests for SourcesParser.

**Validates: Requirements 2.1, 2.2, 2.4**

Property 4: SourcePackageMetadata round-trip.
For any valid SourcePackageMetadata value object, formatting it into a
Sources stanza string and then parsing that string back SHALL produce a
SourcePackageMetadata object equivalent to the original.

Property 5: Invalid Sources stanzas are skipped.
For any Sources stanza that is missing the Package or Version field,
parsing SHALL produce no SourcePackageMetadata output for that stanza
and the parser SHALL not raise an exception.
"""

# Feature: repository-indexer, Property 4: SourcePackageMetadata round-trip
# Feature: repository-indexer, Property 5: Invalid Sources stanzas are skipped

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.domain.indexer.sources_parser import SourcesParser
from debcraft.domain.indexer.values import SourcePackageMetadata

# ---------------------------------------------------------------------------
# Strategies for generating field values
# ---------------------------------------------------------------------------

# Characters safe for Debian field values (printable, no newlines or colons in names)
_FIELD_VALUE_CHARS = st.characters(
    whitelist_categories=("L", "N", "P", "S"),
    blacklist_characters="\x00\n\r:",
)


def _field_value() -> st.SearchStrategy[str]:
    """Generate a plausible field value string."""
    return st.text(_FIELD_VALUE_CHARS, min_size=1, max_size=50).filter(lambda s: s.strip() != "")


def _version_string() -> st.SearchStrategy[str]:
    """Generate a plausible Debian version string."""
    epoch = st.sampled_from(["", "1:", "2:"])
    upstream = st.from_regex(r"[0-9]+\.[0-9]+(\.[0-9]+)?", fullmatch=True)
    revision = st.sampled_from(["", "-1", "-2", "-1ubuntu1"])
    return st.builds(lambda e, u, r: f"{e}{u}{r}", epoch, upstream, revision)


def _package_name() -> st.SearchStrategy[str]:
    """Generate a plausible Debian source package name."""
    return st.from_regex(r"[a-z][a-z0-9\-\+\.]{1,30}", fullmatch=True)


# ---------------------------------------------------------------------------
# Strategies for generating valid SourcePackageMetadata (Property 4)
# ---------------------------------------------------------------------------

# Debian package name characters: lowercase letters, digits, +, -, .
_PACKAGE_NAME_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789+-."


def _debian_package_name() -> st.SearchStrategy[str]:
    """Generate a valid Debian package name.

    Package names start with an alphanumeric character and contain
    lowercase letters, digits, +, -, and . characters.
    """
    first_char = st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789")
    rest = st.text(
        alphabet=_PACKAGE_NAME_CHARS,
        min_size=0,
        max_size=30,
    )
    return st.builds(lambda f, r: f + r, first_char, rest)


# Version characters: digits, letters, ., +, -, ~, :
_VERSION_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789.+-~:"


def _debian_version() -> st.SearchStrategy[str]:
    """Generate a valid Debian version string starting with a digit."""
    first_char = st.sampled_from("0123456789")
    rest = st.text(
        alphabet=_VERSION_CHARS,
        min_size=0,
        max_size=30,
    )
    return st.builds(lambda f, r: f + r, first_char, rest)


def _safe_single_line_text() -> st.SearchStrategy[str]:
    """Generate text suitable for single-line fields.

    Excludes newlines and commas (used as separators), and ensures
    no leading whitespace or colons that could break stanza parsing.
    """
    return (
        st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N", "P", "S"),
                blacklist_characters="\n\r\x00,",
            ),
            min_size=1,
            max_size=60,
        )
        .map(lambda s: s.strip())
        .filter(lambda s: len(s) > 0 and not s.startswith(":"))
    )


def _safe_uploader() -> st.SearchStrategy[str]:
    """Generate a valid uploader string.

    Uploaders are separated by commas, so individual entries must not
    contain commas or newlines.
    """
    return (
        st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N", "P", "S"),
                blacklist_characters="\n\r\x00,",
            ),
            min_size=1,
            max_size=40,
        )
        .map(lambda s: s.strip())
        .filter(lambda s: len(s) > 0 and not s.startswith(":"))
    )


@st.composite
def _source_package_metadata(
    draw: st.DrawFn,
) -> SourcePackageMetadata:
    """Generate a valid SourcePackageMetadata object.

    Ensures all generated values are compatible with the round-trip
    format/parse cycle (no characters that break stanza parsing).
    """
    name = draw(_debian_package_name())
    version = draw(_debian_version())

    maintainer = draw(st.one_of(st.none(), _safe_single_line_text()))

    uploaders = draw(st.lists(_safe_uploader(), min_size=0, max_size=5))

    section = draw(st.one_of(st.none(), _safe_single_line_text()))

    homepage = draw(st.one_of(st.none(), _safe_single_line_text()))

    build_depends = draw(st.one_of(st.none(), _safe_single_line_text()))

    binary_packages = draw(st.lists(_debian_package_name(), min_size=0, max_size=10))

    return SourcePackageMetadata(
        name=name,
        version=version,
        maintainer=maintainer,
        uploaders=uploaders,
        section=section,
        homepage=homepage,
        build_depends=build_depends,
        binary_packages=binary_packages,
    )


# ---------------------------------------------------------------------------
# Property 4: SourcePackageMetadata round-trip
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProperty4SourcePackageMetadataRoundTrip:
    """Property 4: SourcePackageMetadata round-trip.

    For any valid SourcePackageMetadata value object, formatting it
    into a Sources stanza string and then parsing that string back
    SHALL produce a SourcePackageMetadata object equivalent to the
    original.

    **Validates: Requirements 2.1, 2.4**
    """

    @settings(max_examples=200)
    @given(metadata=_source_package_metadata())
    def test_round_trip_produces_equivalent_object(self, metadata: SourcePackageMetadata) -> None:
        """Format then parse produces equivalent SourcePackageMetadata."""
        parser = SourcesParser()

        # Format the metadata into a stanza string
        formatted = parser.format(metadata)

        # Parse the formatted string back
        results = parser.parse(formatted)

        assert len(results) == 1, (
            f"Expected exactly 1 parsed result, got {len(results)}. Formatted stanza:\n{formatted}"
        )

        parsed = results[0]

        assert parsed.name == metadata.name, f"Name mismatch: expected '{metadata.name}', got '{parsed.name}'"
        assert parsed.version == metadata.version, (
            f"Version mismatch: expected '{metadata.version}', got '{parsed.version}'"
        )
        assert parsed.maintainer == metadata.maintainer, (
            f"Maintainer mismatch: expected '{metadata.maintainer}', got '{parsed.maintainer}'"
        )
        assert parsed.uploaders == metadata.uploaders, (
            f"Uploaders mismatch: expected {metadata.uploaders}, got {parsed.uploaders}"
        )
        assert parsed.section == metadata.section, (
            f"Section mismatch: expected '{metadata.section}', got '{parsed.section}'"
        )
        assert parsed.homepage == metadata.homepage, (
            f"Homepage mismatch: expected '{metadata.homepage}', got '{parsed.homepage}'"
        )
        assert parsed.build_depends == metadata.build_depends, (
            f"Build-Depends mismatch: expected '{metadata.build_depends}', got '{parsed.build_depends}'"
        )
        assert parsed.binary_packages == metadata.binary_packages, (
            f"Binary packages mismatch: expected {metadata.binary_packages}, got {parsed.binary_packages}"
        )

    @settings(max_examples=200)
    @given(metadata=_source_package_metadata())
    def test_round_trip_full_equality(self, metadata: SourcePackageMetadata) -> None:
        """Format then parse produces object equal to the original."""
        parser = SourcesParser()

        formatted = parser.format(metadata)
        results = parser.parse(formatted)

        assert len(results) == 1
        assert results[0] == metadata, (
            f"Round-trip inequality:\n  Original: {metadata}\n  Parsed:   {results[0]}\n  Stanza:\n{formatted}"
        )


# ---------------------------------------------------------------------------
# Property 5: Invalid Sources stanzas are skipped
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProperty5InvalidSourcesStanzasSkipped:
    """Property 5: Invalid Sources stanzas are skipped.

    For any Sources stanza that is missing the Package or Version field,
    parsing SHALL produce no SourcePackageMetadata output for that stanza
    and the parser SHALL not raise an exception.

    **Validates: Requirements 2.2**
    """

    @settings(max_examples=100)
    @given(
        omission_type=st.sampled_from(["missing_package", "missing_version", "missing_both"]),
        version=_version_string(),
        package_name=_package_name(),
        maintainer=st.one_of(st.none(), _field_value()),
        section=st.one_of(st.none(), _field_value()),
        homepage=st.one_of(
            st.none(),
            st.builds(lambda h: f"https://{h}.org", _package_name()),
        ),
    )
    def test_missing_required_fields_produce_empty_result(
        self,
        omission_type: str,
        version: str,
        package_name: str,
        maintainer: str | None,
        section: str | None,
        homepage: str | None,
    ) -> None:
        """Stanzas missing Package and/or Version produce no output and no exception."""
        # Build the stanza string, omitting the required field(s)
        lines: list[str] = []

        if omission_type == "missing_version":
            # Include Package but omit Version
            lines.append(f"Package: {package_name}")
        elif omission_type == "missing_package":
            # Include Version but omit Package
            lines.append(f"Version: {version}")
        # "missing_both" — omit both Package and Version

        # Add optional fields that might be present
        if maintainer is not None:
            lines.append(f"Maintainer: {maintainer}")
        if section is not None:
            lines.append(f"Section: {section}")
        if homepage is not None:
            lines.append(f"Homepage: {homepage}")

        content = "\n".join(lines)
        parser = SourcesParser()

        # Should not raise any exception
        result = parser.parse(content)

        # Should produce an empty list (stanza was skipped)
        assert result == [], (
            f"Expected empty result for stanza with {omission_type}, got {len(result)} items. Content:\n{content}"
        )


# ---------------------------------------------------------------------------
# Property 6: Binary field comma splitting
# ---------------------------------------------------------------------------

# Feature: repository-indexer, Property 6: Binary field comma splitting


@pytest.mark.unit
class TestProperty6BinaryFieldCommaSplitting:
    """Property 6: Binary field comma splitting.

    For any list of package name strings, when joined with commas and
    optional whitespace into a Binary field value and embedded in a valid
    Sources stanza, parsing SHALL produce a SourcePackageMetadata with
    binary_packages equal to the original list (each name trimmed of
    whitespace).

    **Validates: Requirements 2.3**
    """

    @settings(max_examples=100)
    @given(
        package_names=st.lists(_debian_package_name(), min_size=1, max_size=10),
        separator=st.sampled_from([", ", ",", " , ", ",  "]),
    )
    def test_binary_field_comma_splitting_preserves_names(
        self,
        package_names: list[str],
        separator: str,
    ) -> None:
        """Joining package names with various separators parses back correctly."""
        # Join the package names with the chosen separator
        binary_field_value = separator.join(package_names)

        # Construct a complete valid Sources stanza
        content = f"Package: test-source-pkg\nVersion: 1.0-1\nBinary: {binary_field_value}\n"

        parser = SourcesParser()
        results = parser.parse(content)

        assert len(results) == 1, f"Expected exactly 1 parsed result, got {len(results)}. Content:\n{content}"

        parsed = results[0]

        assert parsed.binary_packages == package_names, (
            f"Binary packages mismatch:\n"
            f"  Expected: {package_names}\n"
            f"  Got:      {parsed.binary_packages}\n"
            f"  Separator used: {separator!r}\n"
            f"  Binary field value: {binary_field_value!r}"
        )
