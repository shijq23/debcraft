"""Domain parser for .deb binary package archives.

Extracts structured metadata from .deb files using the DebFileReader
protocol for all I/O operations. The parser validates archive structure,
parses control file fields, extracts dependency relationships, enumerates
file listings, and retrieves copyright text.
"""

from __future__ import annotations

import io
import re
import tarfile
from typing import TYPE_CHECKING

from debcraft.domain.package_intelligence.errors import (
    DebParseError,
    DependencyParseError,
)
from debcraft.domain.package_intelligence.values import (
    DebParseResult,
    DependencyRelation,
)

if TYPE_CHECKING:
    from debcraft.domain.package_intelligence.ports import DebFileReader

#: Magic bytes that identify a valid ar archive.
_AR_MAGIC = b"!<arch>\n"

#: Dependency field names that should be parsed into DependencyRelation lists.
_DEPENDENCY_FIELDS = frozenset(
    {
        "Depends",
        "Pre-Depends",
        "Recommends",
        "Suggests",
    }
)


class DebParser:
    """Extracts metadata from .deb binary package archives."""

    PARSER_VERSION: int = 1

    def __init__(self, file_reader: DebFileReader) -> None:
        """Initialize DebParser with a file reader protocol implementation.

        Args:
            file_reader: Protocol implementation for reading .deb archive members.
        """
        self._file_reader = file_reader

    def parse(self, deb_path: str) -> DebParseResult:
        """Parse a .deb file into structured metadata.

        Args:
            deb_path: File system path to the .deb archive.

        Returns:
            DebParseResult containing all extracted metadata.

        Raises:
            DebParseError: If the file is malformed or missing required members.
        """
        self._validate_ar_magic(deb_path)
        self._validate_debian_binary(deb_path)
        control_fields = self._extract_control_fields(deb_path)
        package_name = control_fields.get("Package", "")
        dependencies = self._parse_dependencies(control_fields, package_name)
        file_listing = self._extract_file_listing(deb_path)
        copyright_text = self._extract_copyright(deb_path, package_name)

        return DebParseResult(
            package_name=package_name,
            version=control_fields.get("Version", ""),
            architecture=control_fields.get("Architecture", ""),
            control_fields=control_fields,
            dependencies=dependencies,
            file_listing=file_listing,
            copyright_text=copyright_text,
        )

    def _validate_ar_magic(self, deb_path: str) -> None:
        """Validate that the file starts with ar archive magic bytes.

        Reads the raw file header via the file reader using an empty
        member prefix to get the archive preamble.

        Raises:
            DebParseError: If the magic bytes do not match.
        """
        try:
            # Use empty prefix to read the ar archive header/magic
            raw = self._file_reader.read_ar_member(deb_path, "")
        except Exception as exc:
            raise DebParseError(
                file_path=deb_path,
                reason="Failed to read archive header",
                cause=exc,
            ) from exc

        if not raw.startswith(_AR_MAGIC):
            raise DebParseError(
                file_path=deb_path,
                reason=("Not a valid ar archive: missing magic bytes '!<arch>\\n'"),
            )

    def _validate_debian_binary(self, deb_path: str) -> None:
        """Validate the debian-binary member version.

        Raises:
            DebParseError: If debian-binary is missing or has unsupported version.
        """
        try:
            content = self._file_reader.read_ar_member(deb_path, "debian-binary")
        except Exception as exc:
            raise DebParseError(
                file_path=deb_path,
                reason="Failed to read 'debian-binary' member",
                cause=exc,
            ) from exc

        version_str = content.decode("utf-8", errors="replace").strip()
        if not version_str.startswith("2."):
            raise DebParseError(
                file_path=deb_path,
                reason=(f"Unsupported debian-binary version: '{version_str}' (expected version starting with '2.')"),
            )

    def _extract_control_fields(self, deb_path: str) -> dict[str, str]:
        """Extract and parse control file fields from control.tar.

        Raises:
            DebParseError: If control.tar is missing or control file cannot be parsed.
        """
        try:
            control_tar_bytes = self._file_reader.read_ar_member(deb_path, "control.tar")
        except Exception as exc:
            raise DebParseError(
                file_path=deb_path,
                reason="Missing or unreadable 'control.tar' member",
                cause=exc,
            ) from exc

        control_text = self._read_control_from_tar(deb_path, control_tar_bytes)
        return self._parse_control_text(control_text)

    def _read_control_from_tar(self, deb_path: str, tar_bytes: bytes) -> str:
        """Read the ./control file from control tar bytes.

        Args:
            deb_path: Original .deb path (for error messages).
            tar_bytes: Raw tar archive bytes (already decompressed by reader).

        Returns:
            The text content of the control file.

        Raises:
            DebParseError: If the control file cannot be found in the tar.
        """
        try:
            with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tar:
                # Look for the control file - may be "./control" or "control"
                for member in tar.getmembers():
                    name = member.name.lstrip("./")
                    if name == "control" and member.isfile():
                        extracted = tar.extractfile(member)
                        if extracted is None:
                            continue
                        return extracted.read().decode("utf-8", errors="replace")
        except tarfile.TarError as exc:
            raise DebParseError(
                file_path=deb_path,
                reason="Failed to read control.tar archive",
                cause=exc,
            ) from exc

        raise DebParseError(
            file_path=deb_path,
            reason="No 'control' file found within control.tar",
        )

    def _parse_control_text(self, text: str) -> dict[str, str]:
        """Parse Debian control file format into field dict.

        Standard format: `Field-Name: value` with continuation lines
        (space/tab prefix).

        Args:
            text: Raw control file text.

        Returns:
            Dictionary mapping field names to their values.
        """
        fields: dict[str, str] = {}
        current_field: str | None = None
        current_value_lines: list[str] = []

        for line in text.splitlines():
            if not line or line.isspace():
                # Empty line marks end of paragraph in control files
                # Store current field and stop (single-paragraph control file)
                if current_field is not None:
                    fields[current_field] = "\n".join(current_value_lines)
                break

            if line[0] in (" ", "\t"):
                # Continuation line
                if current_field is not None:
                    # Strip the leading space/tab and append
                    current_value_lines.append(line[1:])
            else:
                # New field
                if current_field is not None:
                    fields[current_field] = "\n".join(current_value_lines)

                colon_pos = line.find(":")
                if colon_pos == -1:
                    # Malformed line, skip
                    current_field = None
                    current_value_lines = []
                    continue

                current_field = line[:colon_pos]
                value = line[colon_pos + 1 :].strip()
                current_value_lines = [value]

        # Don't forget the last field if file doesn't end with blank line
        if current_field is not None and current_field not in fields:
            fields[current_field] = "\n".join(current_value_lines)

        return fields

    def _parse_dependencies(self, control_fields: dict[str, str], package_name: str) -> list[DependencyRelation]:
        """Parse all dependency fields into DependencyRelation lists.

        Args:
            control_fields: Parsed control file fields.
            package_name: Package name for error reporting.

        Returns:
            Combined list of all dependency relationships.

        Raises:
            DependencyParseError: If a dependency field is malformed.
        """
        all_deps: list[DependencyRelation] = []

        for field_name in _DEPENDENCY_FIELDS:
            value = control_fields.get(field_name)
            if value is None:
                continue
            deps = self._parse_dependency_field(value, package_name, field_name)
            all_deps.extend(deps)

        return all_deps

    def _parse_dependency_field(self, value: str, package_name: str, field_name: str) -> list[DependencyRelation]:
        """Parse a single dependency field value.

        Format: comma-separated entries, each optionally with version
        constraint and/or alternatives separated by |.

        Args:
            value: Raw dependency field value string.
            package_name: Package name for error reporting.
            field_name: Field name for error reporting.

        Returns:
            List of DependencyRelation objects.

        Raises:
            DependencyParseError: If the field value is malformed.
        """
        # Normalize multiline values (continuation lines)
        value = re.sub(r"\n\s*", " ", value).strip()

        if not value:
            return []

        relations: list[DependencyRelation] = []
        entries = value.split(",")

        for entry in entries:
            entry = entry.strip()
            if not entry:
                continue

            # Check for alternatives (|)
            alternatives_raw = entry.split("|")
            if len(alternatives_raw) > 1:
                # First alternative is the primary, rest are alternatives
                primary = self._parse_single_dep(alternatives_raw[0].strip(), package_name, field_name)
                alts = []
                for alt_raw in alternatives_raw[1:]:
                    alt_raw = alt_raw.strip()
                    if not alt_raw:
                        continue
                    alts.append(self._parse_single_dep(alt_raw, package_name, field_name))
                relations.append(
                    DependencyRelation(
                        package=primary.package,
                        version_constraint=primary.version_constraint,
                        alternatives=alts,
                    )
                )
            else:
                relations.append(self._parse_single_dep(entry, package_name, field_name))

        return relations

    def _parse_single_dep(self, dep_str: str, package_name: str, field_name: str) -> DependencyRelation:
        """Parse a single dependency specification.

        Format: `package_name (operator version)` or just `package_name`.

        Args:
            dep_str: Single dependency string (e.g. "libc6 (>= 2.17)").
            package_name: Source package name for error reporting.
            field_name: Field name for error reporting.

        Returns:
            A DependencyRelation object.

        Raises:
            DependencyParseError: If the dependency string is malformed.
        """
        dep_str = dep_str.strip()

        # Remove architecture qualifiers like :any, :amd64
        # Pattern: package_name:qualifier
        match = re.match(
            r"^([a-zA-Z0-9][a-zA-Z0-9.+\-]*)(?::([a-zA-Z0-9\-]+))?"
            r"(?:\s*\(([^)]+)\))?\s*(?:\[.*\])?\s*(?:<.*>)?\s*$",
            dep_str,
        )

        if not match:
            raise DependencyParseError(
                package_name=package_name,
                field_name=field_name,
                reason=f"Malformed dependency: '{dep_str}'",
            )

        dep_package = match.group(1)
        version_part = match.group(3)

        version_constraint: str | None = None
        if version_part:
            version_constraint = version_part.strip()

        return DependencyRelation(
            package=dep_package,
            version_constraint=version_constraint,
        )

    def _extract_file_listing(self, deb_path: str) -> list[str]:
        """Extract file listing from data.tar.

        Raises:
            DebParseError: If data.tar is missing or cannot be read.
        """
        try:
            data_tar_bytes = self._file_reader.read_ar_member(deb_path, "data.tar")
        except Exception as exc:
            raise DebParseError(
                file_path=deb_path,
                reason="Missing or unreadable 'data.tar' member",
                cause=exc,
            ) from exc

        try:
            file_listing: list[str] = []
            with tarfile.open(fileobj=io.BytesIO(data_tar_bytes), mode="r:") as tar:
                for member in tar.getmembers():
                    file_listing.append(member.name)
            return file_listing
        except tarfile.TarError as exc:
            raise DebParseError(
                file_path=deb_path,
                reason="Failed to read data.tar archive",
                cause=exc,
            ) from exc

    def _extract_copyright(self, deb_path: str, package_name: str) -> str | None:
        """Extract copyright text from data.tar if present.

        Looks for `usr/share/doc/<package>/copyright` with or without
        leading `./` prefix.

        Args:
            deb_path: Path to the .deb file.
            package_name: Package name from control file.

        Returns:
            Copyright file text content, or None if not found.
        """
        if not package_name:
            return None

        try:
            data_tar_bytes = self._file_reader.read_ar_member(deb_path, "data.tar")
        except Exception:
            # data.tar already validated in _extract_file_listing
            return None

        try:
            with tarfile.open(fileobj=io.BytesIO(data_tar_bytes), mode="r:") as tar:
                for member in tar.getmembers():
                    normalized = member.name.lstrip("./")
                    if normalized == f"usr/share/doc/{package_name}/copyright" and member.isfile():
                        extracted = tar.extractfile(member)
                        if extracted is not None:
                            return extracted.read().decode("utf-8", errors="replace")
        except tarfile.TarError:
            return None

        return None
