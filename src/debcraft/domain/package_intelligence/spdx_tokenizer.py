"""SPDX license expression tokenizer.

Converts raw SPDX expression strings into a sequence of typed tokens
with zero-based character offsets for downstream parsing.
"""

from __future__ import annotations

import re

from debcraft.domain.package_intelligence.errors import SPDXTokenizeError
from debcraft.domain.package_intelligence.values import SPDXToken, SPDXTokenType

# Valid SPDX identifier characters: letters, digits, hyphen, dot
_IDENT_CHAR_RE = re.compile(r"[A-Za-z0-9\-.]")

# Pattern for DocumentRef-xxx:LicenseRef-yyy
_DOCUMENT_REF_RE = re.compile(
    r"DocumentRef-[A-Za-z0-9\-.]+:LicenseRef-[A-Za-z0-9\-.]+",
    re.IGNORECASE,
)

# Pattern for LicenseRef-xxx
_LICENSE_REF_RE = re.compile(r"LicenseRef-[A-Za-z0-9\-.]+", re.IGNORECASE)


class SPDXTokenizer:
    """Tokenizes SPDX license expression strings."""

    def tokenize(self, expression: str) -> list[SPDXToken]:
        """Convert expression string to typed token sequence.

        Produces tokens with zero-based character offsets. Whitespace is
        consumed between tokens without producing whitespace tokens.

        Raises:
            SPDXTokenizeError: If invalid characters are encountered.
        """
        tokens: list[SPDXToken] = []
        pos = 0
        length = len(expression)

        while pos < length:
            char = expression[pos]

            # Skip whitespace
            if char == " " or char == "\t":
                pos += 1
                continue

            # Parentheses
            if char == "(":
                tokens.append(SPDXToken(SPDXTokenType.LPAREN, "(", pos))
                pos += 1
                continue

            if char == ")":
                tokens.append(SPDXToken(SPDXTokenType.RPAREN, ")", pos))
                pos += 1
                continue

            # Try DocumentRef-xxx:LicenseRef-yyy first (must precede LicenseRef)
            doc_ref_match = _DOCUMENT_REF_RE.match(expression, pos)
            if doc_ref_match:
                value = doc_ref_match.group(0)
                tokens.append(SPDXToken(SPDXTokenType.DOCUMENT_REF, value, pos))
                pos += len(value)
                continue

            # Try LicenseRef-xxx
            license_ref_match = _LICENSE_REF_RE.match(expression, pos)
            if license_ref_match:
                value = license_ref_match.group(0)
                tokens.append(SPDXToken(SPDXTokenType.LICENSE_REF, value, pos))
                pos += len(value)
                continue

            # Identifier or operator: must start with a letter or digit
            if _IDENT_CHAR_RE.match(char):
                start = pos
                while pos < length and _IDENT_CHAR_RE.match(expression[pos]):
                    pos += 1

                value = expression[start:pos]
                upper_value = value.upper()

                # Check for operators (case-insensitive)
                if upper_value == "AND":
                    tokens.append(SPDXToken(SPDXTokenType.AND, value, start))
                elif upper_value == "OR":
                    tokens.append(SPDXToken(SPDXTokenType.OR, value, start))
                elif upper_value == "WITH":
                    tokens.append(SPDXToken(SPDXTokenType.WITH, value, start))
                else:
                    # Check for or-later suffix (+)
                    if pos < length and expression[pos] == "+":
                        tokens.append(SPDXToken(SPDXTokenType.OR_LATER, value, start))
                        pos += 1
                    else:
                        tokens.append(SPDXToken(SPDXTokenType.LICENSE_ID, value, start))
                continue

            # Invalid character
            raise SPDXTokenizeError(
                f"Invalid character '{char}'",
                offset=pos,
            )

        return tokens
