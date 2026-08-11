"""SPDX license expression recursive-descent parser.

Parses a sequence of SPDX tokens into an abstract syntax tree with
correct operator precedence: WITH > AND > OR.

Grammar:
    expr      → and_expr ("OR" and_expr)*
    and_expr  → with_expr ("AND" with_expr)*
    with_expr → atom ("WITH" exception_id)?
    atom      → "(" expr ")" | license_id | license_ref | document_ref | or_later
"""

from __future__ import annotations

from debcraft.domain.package_intelligence.errors import SPDXParseError
from debcraft.domain.package_intelligence.values import (
    AndNode,
    OrNode,
    SimpleNode,
    SPDXNode,
    SPDXToken,
    SPDXTokenType,
    WithNode,
)


class SPDXExpressionParser:
    """Recursive-descent parser for SPDX expressions."""

    MAX_NESTING_DEPTH: int = 32

    def __init__(self) -> None:
        """Initialize parser state."""
        self._tokens: list[SPDXToken] = []
        self._pos: int = 0
        self._depth: int = 0

    def parse(self, tokens: list[SPDXToken]) -> SPDXNode:
        """Parse token sequence into AST with correct precedence.

        Precedence: WITH > AND > OR

        Raises:
            SPDXParseError: If the expression is malformed.
        """
        if not tokens:
            raise SPDXParseError("Expression is empty", token_position=0)

        self._tokens = tokens
        self._pos = 0
        self._depth = 0

        result = self._parse_or_expr()

        if self._pos < len(self._tokens):
            raise SPDXParseError(
                "Unexpected token after complete expression",
                token_position=self._pos,
            )

        return result

    def _peek(self) -> SPDXToken | None:
        """Return the current token without consuming it."""
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        return None

    def _advance(self) -> SPDXToken:
        """Consume and return the current token."""
        token = self._tokens[self._pos]
        self._pos += 1
        return token

    def _parse_or_expr(self) -> SPDXNode:
        """Parse: and_expr ("OR" and_expr)*."""
        left = self._parse_and_expr()

        while self._peek() is not None and self._peek().type == SPDXTokenType.OR:  # type: ignore[union-attr]
            self._advance()  # consume OR
            if self._pos >= len(self._tokens):
                raise SPDXParseError(
                    "Missing operand after OR",
                    token_position=self._pos - 1,
                )
            right = self._parse_and_expr()
            left = OrNode(left=left, right=right)

        return left

    def _parse_and_expr(self) -> SPDXNode:
        """Parse: with_expr ("AND" with_expr)*."""
        left = self._parse_with_expr()

        while self._peek() is not None and self._peek().type == SPDXTokenType.AND:  # type: ignore[union-attr]
            self._advance()  # consume AND
            if self._pos >= len(self._tokens):
                raise SPDXParseError(
                    "Missing operand after AND",
                    token_position=self._pos - 1,
                )
            right = self._parse_with_expr()
            left = AndNode(left=left, right=right)

        return left

    def _parse_with_expr(self) -> SPDXNode:
        """Parse: atom ("WITH" exception_id)?"""
        node = self._parse_atom()

        if self._peek() is not None and self._peek().type == SPDXTokenType.WITH:  # type: ignore[union-attr]
            self._advance()  # consume WITH
            if self._pos >= len(self._tokens):
                raise SPDXParseError(
                    "Missing exception identifier after WITH",
                    token_position=self._pos - 1,
                )
            exception_token = self._peek()
            if exception_token is None or exception_token.type not in (
                SPDXTokenType.LICENSE_ID,
                SPDXTokenType.LICENSE_REF,
            ):
                raise SPDXParseError(
                    "Expected exception identifier after WITH",
                    token_position=self._pos,
                )
            self._advance()
            node = WithNode(license=node, exception=exception_token.value)

        return node

    def _parse_atom(self) -> SPDXNode:
        """Parse: "(" expr ")" | license_id | license_ref | document_ref | or_later."""
        token = self._peek()

        if token is None:
            raise SPDXParseError(
                "Unexpected end of expression",
                token_position=self._pos,
            )

        # Parenthesized expression
        if token.type == SPDXTokenType.LPAREN:
            self._advance()  # consume (
            self._depth += 1
            if self._depth > self.MAX_NESTING_DEPTH:
                raise SPDXParseError(
                    "Excessive nesting depth",
                    token_position=self._pos - 1,
                )
            node = self._parse_or_expr()
            self._depth -= 1

            closing = self._peek()
            if closing is None or closing.type != SPDXTokenType.RPAREN:
                raise SPDXParseError(
                    "Unbalanced parentheses: missing closing ')'",
                    token_position=self._pos - 1 if closing is None else self._pos,
                )
            self._advance()  # consume )
            return node

        # License identifier
        if token.type == SPDXTokenType.LICENSE_ID:
            self._advance()
            return SimpleNode(identifier=token.value, or_later=False)

        # Or-later (GPL-2.0+)
        if token.type == SPDXTokenType.OR_LATER:
            self._advance()
            return SimpleNode(identifier=token.value, or_later=True)

        # LicenseRef
        if token.type == SPDXTokenType.LICENSE_REF:
            self._advance()
            return SimpleNode(identifier=token.value, or_later=False)

        # DocumentRef
        if token.type == SPDXTokenType.DOCUMENT_REF:
            self._advance()
            return SimpleNode(identifier=token.value, or_later=False)

        # Unbalanced closing paren
        if token.type == SPDXTokenType.RPAREN:
            raise SPDXParseError(
                "Unbalanced parentheses: unexpected closing ')'",
                token_position=self._pos,
            )

        # Operator in atom position (missing operand)
        if token.type in (
            SPDXTokenType.AND,
            SPDXTokenType.OR,
            SPDXTokenType.WITH,
        ):
            raise SPDXParseError(
                f"Missing operand before {token.value}",
                token_position=self._pos,
            )

        raise SPDXParseError(
            f"Unexpected token: {token.value}",
            token_position=self._pos,
        )
