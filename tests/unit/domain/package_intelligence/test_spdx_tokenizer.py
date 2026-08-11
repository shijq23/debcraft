"""Unit tests for SPDXTokenizer.

Tests cover edge cases: empty input, DocumentRef, LicenseRef, or-later suffix,
case-insensitive operators, invalid characters, and parentheses.
"""

from __future__ import annotations

import pytest

from debcraft.domain.package_intelligence.errors import SPDXTokenizeError
from debcraft.domain.package_intelligence.spdx_tokenizer import SPDXTokenizer
from debcraft.domain.package_intelligence.values import SPDXToken, SPDXTokenType


@pytest.mark.unit
class TestSPDXTokenizerEmptyInput:
    """Verify empty/whitespace input returns empty list."""

    def setup_method(self):
        self.tokenizer = SPDXTokenizer()

    def test_empty_string(self):
        """Empty string produces empty token list."""
        assert self.tokenizer.tokenize("") == []

    def test_whitespace_only(self):
        """Whitespace-only string produces empty token list."""
        assert self.tokenizer.tokenize("   ") == []

    def test_tabs_only(self):
        """Tab-only string produces empty token list."""
        assert self.tokenizer.tokenize("\t\t") == []

    def test_mixed_whitespace(self):
        """Mixed spaces and tabs produce empty token list."""
        assert self.tokenizer.tokenize("  \t  \t") == []


@pytest.mark.unit
class TestSPDXTokenizerLicenseIdentifiers:
    """Verify license identifier tokenization."""

    def setup_method(self):
        self.tokenizer = SPDXTokenizer()

    def test_simple_identifier(self):
        """Single identifier produces one LICENSE_ID token."""
        tokens = self.tokenizer.tokenize("MIT")
        assert tokens == [SPDXToken(SPDXTokenType.LICENSE_ID, "MIT", 0)]

    def test_identifier_with_version(self):
        """Identifier with version number produces one LICENSE_ID token."""
        tokens = self.tokenizer.tokenize("GPL-2.0-only")
        assert tokens == [SPDXToken(SPDXTokenType.LICENSE_ID, "GPL-2.0-only", 0)]

    def test_identifier_with_dots_and_hyphens(self):
        """Identifier with dots and hyphens is handled correctly."""
        tokens = self.tokenizer.tokenize("Apache-2.0")
        assert tokens == [SPDXToken(SPDXTokenType.LICENSE_ID, "Apache-2.0", 0)]

    def test_multiple_identifiers_with_and(self):
        """Multiple identifiers separated by AND operator."""
        tokens = self.tokenizer.tokenize("MIT AND Apache-2.0")
        assert tokens == [
            SPDXToken(SPDXTokenType.LICENSE_ID, "MIT", 0),
            SPDXToken(SPDXTokenType.AND, "AND", 4),
            SPDXToken(SPDXTokenType.LICENSE_ID, "Apache-2.0", 8),
        ]


