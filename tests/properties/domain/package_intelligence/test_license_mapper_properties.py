"""Property-based tests for the license mapper.

# Feature: package-intelligence, Property 6: License Mapper Result Invariant
# Feature: package-intelligence, Property 7: License Mapper Exact Match Confidence
# Feature: package-intelligence, Property 8: License Mapper Normalized Spelling
# Feature: package-intelligence, Property 9: License Mapper Unmapped Fallback
# Feature: package-intelligence, Property 10: License Mapper Fuzzy Confidence Clamping

**Validates: Requirements 7.1, 7.3, 7.5, 7.7, 7.8, 7.9**
"""

from __future__ import annotations

import string

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.domain.package_intelligence.license_mapper import LicenseMapper
from debcraft.domain.package_intelligence.spdx_license_data import load_spdx_license_data
from debcraft.domain.package_intelligence.values import (
    LicenseMappingResult,
    MappingAlgorithm,
)

# ===========================================================================
# Shared fixtures: load SPDX data once for all tests
# ===========================================================================

_spdx_data = load_spdx_license_data()
_mapper = LicenseMapper(_spdx_data)

# All canonical SPDX identifiers
_SPDX_IDENTIFIERS: list[str] = _spdx_data.identifiers


# ===========================================================================
# Strategies
# ===========================================================================

# Arbitrary text including edge cases (empty, whitespace, special chars, unicode)
_arbitrary_text = st.text(
    alphabet=st.characters(codec="utf-8"),
    min_size=0,
    max_size=200,
)


@st.composite
def spdx_identifier_with_case_variation(draw: st.DrawFn) -> str:
    """Draw a known SPDX identifier and randomly change its case.

    This should still match via ExactSPDX (case-insensitive).
    """
    identifier = draw(st.sampled_from(_SPDX_IDENTIFIERS))
    # Apply random case change per character
    chars = []
    for ch in identifier:
        if draw(st.booleans()):
            chars.append(ch.upper())
        else:
            chars.append(ch.lower())
    return "".join(chars)


@st.composite
def normalized_spelling_variation(draw: st.DrawFn) -> str:
    """Generate SPDX identifiers with hyphens/dots/underscores/spaces removed and case changed.

    These should match via NormalizedSpelling, provided they don't also
    match ExactSPDX or DebianAlias.
    """
    identifier = draw(st.sampled_from(_SPDX_IDENTIFIERS))

    # Remove some or all separator characters (hyphens, dots, underscores, spaces)
    # We must remove at least one character that exists in the identifier to ensure
    # it doesn't match as ExactSPDX
    separators = set("-._")
    has_separator = any(ch in separators for ch in identifier)

    if not has_separator:
        # If no separators exist, we can't produce a normalized variation that
        # differs from exact. Use assume to skip.
        from hypothesis import assume

        assume(False)

    # Remove all separator characters
    result = ""
    for ch in identifier:
        if ch in separators:
            continue  # remove separators
        result += ch

    # Apply random case changes
    chars = []
    for ch in result:
        if draw(st.booleans()):
            chars.append(ch.upper())
        else:
            chars.append(ch.lower())
    return "".join(chars)


# Characters that won't accidentally look like SPDX identifiers
_GIBBERISH_CHARS = '!@#$%^&*(){}[]|\\:";<>?,/~`±§¶™©®¡¿'


@st.composite
def unmapped_gibberish(draw: st.DrawFn) -> str:
    """Generate strings that clearly won't match any SPDX identifier.

    Uses special characters, random unicode, and patterns that cannot
    match exact, alias, normalized, full name, or fuzzy thresholds.
    """
    # Generate a mix of special characters and random text
    prefix = draw(st.text(alphabet=_GIBBERISH_CHARS, min_size=3, max_size=10))
    # Add some random digits to further break any fuzzy matches
    digits = draw(st.text(alphabet=string.digits, min_size=2, max_size=5))
    suffix = draw(st.text(alphabet=_GIBBERISH_CHARS, min_size=2, max_size=8))
    return prefix + digits + suffix


# ===========================================================================
# Property 6: License Mapper Result Invariant
# ===========================================================================


