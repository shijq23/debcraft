"""Property-based tests for PURL generator.

# Feature: package-intelligence, Property 13: PURL Format Conformance
# Feature: package-intelligence, Property 14: PURL Generation Error for Missing Fields

**Validates: Requirements 10.1, 10.2, 10.4, 10.5, 10.6**
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.domain.package_intelligence.errors import PURLGenerationError
from debcraft.domain.package_intelligence.purl_generator import generate_purl

# ===========================================================================
# Strategies for Property 13: valid PURL inputs
# ===========================================================================

# Characters that appear in Debian package names: lowercase letters, digits,
# plus, minus, dot (must start with alnum).  We include special chars that
# require percent-encoding to ensure encoding is verified.
_PACKAGE_NAME_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789+-.:"

# Version strings can contain digits, letters, colons (epoch), hyphens,
# dots, tildes, plus signs.
_VERSION_CHARS = "0123456789abcdefghijklmnopqrstuvwxyz.:+-~"

# Common Debian architectures
_ARCHITECTURES = ["amd64", "arm64", "armhf", "i386", "all", "any", "mips64el", "s390x"]

# Distributions
_DISTROS = ["debian", "ubuntu", "Debian", "Ubuntu", "DEBIAN", "Raspbian"]


@st.composite
def valid_package_name(draw: st.DrawFn) -> str:
    """Generate a non-empty package name string."""
    return draw(
        st.text(
            alphabet=st.sampled_from(sorted(_PACKAGE_NAME_CHARS)),
            min_size=1,
            max_size=30,
        )
    )


@st.composite
def valid_version(draw: st.DrawFn) -> str:
    """Generate a non-empty version string."""
    return draw(
        st.text(
            alphabet=st.sampled_from(sorted(_VERSION_CHARS)),
            min_size=1,
            max_size=30,
        )
    )


def valid_architecture() -> st.SearchStrategy[str]:
    """Generate a valid architecture string."""
    return st.sampled_from(_ARCHITECTURES)


def optional_distro() -> st.SearchStrategy[str | None]:
    """Generate an optional distro string (None means default to 'debian')."""
    return st.one_of(
        st.none(),
        st.just(""),
        st.sampled_from(_DISTROS),
    )


# ===========================================================================
# Property 13: PURL Format Conformance
# ===========================================================================


@pytest.mark.unit
@pytest.mark.package
class TestProperty13PURLFormatConformance:
    """Property 13: PURL Format Conformance.

    For any valid (non-empty) package name, version, and architecture, the
    PURL_Generator SHALL produce a string matching the pattern
    `pkg:deb/<distro>/<name>@<version>?arch=<architecture>` where special
    characters in name and version are percent-encoded per the PURL
    specification, and distro is the lowercased distribution or "debian"
    if unspecified.

    **Validates: Requirements 10.1, 10.2, 10.4, 10.5**
    """

    @given(
        name=valid_package_name(),
        version=valid_version(),
        arch=valid_architecture(),
        distro=optional_distro(),
    )
    def test_purl_format_conformance(
        self,
        name: str,
        version: str,
        arch: str,
        distro: str | None,
    ) -> None:
        """Generated PURL matches the expected structural pattern."""
        result = generate_purl(name, version, arch, distro)

        # Must start with pkg:deb/ scheme
        assert result.startswith("pkg:deb/"), f"PURL does not start with 'pkg:deb/': {result!r}"

        # Must contain @ separating name from version
        assert "@" in result, f"PURL does not contain '@' separator: {result!r}"

        # Must contain ?arch= qualifier
        assert "?arch=" in result, f"PURL does not contain '?arch=' qualifier: {result!r}"

        # Verify distro is lowercased; defaults to "debian" when None/empty
        after_scheme = result[len("pkg:deb/") :]
        distro_part = after_scheme.split("/")[0]

        if distro is None or not distro.strip():
            assert distro_part == "debian", f"Expected 'debian' default distro, got: {distro_part!r}"
        else:
            assert distro_part == distro.strip().lower(), (
                f"Expected lowercased distro '{distro.strip().lower()}', got: {distro_part!r}"
            )

        # Verify special characters are percent-encoded in name and version
        # Extract the name@version portion (between distro/ and ?arch=)
        name_version_section = after_scheme.split("/", 1)[1].split("?arch=")[0]

        # ':' should be encoded as %3A
        assert ":" not in name_version_section, f"Unencoded ':' found in name@version: {name_version_section!r}"

        # '+' should be encoded as %2B
        assert "+" not in name_version_section, f"Unencoded '+' found in name@version: {name_version_section!r}"


# ===========================================================================
# Strategies for Property 14: inputs with missing/empty fields
# ===========================================================================


@st.composite
def inputs_with_missing_field(draw: st.DrawFn) -> tuple[str, str, str, str]:
    """Generate inputs where at least one required field is empty/whitespace.

    Returns (name, version, architecture, expected_missing_field).
    """
    # Decide which field(s) to make empty/whitespace
    field_to_invalidate = draw(st.sampled_from(["package_name", "version", "architecture"]))

    # Generate empty/whitespace values
    empty_values = st.one_of(
        st.just(""),
        st.just(" "),
        st.just("  "),
        st.just("\t"),
        st.just(" \t "),
    )

    # Generate valid values for the non-invalidated fields
    valid_name = draw(
        st.text(
            alphabet=st.sampled_from(sorted("abcdefghijklmnopqrstuvwxyz0123456789")),
            min_size=1,
            max_size=15,
        )
    )
    valid_version = draw(
        st.text(
            alphabet=st.sampled_from(sorted("0123456789.")),
            min_size=1,
            max_size=10,
        )
    )
    valid_arch = draw(st.sampled_from(_ARCHITECTURES))

    if field_to_invalidate == "package_name":
        name = draw(empty_values)
        version = valid_version
        arch = valid_arch
    elif field_to_invalidate == "version":
        name = valid_name
        version = draw(empty_values)
        arch = valid_arch
    else:  # architecture
        name = valid_name
        version = valid_version
        arch = draw(empty_values)

    return (name, version, arch, field_to_invalidate)


# ===========================================================================
# Property 14: PURL Generation Error for Missing Fields
# ===========================================================================


@pytest.mark.unit
@pytest.mark.package
class TestProperty14PURLGenerationErrorForMissingFields:
    """Property 14: PURL Generation Error for Missing Fields.

    For any input where at least one required field (package name, version,
    or architecture) is absent or empty, the PURL_Generator SHALL raise a
    PURLGenerationError identifying the missing field.

    **Validates: Requirements 10.6**
    """

    @given(inputs=inputs_with_missing_field())
    def test_purl_generation_error_for_missing_fields(
        self,
        inputs: tuple[str, str, str, str],
    ) -> None:
        """PURLGenerationError raised with correct missing_field attribute."""
        name, version, arch, expected_missing_field = inputs

        with pytest.raises(PURLGenerationError) as exc_info:
            generate_purl(name, version, arch)

        assert exc_info.value.missing_field == expected_missing_field, (
            f"Expected missing_field='{expected_missing_field}', "
            f"got '{exc_info.value.missing_field}'. "
            f"Inputs: name={name!r}, version={version!r}, arch={arch!r}"
        )