@pytest.mark.unit
class TestSPDXTokenizerOperators:
    """Verify operator tokenization is case-insensitive."""

    def setup_method(self):
        self.tokenizer = SPDXTokenizer()

    def test_and_uppercase(self):
        """AND operator recognized in uppercase."""
        tokens = self.tokenizer.tokenize("MIT AND BSD-2-Clause")
        assert tokens[1] == SPDXToken(SPDXTokenType.AND, "AND", 4)

    def test_and_lowercase(self):
        """AND operator recognized in lowercase."""
        tokens = self.tokenizer.tokenize("MIT and BSD-2-Clause")
        assert tokens[1] == SPDXToken(SPDXTokenType.AND, "and", 4)

    def test_and_mixed_case(self):
        """AND operator recognized in mixed case."""
        tokens = self.tokenizer.tokenize("MIT And BSD-2-Clause")
        assert tokens[1] == SPDXToken(SPDXTokenType.AND, "And", 4)

    def test_or_uppercase(self):
        """OR operator recognized in uppercase."""
        tokens = self.tokenizer.tokenize("MIT OR Apache-2.0")
        assert tokens[1] == SPDXToken(SPDXTokenType.OR, "OR", 4)

    def test_or_lowercase(self):
        """OR operator recognized in lowercase."""
        tokens = self.tokenizer.tokenize("MIT or Apache-2.0")
        assert tokens[1] == SPDXToken(SPDXTokenType.OR, "or", 4)

    def test_with_uppercase(self):
        """WITH operator recognized in uppercase."""
        tokens = self.tokenizer.tokenize("GPL-2.0-only WITH Classpath-exception-2.0")
        assert tokens[1] == SPDXToken(SPDXTokenType.WITH, "WITH", 13)

    def test_with_lowercase(self):
        """WITH operator recognized in lowercase."""
        tokens = self.tokenizer.tokenize("GPL-2.0-only with Classpath-exception-2.0")
        assert tokens[1] == SPDXToken(SPDXTokenType.WITH, "with", 13)


@pytest.mark.unit
class TestSPDXTokenizerOrLater:
    """Verify or-later (+) suffix handling."""

    def setup_method(self):
        self.tokenizer = SPDXTokenizer()

    def test_or_later_suffix(self):
        """Identifier with + suffix produces OR_LATER token."""
        tokens = self.tokenizer.tokenize("GPL-2.0+")
        assert tokens == [SPDXToken(SPDXTokenType.OR_LATER, "GPL-2.0", 0)]

    def test_or_later_in_compound_expression(self):
        """Or-later works within a compound expression."""
        tokens = self.tokenizer.tokenize("GPL-2.0+ OR MIT")
        assert tokens == [
            SPDXToken(SPDXTokenType.OR_LATER, "GPL-2.0", 0),
            SPDXToken(SPDXTokenType.OR, "OR", 9),
            SPDXToken(SPDXTokenType.LICENSE_ID, "MIT", 12),
        ]


@pytest.mark.unit
class TestSPDXTokenizerParentheses:
    """Verify parentheses tokenization."""

    def setup_method(self):
        self.tokenizer = SPDXTokenizer()

    def test_parenthesized_expression(self):
        """Parentheses produce LPAREN and RPAREN tokens."""
        tokens = self.tokenizer.tokenize("(MIT OR Apache-2.0)")
        assert tokens[0] == SPDXToken(SPDXTokenType.LPAREN, "(", 0)
        assert tokens[-1] == SPDXToken(SPDXTokenType.RPAREN, ")", 18)

    def test_nested_parentheses(self):
        """Nested parentheses produce correct tokens."""
        tokens = self.tokenizer.tokenize("((MIT))")
        assert tokens == [
            SPDXToken(SPDXTokenType.LPAREN, "(", 0),
            SPDXToken(SPDXTokenType.LPAREN, "(", 1),
            SPDXToken(SPDXTokenType.LICENSE_ID, "MIT", 2),
            SPDXToken(SPDXTokenType.RPAREN, ")", 5),
            SPDXToken(SPDXTokenType.RPAREN, ")", 6),
        ]


@pytest.mark.unit
class TestSPDXTokenizerLicenseRef:
    """Verify LicenseRef handling."""

    def setup_method(self):
        self.tokenizer = SPDXTokenizer()

    def test_license_ref(self):
        """LicenseRef-xxx produces LICENSE_REF token."""
        tokens = self.tokenizer.tokenize("LicenseRef-custom-1")
        assert tokens == [SPDXToken(SPDXTokenType.LICENSE_REF, "LicenseRef-custom-1", 0)]

    def test_license_ref_in_expression(self):
        """LicenseRef within compound expression."""
        tokens = self.tokenizer.tokenize("MIT AND LicenseRef-my-license")
        assert tokens == [
            SPDXToken(SPDXTokenType.LICENSE_ID, "MIT", 0),
            SPDXToken(SPDXTokenType.AND, "AND", 4),
            SPDXToken(SPDXTokenType.LICENSE_REF, "LicenseRef-my-license", 8),
        ]


