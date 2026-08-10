"""Parser for Debian Release file metadata.

Extracts repository-level identity information (suite, codename, origin,
label, architectures, components, date) from Release file content.
"""

from __future__ import annotations

import logging

from debcraft.domain.indexer.errors import ReleaseParseError
from debcraft.domain.indexer.values import RepositoryIdentity

logger = logging.getLogger(__name__)


class ReleaseMetadataParser:
    """Extracts repository identity from Release file content.

    The Release file format is a single stanza with key-value pairs
    separated by colons. Multi-value fields like Architectures and
    Components are space-separated lists.
    """

    def parse(self, content: str) -> RepositoryIdentity:
        """Extract repository metadata from Release file content.

        Parses Suite, Codename, Origin, Label, Architectures, Components,
        and Date fields. Falls back to Codename as suite when Suite is absent.

        Args:
            content: Raw text content of a Release file.

        Returns:
            RepositoryIdentity value object with extracted fields.

        Raises:
            ReleaseParseError: When neither Suite nor Codename is present.
        """
        fields = self._parse_fields(content)

        suite = fields.get("suite")
        codename = fields.get("codename")

        if not suite and not codename:
            raise ReleaseParseError("Release file contains neither Suite nor Codename field")

        # Suite fallback: use Codename when Suite is absent
        effective_suite = suite if suite else codename

        origin = fields.get("origin")
        label = fields.get("label")
        date = fields.get("date")

        # Architectures and Components are space-separated lists
        architectures_raw = fields.get("architectures", "")
        architectures = architectures_raw.split() if architectures_raw else []

        components_raw = fields.get("components", "")
        components = components_raw.split() if components_raw else []

        logger.debug(
            "Parsed Release: suite=%s, codename=%s, origin=%s, architectures=%s, components=%s",
            effective_suite,
            codename,
            origin,
            architectures,
            components,
        )

        return RepositoryIdentity(
            suite=effective_suite,  # type: ignore[arg-type]
            codename=codename,
            origin=origin,
            label=label,
            architectures=architectures,
            components=components,
            date=date,
        )

    def _parse_fields(self, content: str) -> dict[str, str]:
        """Parse key-value fields from Release file content.

        Handles the standard Debian control file format where fields
        are `Key: Value` pairs, with continuation lines starting with
        whitespace.

        Args:
            content: Raw Release file text.

        Returns:
            Dictionary mapping lowercased field names to their values.
        """
        fields: dict[str, str] = {}
        current_key: str | None = None
        current_value_lines: list[str] = []

        for line in content.splitlines():
            if line.startswith(" ") or line.startswith("\t"):
                # Continuation line
                if current_key is not None:
                    current_value_lines.append(line)
            elif ":" in line:
                # Save previous field
                if current_key is not None:
                    fields[current_key] = "\n".join(current_value_lines)

                key, _, value = line.partition(":")
                current_key = key.strip().lower()
                current_value_lines = [value.strip()]
            else:
                # Empty line or non-field line: save current and reset
                if current_key is not None:
                    fields[current_key] = "\n".join(current_value_lines)
                    current_key = None
                    current_value_lines = []

        # Save last field
        if current_key is not None:
            fields[current_key] = "\n".join(current_value_lines)

        return fields
