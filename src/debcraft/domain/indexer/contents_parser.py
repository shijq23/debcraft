"""Parser for Debian Contents index files.

Extracts file-to-package ownership mappings from decompressed Contents
file content. The format is line-based: each line maps a filesystem path
to one or more qualified package names (section/package_name).

Typical Contents file format:
    FILE                                    LOCATION
    usr/bin/foo                             utils/foo-tools
    usr/lib/libfoo.so.1                     libs/libfoo,libs/libfoo-alt
    etc/foo.conf                            admin/foo

The file may have an optional header section at the top (column headers
or other non-data lines). These are detected by not matching the expected
two-column format and are skipped.
"""

from __future__ import annotations

import logging

from debcraft.domain.indexer.values import FileOwnership

logger = logging.getLogger(__name__)


class ContentsParser:
    """Parses Debian Contents index files (decompressed).

    Extracts filesystem path to qualified package name mappings.
    Each line that has at least two whitespace-separated columns is
    treated as a data line. Lines with fewer than two columns are
    skipped as malformed or header lines.

    When a line maps one path to multiple comma-separated packages,
    one FileOwnership record is produced per package.
    """

    PARSER_VERSION: int = 1

    def parse(self, content: str) -> list[FileOwnership]:
        """Parse Contents file lines into FileOwnership value objects.

        Handles optional header section. Lines mapping one path to
        multiple packages produce multiple FileOwnership records.

        Args:
            content: The full decompressed text content of a Contents file.

        Returns:
            List of FileOwnership for each valid path-to-package mapping.
            Malformed lines and header lines are skipped.
        """
        if not content or not content.strip():
            return []

        ownerships: list[FileOwnership] = []

        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            parsed = self._parse_line(stripped)
            if parsed is not None:
                ownerships.extend(parsed)

        return ownerships

    def _parse_line(self, line: str) -> list[FileOwnership] | None:
        """Parse a single Contents file line into FileOwnership objects.

        The format is: path followed by whitespace followed by one or more
        comma-separated qualified package names (section/package_name).

        The path is everything up to the last whitespace-separated token,
        which contains the package list. This handles paths that don't
        contain spaces (the common case) by splitting on the last
        whitespace boundary.

        Args:
            line: A stripped, non-empty line from the Contents file.

        Returns:
            List of FileOwnership objects if the line is valid,
            None if the line is malformed.
        """
        # The Contents format uses the last whitespace-separated token
        # as the package list, and everything before it as the file path.
        # We use rsplit with maxsplit=1 to handle this correctly.
        parts = line.rsplit(None, 1)

        if len(parts) < 2:
            logger.debug(
                "Skipping malformed Contents line: %s",
                line[:80],
            )
            return None

        file_path = parts[0].strip()
        packages_field = parts[1].strip()

        if not file_path or not packages_field:
            logger.debug(
                "Skipping malformed Contents line: %s",
                line[:80],
            )
            return None

        # Split comma-separated package names and produce one record each
        package_names = packages_field.split(",")
        results: list[FileOwnership] = []

        for pkg in package_names:
            qualified_name = pkg.strip()
            if qualified_name:
                results.append(
                    FileOwnership(
                        path=file_path,
                        qualified_package_name=qualified_name,
                    )
                )

        if not results:
            logger.debug(
                "Skipping Contents line with empty package names: %s",
                line[:80],
            )
            return None

        return results