@pytest.mark.unit
class TestSPDXTokenizerDocumentRef:
    """Verify DocumentRef handling."""

    def setup_method(self):
        self.tokenizer = SPDXTokenizer()

    def test_document_ref(self):
        """DocumentRef-xxx:LicenseRef-yyy produces DOCUMENT_REF token."""
        tokens = self.tokenizer.tokenize("DocumentRef-ext:LicenseRef-Custom")
        assert tokens == [
            SPDXToken(
                SPDXTokenType.DOCUMENT_REF,
                "DocumentRef-ext:LicenseRef-Custom",
                0,
            )
        ]

    def test_document_ref_in_expression(self):
        """DocumentRef within compound expression."""
        tokens = self.tokenizer.tokenize("MIT OR DocumentRef-spdx-tool-1.2:LicenseRef-MIT-Style-2")
        assert tokens == [
            SPDXToken(SPDXTokenType.LICENSE_ID, "MIT", 0),
            SPDXToken(SPDXTokenType.OR, "OR", 4),
            SPDXToken(
                SPDXTokenType.DOCUMENT_REF,
                "DocumentRef-spdx-tool-1.2:LicenseRef-MIT-Style-2",
                7,
            ),
        ]


@pytest.mark.unit
class TestSPDXTokenizerErrors:
    """Verify error handling for invalid characters."""

    def setup_method(self):
        self.tokenizer = SPDXTokenizer()

    def test_invalid_character_at_start(self):
        """Invalid character at position 0 raises error with offset 0."""
        with pytest.raises(SPDXTokenizeError) as exc_info:
            self.tokenizer.tokenize("@MIT")
        assert exc_info.value.offset == 0

    def test_invalid_character_in_middle(self):
        """Invalid character in middle raises error with correct offset."""
        with pytest.raises(SPDXTokenizeError) as exc_info:
            self.tokenizer.tokenize("MIT ! Apache-2.0")
        assert exc_info.value.offset == 4

    def test_invalid_character_after_whitespace(self):
        """Invalid character after whitespace has correct offset."""
        with pytest.raises(SPDXTokenizeError) as exc_info:
            self.tokenizer.tokenize("   #")
        assert exc_info.value.offset == 3

    def test_semicolon_is_invalid(self):
        """Semicolon is not a valid SPDX character."""
        with pytest.raises(SPDXTokenizeError) as exc_info:
            self.tokenizer.tokenize("MIT;")
        assert exc_info.value.offset == 3

    def test_equals_is_invalid(self):
        """Equals sign is not a valid SPDX character."""
        with pytest.raises(SPDXTokenizeError) as exc_info:
            self.tokenizer.tokenize("MIT = Apache-2.0")
        assert exc_info.value.offset == 4


@pytest.mark.unit
class TestSPDXTokenizerOffsets:
    """Verify zero-based character offsets are correct."""

    def setup_method(self):
        self.tokenizer = SPDXTokenizer()

    def test_offsets_with_single_spaces(self):
        """Tokens have correct offsets with single-space separation."""
        tokens = self.tokenizer.tokenize("MIT AND Apache-2.0")
        assert tokens[0].offset == 0
        assert tokens[1].offset == 4
        assert tokens[2].offset == 8

    def test_offsets_with_multiple_spaces(self):
        """Tokens have correct offsets with multiple spaces."""
        tokens = self.tokenizer.tokenize("MIT   AND   Apache-2.0")
        assert tokens[0].offset == 0
        assert tokens[1].offset == 6
        assert tokens[2].offset == 12

    def test_offsets_with_leading_whitespace(self):
        """Tokens have correct offsets with leading whitespace."""
        tokens = self.tokenizer.tokenize("  MIT")
        assert tokens[0].offset == 2
