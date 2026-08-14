"""Maps Debian license identifiers to SPDX expressions.

Uses a cascade of algorithms in precedence order to resolve Debian license
names into canonical SPDX expressions with confidence metadata.
"""

from __future__ import annotations

import difflib
import re
from typing import TYPE_CHECKING

from debcraft.domain.package_intelligence.values import (
    LicenseMappingResult,
    MappingAlgorithm,
)

if TYPE_CHECKING:
    from debcraft.domain.package_intelligence.spdx_license_data import SPDXLicenseData

# Maximum input length before truncation
_MAX_INPUT_LENGTH = 512

# Maximum result field lengths
_MAX_SPDX_EXPRESSION_LENGTH = 1024
_MAX_RATIONALE_LENGTH = 512

# Fuzzy matching threshold (0.0 to 1.0)
_FUZZY_THRESHOLD = 0.80

# Fuzzy confidence clamping range
_FUZZY_CONFIDENCE_MIN = 90
_FUZZY_CONFIDENCE_MAX = 97

# Built-in Debian-to-SPDX alias table (case-insensitive keys)
_DEBIAN_ALIASES: dict[str, str] = {
    "gpl-2+": "GPL-2.0-or-later",
    "gpl-2": "GPL-2.0-only",
    "gpl-3+": "GPL-3.0-or-later",
    "gpl-3": "GPL-3.0-only",
    "lgpl-2+": "LGPL-2.0-or-later",
    "lgpl-2": "LGPL-2.0-only",
    "lgpl-2.1+": "LGPL-2.1-or-later",
    "lgpl-2.1": "LGPL-2.1-only",
    "lgpl-3+": "LGPL-3.0-or-later",
    "lgpl-3": "LGPL-3.0-only",
    "gfdl-1.2+": "GFDL-1.2-or-later",
    "gfdl-1.2": "GFDL-1.2-only",
    "gfdl-1.3+": "GFDL-1.3-or-later",
    "gfdl-1.3": "GFDL-1.3-only",
    "expat": "MIT",
    "bsd": "BSD-3-Clause",
    "public-domain": "CC0-1.0",
    "artistic": "Artistic-2.0",
}


def _normalize_for_spelling(s: str) -> str:
    """Normalize a string by lowercasing and removing hyphens, underscores, dots, spaces."""
    return s.lower().replace("-", "").replace("_", "").replace(".", "").replace(" ", "")


