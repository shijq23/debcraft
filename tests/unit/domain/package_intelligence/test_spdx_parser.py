"""Unit tests for SPDXExpressionParser.

Tests cover edge cases: empty tokens, depth limit, unbalanced parentheses,
missing operands, consecutive operators, precedence correctness, and all
node types (SimpleNode, WithNode, AndNode, OrNode).
"""

from __future__ import annotations

import pytest

from debcraft.domain.package_intelligence.errors import SPDXParseError
from debcraft.domain.package_intelligence.spdx_parser import SPDXExpressionParser
from debcraft.domain.package_intelligence.values import (
    AndNode,
    OrNode,
    SimpleNode,
    SPDXToken,
    SPDXTokenType,
    WithNode,
)


def _tok(type_: SPDXTokenType, value: str, offset: int = 0) -> SPDXToken:
    """Helper to create tokens with a default offset."""
    return SPDXToken(type=type_, value=value, offset=offset)


@pytest.mark.unit
class TestSPDXParserEmptyInput:
    """Verify parser raises on empty token list."""

    def setup_method(self):
        self.parser = SPDXExpressionParser()

    def test_empty_token_list(self):
        """Empty token list raises SPDXParseError at position 0."""
        with pytest.raises(SPDXParseError) as exc_info:
            self.parser.parse([])
        assert exc_info.value.token_position == 0
        assert "empty" in str(exc_info.value).lower()


@pytest.mark.unit
class TestSPDXParserSimpleExpressions:
    """Verify parsing of simple license identifiers."""

    def setup_method(self):
        self.parser = SPDXExpressionParser()

    def test_single_license_id(self):
        """Single LICENSE_ID produces SimpleNode."""
        tokens = [_tok(SPDXTokenType.LICENSE_ID, "MIT")]
        result = self.parser.parse(tokens)
        assert result == SimpleNode(identifier="MIT", or_later=False)

    def test_or_later(self):
        """OR_LATER token produces SimpleNode with or_later=True."""
        tokens = [_tok(SPDXTokenType.OR_LATER, "GPL-2.0")]
        result = self.parser.parse(tokens)
        assert result == SimpleNode(identifier="GPL-2.0", or_later=True)

    def test_license_ref(self):
        """LICENSE_REF token produces SimpleNode."""
        tokens = [_tok(SPDXTokenType.LICENSE_REF, "LicenseRef-custom")]
        result = self.parser.parse(tokens)
        assert result == SimpleNode(identifier="LicenseRef-custom", or_later=False)

    def test_document_ref(self):
        """DOCUMENT_REF token produces SimpleNode."""
        tokens = [_tok(SPDXTokenType.DOCUMENT_REF, "DocumentRef-ext:LicenseRef-MIT")]
        result = self.parser.parse(tokens)
        assert result == SimpleNode(identifier="DocumentRef-ext:LicenseRef-MIT", or_later=False)


@pytest.mark.unit
class TestSPDXParserBinaryOperators:
    """Verify AND and OR operators produce correct AST."""

    def setup_method(self):
        self.parser = SPDXExpressionParser()

    def test_simple_and(self):
        """MIT AND Apache-2.0 produces AndNode."""
        tokens = [
            _tok(SPDXTokenType.LICENSE_ID, "MIT", 0),
            _tok(SPDXTokenType.AND, "AND", 4),
            _tok(SPDXTokenType.LICENSE_ID, "Apache-2.0", 8),
        ]
        result = self.parser.parse(tokens)
        assert result == AndNode(
            left=SimpleNode("MIT"),
            right=SimpleNode("Apache-2.0"),
        )

    def test_simple_or(self):
        """MIT OR Apache-2.0 produces OrNode."""
        tokens = [
            _tok(SPDXTokenType.LICENSE_ID, "MIT", 0),
            _tok(SPDXTokenType.OR, "OR", 4),
            _tok(SPDXTokenType.LICENSE_ID, "Apache-2.0", 7),
        ]
        result = self.parser.parse(tokens)
        assert result == OrNode(
            left=SimpleNode("MIT"),
            right=SimpleNode("Apache-2.0"),
        )

    def test_with_operator(self):
        """GPL-2.0 WITH Classpath produces WithNode."""
        tokens = [
            _tok(SPDXTokenType.LICENSE_ID, "GPL-2.0-only", 0),
            _tok(SPDXTokenType.WITH, "WITH", 13),
            _tok(SPDXTokenType.LICENSE_ID, "Classpath-exception-2.0", 18),
        ]
        result = self.parser.parse(tokens)
        assert result == WithNode(
            license=SimpleNode("GPL-2.0-only"),
            exception="Classpath-exception-2.0",
        )


