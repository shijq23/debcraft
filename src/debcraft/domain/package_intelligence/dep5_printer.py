"""DEP-5 machine-readable copyright file printer.

Serializes a structured DEP5Document back into valid DEP-5 formatted
text, supporting round-trip correctness with the DEP5Parser.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from debcraft.domain.package_intelligence.values import (
        DEP5Document,
        DEP5FilesParagraph,
        DEP5Header,
        DEP5LicenseParagraph,
    )


class DEP5Printer:
    """Serializes DEP5Document back to DEP-5 formatted text."""

    def print(self, document: DEP5Document) -> str:
        """Format a DEP5Document as valid DEP-5 text.

        Emits paragraphs in document order: header first, then Files
        paragraphs, then standalone License paragraphs. Paragraphs are
        separated by exactly one blank line. Output ends with exactly
        one trailing newline.
        """
        paragraphs: list[str] = []

        paragraphs.append(self._format_header(document.header))

        for files_para in document.files_paragraphs:
            paragraphs.append(self._format_files_paragraph(files_para))

        for license_para in document.license_paragraphs:
            paragraphs.append(self._format_license_paragraph(license_para))

        # Each formatted paragraph ends with \n from its last field.
        # Joining with \n gives \n\n between paragraphs (the blank line separator).
        # The last paragraph already ends with \n, so no extra newline needed.
        return "\n".join(paragraphs)

    def _format_header(self, header: DEP5Header) -> str:
        """Format the header paragraph."""
        lines: list[str] = []

        lines.append(self._format_field("Format", header.format_url))

        if header.upstream_name is not None:
            lines.append(self._format_field("Upstream-Name", header.upstream_name))

        if header.upstream_contact is not None:
            lines.append(self._format_field("Upstream-Contact", header.upstream_contact))

        if header.source is not None:
            lines.append(self._format_field("Source", header.source))

        if header.comment is not None:
            lines.append(self._format_field("Comment", header.comment))

        for key, value in header.extra_fields.items():
            lines.append(self._format_field(self._title_case_field(key), value))

        return "".join(lines)

    def _format_files_paragraph(self, para: DEP5FilesParagraph) -> str:
        """Format a Files paragraph."""
        lines: list[str] = []

        # Files field: join patterns with spaces
        files_value = " ".join(para.files)
        lines.append(self._format_field("Files", files_value))

        lines.append(self._format_field("Copyright", para.copyright))

        # License field: first line is the name, subsequent lines are the text
        license_value = (
            para.license_name + "\n" + para.license_text if para.license_text is not None else para.license_name
        )
        lines.append(self._format_field("License", license_value))

        if para.comment is not None:
            lines.append(self._format_field("Comment", para.comment))

        for key, value in para.extra_fields.items():
            lines.append(self._format_field(self._title_case_field(key), value))

        return "".join(lines)

    def _format_license_paragraph(self, para: DEP5LicenseParagraph) -> str:
        """Format a standalone License paragraph."""
        lines: list[str] = []

        # License field: first line is the name, subsequent lines are the text
        license_value = para.license_name + "\n" + para.license_text if para.license_text else para.license_name
        lines.append(self._format_field("License", license_value))

        if para.comment is not None:
            lines.append(self._format_field("Comment", para.comment))

        for key, value in para.extra_fields.items():
            lines.append(self._format_field(self._title_case_field(key), value))

        return "".join(lines)

    def _format_field(self, name: str, value: str) -> str:
        r"""Format a single field with its value.

        Single-line values: ``Field-Name: value\n``
        Multiline values: first line on same line as field name,
        subsequent lines prefixed with a single space. Empty lines
        within the value become `` .\n`` (space-dot).
        """
        field_lines = value.split("\n")
        result = f"{name}: {field_lines[0]}\n"

        for continuation in field_lines[1:]:
            if continuation == "":
                result += " .\n"
            else:
                result += f" {continuation}\n"

        return result

    @staticmethod
    def _title_case_field(key: str) -> str:
        """Convert a lowercase field key to Title-Case.

        Splits on hyphens and capitalizes each word segment.
        For example, ``upstream-name`` becomes ``Upstream-Name``.
        """
        return "-".join(word.capitalize() for word in key.split("-"))