@pytest.mark.unit
@pytest.mark.package
class TestProperty6LicenseMapperResultInvariant:
    """Property 6: License Mapper Result Invariant.

    For any input string (including empty, whitespace, or arbitrary text),
    the License_Mapper SHALL return a LicenseMappingResult where:
    spdx_expression is at most 1024 characters, confidence is an integer
    in [0, 100], algorithm is a valid MappingAlgorithm variant, and
    rationale is a non-empty string of at most 512 characters.

    **Validates: Requirements 7.8**
    """

    @given(input_text=_arbitrary_text)
    def test_result_invariant_for_arbitrary_input(self, input_text: str) -> None:
        """All result fields satisfy their invariants for any input."""
        result = _mapper.map(input_text)

        # Result must be a LicenseMappingResult
        assert isinstance(result, LicenseMappingResult)

        # spdx_expression <= 1024 characters
        assert len(result.spdx_expression) <= 1024, (
            f"spdx_expression too long: {len(result.spdx_expression)} chars for input {input_text!r}"
        )

        # confidence is an integer in [0, 100]
        assert isinstance(result.confidence, int)
        assert 0 <= result.confidence <= 100, f"confidence out of range: {result.confidence} for input {input_text!r}"

        # algorithm is a valid MappingAlgorithm variant
        assert isinstance(result.algorithm, MappingAlgorithm), (
            f"algorithm not a MappingAlgorithm: {result.algorithm!r} for input {input_text!r}"
        )

        # rationale is non-empty and at most 512 characters
        assert len(result.rationale) > 0, f"rationale is empty for input {input_text!r}"
        assert len(result.rationale) <= 512, (
            f"rationale too long: {len(result.rationale)} chars for input {input_text!r}"
        )


# ===========================================================================
# Property 7: License Mapper Exact Match Confidence
# ===========================================================================


@pytest.mark.unit
@pytest.mark.package
class TestProperty7LicenseMapperExactMatchConfidence:
    """Property 7: License Mapper Exact Match Confidence.

    For any identifier drawn from the SPDX license list (case-insensitive),
    the License_Mapper SHALL return confidence 100 and algorithm ExactSPDX.

    **Validates: Requirements 7.1, 7.9**
    """

    @given(identifier=spdx_identifier_with_case_variation())
    def test_exact_match_returns_confidence_100(self, identifier: str) -> None:
        """SPDX identifiers (any case) map with confidence 100 and ExactSPDX."""
        result = _mapper.map(identifier)

        assert result.confidence == 100, (
            f"Expected confidence 100 for SPDX identifier {identifier!r}, "
            f"got {result.confidence} (algorithm: {result.algorithm.value})"
        )
        assert result.algorithm == MappingAlgorithm.EXACT_SPDX, (
            f"Expected ExactSPDX algorithm for {identifier!r}, got {result.algorithm.value}"
        )


# ===========================================================================
# Property 8: License Mapper Normalized Spelling
# ===========================================================================


@pytest.mark.unit
@pytest.mark.package
class TestProperty8LicenseMapperNormalizedSpelling:
    """Property 8: License Mapper Normalized Spelling.

    For any known SPDX identifier with arbitrary case changes and removed
    hyphens/underscores/dots/spaces, the License_Mapper SHALL return
    confidence 99 and algorithm NormalizedSpelling (provided the spelling
    variation does not also produce an exact or alias match).

    **Validates: Requirements 7.3**
    """

    @given(variation=normalized_spelling_variation())
    def test_normalized_spelling_returns_confidence_99(self, variation: str) -> None:
        """Spelling variations map with confidence 99 and NormalizedSpelling."""
        from hypothesis import assume

        # Skip if this variation also happens to match ExactSPDX or DebianAlias
        # (which take precedence in the cascade)
        exact_result = _spdx_data.get_by_id(variation)
        if exact_result is not None:
            assume(False)

        # Check if it matches a Debian alias
        from debcraft.domain.package_intelligence.license_mapper import _DEBIAN_ALIASES

        if variation.lower() in _DEBIAN_ALIASES:
            assume(False)

        result = _mapper.map(variation)

        assert result.confidence == 99, (
            f"Expected confidence 99 for normalized spelling {variation!r}, "
            f"got {result.confidence} (algorithm: {result.algorithm.value})"
        )
        assert result.algorithm == MappingAlgorithm.NORMALIZED_SPELLING, (
            f"Expected NormalizedSpelling for {variation!r}, got {result.algorithm.value}"
        )


# ===========================================================================
# Property 9: License Mapper Unmapped Fallback
# ===========================================================================


