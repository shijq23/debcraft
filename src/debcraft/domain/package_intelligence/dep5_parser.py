"""DEP-5 machine-readable copyright file parser.

Parses Debian DEP-5 formatted copyright documents into a structured
document model consisting of a header paragraph, files paragraphs, and
standalone license paragraphs.
"""

from __future__ import annotations

from debcraft.domain.package_intelligence.errors import DEP5ParseError
from debcraft.domain.package_intelligence.values import (
    DEP5Document,
    DEP5FilesParagraph,
    DEP5Header,
    DEP5LicenseParagraph,
)


class DEP5Parser:
    """Parses DEP-5 machine-readable copyright files."""

    PARSER_VERSION: int = 1

    def parse(self, text: str) -> DEP5Document:
        """Parse DEP-5 formatted text into a structured document.

        Args:
            text: The raw DEP-5 formatted text content.

        Returns:
            A structured DEP5Document.

        Raises:
            DEP5ParseError: If the input is not valid DEP-5.
        """
        if not text or not text.strip():
            raise DEP5ParseError("No content found")

        raw_paragraphs = self._split_paragraphs(text)
        if not raw_paragraphs:
            raise DEP5ParseError("No content found")

        paragraphs = [self._parse_fields(raw) for raw in raw_paragraphs]

        header = self._parse_header(paragraphs[0])

        files_paragraphs: list[DEP5FilesParagraph] = []
        license_paragraphs: list[DEP5LicenseParagraph] = []

        for idx, fields in enumerate(paragraphs[1:], start=1):
            if "files" in fields:
                files_paragraphs.append(self._parse_files_paragraph(fields, idx))
            elif "license" in fields:
                license_paragraphs.append(self._parse_license_paragraph(fields))
            # Paragraphs without Files or License are ignored per spec

        return DEP5Document(
            header=header,
            files_paragraphs=files_paragraphs,
            license_paragraphs=license_paragraphs,
        )

    def _split_paragraphs(self, text: str) -> list[str]:
        """Split text into paragraph blocks separated by blank lines.

        A blank line is a line containing only whitespace or nothing at all.
        Consecutive blank lines are treated as a single separator.
        """
        paragraphs: list[str] = []
        current_lines: list[str] = []

        for line in text.split("\n"):
            if line.strip() == "":
                if current_lines:
                    paragraphs.append("\n".join(current_lines))
                    current_lines = []
            else:
                current_lines.append(line)

        if current_lines:
            paragraphs.append("\n".join(current_lines))

        return paragraphs

    def _parse_fields(self, paragraph: str) -> dict[str, str]:
        """Parse a paragraph into an ordered dict of field name → value.

        Handles continuation lines (starting with space or tab) and
        lone-dot empty line markers within multiline values.
        """
        fields: dict[str, str] = {}
        current_field: str | None = None
        current_value_lines: list[str] = []

        for line in paragraph.split("\n"):
            if line.startswith(" ") or line.startswith("\t"):
                # Continuation line
                if current_field is None:
                    # Continuation without a preceding field, skip
                    continue
                # Strip the leading space/tab prefix
                continuation = line[1:]
                # Lone dot represents an empty line in the value
                if continuation == ".":
                    current_value_lines.append("")
                else:
                    current_value_lines.append(continuation)
            elif ":" in line:
                # New field definition
                if current_field is not None:
                    fields[current_field] = "\n".join(current_value_lines)

                colon_pos = line.index(":")
                field_name = line[:colon_pos].strip()
                field_value = line[colon_pos + 1 :].strip()
                current_field = field_name.lower()
                current_value_lines = [field_value]
            else:
                # Line that doesn't look like a field or continuation
                # Treat as continuation of current field if one exists
                if current_field is not None:
                    current_value_lines.append(line)

        # Don't forget the last field
        if current_field is not None:
            fields[current_field] = "\n".join(current_value_lines)

        return fields

    def _parse_header(self, fields: dict[str, str]) -> DEP5Header:
        """Parse the header paragraph fields into a DEP5Header.

        Raises:
            DEP5ParseError: If the Format field is missing.
        """
        if "format" not in fields:
            raise DEP5ParseError(
                "Missing required field 'Format' in header paragraph",
                paragraph_index=0,
            )

        # Known header fields
        known_fields = {
            "format",
            "upstream-name",
            "upstream-contact",
            "source",
            "comment",
        }

        extra_fields: dict[str, str] = {}
        for key, value in fields.items():
            if key not in known_fields:
                extra_fields[key] = value

        return DEP5Header(
            format_url=fields["format"],
            upstream_name=fields.get("upstream-name"),
            upstream_contact=fields.get("upstream-contact"),
            source=fields.get("source"),
            comment=fields.get("comment"),
            extra_fields=extra_fields,
        )

    def _parse_files_paragraph(self, fields: dict[str, str], paragraph_index: int) -> DEP5FilesParagraph:
        """Parse a Files paragraph into a DEP5FilesParagraph.

        Raises:
            DEP5ParseError: If required fields are missing.
        """
        required = {"files": "Files", "copyright": "Copyright", "license": "License"}
        for field_key, field_display in required.items():
            if field_key not in fields:
                raise DEP5ParseError(
                    f"Missing required field '{field_display}' in Files paragraph",
                    paragraph_index=paragraph_index,
                )

        # Parse the Files field: space-separated glob patterns
        files_value = fields["files"]
        file_patterns = files_value.split()

        # Parse the License field: first line is short-name,
        # remaining lines are the full text body
        license_value = fields["license"]
        license_lines = license_value.split("\n")
        license_name = license_lines[0].strip()
        license_text: str | None = None
        if len(license_lines) > 1:
            license_text = "\n".join(license_lines[1:])

        # Known fields for Files paragraphs
        known_fields = {"files", "copyright", "license", "comment"}
        extra_fields: dict[str, str] = {}
        for key, value in fields.items():
            if key not in known_fields:
                extra_fields[key] = value

        return DEP5FilesParagraph(
            files=file_patterns,
            copyright=fields["copyright"],
            license_name=license_name,
            license_text=license_text,
            comment=fields.get("comment"),
            extra_fields=extra_fields,
        )

    def _parse_license_paragraph(self, fields: dict[str, str]) -> DEP5LicenseParagraph:
        """Parse a standalone License paragraph into a DEP5LicenseParagraph."""
        license_value = fields["license"]
        license_lines = license_value.split("\n")
        license_name = license_lines[0].strip()
        license_text = "\n".join(license_lines[1:]) if len(license_lines) > 1 else ""

        # Known fields for standalone License paragraphs
        known_fields = {"license", "comment"}
        extra_fields: dict[str, str] = {}
        for key, value in fields.items():
            if key not in known_fields:
                extra_fields[key] = value

        return DEP5LicenseParagraph(
            license_name=license_name,
            license_text=license_text,
            comment=fields.get("comment"),
            extra_fields=extra_fields,
        )
