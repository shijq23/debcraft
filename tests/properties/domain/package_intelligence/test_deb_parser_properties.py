"""Property-based tests for deb parser.

# Feature: package-intelligence, Property 17: Control File Field Extraction
# Feature: package-intelligence, Property 18: Dependency String Parsing Preservation
# Feature: package-intelligence, Property 19: Invalid Input Rejection by Deb Parser

**Validates: Requirements 1.6, 1.10, 1.11**
"""

from __future__ import annotations

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from debcraft.domain.package_intelligence.deb_parser import DebParser
from debcraft.domain.package_intelligence.errors import DebParseError

# ===========================================================================
# Strategies for Property 17: valid control file text generation
# ===========================================================================

# Characters valid in Debian control file field names (letters, digits, hyphens)
_FIELD_NAME_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-"

# Characters valid in single-line field values (printable ASCII excluding newline)
_FIELD_VALUE_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 !@#$%^&*()-_=+[]{}|;',.<>?/~`"


@st.composite
def control_field_name(draw: st.DrawFn) -> str:
    """Generate a valid control file field name.

    Must start with a letter and contain only letters, digits, hyphens.
    """
    first_char = draw(st.sampled_from("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"))
    rest = draw(st.text(alphabet=_FIELD_NAME_CHARS, min_size=0, max_size=20))
    return first_char + rest


@st.composite
def control_field_value(draw: st.DrawFn) -> str:
    """Generate a valid single-line field value (no newlines, no leading/trailing space/tab).

    Trailing whitespace is stripped by control file parsers (it is not semantically
    significant in Debian control format), so we avoid generating it.
    """
    value = draw(st.text(alphabet=_FIELD_VALUE_CHARS, min_size=1, max_size=60))
    # Strip leading and trailing spaces - leading space/tab would be a continuation
    # line, and trailing whitespace is stripped by the parser as non-significant.
    stripped = value.strip(" \t")
    return stripped or "value"


@st.composite
def control_file_text(draw: st.DrawFn) -> tuple[dict[str, str], str]:
    """Generate a valid Debian control file text and expected fields dict.

    Returns a tuple of (expected_fields_dict, control_text_string).
    """
    num_fields = draw(st.integers(min_value=1, max_value=8))

    # Generate unique field names
    field_names: list[str] = []
    for _ in range(num_fields):
        name = draw(control_field_name())
        # Ensure unique field names (case-sensitive for simplicity)
        assume(name not in field_names)
        field_names.append(name)

    # Generate values for each field
    fields: dict[str, str] = {}
    lines: list[str] = []

    for name in field_names:
        value = draw(control_field_value())
        fields[name] = value
        lines.append(f"{name}: {value}")

    text = "\n".join(lines) + "\n"
    return (fields, text)


# ===========================================================================
# Strategies for Property 18: valid dependency string generation
# ===========================================================================

# Valid characters for Debian package names (must start with alnum)
_PKG_NAME_START = "abcdefghijklmnopqrstuvwxyz0123456789"
_PKG_NAME_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789.+-"

# Version operators
_VERSION_OPS = [">=", "<=", ">>", "<<", "="]

# Version string characters
_VERSION_CHARS = "0123456789.+-~:"


@st.composite
def debian_package_name(draw: st.DrawFn) -> str:
    """Generate a valid Debian package name."""
    first = draw(st.sampled_from(list(_PKG_NAME_START)))
    rest = draw(st.text(alphabet=_PKG_NAME_CHARS, min_size=0, max_size=15))
    return first + rest


@st.composite
def version_string(draw: st.DrawFn) -> str:
    """Generate a valid version string for constraints."""
    return draw(st.text(alphabet=_VERSION_CHARS, min_size=1, max_size=12))


@st.composite
def single_dep_with_version(draw: st.DrawFn) -> tuple[str, str, str | None]:
    """Generate a single dependency with optional version constraint.

    Returns (dep_string, expected_package_name, expected_version_constraint).
    """
    pkg = draw(debian_package_name())
    has_version = draw(st.booleans())

    if has_version:
        op = draw(st.sampled_from(_VERSION_OPS))
        ver = draw(version_string())
        dep_str = f"{pkg} ({op} {ver})"
        expected_constraint = f"{op} {ver}"
    else:
        dep_str = pkg
        expected_constraint = None

    return (dep_str, pkg, expected_constraint)


