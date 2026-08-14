"""Serializer for dpkg status stanzas (round-trip support)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from debcraft.domain.scanner.dpkg_parser import DpkgStanza


def format_dpkg_status(stanzas: list[DpkgStanza]) -> str:
    r"""Format parsed stanzas back into dpkg status file text.

    Rules:
    - Each field emitted as "Field-Name: value\\n"
    - Multiline values use continuation lines (leading space)
    - Empty lines in multiline values become " .\\n"
    - Stanzas separated by exactly one blank line
    - Output ends with exactly one trailing newline
    - Empty stanza list returns empty string
    - Field order preserved as encountered during parsing

    Args:
        stanzas: List of DpkgStanza objects from the parser.

    Returns:
        Valid dpkg status file text representation.
    """
    if not stanzas:
        return ""

    formatted = []
    for stanza in stanzas:
        formatted.append(_format_stanza(stanza))

    return "\n\n".join(formatted) + "\n"


def _format_stanza(stanza: DpkgStanza) -> str:
    """Format a single stanza to text."""
    lines = []
    for field_name, field_value in stanza.fields:
        formatted_value = _format_field_value(field_value)
        lines.append(f"{field_name}: {formatted_value}")

    return "\n".join(lines)


def _format_field_value(value: str) -> str:
    """Format a field value, handling multiline continuation syntax.

    For single-line values, returns the value as-is.
    For multiline values, the first line is returned directly and
    subsequent lines are prefixed with a single space. Empty
    continuation lines become " ." (space + dot).
    """
    if "\n" not in value:
        return value

    parts = value.split("\n")
    result_parts = [parts[0]]

    for part in parts[1:]:
        if part in ("", "."):
            # Empty lines in multiline values become " ."
            result_parts.append(" .")
        else:
            # Continuation lines prefixed with single space
            result_parts.append(f" {part}")

    return "\n".join(result_parts)
