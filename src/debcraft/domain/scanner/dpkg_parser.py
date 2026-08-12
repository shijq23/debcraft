"""Pure-function parser for dpkg status files.

Parses dpkg status file content into structured IdentifiedPackage entries.
This module is a pure function: no I/O, no side effects, deterministic output.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from debcraft.domain.scanner.values import IdentifiedPackage


@dataclass(frozen=True)
class DpkgStanza:
    """A parsed dpkg status stanza preserving all fields and order.

    Attributes:
        fields: Ordered list of (field_name, field_value) tuples.
    """

    fields: list[tuple[str, str]] = field(default_factory=list)

    def get(self, name: str) -> str | None:
        """Get field value by name (case-insensitive).

        Args:
            name: The field name to look up.

        Returns:
            The field value if found, None otherwise.
        """
        lower = name.lower()
        for k, v in self.fields:
            if k.lower() == lower:
                return v
        return None


@dataclass(frozen=True)
class DpkgParseResult:
    """Result of parsing a dpkg status file.

    Attributes:
        packages: Successfully parsed IdentifiedPackage entries.
        diagnostics: Warning messages for skipped/malformed stanzas.
        stanzas: Raw parsed stanzas (for round-trip printing).
    """

    packages: list[IdentifiedPackage] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    stanzas: list[DpkgStanza] = field(default_factory=list)


def parse_dpkg_status(content: str) -> DpkgParseResult:
    """Parse dpkg status file content into identified packages.

    Pure function: no I/O, no side effects, deterministic output.

    Algorithm:
    1. Split content on blank lines into stanza texts
    2. For each stanza text, parse field:value lines with continuation handling
    3. Extract Package, Version, Architecture, Status fields
    4. Filter: include only packages with desired action "install" or "hold"
       AND current state "installed" or "config-files"
    5. Exclude packages with desired action "deinstall" or "purge"

    Args:
        content: Raw text content of a dpkg status file.

    Returns:
        DpkgParseResult with packages, diagnostics, and raw stanzas.
    """
    stanza_texts = _split_stanzas(content)

    packages: list[IdentifiedPackage] = []
    diagnostics: list[str] = []
    stanzas: list[DpkgStanza] = []

    for index, stanza_text in enumerate(stanza_texts):
        parsed_fields = _parse_stanza_fields(stanza_text)
        stanza = DpkgStanza(fields=parsed_fields)
        stanzas.append(stanza)

        pkg, diagnostic = _classify_package(stanza, index)
        if pkg is not None:
            packages.append(pkg)
        if diagnostic is not None:
            diagnostics.append(diagnostic)

    return DpkgParseResult(
        packages=packages,
        diagnostics=diagnostics,
        stanzas=stanzas,
    )


def _split_stanzas(content: str) -> list[str]:
    """Split content into stanza text blocks on blank lines.

    A blank line is a line containing only whitespace. One or more consecutive
    blank lines delimit stanzas. Leading and trailing blank lines are ignored.

    Args:
        content: The full dpkg status file text.

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

    # Don't forget the last stanza if file doesn't end with blank line
    if current_lines:
        stanzas.append("\n".join(current_lines))

    return stanzas


def _parse_stanza_fields(stanza_text: str) -> list[tuple[str, str]]:
    """Parse a single stanza into ordered (field_name, value) pairs.

    Handles continuation lines (leading space/tab) by appending
    to the previous field value with a newline separator. The leading
    whitespace character is stripped, preserving the rest of the content.

    Args:
        stanza_text: Text of a single stanza (no blank lines within).

    Returns:
        Ordered list of (field_name, field_value) tuples.
    """
    fields: list[tuple[str, str]] = []

    for line in stanza_text.split("\n"):
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


def _classify_package(stanza: DpkgStanza, stanza_index: int) -> tuple[IdentifiedPackage | None, str | None]:
    """Classify a stanza into an IdentifiedPackage or diagnostic.

    Applies the dpkg status classification rules:
    - "install ok installed" or "hold ok installed" -> status "installed"
    - "install ok config-files" or "hold ok config-files" -> status "config-files"
    - "deinstall" or "purge" as desired action -> silently excluded
    - "install"/"hold" with unrecognized current state -> excluded with diagnostic
    - Missing Package or Version -> excluded with diagnostic

    Args:
        stanza: The parsed stanza.
        stanza_index: 0-based index of the stanza in the file.

    Returns:
        (package, None) for included packages.
        (None, diagnostic) for excluded/skipped stanzas with diagnostic.
        (None, None) for silently excluded stanzas (deinstall/purge).
    """
    package_name = stanza.get("Package")
    version = stanza.get("Version")

    # Check for missing required fields (use 1-based index in diagnostics)
    missing_fields: list[str] = []
    if package_name is None:
        missing_fields.append("Package")
    if version is None:
        missing_fields.append("Version")

    if missing_fields:
        fields_str = ", ".join(missing_fields)
        return (
            None,
            f"Stanza {stanza_index + 1}: skipped due to missing field(s): {fields_str}",
        )

    # Parse the Status field
    status_field = stanza.get("Status")
    if status_field is None:
        return (
            None,
            f"Stanza {stanza_index + 1}: skipped due to missing field(s): Status",
        )

    status_parts = status_field.strip().split()
    if len(status_parts) < 3:
        return (
            None,
            f"Stanza {stanza_index + 1}: skipped due to malformed Status field: '{status_field}'",
        )

    desired_action = status_parts[0]
    current_state = status_parts[2]

    # Silently exclude deinstall/purge
    if desired_action in ("deinstall", "purge"):
        return (None, None)

    # Include only install/hold with installed/config-files
    if desired_action in ("install", "hold"):
        if current_state == "installed":
            pkg_status = "installed"
        elif current_state == "config-files":
            pkg_status = "config-files"
        else:
            return (
                None,
                f"Stanza {stanza_index + 1}: package '{package_name}' excluded "
                f"due to unrecognized installation state: '{current_state}'",
            )
    else:
        # Unknown desired action - exclude with diagnostic
        return (
            None,
            f"Stanza {stanza_index + 1}: package '{package_name}' excluded "
            f"due to unrecognized desired action: '{desired_action}'",
        )

    # Get architecture (empty string if missing)
    architecture = stanza.get("Architecture") or ""

    # At this point, package_name and version are guaranteed non-None
    # (the missing-fields check above returns early if either is None)
    assert package_name is not None  # noqa: S101
    assert version is not None  # noqa: S101

    return (
        IdentifiedPackage(
            name=package_name,
            version=version,
            architecture=architecture,
            status=pkg_status,
        ),
        None,
    )