@pytest.mark.unit
class TestSPDXParserPrecedence:
    """Verify operator precedence: WITH > AND > OR."""

    def setup_method(self):
        self.parser = SPDXExpressionParser()

    def test_and_binds_tighter_than_or(self):
        """A OR B AND C → OrNode(A, AndNode(B, C))."""
        tokens = [
            _tok(SPDXTokenType.LICENSE_ID, "A", 0),
            _tok(SPDXTokenType.OR, "OR", 2),
            _tok(SPDXTokenType.LICENSE_ID, "B", 5),
            _tok(SPDXTokenType.AND, "AND", 7),
            _tok(SPDXTokenType.LICENSE_ID, "C", 11),
        ]
        result = self.parser.parse(tokens)
        assert result == OrNode(
            left=SimpleNode("A"),
            right=AndNode(left=SimpleNode("B"), right=SimpleNode("C")),
        )

    def test_with_binds_tighter_than_and(self):
        """A AND B WITH exc → AndNode(A, WithNode(B, exc))."""
        tokens = [
            _tok(SPDXTokenType.LICENSE_ID, "A", 0),
            _tok(SPDXTokenType.AND, "AND", 2),
            _tok(SPDXTokenType.LICENSE_ID, "B", 6),
            _tok(SPDXTokenType.WITH, "WITH", 8),
            _tok(SPDXTokenType.LICENSE_ID, "exc", 13),
        ]
        result = self.parser.parse(tokens)
        assert result == AndNode(
            left=SimpleNode("A"),
            right=WithNode(license=SimpleNode("B"), exception="exc"),
        )

    def test_full_precedence_chain(self):
        """A OR B AND C WITH exc → OrNode(A, AndNode(B, WithNode(C, exc)))."""
        tokens = [
            _tok(SPDXTokenType.LICENSE_ID, "A", 0),
            _tok(SPDXTokenType.OR, "OR", 2),
            _tok(SPDXTokenType.LICENSE_ID, "B", 5),
            _tok(SPDXTokenType.AND, "AND", 7),
            _tok(SPDXTokenType.LICENSE_ID, "C", 11),
            _tok(SPDXTokenType.WITH, "WITH", 13),
            _tok(SPDXTokenType.LICENSE_ID, "exc", 18),
        ]
        result = self.parser.parse(tokens)
        assert result == OrNode(
            left=SimpleNode("A"),
            right=AndNode(
                left=SimpleNode("B"),
                right=WithNode(license=SimpleNode("C"), exception="exc"),
            ),
        )

    def test_left_associativity_and(self):
        """A AND B AND C → AndNode(AndNode(A, B), C)."""
        tokens = [
            _tok(SPDXTokenType.LICENSE_ID, "A", 0),
            _tok(SPDXTokenType.AND, "AND", 2),
            _tok(SPDXTokenType.LICENSE_ID, "B", 6),
            _tok(SPDXTokenType.AND, "AND", 8),
            _tok(SPDXTokenType.LICENSE_ID, "C", 12),
        ]
        result = self.parser.parse(tokens)
        assert result == AndNode(
            left=AndNode(left=SimpleNode("A"), right=SimpleNode("B")),
            right=SimpleNode("C"),
        )

    def test_left_associativity_or(self):
        """A OR B OR C → OrNode(OrNode(A, B), C)."""
        tokens = [
            _tok(SPDXTokenType.LICENSE_ID, "A", 0),
            _tok(SPDXTokenType.OR, "OR", 2),
            _tok(SPDXTokenType.LICENSE_ID, "B", 5),
            _tok(SPDXTokenType.OR, "OR", 7),
            _tok(SPDXTokenType.LICENSE_ID, "C", 10),
        ]
        result = self.parser.parse(tokens)
        assert result == OrNode(
            left=OrNode(left=SimpleNode("A"), right=SimpleNode("B")),
            right=SimpleNode("C"),
        )


@pytest.mark.unit
class TestSPDXParserParentheses:
    """Verify parenthesized grouping."""

    def setup_method(self):
        self.parser = SPDXExpressionParser()

    def test_parenthesized_or_in_and(self):
        """(A OR B) AND C → AndNode(OrNode(A, B), C)."""
        tokens = [
            _tok(SPDXTokenType.LPAREN, "(", 0),
            _tok(SPDXTokenType.LICENSE_ID, "A", 1),
            _tok(SPDXTokenType.OR, "OR", 3),
            _tok(SPDXTokenType.LICENSE_ID, "B", 6),
            _tok(SPDXTokenType.RPAREN, ")", 7),
            _tok(SPDXTokenType.AND, "AND", 9),
            _tok(SPDXTokenType.LICENSE_ID, "C", 13),
        ]
        result = self.parser.parse(tokens)
        assert result == AndNode(
            left=OrNode(left=SimpleNode("A"), right=SimpleNode("B")),
            right=SimpleNode("C"),
        )

    def test_nested_parentheses(self):
        """((A)) parses correctly as SimpleNode(A)."""
        tokens = [
            _tok(SPDXTokenType.LPAREN, "(", 0),
            _tok(SPDXTokenType.LPAREN, "(", 1),
            _tok(SPDXTokenType.LICENSE_ID, "A", 2),
            _tok(SPDXTokenType.RPAREN, ")", 3),
            _tok(SPDXTokenType.RPAREN, ")", 4),
        ]
        result = self.parser.parse(tokens)
        assert result == SimpleNode("A")


