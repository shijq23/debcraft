"""Shared stanza-parsing utilities for Debian control-file formats.

Provides pure-function utilities for splitting stanza-based content into
blocks and extracting key-value fields. Used by packages_parser, sources_parser,
mirror/packages_parser, and scanner/dpkg_parser modules.

Stanza format:
    Field-Name: value
     continuation line (starts with space or tab)
    Another-Field: value

    Next-Stanza-Field: value
"""

from __future__ import annotations


def split_stanzas(content: str) -> list[str]:
    """Split content into stanza blocks separated by blank lines.

    A blank line is a line containing only whitespace. One or more consecutive
    blank lines delimit stanzas. Leading and trailing blank lines are ignored.

    Args:
        content: The full text content to split into stanzas.

    Returns:
        List of non-empty stanza text blocks.
    """
    if not content or content.isspace():
        return []

    stanzas: list[str] = []
    current_lines: list[str] = []

    for line in content.split("\n"):
        if line.strip() == "":
            # Blank line: end current stanza if we have content
            if current_lines:
                stanzas.append("\n".join(current_lines))
                current_lines = []
        else:
            current_lines.append(line)

    # Don't forget the last stanza if content doesn't end with blank line
    if current_lines:
        stanzas.append("\n".join(current_lines))

    return stanzas


def parse_stanza_fields(
    stanza: str,
    *,
    preserve_continuations: bool = False,
) -> dict[str, str]:
    """Parse key-value fields from a single stanza.

    Each field is on a line of the form ``Key: Value``. Lines starting with
    whitespace (space or tab) are continuation lines of the previous field.

    Args:
        stanza: A single stanza block (no blank lines within).
        preserve_continuations: If True, append continuation lines to the
            preceding field value (stripped of one leading whitespace char,
            joined with newline). If False, skip continuation lines entirely
            and only keep the first occurrence of each field name.

    Returns:
        Dictionary mapping field names to their values.
    """
    if preserve_continuations:
        return _parse_with_continuations(stanza)
    return _parse_without_continuations(stanza)


def parse_stanza_fields_ordered(stanza: str) -> list[tuple[str, str]]:
    """Parse key-value fields preserving order as a list of tuples.

    Handles continuation lines (leading space/tab) by appending to the
    previous field value with a newline separator. The leading whitespace
    character is stripped, preserving the rest of the content.

    This variant is used by the dpkg_parser which needs ordered fields
    for round-trip printing.

    Args:
        stanza: Text of a single stanza (no blank lines within).

    Returns:
        Ordered list of (field_name, field_value) tuples.
    """
    fields: list[tuple[str, str]] = []

    for line in stanza.split("\n"):
        if line.startswith(" ") or line.startswith("\t"):
            # Continuation line: append to preceding field value
            if fields:
                name, value = fields[-1]
                # Strip the leading whitespace character, preserve the rest
                continuation_content = line[1:]
                fields[-1] = (name, value + "\n" + continuation_content)
        elif ":" in line:
            # Field line: "Field-Name: value" or "Field-Name:"
            colon_pos = line.index(":")
            field_name = line[:colon_pos]
            # Value is everything after ": " (or just ":" if no space follows)
            field_value = line[colon_pos + 1 :]
            if field_value.startswith(" "):
                field_value = field_value[1:]
            fields.append((field_name, field_value))

    return fields


def _parse_with_continuations(stanza: str) -> dict[str, str]:
    """Parse stanza fields, appending continuation lines to field values.

    Continuation lines (starting with space/tab) have their leading whitespace
    character stripped and are appended to the previous field's value with a
    newline separator.

    Args:
        stanza: A single stanza block.

    Returns:
        Dictionary mapping field names to their values.
    """
    fields: dict[str, str] = {}
    current_key: str | None = None
    current_value_lines: list[str] = []

    for line in stanza.split("\n"):
        if line.startswith(" ") or line.startswith("\t"):
            # Continuation line
            if current_key is not None:
                current_value_lines.append(line[1:])
        else:
            # Save previous field
            if current_key is not None:
                fields[current_key] = "\n".join(current_value_lines)

            # Parse new field
            colon_idx = line.find(":")
            if colon_idx == -1:
                current_key = None
                current_value_lines = []
                continue
            current_key = line[:colon_idx]
            current_value_lines = [line[colon_idx + 1 :].strip()]

    # Save last field
    if current_key is not None:
        fields[current_key] = "\n".join(current_value_lines)

    return fields


def _parse_without_continuations(stanza: str) -> dict[str, str]:
    """Parse stanza fields, skipping continuation lines.

    Only the first occurrence of each field name is kept.
    Continuation lines (starting with space/tab) are ignored.

    Args:
        stanza: A single stanza block.

    Returns:
        Dictionary mapping field names to their values (first occurrence only).
    """
    fields: dict[str, str] = {}

    for line in stanza.split("\n"):
        # Multi-line continuation (starts with space or tab) — skip
        if line.startswith(" ") or line.startswith("\t"):
            continue
        # Parse Key: Value
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if key and key not in fields:
                fields[key] = value

    return fields