@st.composite
def dependency_string_with_expected(
    draw: st.DrawFn,
) -> tuple[str, list[tuple[str, str | None, list[tuple[str, str | None]]]]]:
    """Generate a comma-separated dependency string with expected structure.

    Returns (full_dep_string, list_of_expected_relations) where each relation
    is (package_name, version_constraint, alternatives).
    """
    num_deps = draw(st.integers(min_value=1, max_value=4))
    dep_parts: list[str] = []
    expected: list[tuple[str, str | None, list[tuple[str, str | None]]]] = []

    for _ in range(num_deps):
        # Decide if this entry has alternatives
        has_alternatives = draw(st.booleans())

        if has_alternatives:
            num_alts = draw(st.integers(min_value=1, max_value=2))
            # Primary dependency
            primary_str, primary_pkg, primary_ver = draw(single_dep_with_version())
            alt_strs: list[str] = [primary_str]
            alt_expected: list[tuple[str, str | None]] = []

            for _ in range(num_alts):
                alt_str, alt_pkg, alt_ver = draw(single_dep_with_version())
                alt_strs.append(alt_str)
                alt_expected.append((alt_pkg, alt_ver))

            dep_parts.append(" | ".join(alt_strs))
            expected.append((primary_pkg, primary_ver, alt_expected))
        else:
            dep_str, pkg, ver = draw(single_dep_with_version())
            dep_parts.append(dep_str)
            expected.append((pkg, ver, []))

    full_string = ", ".join(dep_parts)
    return (full_string, expected)


# ===========================================================================
# Strategies for Property 19: invalid byte sequences
# ===========================================================================

_AR_MAGIC = b"!<arch>\n"


@st.composite
def bytes_without_ar_magic(draw: st.DrawFn) -> bytes:
    r"""Generate byte sequences that do NOT start with ar magic bytes.

    Ensures the generated bytes don't begin with `!<arch>\\n`.
    """
    data = draw(st.binary(min_size=0, max_size=256))
    # Ensure it does not start with the ar magic
    assume(not data.startswith(_AR_MAGIC))
    return data


# ===========================================================================
# Mock DebFileReader for Property 19
# ===========================================================================


class _MockDebFileReaderInvalid:
    """Mock DebFileReader that returns bytes without ar magic."""

    def __init__(self, raw_bytes: bytes) -> None:
        self._raw_bytes = raw_bytes

    def read_ar_member(self, deb_path: str, member_prefix: str) -> bytes:
        """Return the stored raw bytes regardless of member prefix."""
        return self._raw_bytes

    def compute_sha256(self, file_path: str) -> str:
        """Return a dummy SHA256 hash."""
        return "0" * 64


# ===========================================================================
# Property 17: Control File Field Extraction
# ===========================================================================


@pytest.mark.unit
@pytest.mark.package
class TestProperty17ControlFileFieldExtraction:
    """Property 17: Control File Field Extraction.

    For any valid Debian control file text containing a set of known fields,
    the control file parser SHALL extract each present field with its exact
    value and represent absent fields as None.

    **Validates: Requirements 1.10**
    """

    @settings(max_examples=100)
    @given(data=control_file_text())
    def test_all_fields_extracted_with_exact_values(self, data: tuple[dict[str, str], str]) -> None:
        """Each field in the control text is extracted with its exact value."""
        expected_fields, text = data

        # Create a parser instance (file_reader not needed for _parse_control_text)
        parser = DebParser.__new__(DebParser)
        result = parser._parse_control_text(text)

        for field_name, expected_value in expected_fields.items():
            assert field_name in result, (
                f"Field '{field_name}' not found in parsed result.\nInput text:\n{text!r}\nParsed fields: {result}"
            )
            assert result[field_name] == expected_value, (
                f"Field '{field_name}' has wrong value.\n"
                f"Expected: {expected_value!r}\n"
                f"Got: {result[field_name]!r}\n"
                f"Input text:\n{text!r}"
            )

    @settings(max_examples=100)
    @given(data=control_file_text())
    def test_no_extra_fields_beyond_input(self, data: tuple[dict[str, str], str]) -> None:
        """Parser does not invent fields that are not in the input."""
        expected_fields, text = data

        parser = DebParser.__new__(DebParser)
        result = parser._parse_control_text(text)

        for field_name in result:
            assert field_name in expected_fields, (
                f"Unexpected field '{field_name}' in parsed result.\n"
                f"Expected fields: {list(expected_fields.keys())}\n"
                f"Input text:\n{text!r}"
            )


# ===========================================================================
# Property 18: Dependency String Parsing Preservation
# ===========================================================================


