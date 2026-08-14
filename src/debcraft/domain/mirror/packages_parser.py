"""Parser for Debian Packages index files.

Extracts file entries (Filename, SHA256, Size) from decompressed
Packages index content. The format is stanza-based: each package
is described by a block of `Key: Value` lines separated by one
or more blank lines.

Typical Packages file format:
    Package: libssl3
    Version: 3.0.2-0ubuntu1
    Filename: pool/main/l/libssl3/libssl3_3.0.2-0ubuntu1_amd64.deb
    Size: 1234567
    SHA256: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
    Description: OpenSSL shared library
     This is a multi-line
     continuation.

    Package: another-package
    ...
"""

from __future__ import annotations

import logging

from debcraft.domain._stanza_parser import parse_stanza_fields, split_stanzas
from debcraft.domain.mirror.values import FileEntry

logger = logging.getLogger(__name__)


class PackagesParser:
    """Parses Debian Packages index files (decompressed).

    Extracts Filename, SHA256, and Size fields from each package
    stanza. Stanzas missing any of these required fields are
    silently skipped with a debug log message.
    """

    _REQUIRED_FIELDS = ("Filename", "SHA256", "Size")

    def parse(self, content: str) -> list[FileEntry]:
        """Extract package file entries with SHA256, size, and filename.

        Parses stanza-separated package entries. Each stanza is a block
        of `Key: Value` lines. Lines starting with whitespace are
        multi-line continuations of the previous field and are ignored
        for extraction purposes.

        Args:
            content: The full decompressed text content of a Packages file.

        Returns:
            List of FileEntry for each package that has all three
            required fields (Filename, SHA256, Size). Packages missing
            any required field are skipped.
        """
        if not content or not content.strip():
            return []

        entries: list[FileEntry] = []
        stanzas = split_stanzas(content)

        for stanza in stanzas:
            fields = parse_stanza_fields(stanza, preserve_continuations=False)
            entry = self._extract_entry(fields)
            if entry is not None:
                entries.append(entry)

        return entries

    def _extract_entry(self, fields: dict[str, str]) -> FileEntry | None:
        """Extract a FileEntry from parsed stanza fields.

        Returns None if any required field (Filename, SHA256, Size)
        is missing or if Size is not a valid non-negative integer.

        Args:
            fields: Dictionary of field names to values from a stanza.

        Returns:
            FileEntry if all required fields are valid, None otherwise.
        """
        filename = fields.get("Filename")
        sha256 = fields.get("SHA256")
        size_str = fields.get("Size")

        if not filename or not sha256 or not size_str:
            missing = [f for f in self._REQUIRED_FIELDS if not fields.get(f)]
            pkg_name = fields.get("Package", "<unknown>")
            logger.debug(
                "Skipping package %s: missing fields %s",
                pkg_name,
                missing,
            )
            return None

        try:
            size_bytes = int(size_str)
        except ValueError:
            pkg_name = fields.get("Package", "<unknown>")
            logger.debug(
                "Skipping package %s: invalid Size value '%s'",
                pkg_name,
                size_str,
            )
            return None

        if size_bytes < 0:
            pkg_name = fields.get("Package", "<unknown>")
            logger.debug(
                "Skipping package %s: negative Size value %d",
                pkg_name,
                size_bytes,
            )
            return None

        return FileEntry(
            relative_path=filename,
            sha256=sha256,
            size_bytes=size_bytes,
        )
