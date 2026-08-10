"""Property-based tests for PackagesParser.

# Feature: repository-indexer, Property 1: PackageMetadata round-trip
# Feature: repository-indexer, Property 2: Source field inference rules
# Feature: repository-indexer, Property 3: Invalid Packages stanzas are skipped

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6**
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.domain.indexer.packages_parser import PackagesParser
from debcraft.domain.indexer.values import PackageMetadata

# ===========================================================================
# Property 1: PackageMetadata round-trip strategies
# ===========================================================================

# Debian package names: lowercase alphanumerics, +, -, . (must start with alnum)
_DEBIAN_NAME_START = "abcdefghijklmnopqrstuvwxyz0123456789"
_DEBIAN_NAME_CHARS = _DEBIAN_NAME_START + "+-."


def _debian_package_name() -> st.SearchStrategy[str]:
    """Generate a valid Debian package name."""
    return st.builds(
        lambda start, rest: start + rest,
        st.text(alphabet=_DEBIAN_NAME_START, min_size=1, max_size=1),
        st.text(alphabet=_DEBIAN_NAME_CHARS, min_size=0, max_size=30),
    ).filter(lambda s: not s.endswith("+") and not s.endswith("-") and not s.endswith("."))


# Debian version characters: alphanumerics, ., +, ~, -
_VERSION_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789.+~-"


def _debian_version() -> st.SearchStrategy[str]:
    """Generate a valid Debian version string."""
    return st.text(
        alphabet=_VERSION_CHARS,
        min_size=1,
        max_size=30,
    ).filter(lambda s: s[0].isalnum() and s[-1].isalnum())


_ARCHITECTURES = st.sampled_from(["amd64", "arm64", "i386", "all", "armhf"])


def _debian_filename() -> st.SearchStrategy[str]:
    """Generate a valid .deb filename path."""
    return st.builds(
        lambda pkg, ver, arch: f"pool/main/{pkg[0]}/{pkg}/{pkg}_{ver}_{arch}.deb",
        _debian_package_name(),
        st.text(alphabet="0123456789.", min_size=1, max_size=10).filter(lambda s: s[0].isdigit() and s[-1].isdigit()),
        _ARCHITECTURES,
    )


def _sha256_hash() -> st.SearchStrategy[str]:
    """Generate a valid SHA256 hash: exactly 64 lowercase hex characters."""
    return st.text(
        alphabet="0123456789abcdef",
        min_size=64,
        max_size=64,
    )


# Single-line field text: no newlines, no colons at start, no blank content
_SINGLE_LINE_CHARS = st.characters(
    whitelist_categories=("L", "N", "P", "S"),
    blacklist_characters="\n\r\x00",
)


def _single_line_text() -> st.SearchStrategy[str]:
    """Generate text suitable for a single-line stanza field value."""
    return st.text(
        alphabet=_SINGLE_LINE_CHARS,
        min_size=1,
        max_size=60,
    ).filter(lambda s: s.strip() != "" and not s.startswith(":"))


def _optional_single_line() -> st.SearchStrategy[str | None]:
    """Generate either None or a valid single-line text value."""
    return st.one_of(st.none(), _single_line_text())


# Description: may contain newlines but continuation lines must not be empty
_DESC_LINE_CHARS = st.characters(
    whitelist_categories=("L", "N", "P", "S", "Z"),
    blacklist_characters="\n\r\x00",
)


def _description_line() -> st.SearchStrategy[str]:
    """Generate a single description line (no newlines, no surrounding whitespace).

    The parser strips leading/trailing whitespace from field values (standard
    Debian format behavior), so generated values must be pre-stripped for
    round-trip equivalence.
    """
    return st.text(
        alphabet=_DESC_LINE_CHARS,
        min_size=1,
        max_size=60,
    ).filter(lambda s: s.strip() == s and len(s) > 0)


def _description() -> st.SearchStrategy[str | None]:
    """Generate a valid description (possibly multi-line) or None."""
    single = _description_line()
    multi = st.builds(
        lambda first, rest: "\n".join([first, *rest]),
        _description_line(),
        st.lists(_description_line(), min_size=0, max_size=3),
    )
    return st.one_of(st.none(), single, multi)


@st.composite
def _valid_package_metadata(draw: st.DrawFn) -> PackageMetadata:
    """Generate a valid PackageMetadata value object."""
    package_name = draw(_debian_package_name())
    version = draw(_debian_version())
    architecture = draw(_ARCHITECTURES)
    filename = draw(_debian_filename())
    sha256 = draw(_sha256_hash())
    size_bytes = draw(st.integers(min_value=0, max_value=10**12))

    # Source package and version: may or may not differ from binary
    source_package = draw(st.one_of(st.just(package_name), _debian_package_name()))
    source_version = draw(st.one_of(st.just(version), _debian_version()))

    homepage = draw(_optional_single_line())
    maintainer = draw(_optional_single_line())
    depends = draw(_optional_single_line())
    provides = draw(_optional_single_line())
    section = draw(_optional_single_line())
    priority = draw(_optional_single_line())
    description = draw(_description())

    return PackageMetadata(
        package_name=package_name,
        version=version,
        architecture=architecture,
        filename=filename,
        sha256=sha256,
        size_bytes=size_bytes,
        source_package=source_package,
        source_version=source_version,
        homepage=homepage,
        maintainer=maintainer,
        depends=depends,
        provides=provides,
        section=section,
        priority=priority,
        description=description,
    )


# ---------------------------------------------------------------------------
# Property 1: PackageMetadata round-trip
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProperty1PackageMetadataRoundTrip:
    """Property 1: PackageMetadata round-trip.

    For any valid PackageMetadata value object, formatting it into a
    Packages stanza string and then parsing that string back SHALL
    produce a PackageMetadata object equivalent to the original.

    **Validates: Requirements 1.1, 1.6**
    """

    @settings(max_examples=200)
    @given(metadata=_valid_package_metadata())
    def test_format_then_parse_produces_equivalent_metadata(self, metadata: PackageMetadata) -> None:
        """format(metadata) -> parse(stanza) produces an equivalent object."""
        parser = PackagesParser()

        # Format the metadata into a stanza string
        stanza = parser.format(metadata)

        # Parse it back (parse returns a list; our single stanza should yield one result)
        results = parser.parse(stanza)

        assert len(results) == 1, f"Expected exactly 1 parsed result, got {len(results)}.\nStanza:\n{stanza}"

        parsed = results[0]
        assert parsed == metadata, f"Round-trip mismatch.\nOriginal: {metadata}\nParsed:   {parsed}\nStanza:\n{stanza}"


# ===========================================================================
# Property 2: Source field inference rules
# ===========================================================================

# Helper to build a minimal valid stanza with optional Source field
_SOURCE_TEST_TEMPLATE = (
    "Package: {package}\n"
    "Version: {version}\n"
    "Architecture: amd64\n"
    "Filename: pool/main/{package}_{version}_amd64.deb\n"
    "SHA256: "
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\n"
    "Size: 1024"
)


def _build_source_test_stanza(
    package: str,
    version: str,
    source_line: str | None = None,
) -> str:
    """Build a minimal valid Packages stanza with optional Source field."""
    base = _SOURCE_TEST_TEMPLATE.format(package=package, version=version)
    if source_line is not None:
        return base + "\n" + source_line
    return base


# Source name: lowercase letter start, then allowed chars (no parens, no whitespace)
def _source_package_name() -> st.SearchStrategy[str]:
    """Generate a valid Debian source package name."""
    return st.builds(
        lambda start, rest: start + rest,
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=1),
        st.text(alphabet=_DEBIAN_NAME_CHARS, min_size=1, max_size=25),
    ).filter(lambda s: not s.endswith("+") and not s.endswith("-") and not s.endswith("."))


# Source version: digit start, no parens or whitespace
_SOURCE_VERSION_CHARS = "0123456789abcdefghijklmnopqrstuvwxyz.+~-"


def _source_version() -> st.SearchStrategy[str]:
    """Generate a valid Debian source version (no parens, no whitespace)."""
    return st.text(
        alphabet=_SOURCE_VERSION_CHARS,
        min_size=1,
        max_size=20,
    ).filter(lambda s: s[0].isdigit() and s[-1].isalnum())


@pytest.mark.unit
class TestProperty2SourceFieldInferenceRules:
    """Property 2: Source field inference rules.

    For any valid Packages stanza, the inferred source_package and
    source_version SHALL follow these rules:
    - If Source: name (version), use name and version.
    - If Source: name (no parens), use name and binary version.
    - If Source is absent, use binary package name and binary version.

    **Validates: Requirements 1.3, 1.4, 1.5**
    """

    @settings(max_examples=200)
    @given(
        source_name=_source_package_name(),
        source_ver=_source_version(),
        package_name=_debian_package_name(),
        binary_version=_debian_version(),
    )
    def test_source_with_version_extracts_both(
        self,
        source_name: str,
        source_ver: str,
        package_name: str,
        binary_version: str,
    ) -> None:
        """Source: name (version) -> source_package=name, source_version=version.

        Validates: Requirement 1.3
        """
        source_line = f"Source: {source_name} ({source_ver})"
        stanza = _build_source_test_stanza(package_name, binary_version, source_line)

        parser = PackagesParser()
        results = parser.parse(stanza)

        assert len(results) == 1
        metadata = results[0]
        assert metadata.source_package == source_name
        assert metadata.source_version == source_ver

    @settings(max_examples=200)
    @given(
        source_name=_source_package_name(),
        package_name=_debian_package_name(),
        binary_version=_debian_version(),
    )
    def test_source_name_only_uses_binary_version(
        self,
        source_name: str,
        package_name: str,
        binary_version: str,
    ) -> None:
        """Source: name (no parens) -> source_package=name, source_version=binary_version.

        Validates: Requirement 1.4
        """
        source_line = f"Source: {source_name}"
        stanza = _build_source_test_stanza(package_name, binary_version, source_line)

        parser = PackagesParser()
        results = parser.parse(stanza)

        assert len(results) == 1
        metadata = results[0]
        assert metadata.source_package == source_name
        assert metadata.source_version == binary_version

    @settings(max_examples=200)
    @given(
        package_name=_debian_package_name(),
        binary_version=_debian_version(),
    )
    def test_source_absent_uses_package_name_and_version(
        self,
        package_name: str,
        binary_version: str,
    ) -> None:
        """Source absent -> source_package=package_name, source_version=binary_version.

        Validates: Requirement 1.5
        """
        stanza = _build_source_test_stanza(package_name, binary_version, source_line=None)

        parser = PackagesParser()
        results = parser.parse(stanza)

        assert len(results) == 1
        metadata = results[0]
        assert metadata.source_package == package_name
        assert metadata.source_version == binary_version


# ===========================================================================
# Property 3: Invalid Packages stanzas are skipped (strategies)
# ===========================================================================

# ---------------------------------------------------------------------------
# Required fields for a valid Packages stanza
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = [
    "Package",
    "Version",
    "Architecture",
    "Filename",
    "SHA256",
    "Size",
]

# ---------------------------------------------------------------------------
# Strategies for generating field values
# ---------------------------------------------------------------------------

_HEX_CHARS = "0123456789abcdef"


def _field_value_for(field_name: str) -> st.SearchStrategy[str]:
    """Generate a plausible value for a given Packages field."""
    if field_name == "Package":
        return st.from_regex(r"[a-z][a-z0-9.+\-]{1,30}", fullmatch=True)
    if field_name == "Version":
        return st.from_regex(r"[0-9]+\.[0-9]+(\-[0-9]+)?", fullmatch=True)
    if field_name == "Architecture":
        return st.sampled_from(["amd64", "arm64", "i386", "all", "armhf"])
    if field_name == "Filename":
        return st.from_regex(
            r"pool/main/[a-z]/[a-z0-9\-]+/[a-z0-9\-]+_[0-9.]+_[a-z0-9]+\.deb",
            fullmatch=True,
        )
    if field_name == "SHA256":
        return st.text(alphabet=_HEX_CHARS, min_size=64, max_size=64)
    if field_name == "Size":
        return st.integers(min_value=0, max_value=10**9).map(str)
    return st.text(min_size=1, max_size=50)


# ---------------------------------------------------------------------------
# Strategy: generate a stanza missing at least one required field
# ---------------------------------------------------------------------------


@st.composite
def _stanza_missing_required_fields(
    draw: st.DrawFn,
) -> str:
    """Generate a Packages stanza missing at least one required field.

    Starts with all 6 required fields, selects at least one to remove,
    and builds the stanza string from the remaining fields.
    """
    # Select which fields to remove (at least 1)
    fields_to_remove = draw(
        st.sets(
            st.sampled_from(_REQUIRED_FIELDS),
            min_size=1,
        )
    )

    # Build stanza with remaining fields
    remaining_fields = [f for f in _REQUIRED_FIELDS if f not in fields_to_remove]

    lines: list[str] = []
    for field_name in remaining_fields:
        value = draw(_field_value_for(field_name))
        lines.append(f"{field_name}: {value}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Property 3: Invalid Packages stanzas are skipped
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProperty3InvalidStanzasSkipped:
    """Property 3: Invalid Packages stanzas are skipped.

    For any Packages stanza that is missing at least one required field
    (Package, Version, Architecture, Filename, SHA256, or Size),
    parsing SHALL produce no PackageMetadata output for that stanza
    and the parser SHALL not raise an exception.

    **Validates: Requirements 1.2**
    """

    @settings(max_examples=100)
    @given(stanza=_stanza_missing_required_fields())
    def test_missing_required_field_produces_empty_result(self, stanza: str) -> None:
        """Stanzas missing required fields produce no output."""
        parser = PackagesParser()
        result = parser.parse(stanza)
        assert result == [], (
            f"Expected empty list for stanza missing required fields, got {len(result)} result(s).\nStanza:\n{stanza}"
        )

    @settings(max_examples=100)
    @given(stanza=_stanza_missing_required_fields())
    def test_missing_required_field_does_not_raise(self, stanza: str) -> None:
        """Stanzas missing required fields do not raise exceptions."""
        parser = PackagesParser()
        # Should not raise any exception
        try:
            parser.parse(stanza)
        except Exception as exc:
            pytest.fail(f"Parser raised {type(exc).__name__}: {exc}\nStanza:\n{stanza}")
