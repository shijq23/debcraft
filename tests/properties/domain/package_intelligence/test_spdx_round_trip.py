"""Property-based tests for SPDX expression processing.

# Feature: package-intelligence, Property 3: SPDX Expression Round-Trip
# Feature: package-intelligence, Property 4: SPDX Tokenizer Error Offset Accuracy
# Feature: package-intelligence, Property 5: SPDX Parser Rejects Malformed Input

**Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.6, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 6.1, 6.2, 6.3, 6.4, 6.5**
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.domain.package_intelligence.errors import SPDXParseError, SPDXTokenizeError
from debcraft.domain.package_intelligence.spdx_parser import SPDXExpressionParser
from debcraft.domain.package_intelligence.spdx_printer import SPDXPrinter
from debcraft.domain.package_intelligence.spdx_tokenizer import SPDXTokenizer
from debcraft.domain.package_intelligence.values import (
    AndNode,
    OrNode,
    SimpleNode,
    SPDXNode,
    SPDXToken,
    SPDXTokenType,
    WithNode,
)

# ===========================================================================
# Strategies for generating valid SPDX AST nodes (Property 3)
# ===========================================================================

# Reserved keywords that must not be used as identifiers (case-insensitive)
_KEYWORDS = frozenset({"AND", "OR", "WITH"})

# Valid SPDX identifier: starts with letter, followed by letters/digits/hyphens/dots
_IDENT_START = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_IDENT_CHARS = _IDENT_START + "0123456789-."


def _spdx_identifier() -> st.SearchStrategy[str]:
    r"""Generate a valid SPDX license identifier string.

    Matches [A-Za-z][A-Za-z0-9\\-.]* and avoids collisions with
    AND/OR/WITH keywords (case-insensitive).
    """
    return st.builds(
        lambda start, rest: start + rest,
        st.text(alphabet=_IDENT_START, min_size=1, max_size=1),
        st.text(alphabet=_IDENT_CHARS, min_size=0, max_size=15),
    ).filter(lambda s: s.upper() not in _KEYWORDS)


def _simple_node() -> st.SearchStrategy[SimpleNode]:
    """Generate a SimpleNode (with or without or_later flag)."""
    return st.builds(
        SimpleNode,
        identifier=_spdx_identifier(),
        or_later=st.booleans(),
    )


def _with_node() -> st.SearchStrategy[WithNode]:
    """Generate a WithNode.

    WITH can only apply to a SimpleNode (leaf), since the grammar defines
    with_expr → atom ("WITH" exception)?, and the printer does not
    parenthesize the license child of WithNode.
    """
    return st.builds(
        WithNode,
        license=_simple_node(),
        exception=_spdx_identifier(),
    )


def _leaf_node() -> st.SearchStrategy[SPDXNode]:
    """Generate leaf-level nodes: SimpleNode or WithNode.

    These are the atoms that can appear as operands in AND/OR expressions.
    """
    return st.one_of(_simple_node(), _with_node())


@st.composite
def _and_chain(draw: st.DrawFn, child: st.SearchStrategy[SPDXNode]) -> SPDXNode:
    """Generate a left-associative AND chain.

    The SPDX parser is left-associative for AND, so we must generate
    trees in the same shape: AndNode(AndNode(A, B), C) not AndNode(A, AndNode(B, C)).
    """
    operands = draw(st.lists(child, min_size=2, max_size=5))
    result = operands[0]
    for operand in operands[1:]:
        result = AndNode(left=result, right=operand)
    return result


@st.composite
def _or_chain(draw: st.DrawFn, child: st.SearchStrategy[SPDXNode]) -> SPDXNode:
    """Generate a left-associative OR chain.

    The SPDX parser is left-associative for OR, so we must generate
    trees in the same shape: OrNode(OrNode(A, B), C) not OrNode(A, OrNode(B, C)).
    """
    operands = draw(st.lists(child, min_size=2, max_size=5))
    result = operands[0]
    for operand in operands[1:]:
        result = OrNode(left=result, right=operand)
    return result


def spdx_node_strategy() -> st.SearchStrategy[SPDXNode]:
    """Generate valid SPDXNode ASTs that round-trip correctly.

    Generates trees respecting the SPDX grammar constraints:
    - WITH applies only to SimpleNode leaves (atoms)
    - AND/OR are left-associative chains
    - OR children of AND nodes get parenthesized by the printer,
      so arbitrary nesting is valid as long as associativity is correct

    The strategy builds layered expressions matching the grammar precedence:
    expr → or_chain of and_exprs
    and_expr → and_chain of with_exprs/atoms
    with_expr → SimpleNode WITH exception | SimpleNode
    """
    # Level 0: leaves (SimpleNode, WithNode)
    leaf = _leaf_node()

    # Level 1: AND expressions (left-associative chains of leaves)
    # Can be a single leaf or a chain of leaves joined by AND
    and_expr: st.SearchStrategy[SPDXNode] = st.one_of(
        leaf,
        _and_chain(leaf),
    )

    # Level 2: OR expressions (left-associative chains of AND expressions)
    # Can be a single and_expr or a chain of and_exprs joined by OR
    return st.one_of(
        and_expr,
        _or_chain(and_expr),
    )


# ===========================================================================
# Property 3: SPDX Expression Round-Trip
# ===========================================================================


@pytest.mark.unit
@pytest.mark.package
class TestProperty3SPDXExpressionRoundTrip:
    """Property 3: SPDX Expression Round-Trip.

    For any valid SPDXNode AST, printing it with SPDXPrinter, tokenizing
    the result with SPDXTokenizer, and parsing the tokens with
    SPDXExpressionParser SHALL produce an SPDXNode that is structurally
    identical to the original.

    **Validates: Requirements 4.1, 4.2, 4.4, 4.6, 5.1, 5.2, 5.3, 5.4, 5.6, 6.1, 6.2, 6.3, 6.4, 6.5**
    """

    @settings(max_examples=100)
    @given(node=spdx_node_strategy())
    def test_spdx_round_trip(self, node: SPDXNode) -> None:
        """Print → tokenize → parse produces structurally identical AST."""
        printer = SPDXPrinter()
        tokenizer = SPDXTokenizer()
        parser = SPDXExpressionParser()

        printed = printer.print(node)
        tokens = tokenizer.tokenize(printed)
        parsed = parser.parse(tokens)

        assert parsed == node, (
            f"Round-trip failed!\nOriginal node: {node!r}\nPrinted: {printed!r}\nParsed back: {parsed!r}"
        )


# ===========================================================================
# Strategies for generating malformed token sequences (Property 5)
# ===========================================================================

# Valid license identifiers for use as operands
_LICENSE_IDS = [
    "MIT",
    "Apache-2.0",
    "GPL-2.0-only",
    "BSD-3-Clause",
    "ISC",
    "MPL-2.0",
]

# Operator token types
_OPERATOR_TYPES = [SPDXTokenType.AND, SPDXTokenType.OR]


def _license_token(offset: int = 0) -> SPDXToken:
    """Create a LICENSE_ID token with given offset."""
    return SPDXToken(type=SPDXTokenType.LICENSE_ID, value="MIT", offset=offset)


def _operator_token(op_type: SPDXTokenType, offset: int = 0) -> SPDXToken:
    """Create an operator token."""
    value = "AND" if op_type == SPDXTokenType.AND else "OR"
    return SPDXToken(type=op_type, value=value, offset=offset)


@st.composite
def _unbalanced_open_parens(draw: st.DrawFn) -> list[SPDXToken]:
    """Generate tokens with more LPARENs than RPARENs.

    Creates expressions like: ( MIT AND ( Apache-2.0
    where there are unclosed parentheses.
    """
    # Number of extra open parens (at least 1 unbalanced)
    extra_opens = draw(st.integers(min_value=1, max_value=4))
    # Number of balanced pairs
    balanced_pairs = draw(st.integers(min_value=0, max_value=2))

    tokens: list[SPDXToken] = []
    offset = 0

    # Add opening parens (balanced + extra)
    total_opens = balanced_pairs + extra_opens
    for _ in range(total_opens):
        tokens.append(SPDXToken(type=SPDXTokenType.LPAREN, value="(", offset=offset))
        offset += 2

    # Add a license identifier
    tokens.append(SPDXToken(type=SPDXTokenType.LICENSE_ID, value="MIT", offset=offset))
    offset += 4

    # Add closing parens (only the balanced ones)
    for _ in range(balanced_pairs):
        tokens.append(SPDXToken(type=SPDXTokenType.RPAREN, value=")", offset=offset))
        offset += 2

    return tokens


@st.composite
def _unbalanced_close_parens(draw: st.DrawFn) -> list[SPDXToken]:
    """Generate tokens with RPAREN before any LPAREN or more RPARENs than LPARENs.

    Creates expressions like: ) MIT or MIT ) )
    """
    strategy_choice = draw(st.integers(min_value=0, max_value=1))

    tokens: list[SPDXToken] = []
    offset = 0

    if strategy_choice == 0:
        # RPAREN at the start (before any LPAREN)
        tokens.append(SPDXToken(type=SPDXTokenType.RPAREN, value=")", offset=offset))
        offset += 2
        tokens.append(SPDXToken(type=SPDXTokenType.LICENSE_ID, value="MIT", offset=offset))
    else:
        # More RPARENs than LPARENs
        num_opens = draw(st.integers(min_value=0, max_value=2))
        num_closes = draw(st.integers(min_value=num_opens + 1, max_value=num_opens + 3))

        for _ in range(num_opens):
            tokens.append(SPDXToken(type=SPDXTokenType.LPAREN, value="(", offset=offset))
            offset += 2

        tokens.append(SPDXToken(type=SPDXTokenType.LICENSE_ID, value="MIT", offset=offset))
        offset += 4

        for _ in range(num_closes):
            tokens.append(SPDXToken(type=SPDXTokenType.RPAREN, value=")", offset=offset))
            offset += 2

    return tokens


@st.composite
def _consecutive_operators(draw: st.DrawFn) -> list[SPDXToken]:
    """Generate tokens with two AND/OR operators next to each other.

    Creates expressions like: MIT AND OR Apache-2.0
    """
    license_id = draw(st.sampled_from(_LICENSE_IDS))
    op1_type = draw(st.sampled_from(_OPERATOR_TYPES))
    op2_type = draw(st.sampled_from(_OPERATOR_TYPES))

    offset = 0
    tokens: list[SPDXToken] = []

    # Leading operand
    tokens.append(SPDXToken(type=SPDXTokenType.LICENSE_ID, value=license_id, offset=offset))
    offset += len(license_id) + 1

    # First operator
    op1_value = "AND" if op1_type == SPDXTokenType.AND else "OR"
    tokens.append(SPDXToken(type=op1_type, value=op1_value, offset=offset))
    offset += len(op1_value) + 1

    # Second operator (consecutive — no operand between them)
    op2_value = "AND" if op2_type == SPDXTokenType.AND else "OR"
    tokens.append(SPDXToken(type=op2_type, value=op2_value, offset=offset))
    offset += len(op2_value) + 1

    # Trailing operand
    tokens.append(SPDXToken(type=SPDXTokenType.LICENSE_ID, value="BSD-3-Clause", offset=offset))

    return tokens


@st.composite
def _missing_operand_at_end(draw: st.DrawFn) -> list[SPDXToken]:
    """Generate tokens ending with an operator (missing right operand).

    Creates expressions like: MIT AND or Apache-2.0 OR
    """
    license_id = draw(st.sampled_from(_LICENSE_IDS))
    op_type = draw(st.sampled_from(_OPERATOR_TYPES))

    offset = 0
    tokens: list[SPDXToken] = []

    # Operand
    tokens.append(SPDXToken(type=SPDXTokenType.LICENSE_ID, value=license_id, offset=offset))
    offset += len(license_id) + 1

    # Trailing operator with no right operand
    op_value = "AND" if op_type == SPDXTokenType.AND else "OR"
    tokens.append(SPDXToken(type=op_type, value=op_value, offset=offset))

    return tokens


@st.composite
def _excessive_nesting_depth(draw: st.DrawFn) -> list[SPDXToken]:
    """Generate tokens with nesting depth exceeding 32.

    Creates expressions like: (((((...(MIT)...)))))  with 33+ levels.
    """
    depth = draw(st.integers(min_value=33, max_value=40))

    tokens: list[SPDXToken] = []
    offset = 0

    # Open parens
    for _ in range(depth):
        tokens.append(SPDXToken(type=SPDXTokenType.LPAREN, value="(", offset=offset))
        offset += 2

    # License in the middle
    tokens.append(SPDXToken(type=SPDXTokenType.LICENSE_ID, value="MIT", offset=offset))
    offset += 4

    # Close parens
    for _ in range(depth):
        tokens.append(SPDXToken(type=SPDXTokenType.RPAREN, value=")", offset=offset))
        offset += 2

    return tokens


def malformed_token_sequences() -> st.SearchStrategy[list[SPDXToken]]:
    """Combine all malformed token strategies into one."""
    return st.one_of(
        _unbalanced_open_parens(),
        _unbalanced_close_parens(),
        _consecutive_operators(),
        _missing_operand_at_end(),
        _excessive_nesting_depth(),
    )


# ===========================================================================
# Property 5: SPDX Parser Rejects Malformed Input
# ===========================================================================


@pytest.mark.unit
@pytest.mark.package
class TestProperty5SPDXParserRejectsMalformedInput:
    """Property 5: SPDX Parser Rejects Malformed Input.

    For any malformed token sequence (unbalanced parentheses, consecutive
    operators, missing operands, or nesting depth exceeding 32), the
    SPDX_Expression_Parser SHALL return an SPDXParseError containing the
    error category and a token position within the valid index range of
    the input sequence.

    **Validates: Requirements 5.5**
    """

    @settings(max_examples=200)
    @given(tokens=malformed_token_sequences())
    def test_spdx_parser_rejects_malformed_input(self, tokens: list[SPDXToken]) -> None:
        """Parser raises SPDXParseError with valid token position for malformed input."""
        parser = SPDXExpressionParser()
        with pytest.raises(SPDXParseError) as exc_info:
            parser.parse(tokens)
        assert 0 <= exc_info.value.token_position <= len(tokens)


# ===========================================================================
# Strategies for generating strings with invalid SPDX characters (Property 4)
# ===========================================================================

# Valid SPDX expression characters: letters, digits, hyphen, dot, parens, plus,
# space, and tab.
_VALID_SPDX_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-.() +\t")

# Characters that are definitely invalid in SPDX expressions
_INVALID_SPDX_CHARS = "@#!;={}[]<>\\|&^%$~`\"',?:/_*"

# Characters safe for a prefix: the tokenizer can always consume these without
# error. We exclude '+' because it is only valid after an identifier, and
# standalone '+' triggers SPDXTokenizeError. We exclude ')' because an unmatched
# close paren is also rejected before reaching the invalid character.
_SAFE_PREFIX_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-. \t"


@st.composite
def strings_with_invalid_spdx_chars(draw: st.DrawFn) -> str:
    """Generate strings guaranteed to contain at least one invalid SPDX character.

    Strategy: generate a safe prefix of characters that the tokenizer can
    always consume (letters, digits, hyphen, dot, space, tab), then append
    an invalid character. This ensures the tokenizer will encounter the
    invalid char at the error offset.

    We exclude parens and '+' from the prefix because:
    - ')' without a matching '(' can cause an error before the invalid char
    - '+' without a preceding identifier triggers SPDXTokenizeError pointing
      to '+' itself, which is technically a valid SPDX char
    """
    # Generate a prefix of safe SPDX characters (may be empty)
    valid_prefix = draw(
        st.text(
            alphabet=st.sampled_from(sorted(_SAFE_PREFIX_CHARS)),
            min_size=0,
            max_size=20,
        )
    )

    # Pick at least one invalid character
    invalid_char = draw(st.sampled_from(list(_INVALID_SPDX_CHARS)))

    # Generate a suffix (mix of valid and invalid chars — after the guaranteed
    # invalid char, anything can appear)
    suffix = draw(
        st.text(
            alphabet=st.sampled_from(sorted(_SAFE_PREFIX_CHARS) + list(_INVALID_SPDX_CHARS)),
            min_size=0,
            max_size=20,
        )
    )

    return valid_prefix + invalid_char + suffix


# ===========================================================================
# Property 4: SPDX Tokenizer Error Offset Accuracy
# ===========================================================================


@pytest.mark.unit
@pytest.mark.package
class TestProperty4SPDXTokenizerErrorOffsetAccuracy:
    """Property 4: SPDX Tokenizer Error Offset Accuracy.

    For any string containing at least one character outside the valid SPDX
    character set, the SPDX_Tokenizer SHALL produce an error identifying a
    zero-based character offset that points to a character which is indeed
    invalid in an SPDX expression.

    **Validates: Requirements 4.3**
    """

    @settings(max_examples=100)
    @given(expression=strings_with_invalid_spdx_chars())
    def test_spdx_tokenizer_error_offset_accuracy(self, expression: str) -> None:
        """Error offset points to a character that is invalid in SPDX."""
        tokenizer = SPDXTokenizer()

        with pytest.raises(SPDXTokenizeError) as exc_info:
            tokenizer.tokenize(expression)

        offset = exc_info.value.offset

        # Offset must be within bounds of the expression
        assert 0 <= offset < len(expression), (
            f"Error offset {offset} is out of bounds for expression of length {len(expression)}: {expression!r}"
        )

        # The character at the error offset must be invalid in SPDX
        char = expression[offset]
        assert char not in _VALID_SPDX_CHARS, (
            f"Error offset {offset} points to '{char}' which is a valid SPDX character. Expression: {expression!r}"
        )