@pytest.mark.unit
@pytest.mark.package
class TestProperty18DependencyStringParsingPreservation:
    """Property 18: Dependency String Parsing Preservation.

    For any valid dependency string containing package names with version
    constraints and alternatives, parsing SHALL produce a list of
    DependencyRelation objects that preserve all package names, version
    operators, version numbers, and alternative groupings from the input.

    **Validates: Requirements 1.11**
    """

    @settings(max_examples=100)
    @given(data=dependency_string_with_expected())
    def test_dependency_package_names_preserved(
        self,
        data: tuple[str, list[tuple[str, str | None, list[tuple[str, str | None]]]]],
    ) -> None:
        """All package names from the input are preserved in the parse result."""
        dep_string, expected_relations = data

        parser = DebParser.__new__(DebParser)
        result = parser._parse_dependency_field(dep_string, "test-pkg", "Depends")

        assert len(result) == len(expected_relations), (
            f"Expected {len(expected_relations)} relations, got {len(result)}.\nInput: {dep_string!r}\nResult: {result}"
        )

        for i, (relation, expected) in enumerate(zip(result, expected_relations, strict=False)):
            exp_pkg, _exp_ver, _exp_alts = expected

            assert relation.package == exp_pkg, (
                f"Relation {i}: expected package '{exp_pkg}', got '{relation.package}'.\nInput: {dep_string!r}"
            )

    @settings(max_examples=100)
    @given(data=dependency_string_with_expected())
    def test_dependency_version_constraints_preserved(
        self,
        data: tuple[str, list[tuple[str, str | None, list[tuple[str, str | None]]]]],
    ) -> None:
        """Version constraints from the input are preserved in the parse result."""
        dep_string, expected_relations = data

        parser = DebParser.__new__(DebParser)
        result = parser._parse_dependency_field(dep_string, "test-pkg", "Depends")

        for i, (relation, expected) in enumerate(zip(result, expected_relations, strict=False)):
            _exp_pkg, exp_ver, _exp_alts = expected

            assert relation.version_constraint == exp_ver, (
                f"Relation {i}: expected version_constraint {exp_ver!r}, "
                f"got {relation.version_constraint!r}.\n"
                f"Input: {dep_string!r}"
            )

    @settings(max_examples=100)
    @given(data=dependency_string_with_expected())
    def test_dependency_alternatives_preserved(
        self,
        data: tuple[str, list[tuple[str, str | None, list[tuple[str, str | None]]]]],
    ) -> None:
        """Alternative packages are preserved in the parse result."""
        dep_string, expected_relations = data

        parser = DebParser.__new__(DebParser)
        result = parser._parse_dependency_field(dep_string, "test-pkg", "Depends")

        for i, (relation, expected) in enumerate(zip(result, expected_relations, strict=False)):
            _exp_pkg, _exp_ver, exp_alts = expected

            assert len(relation.alternatives) == len(exp_alts), (
                f"Relation {i}: expected {len(exp_alts)} alternatives, "
                f"got {len(relation.alternatives)}.\n"
                f"Input: {dep_string!r}"
            )

            for j, (alt, exp_alt) in enumerate(zip(relation.alternatives, exp_alts, strict=False)):
                exp_alt_pkg, exp_alt_ver = exp_alt
                assert alt.package == exp_alt_pkg, (
                    f"Relation {i}, alt {j}: expected package "
                    f"'{exp_alt_pkg}', got '{alt.package}'.\n"
                    f"Input: {dep_string!r}"
                )
                assert alt.version_constraint == exp_alt_ver, (
                    f"Relation {i}, alt {j}: expected version "
                    f"{exp_alt_ver!r}, got {alt.version_constraint!r}.\n"
                    f"Input: {dep_string!r}"
                )


# ===========================================================================
# Property 19: Invalid Input Rejection by Deb Parser
# ===========================================================================


@pytest.mark.unit
@pytest.mark.package
class TestProperty19InvalidInputRejectionByDebParser:
    r"""Property 19: Invalid Input Rejection by Deb Parser.

    For any byte sequence that does not begin with the `!<arch>\\n` magic
    bytes (valid ar archive signature), the Deb_Parser SHALL raise a
    DebParseError.

    **Validates: Requirements 1.6**
    """

    @settings(max_examples=100)
    @given(raw_bytes=bytes_without_ar_magic())
    def test_rejects_bytes_without_ar_magic(self, raw_bytes: bytes) -> None:
        """DebParser raises DebParseError for bytes missing ar magic."""
        reader = _MockDebFileReaderInvalid(raw_bytes)
        parser = DebParser(file_reader=reader)

        with pytest.raises(DebParseError) as exc_info:
            parser.parse("/fake/path.deb")

        assert exc_info.value.file_path == "/fake/path.deb"
        assert "magic" in exc_info.value.reason.lower() or "ar archive" in exc_info.value.reason.lower(), (
            f"Error reason should mention magic bytes or ar archive, got: {exc_info.value.reason!r}"
        )
