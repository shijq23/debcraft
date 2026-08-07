"""Parser for Debian Release/InRelease files.

Extracts SHA256 checksum entries and optional metadata fields
from the standard Debian Release file format. The parser handles
both `SHA256:` and `SHA256Sums:` section headers.

Typical Release file format:
    Origin: Debian
    Codename: bookworm
    Date: Sat, 01 Jan 2024 00:00:00 UTC
    SHA256:
     e3b0c44...  1234 main/binary-amd64/Packages
     abc123...   5678 main/binary-amd64/Packages.gz
"""

from __future__ import annotations

from dataclasses import dataclass, field

from debcraft.domain.mirror.errors import ReleaseParseError
from debcraft.domain.mirror.values import FileEntry


@dataclass(frozen=True)
class ReleaseMetadata:
    """Parsed content of a Release file.

    Attributes:
        files: List of file entries extracted from the SHA256 section.
        date: The Date field from the Release file header, if present.
        codename: The Codename field from the Release file header, if present.
        origin: The Origin field from the Release file header, if present.
        label: The Label field from the Release file header, if present.
        suite: The Suite field from the Release file header, if present.
        architectures: The Architectures field from the Release file header, if present.
    """

    files: list[FileEntry] = field(default_factory=list)
    date: str | None = None
    codename: str | None = None
    origin: str | None = None
    label: str | None = None
    suite: str | None = None
    architectures: str | None = None


class ReleaseParser:
    """Parses Debian Release/InRelease files.

    Extracts the SHA256 checksums section and optional top-level
    metadata fields (Date, Codename, Origin, etc.) from Release
    file content.
    """

    _SHA256_HEADERS = ("SHA256:", "SHA256Sums:")

    def parse(self, content: str, url: str = "") -> ReleaseMetadata:
        """Extract SHA256 entries from a Release file.

        Args:
            content: The full text content of a Release or InRelease file.
            url: Optional URL of the Release file, used in error messages.

        Returns:
            ReleaseMetadata containing file entries with checksums
            and optional metadata fields.

        Raises:
            ReleaseParseError: If content is empty, malformed, or
                missing a SHA256/SHA256Sums section.
        """
        if not content or not content.strip():
            raise ReleaseParseError(url, "Release file content is empty")

        metadata_fields = self._parse_header_fields(content)
        entries = self._parse_sha256_section(content, url)

        return ReleaseMetadata(
            files=entries,
            date=metadata_fields.get("date"),
            codename=metadata_fields.get("codename"),
            origin=metadata_fields.get("origin"),
            label=metadata_fields.get("label"),
            suite=metadata_fields.get("suite"),
            architectures=metadata_fields.get("architectures"),
        )

    def _parse_header_fields(self, content: str) -> dict[str, str]:
        """Extract top-level key-value metadata from the Release header.

        Parses lines of the form `Key: Value` that appear before
        any checksum section. Lines starting with whitespace or
        belonging to a multi-line section are skipped.

        Returns:
            Dictionary mapping lowercase field names to their string values.
        """
        fields: dict[str, str] = {}
        for line in content.splitlines():
            # Stop if we hit a checksum section header
            if line.rstrip() in self._SHA256_HEADERS:
                break
            # Also stop at other checksum section headers (MD5Sum, SHA1, SHA512)
            if line.rstrip().endswith(":") and line.rstrip() in (
                "MD5Sum:",
                "SHA1:",
                "SHA512:",
            ):
                break
            # Skip indented lines (part of a multi-line section)
            if line.startswith(" ") or line.startswith("\t"):
                continue
            # Parse key: value lines
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip().lower()
                value = value.strip()
                if key and value:
                    fields[key] = value
        return fields

    def _parse_sha256_section(self, content: str, url: str) -> list[FileEntry]:
        """Extract file entries from the SHA256/SHA256Sums section.

        The section starts with a `SHA256:` or `SHA256Sums:` line and
        contains indented lines with format:
            {hash} {size} {relative_path}

        Fields are separated by whitespace (one or more spaces).
        The section ends when a line without leading whitespace is
        encountered or at end of file.

        Args:
            content: The full Release file content.
            url: URL of the Release file for error messages.

        Returns:
            List of FileEntry objects parsed from the section.

        Raises:
            ReleaseParseError: If no SHA256 section is found or if
                an entry line is malformed.
        """
        lines = content.splitlines()
        sha256_start = self._find_sha256_section(lines)

        if sha256_start is None:
            raise ReleaseParseError(url, "No SHA256 or SHA256Sums section found")

        entries: list[FileEntry] = []
        for i in range(sha256_start + 1, len(lines)):
            line = lines[i]
            # Section ends at a non-indented line
            if not line.startswith(" ") and not line.startswith("\t"):
                break
            # Skip empty indented lines
            stripped = line.strip()
            if not stripped:
                continue
            entry = self._parse_entry_line(stripped, i + 1, url)
            entries.append(entry)

        if not entries:
            raise ReleaseParseError(
                url,
                "SHA256 section is present but contains no valid entries",
            )

        return entries

    def _find_sha256_section(self, lines: list[str]) -> int | None:
        """Find the line index of the SHA256 section header.

        Returns:
            The index of the SHA256/SHA256Sums header line, or None
            if not found.
        """
        for i, line in enumerate(lines):
            if line.rstrip() in self._SHA256_HEADERS:
                return i
        return None

    def _parse_entry_line(self, stripped_line: str, line_number: int, url: str) -> FileEntry:
        """Parse a single SHA256 entry line into a FileEntry.

        Expected format: `{hash} {size} {relative_path}`
        where fields are separated by one or more whitespace characters.

        Args:
            stripped_line: The entry line with leading/trailing whitespace removed.
            line_number: 1-based line number in the original file (for error messages).
            url: URL of the Release file for error messages.

        Returns:
            A FileEntry with the parsed hash, size, and path.

        Raises:
            ReleaseParseError: If the line does not have exactly 3 fields,
                or if the size field is not a valid non-negative integer,
                or if the hash is not a valid hex string of length 64.
        """
        parts = stripped_line.split()
        if len(parts) != 3:
            raise ReleaseParseError(
                url,
                f"Malformed entry at line {line_number}: expected 3 fields (hash, size, path), got {len(parts)}",
            )

        sha256_hash, size_str, relative_path = parts

        # Validate SHA256 hash: must be 64 hex characters
        if len(sha256_hash) != 64:
            raise ReleaseParseError(
                url,
                f"Invalid SHA256 hash at line {line_number}: expected 64 hex characters, got {len(sha256_hash)}",
            )
        try:
            int(sha256_hash, 16)
        except ValueError as exc:
            raise ReleaseParseError(
                url,
                f"Invalid SHA256 hash at line {line_number}: contains non-hex characters",
            ) from exc

        # Validate size: must be a non-negative integer
        try:
            size_bytes = int(size_str)
        except ValueError as exc:
            raise ReleaseParseError(
                url,
                f"Invalid size at line {line_number}: '{size_str}' is not a valid integer",
            ) from exc
        if size_bytes < 0:
            raise ReleaseParseError(
                url,
                f"Invalid size at line {line_number}: size cannot be negative ({size_bytes})",
            )

        return FileEntry(
            relative_path=relative_path,
            sha256=sha256_hash,
            size_bytes=size_bytes,
        )
