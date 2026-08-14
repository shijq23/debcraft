"""Parser for Debian Sources index file content.

Extracts source package metadata from the stanza-based format used in
Sources index files. Each stanza is separated by blank lines and contains
key-value fields describing a source package.
"""

from __future__ import annotations

import logging

from debcraft.domain._stanza_parser import parse_stanza_fields, split_stanzas
from debcraft.domain.indexer.values import SourcePackageMetadata

logger = logging.getLogger(__name__)


class SourcesParser:
    """Parses source package metadata from decompressed Sources file content."""

    PARSER_VERSION: int = 1

    def parse(self, content: str) -> list[SourcePackageMetadata]:
        """Parse all valid source stanzas into SourcePackageMetadata objects.

        Stanzas missing required fields (Package, Version) are skipped
        with a debug log message.

        Args:
            content: Full decompressed Sources file content.

        Returns:
            List of SourcePackageMetadata value objects for valid stanzas.
        """
        if not content or not content.strip():
            return []

        results: list[SourcePackageMetadata] = []

        for stanza in split_stanzas(content):
            fields = parse_stanza_fields(stanza, preserve_continuations=True)
            metadata = self._build_metadata(fields)
            if metadata is not None:
                results.append(metadata)

        return results

    def format(self, metadata: SourcePackageMetadata) -> str:
        """Format a SourcePackageMetadata back into a Sources stanza string.

        Used for round-trip verification in property-based tests.

        Args:
            metadata: The source package metadata to format.

        Returns:
            A Sources stanza string representation.
        """
        lines: list[str] = []
        lines.append(f"Package: {metadata.name}")
        lines.append(f"Version: {metadata.version}")

        if metadata.maintainer is not None:
            lines.append(f"Maintainer: {metadata.maintainer}")

        if metadata.uploaders:
            lines.append(f"Uploaders: {', '.join(metadata.uploaders)}")

        if metadata.section is not None:
            lines.append(f"Section: {metadata.section}")

        if metadata.homepage is not None:
            lines.append(f"Homepage: {metadata.homepage}")

        if metadata.build_depends is not None:
            lines.append(f"Build-Depends: {metadata.build_depends}")

        if metadata.binary_packages:
            lines.append(f"Binary: {', '.join(metadata.binary_packages)}")

        return "\n".join(lines)

    def _build_metadata(self, fields: dict[str, str]) -> SourcePackageMetadata | None:
        """Build a SourcePackageMetadata from parsed fields.

        Returns None if required fields are missing.

        Args:
            fields: Parsed field mapping from a stanza.

        Returns:
            SourcePackageMetadata or None if required fields are absent.
        """
        name = fields.get("Package")
        version = fields.get("Version")

        if not name:
            logger.debug("Skipping source stanza: missing Package field")
            return None

        if not version:
            logger.debug(
                "Skipping source stanza for '%s': missing Version field",
                name,
            )
            return None

        maintainer = fields.get("Maintainer")
        uploaders = self._parse_uploaders(fields.get("Uploaders", ""))
        section = fields.get("Section")
        homepage = fields.get("Homepage")
        build_depends = fields.get("Build-Depends")
        binary_packages = self._parse_binary_field(fields.get("Binary", ""))

        return SourcePackageMetadata(
            name=name,
            version=version,
            maintainer=maintainer,
            uploaders=uploaders,
            section=section,
            homepage=homepage,
            build_depends=build_depends,
            binary_packages=binary_packages,
        )

    def _parse_binary_field(self, value: str) -> list[str]:
        """Split the Binary field on commas and trim whitespace.

        Args:
            value: Raw Binary field value (may span continuation lines).

        Returns:
            List of trimmed binary package names, empty list if value is empty.
        """
        if not value.strip():
            return []

        # Replace continuation newlines with space for splitting
        normalized = value.replace("\n", " ")
        return [pkg.strip() for pkg in normalized.split(",") if pkg.strip()]

    def _parse_uploaders(self, value: str) -> list[str]:
        """Split the Uploaders field on commas and trim whitespace.

        Args:
            value: Raw Uploaders field value (may span continuation lines).

        Returns:
            List of trimmed uploader strings, empty list if value is empty.
        """
        if not value.strip():
            return []

        # Replace continuation newlines with space for splitting
        normalized = value.replace("\n", " ")
        return [uploader.strip() for uploader in normalized.split(",") if uploader.strip()]