def _normalize_for_license_ref(s: str) -> str:
    """Normalize a string for use in a LicenseRef identifier.

    Lowercase, replace characters outside [a-z0-9] with hyphens,
    collapse consecutive hyphens.
    """
    normalized = s.lower()
    normalized = re.sub(r"[^a-z0-9]", "-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized)
    normalized = normalized.strip("-")
    return normalized


def _clamp(value: int, lo: int, hi: int) -> int:
    """Clamp an integer to [lo, hi]."""
    return max(lo, min(hi, value))


def _truncate_rationale(rationale: str) -> str:
    """Ensure rationale is non-empty and within length limit."""
    if not rationale:
        rationale = "No rationale available"
    if len(rationale) > _MAX_RATIONALE_LENGTH:
        rationale = rationale[: _MAX_RATIONALE_LENGTH - 3] + "..."
    return rationale


def _truncate_expression(expression: str) -> str:
    """Ensure SPDX expression is within length limit."""
    if len(expression) > _MAX_SPDX_EXPRESSION_LENGTH:
        expression = expression[:_MAX_SPDX_EXPRESSION_LENGTH]
    return expression


class LicenseMapper:
    """Maps Debian license identifiers to SPDX expressions."""

    def __init__(self, spdx_license_data: SPDXLicenseData) -> None:
        """Initialize the mapper with SPDX license reference data.

        Args:
            spdx_license_data: The SPDX license list data used for lookups.
        """
        self._spdx_data = spdx_license_data

        # Pre-compute normalized spelling index: normalized_form -> canonical license_id
        self._normalized_index: dict[str, str] = {}
        for entry in spdx_license_data.licenses:
            norm = _normalize_for_spelling(entry.license_id)
            if norm not in self._normalized_index:
                self._normalized_index[norm] = entry.license_id

    def map(self, debian_identifier: str, license_text: str | None = None) -> LicenseMappingResult:
        """Resolve a Debian license identifier to an SPDX expression.

        Algorithms applied in precedence order:
        ExactSPDX → DebianAlias → NormalizedSpelling → SPDXFullName →
        LicenseTextHash → FuzzySimilarity → Unmapped

        Args:
            debian_identifier: The Debian license name to resolve.
            license_text: Optional license text body for hash-based matching.

        Returns:
            A LicenseMappingResult with the mapped SPDX expression,
            confidence score, algorithm used, and rationale.
        """
        # Handle empty/whitespace input
        if not debian_identifier or not debian_identifier.strip():
            return self._make_result(
                spdx_expression="LicenseRef-debcraft-unknown",
                confidence=0,
                algorithm=MappingAlgorithm.UNMAPPED,
                rationale="Input identifier was empty or whitespace-only",
            )

        # Handle long input: truncate with rationale note
        truncated = False
        if len(debian_identifier) > _MAX_INPUT_LENGTH:
            debian_identifier = debian_identifier[:_MAX_INPUT_LENGTH]
            truncated = True

        identifier = debian_identifier.strip()

        # 1. ExactSPDX: case-insensitive match against SPDX identifiers
        result = self._try_exact_spdx(identifier)
        if result is not None:
            return self._apply_truncation_note(result, truncated)

        # 2. DebianAlias: match against built-in alias table
        result = self._try_debian_alias(identifier)
        if result is not None:
            return self._apply_truncation_note(result, truncated)

        # 3. NormalizedSpelling: normalize and compare
        result = self._try_normalized_spelling(identifier)
        if result is not None:
            return self._apply_truncation_note(result, truncated)

        # 4. SPDXFullName: case-insensitive match against SPDX full names
        result = self._try_spdx_full_name(identifier)
        if result is not None:
            return self._apply_truncation_note(result, truncated)

        # 5. LicenseTextHash: SHA-256 hash of license text (stub for now)
        result = self._try_license_text_hash(license_text)  # pylint: disable=assignment-from-none
        if result is not None:
            return self._apply_truncation_note(result, truncated)

        # 6. FuzzySimilarity: difflib SequenceMatcher against SPDX identifiers
        result = self._try_fuzzy_similarity(identifier)
        if result is not None:
            return self._apply_truncation_note(result, truncated)

        # 7. Unmapped: fallback with LicenseRef-debcraft-<normalized>
        return self._apply_truncation_note(self._unmapped_fallback(identifier), truncated)

    def _try_exact_spdx(self, identifier: str) -> LicenseMappingResult | None:
        """Try case-insensitive exact match against SPDX license identifiers."""
        entry = self._spdx_data.get_by_id(identifier)
        if entry is not None:
            return self._make_result(
                spdx_expression=entry.license_id,
                confidence=100,
                algorithm=MappingAlgorithm.EXACT_SPDX,
                rationale=(f"Exact case-insensitive match against SPDX identifier '{entry.license_id}'"),
            )
        return None

    def _try_debian_alias(self, identifier: str) -> LicenseMappingResult | None:
        """Try matching against the built-in Debian alias table."""
        spdx_id = _DEBIAN_ALIASES.get(identifier.lower())
        if spdx_id is not None:
            return self._make_result(
                spdx_expression=spdx_id,
                confidence=100,
                algorithm=MappingAlgorithm.DEBIAN_ALIAS,
                rationale=(f"Debian alias '{identifier}' maps to SPDX expression '{spdx_id}'"),
            )
        return None

    def _try_normalized_spelling(self, identifier: str) -> LicenseMappingResult | None:
        """Try normalized spelling comparison (remove hyphens, underscores, dots, spaces)."""
        norm_input = _normalize_for_spelling(identifier)
        canonical_id = self._normalized_index.get(norm_input)
        if canonical_id is not None:
            return self._make_result(
                spdx_expression=canonical_id,
                confidence=99,
                algorithm=MappingAlgorithm.NORMALIZED_SPELLING,
                rationale=(
                    f"Normalized spelling match: '{identifier}' resolved to "
                    f"'{canonical_id}' after removing hyphens, underscores, dots, "
                    f"and spaces"
                ),
            )
        return None

    def _try_spdx_full_name(self, identifier: str) -> LicenseMappingResult | None:
        """Try case-insensitive match against SPDX license full names."""
        entry = self._spdx_data.get_by_name(identifier)
        if entry is not None:
            return self._make_result(
                spdx_expression=entry.license_id,
                confidence=98,
                algorithm=MappingAlgorithm.SPDX_FULL_NAME,
                rationale=(
                    f"Full name match: '{identifier}' matches SPDX license name "
                    f"'{entry.name}' (identifier: '{entry.license_id}')"
                ),
            )
        return None

    def _try_license_text_hash(self, _license_text: str | None) -> LicenseMappingResult | None:
        """Try SHA-256 hash matching against known license texts.

        Note: Full text hash table not yet available. This is a stub that
        always returns None.
        """
        # TODO: Implement when known license text hash table is available
        return None

    def _try_fuzzy_similarity(self, identifier: str) -> LicenseMappingResult | None:
        """Try fuzzy matching using difflib.SequenceMatcher against SPDX identifiers."""
        best_ratio = 0.0
        best_id = ""

        for spdx_id in self._spdx_data.identifiers:
            ratio = difflib.SequenceMatcher(None, identifier.lower(), spdx_id.lower()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_id = spdx_id

        if best_ratio >= _FUZZY_THRESHOLD:
            confidence = _clamp(int(best_ratio * 100), _FUZZY_CONFIDENCE_MIN, _FUZZY_CONFIDENCE_MAX)
            return self._make_result(
                spdx_expression=best_id,
                confidence=confidence,
                algorithm=MappingAlgorithm.FUZZY_SIMILARITY,
                rationale=(
                    f"Fuzzy match: '{identifier}' is similar to SPDX identifier "
                    f"'{best_id}' with ratio {best_ratio:.2f} "
                    f"(confidence clamped to {confidence})"
                ),
            )
        return None

    def _unmapped_fallback(self, identifier: str) -> LicenseMappingResult:
        """Generate an unmapped LicenseRef result."""
        normalized = _normalize_for_license_ref(identifier)
        if not normalized:
            normalized = "unknown"
        license_ref = f"LicenseRef-debcraft-{normalized}"
        return self._make_result(
            spdx_expression=license_ref,
            confidence=0,
            algorithm=MappingAlgorithm.UNMAPPED,
            rationale=(f"No mapping found for '{identifier}'; assigned custom LicenseRef"),
        )

    def _apply_truncation_note(self, result: LicenseMappingResult, truncated: bool) -> LicenseMappingResult:
        """Add truncation note to rationale if input was truncated."""
        if not truncated:
            return result
        truncation_note = f" [Note: input was truncated to {_MAX_INPUT_LENGTH} characters]"
        # Make room for the truncation note within the rationale limit
        available = _MAX_RATIONALE_LENGTH - len(truncation_note)
        base_rationale = result.rationale
        if len(base_rationale) > available:
            base_rationale = base_rationale[: available - 3] + "..."
        new_rationale = base_rationale + truncation_note
        return LicenseMappingResult(
            spdx_expression=result.spdx_expression,
            confidence=result.confidence,
            algorithm=result.algorithm,
            rationale=new_rationale,
        )

    def _make_result(
        self,
        spdx_expression: str,
        confidence: int,
        algorithm: MappingAlgorithm,
        rationale: str,
    ) -> LicenseMappingResult:
        """Create a LicenseMappingResult ensuring all invariants hold."""
        # Enforce spdx_expression <= 1024 chars
        spdx_expression = _truncate_expression(spdx_expression)

        # Enforce confidence in [0, 100]
        confidence = _clamp(confidence, 0, 100)

        # Enforce non-empty rationale <= 512 chars
        rationale = _truncate_rationale(rationale)

        return LicenseMappingResult(
            spdx_expression=spdx_expression,
            confidence=confidence,
            algorithm=algorithm,
            rationale=rationale,
        )