@pytest.mark.unit
class TestSPDXParserErrors:
    """Verify error handling for malformed expressions."""

    def setup_method(self):
        self.parser = SPDXExpressionParser()

    def test_unbalanced_open_paren(self):
        """Missing closing paren raises SPDXParseError."""
        tokens = [
            _tok(SPDXTokenType.LPAREN, "(", 0),
            _tok(SPDXTokenType.LICENSE_ID, "MIT", 1),
        ]
        with pytest.raises(SPDXParseError):
            self.parser.parse(tokens)

    def test_unbalanced_close_paren(self):
        """Unexpected closing paren raises SPDXParseError."""
        tokens = [
            _tok(SPDXTokenType.LICENSE_ID, "MIT", 0),
            _tok(SPDXTokenType.RPAREN, ")", 3),
        ]
        with pytest.raises(SPDXParseError):
            self.parser.parse(tokens)

    def test_missing_right_operand_and(self):
        """MIT AND (no more tokens) raises SPDXParseError."""
        tokens = [
            _tok(SPDXTokenType.LICENSE_ID, "MIT", 0),
            _tok(SPDXTokenType.AND, "AND", 4),
        ]
        with pytest.raises(SPDXParseError):
            self.parser.parse(tokens)

    def test_missing_right_operand_or(self):
        """MIT OR (no more tokens) raises SPDXParseError."""
        tokens = [
            _tok(SPDXTokenType.LICENSE_ID, "MIT", 0),
            _tok(SPDXTokenType.OR, "OR", 4),
        ]
        with pytest.raises(SPDXParseError):
            self.parser.parse(tokens)

    def test_missing_left_operand(self):
        """AND MIT raises SPDXParseError (operator in atom position)."""
        tokens = [
            _tok(SPDXTokenType.AND, "AND", 0),
            _tok(SPDXTokenType.LICENSE_ID, "MIT", 4),
        ]
        with pytest.raises(SPDXParseError):
            self.parser.parse(tokens)

    def test_consecutive_operators(self):
        """MIT AND AND Apache raises SPDXParseError."""
        tokens = [
            _tok(SPDXTokenType.LICENSE_ID, "MIT", 0),
            _tok(SPDXTokenType.AND, "AND", 4),
            _tok(SPDXTokenType.AND, "AND", 8),
            _tok(SPDXTokenType.LICENSE_ID, "Apache-2.0", 12),
        ]
        with pytest.raises(SPDXParseError):
            self.parser.parse(tokens)

    def test_missing_exception_after_with(self):
        """MIT WITH (no more tokens) raises SPDXParseError."""
        tokens = [
            _tok(SPDXTokenType.LICENSE_ID, "MIT", 0),
            _tok(SPDXTokenType.WITH, "WITH", 4),
        ]
        with pytest.raises(SPDXParseError):
            self.parser.parse(tokens)

    def test_operator_as_with_exception(self):
        """MIT WITH AND raises SPDXParseError (operator not valid as exception)."""
        tokens = [
            _tok(SPDXTokenType.LICENSE_ID, "MIT", 0),
            _tok(SPDXTokenType.WITH, "WITH", 4),
            _tok(SPDXTokenType.AND, "AND", 9),
        ]
        with pytest.raises(SPDXParseError):
            self.parser.parse(tokens)

    def test_excessive_nesting_depth(self):
        """Nesting deeper than MAX_NESTING_DEPTH raises SPDXParseError."""
        # Build 33 levels of nesting
        depth = 33
        tokens: list[SPDXToken] = []
        for i in range(depth):
            tokens.append(_tok(SPDXTokenType.LPAREN, "(", i))
        tokens.append(_tok(SPDXTokenType.LICENSE_ID, "MIT", depth))
        for i in range(depth):
            tokens.append(_tok(SPDXTokenType.RPAREN, ")", depth + 1 + i))

        with pytest.raises(SPDXParseError) as exc_info:
            self.parser.parse(tokens)
        assert "depth" in str(exc_info.value).lower()

    def test_trailing_tokens(self):
        """MIT MIT (unexpected token after expression) raises SPDXParseError."""
        tokens = [
            _tok(SPDXTokenType.LICENSE_ID, "MIT", 0),
            _tok(SPDXTokenType.LICENSE_ID, "MIT", 4),
        ]
        with pytest.raises(SPDXParseError):
            self.parser.parse(tokens)

    def test_empty_parentheses(self):
        """() raises SPDXParseError."""
        tokens = [
            _tok(SPDXTokenType.LPAREN, "(", 0),
            _tok(SPDXTokenType.RPAREN, ")", 1),
        ]
        with pytest.raises(SPDXParseError):
            self.parser.parse(tokens)