@pytest.mark.unit
@pytest.mark.package
class TestProperty9LicenseMapperUnmappedFallback:
    """Property 9: License Mapper Unmapped Fallback.

    For any input string that does not match any SPDX identifier, alias,
    normalized spelling, full name, text hash, or fuzzy threshold, the
    License_Mapper SHALL return a result with spdx_expression matching
    the pattern `LicenseRef-debcraft-*`, confidence 0, and algorithm Unmapped.

    **Validates: Requirements 7.7**
    """

    @given(gibberish=unmapped_gibberish())
    def test_unmapped_returns_license_ref_pattern(self, gibberish: str) -> None:
        """Unmappable input produces LicenseRef-debcraft-* with confidence 0."""
        result = _mapper.map(gibberish)

        assert result.spdx_expression.startswith("LicenseRef-debcraft-"), (
            f"Expected 'LicenseRef-debcraft-*' pattern for {gibberish!r}, got {result.spdx_expression!r}"
        )
        assert result.confidence == 0, (
            f"Expected confidence 0 for unmapped input {gibberish!r}, got {result.confidence}"
        )
        assert result.algorithm == MappingAlgorithm.UNMAPPED, (
            f"Expected Unmapped algorithm for {gibberish!r}, got {result.algorithm.value}"
        )


# ===========================================================================
# Strategies for Property 10: inputs likely to trigger fuzzy matching
# ===========================================================================


@st.composite
def fuzzy_match_candidate(draw: st.DrawFn) -> str:
    """Generate inputs that are close to SPDX identifiers but not exact matches.

    Applies small perturbations to SPDX identifiers (character swaps,
    insertions, deletions) to produce strings that should trigger
    FuzzySimilarity matching (ratio >= 0.80) but not ExactSPDX,
    DebianAlias, or NormalizedSpelling.
    """
    identifier = draw(st.sampled_from(_SPDX_IDENTIFIERS))

    # Choose a perturbation that breaks exact/normalized but keeps fuzzy close
    perturbation = draw(st.sampled_from(["swap", "insert", "delete", "replace"]))

    chars = list(identifier)
    if len(chars) < 3:
        # Very short identifiers — just append a character
        chars.append(draw(st.sampled_from(list("xyz123"))))
    elif perturbation == "swap" and len(chars) >= 4:
        # Swap two adjacent characters (not separators to avoid normalized match)
        idx = draw(st.integers(min_value=1, max_value=len(chars) - 2))
        chars[idx], chars[idx + 1] = chars[idx + 1], chars[idx]
    elif perturbation == "insert":
        # Insert a random character at a random position
        idx = draw(st.integers(min_value=1, max_value=len(chars) - 1))
        ch = draw(st.sampled_from(list("abcxyz123")))
        chars.insert(idx, ch)
    elif perturbation == "delete" and len(chars) >= 5:
        # Delete a character that is NOT a separator (to avoid normalized match)
        non_sep_indices = [i for i, c in enumerate(chars) if c not in "-._"]
        if non_sep_indices:
            idx = draw(st.sampled_from(non_sep_indices))
            del chars[idx]
        else:
            chars.append("x")
    else:
        # Replace a character with a different one
        idx = draw(st.integers(min_value=0, max_value=len(chars) - 1))
        replacements = [c for c in "abcxyz123" if c != chars[idx].lower()]
        if replacements:
            chars[idx] = draw(st.sampled_from(replacements))

    return "".join(chars)


# ===========================================================================
# Property 10: License Mapper Fuzzy Confidence Clamping
# ===========================================================================


@pytest.mark.unit
@pytest.mark.package
class TestProperty10LicenseMapperFuzzyConfidenceClamping:
    """Property 10: License Mapper Fuzzy Confidence Clamping.

    For any input that produces a FuzzySimilarity match, the returned
    confidence SHALL be in the range [90, 97].

    **Validates: Requirements 7.5**
    """

    @given(input_text=fuzzy_match_candidate())
    def test_fuzzy_confidence_is_clamped(self, input_text: str) -> None:
        """If FuzzySimilarity is used, confidence is in [90, 97]."""
        from hypothesis import assume

        result = _mapper.map(input_text)

        # Only check the clamping invariant when FuzzySimilarity was selected
        assume(result.algorithm == MappingAlgorithm.FUZZY_SIMILARITY)

        assert 90 <= result.confidence <= 97, (
            f"Fuzzy confidence out of [90, 97] range: {result.confidence} for input {input_text!r}"
        )
