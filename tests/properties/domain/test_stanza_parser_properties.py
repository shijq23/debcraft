r"""Property-based tests for the shared stanza parser utility.

# Feature: pylint-refactoring, Property 1: Stanza Parsing Equivalence

**Validates: Requirements 6.3**

Property 1: Stanza Parsing Equivalence.
For any valid stanza-formatted content string, the shared `split_stanzas`
utility followed by `parse_stanza_fields(preserve_continuations=True)` SHALL
produce identical field dictionaries to the original implementations for the
same input.

Since the old implementations have been migrated, we verify the shared utility's
properties directly:
  - split_stanzas should round-trip: joining stanzas with '\\n\\n' separator
    and re-splitting should give the same result
  - parse_stanza_fields with preserve_continuations=True should handle all
    valid stanza formats correctly
  - parse_stanza_fields_ordered should produce results consistent with
    parse_stanza_fields(preserve_continuations=True) for field names/values
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.domain._stanza_parser import (
    parse_stanza_fields,
    parse_stanza_fields_ordered,
    split_stanzas,
)

# ===========================================================================
# Strategies for generating stanza content
# ===========================================================================

# Field names: ASCII letters, digits, and hyphens (must start with a letter)
_FIELD_NAME_START = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_FIELD_NAME_CHARS = _FIELD_NAME_START + "0123456789-"


def _field_name() -> st.SearchStrategy[str]:
    """Generate a valid Debian stanza field name.

    Field names start with a letter and contain letters, digits, and hyphens.
    """
    return st.builds(
        lambda start, rest: start + rest,
        st.text(alphabet=_FIELD_NAME_START, min_size=1, max_size=1),
        st.text(alphabet=_FIELD_NAME_CHARS, min_size=0, max_size=20),
    )


# Field values: printable text without newlines
_FIELD_VALUE_CHARS = st.characters(
    whitelist_categories=("L", "N", "P", "S"),
    blacklist_characters="\x00\n\r",
)


def _field_value() -> st.SearchStrategy[str]:
    """Generate a valid single-line field value."""
    return st.text(
        alphabet=_FIELD_VALUE_CHARS,
        min_size=1,
        max_size=60,
    ).filter(lambda s: s.strip() != "")


# Continuation line content: printable text that is non-empty after the leading
# space is prepended. An empty continuation would produce a whitespace-only line,
# which split_stanzas correctly treats as a blank separator.
def _continuation_content() -> st.SearchStrategy[str]:
    """Generate content for a continuation line (non-empty to avoid blank lines)."""
    return st.text(
        alphabet=_FIELD_VALUE_CHARS,
        min_size=1,
        max_size=40,
    ).filter(lambda s: s.strip() != "")


@st.composite
def _stanza_field(draw: st.DrawFn) -> tuple[str, str, str]:
    """Generate a single stanza field with optional continuation lines.

    Returns (field_name, raw_stanza_text, expected_value_with_continuations).
    """
    name = draw(_field_name())
    value = draw(_field_value())
    continuations = draw(st.lists(_continuation_content(), min_size=0, max_size=3))

    # Build the raw stanza text for this field
    lines = [f"{name}: {value}"]
    for cont in continuations:
        lines.append(f" {cont}")

    raw_text = "\n".join(lines)

    # Expected value when preserve_continuations=True:
    # The initial value is stripped, then continuations appended with \n
    expected_value = value.strip()
    for cont in continuations:
        expected_value += "\n" + cont

    return (name, raw_text, expected_value)


@st.composite
def _single_stanza(draw: st.DrawFn) -> tuple[str, dict[str, str]]:
    """Generate a single stanza block with 1-5 fields.

    Returns (stanza_text, expected_fields_dict_with_continuations).
    Uses unique field names to avoid ambiguity.
    """
    num_fields = draw(st.integers(min_value=1, max_value=5))

    # Generate unique field names
    names_used: set[str] = set()
    fields: list[tuple[str, str, str]] = []

    for _ in range(num_fields):
        field = draw(_stanza_field())
        name = field[0]
        # Ensure unique field names (case-sensitive)
        if name in names_used:
            continue
        names_used.add(name)
        fields.append(field)

    if not fields:
        # Fallback: generate at least one field
        field = draw(_stanza_field())
        fields.append(field)

    # Build stanza text from field texts
    stanza_text = "\n".join(f[1] for f in fields)

    # Build expected dict
    expected_dict = {f[0]: f[2] for f in fields}

    return (stanza_text, expected_dict)


@st.composite
def _multi_stanza_content(draw: st.DrawFn) -> tuple[str, list[str]]:
    """Generate multi-stanza content (1-4 stanzas separated by blank lines).

    Returns (full_content, list_of_individual_stanza_texts).
    """
    num_stanzas = draw(st.integers(min_value=1, max_value=4))
    stanzas: list[str] = []

    for _ in range(num_stanzas):
        stanza_text, _ = draw(_single_stanza())
        stanzas.append(stanza_text)

    full_content = "\n\n".join(stanzas)
    return (full_content, stanzas)


# ===========================================================================
# Property 1: Stanza Parsing Equivalence
# ===========================================================================


@pytest.mark.unit
class TestProperty1StanzaParsingEquivalence:
    r"""Property 1: Stanza Parsing Equivalence.

    For any valid stanza-formatted content string, the shared stanza parser
    utilities SHALL produce correct and consistent results:
    - split_stanzas round-trips: joining with '\\n\\n' and re-splitting yields
      the same stanzas
    - parse_stanza_fields(preserve_continuations=True) correctly extracts all
      fields with their continuation lines
    - parse_stanza_fields_ordered is consistent with parse_stanza_fields for
      field names and values

    **Validates: Requirements 6.3**
    """

    @given(data=_multi_stanza_content())
    def test_split_stanzas_round_trip(self, data: tuple[str, list[str]]) -> None:
        r"""split_stanzas round-trips.

        Joining with '\\n\\n' and re-splitting
        produces the same list of stanza blocks.

        Validates: Requirements 6.3
        """
        full_content, _expected_stanzas = data

        # Split the content into stanzas
        result = split_stanzas(full_content)

        # Re-join and re-split should yield the same result
        rejoined = "\n\n".join(result)
        re_split = split_stanzas(rejoined)

        assert result == re_split, (
            f"Round-trip mismatch.\n"
            f"  Original split: {result}\n"
            f"  Re-split:       {re_split}\n"
            f"  Content:\n{full_content}"
        )

    @given(data=_multi_stanza_content())
    def test_split_stanzas_count_matches(self, data: tuple[str, list[str]]) -> None:
        """split_stanzas produces the expected number of stanza blocks.

        Validates: Requirements 6.3
        """
        full_content, expected_stanzas = data

        result = split_stanzas(full_content)

        assert len(result) == len(expected_stanzas), (
            f"Stanza count mismatch: expected {len(expected_stanzas)}, got {len(result)}.\n  Content:\n{full_content}"
        )

    @given(stanza_data=_single_stanza())
    def test_parse_stanza_fields_with_continuations(self, stanza_data: tuple[str, dict[str, str]]) -> None:
        """parse_stanza_fields(preserve_continuations=True) correctly extracts all fields.

        All fields with their continuation lines are appended.

        Validates: Requirements 6.3
        """
        stanza_text, expected_fields = stanza_data

        result = parse_stanza_fields(stanza_text, preserve_continuations=True)

        assert result == expected_fields, (
            f"Field extraction mismatch.\n  Expected: {expected_fields}\n  Got:      {result}\n  Stanza:\n{stanza_text}"
        )

    @given(stanza_data=_single_stanza())
    def test_parse_stanza_fields_ordered_consistent_with_dict(self, stanza_data: tuple[str, dict[str, str]]) -> None:
        """parse_stanza_fields_ordered is consistent with parse_stanza_fields.

        Results are consistent with
        parse_stanza_fields(preserve_continuations=True) for field
        names and values.

        Validates: Requirements 6.3
        """
        stanza_text, _ = stanza_data

        dict_result = parse_stanza_fields(stanza_text, preserve_continuations=True)
        ordered_result = parse_stanza_fields_ordered(stanza_text)

        # Convert ordered result to dict for comparison
        ordered_dict = dict(ordered_result)

        assert ordered_dict == dict_result, (
            f"Ordered vs dict mismatch.\n"
            f"  Dict result:    {dict_result}\n"
            f"  Ordered as dict: {ordered_dict}\n"
            f"  Stanza:\n{stanza_text}"
        )

    @given(stanza_data=_single_stanza())
    def test_parse_stanza_fields_ordered_preserves_field_names(self, stanza_data: tuple[str, dict[str, str]]) -> None:
        """parse_stanza_fields_ordered preserves all field names from the stanza.

        Validates: Requirements 6.3
        """
        stanza_text, expected_fields = stanza_data

        ordered_result = parse_stanza_fields_ordered(stanza_text)
        ordered_names = [name for name, _ in ordered_result]

        # All expected field names should appear in the ordered result
        for name in expected_fields:
            assert name in ordered_names, (
                f"Field name '{name}' missing from ordered result.\n"
                f"  Expected names: {list(expected_fields.keys())}\n"
                f"  Ordered names:  {ordered_names}\n"
                f"  Stanza:\n{stanza_text}"
            )
