"""Parser for Debian Packages index files.

Extracts full binary package metadata from decompressed Packages file content,
producing PackageMetadata value objects for persistence by the indexer service.
"""

from __future__ import annotations

import logging
import re

from debcraft.domain._stanza_parser import parse_stanza_fields, split_stanzas
from debcraft.domain.indexer.values import PackageMetadata

logger = logging.getLogger(__name__)

_SOURCE_WITH_VERSION_RE = re.compile(r"^(.+?)\s*\(([^)]+)\)\s*$")

_REQUIRED_FIELDS = frozenset({"Package", "Version", "Architecture", "Filename", "SHA256", "Size"})


class PackagesParser:
    """Parses full binary package metadata from decompressed Packages file content."""

    PARSER_VERSION: int = 1

    def parse(self, content: str) -> list[PackageMetadata]:
        """Parse all valid package stanzas into PackageMetadata value objects.

        Stanzas missing required fields (Package, Version, Architecture,
        Filename, SHA256, Size) are skipped with a debug log.

        Args:
            content: The full decompressed Packages file content.

        Returns:
            A list of PackageMetadata value objects for all valid stanzas.
        """
        if not content or not content.strip():
            return []

        results: list[PackageMetadata] = []

        for stanza in split_stanzas(content):
            fields = parse_stanza_fields(stanza, preserve_continuations=True)
            metadata = self._build_metadata(fields)
            if metadata is not None:
                results.append(metadata)

        return results

    def _build_metadata(self, fields: dict[str, str]) -> PackageMetadata | None:
        """Build a PackageMetadata from parsed fields, or None if invalid.

        Validates required fields are present and Size is a valid non-negative
        integer. Infers source_package and source_version from the Source field.
        """
        missing = _REQUIRED_FIELDS - fields.keys()
        if missing:
            pkg_name = fields.get("Package", "<unknown>")
            logger.debug(
                "Skipping stanza for %s: missing fields %s",
                pkg_name,
                sorted(missing),
            )
            return None

        # Validate Size is a valid non-negative integer
        size_str = fields["Size"].strip()
        try:
            size_bytes = int(size_str)
        except ValueError:
            logger.debug(
                "Skipping stanza for %s: Size field is not a valid integer: %r",
                fields["Package"],
                size_str,
            )
            return None

        if size_bytes < 0:
            logger.debug(
                "Skipping stanza for %s: Size field is negative: %d",
                fields["Package"],
                size_bytes,
            )
            return None

        # Infer source package and version
        source_package, source_version = self._infer_source(
            fields.get("Source"),
            fields["Package"],
            fields["Version"],
        )

        return PackageMetadata(
            package_name=fields["Package"],
            version=fields["Version"],
            architecture=fields["Architecture"],
            filename=fields["Filename"],
            sha256=fields["SHA256"],
            size_bytes=size_bytes,
            source_package=source_package,
            source_version=source_version,
            homepage=fields.get("Homepage") or None,
            maintainer=fields.get("Maintainer") or None,
            depends=fields.get("Depends") or None,
            provides=fields.get("Provides") or None,
            section=fields.get("Section") or None,
            priority=fields.get("Priority") or None,
            description=fields.get("Description") or None,
        )

    def format(self, metadata: PackageMetadata) -> str:
        """Format a PackageMetadata back into a Packages stanza string.

        Used for round-trip verification in property-based tests.

        Output order:
            1. Required fields: Package, Version, Architecture, Filename, SHA256, Size
            2. Source field (only if source differs from defaults)
            3. Optional fields: Homepage, Maintainer, Depends, Provides, Section, Priority, Description
        """
        lines: list[str] = []

        # Required fields
        lines.append(f"Package: {metadata.package_name}")
        lines.append(f"Version: {metadata.version}")
        lines.append(f"Architecture: {metadata.architecture}")
        lines.append(f"Filename: {metadata.filename}")
        lines.append(f"SHA256: {metadata.sha256}")
        lines.append(f"Size: {metadata.size_bytes}")

        # Source field: only emit if source differs from the default inference
        source_name_differs = metadata.source_package != metadata.package_name
        source_version_differs = metadata.source_version != metadata.version
        if source_name_differs and source_version_differs:
            lines.append(f"Source: {metadata.source_package} ({metadata.source_version})")
        elif source_name_differs:
            lines.append(f"Source: {metadata.source_package}")
        elif source_version_differs:
            lines.append(f"Source: {metadata.source_package} ({metadata.source_version})")

        # Optional fields
        if metadata.homepage is not None:
            lines.append(f"Homepage: {metadata.homepage}")
        if metadata.maintainer is not None:
            lines.append(f"Maintainer: {metadata.maintainer}")
        if metadata.depends is not None:
            lines.append(f"Depends: {metadata.depends}")
        if metadata.provides is not None:
            lines.append(f"Provides: {metadata.provides}")
        if metadata.section is not None:
            lines.append(f"Section: {metadata.section}")
        if metadata.priority is not None:
            lines.append(f"Priority: {metadata.priority}")
        if metadata.description is not None:
            # Description may contain newlines; continuation lines are prefixed with a space
            desc_lines = metadata.description.split("\n")
            lines.append(f"Description: {desc_lines[0]}")
            for continuation in desc_lines[1:]:
                lines.append(f" {continuation}")

        return "\n".join(lines)

    def _infer_source(
        self,
        source_field: str | None,
        package_name: str,
        binary_version: str,
    ) -> tuple[str, str]:
        """Infer source_package and source_version from the Source field.

        Rules:
            1. Source: name (version) → source_package=name, source_version=version
            2. Source: name (no parens) → source_package=name, source_version=binary_version
            3. Source absent → source_package=package_name, source_version=binary_version
        """
        if not source_field:
            return package_name, binary_version

        source_value = source_field.strip()
        if not source_value:
            return package_name, binary_version

        match = _SOURCE_WITH_VERSION_RE.match(source_value)
        if match:
            return match.group(1).strip(), match.group(2).strip()

        # Name only, no parenthesized version
        return source_value, binary_version
