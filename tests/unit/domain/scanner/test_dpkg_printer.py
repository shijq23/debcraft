"""Unit tests for dpkg status printer."""

import pytest

from debcraft.domain.scanner.dpkg_parser import DpkgStanza
from debcraft.domain.scanner.dpkg_printer import (
    _format_field_value,
    _format_stanza,
    format_dpkg_status,
)

pytestmark = [pytest.mark.unit]


class TestFormatDpkgStatus:
    """Tests for the top-level format_dpkg_status function."""

    def test_empty_list_returns_empty_string(self) -> None:
        """Empty stanza list returns empty string (Req 3.5)."""
        assert format_dpkg_status([]) == ""

    def test_single_stanza_with_simple_fields(self) -> None:
        """Single stanza with simple fields formatted correctly."""
        stanza = DpkgStanza(
            fields=[
                ("Package", "bash"),
                ("Version", "5.2-1"),
                ("Architecture", "amd64"),
            ]
        )
        result = format_dpkg_status([stanza])
        expected = "Package: bash\nVersion: 5.2-1\nArchitecture: amd64\n"
        assert result == expected

    def test_multiple_stanzas_separated_by_blank_line(self) -> None:
        """Multiple stanzas separated by exactly one blank line (Req 3.1)."""
        stanza1 = DpkgStanza(fields=[("Package", "bash"), ("Version", "5.2-1")])
        stanza2 = DpkgStanza(fields=[("Package", "coreutils"), ("Version", "9.1-1")])
        result = format_dpkg_status([stanza1, stanza2])
        expected = "Package: bash\nVersion: 5.2-1\n\nPackage: coreutils\nVersion: 9.1-1\n"
        assert result == expected

    def test_output_ends_with_single_trailing_newline(self) -> None:
        """Output ends with exactly one trailing newline (Req 3.1)."""
        stanza = DpkgStanza(fields=[("Package", "bash")])
        result = format_dpkg_status([stanza])
        assert result.endswith("\n")
        assert not result.endswith("\n\n")

    def test_field_order_preserved(self) -> None:
        """Field order preserved as encountered during parsing (Req 3.4)."""
        stanza = DpkgStanza(
            fields=[
                ("Status", "install ok installed"),
                ("Package", "bash"),
                ("Version", "5.2-1"),
                ("Architecture", "amd64"),
            ]
        )
        result = format_dpkg_status([stanza])
        lines = result.strip().split("\n")
        assert lines[0] == "Status: install ok installed"
        assert lines[1] == "Package: bash"
        assert lines[2] == "Version: 5.2-1"
        assert lines[3] == "Architecture: amd64"

    def test_multiline_value_uses_continuation_lines(self) -> None:
        """Multiline values use continuation lines with space prefix (Req 3.2)."""
        stanza = DpkgStanza(
            fields=[
                ("Package", "bash"),
                ("Description", "GNU Bourne Again SHell\nbash is sh compatible"),
            ]
        )
        result = format_dpkg_status([stanza])
        expected = "Package: bash\nDescription: GNU Bourne Again SHell\n bash is sh compatible\n"
        assert result == expected

    def test_empty_lines_in_multiline_become_space_dot(self) -> None:
        """Empty lines within multiline values become ' .' (Req 3.2)."""
        stanza = DpkgStanza(
            fields=[
                ("Package", "bash"),
                ("Description", "Short desc\nParagraph one\n\nParagraph two"),
            ]
        )
        result = format_dpkg_status([stanza])
        expected = "Package: bash\nDescription: Short desc\n Paragraph one\n .\n Paragraph two\n"
        assert result == expected

    def test_three_stanzas_separated_correctly(self) -> None:
        """Three stanzas each separated by one blank line."""
        stanzas = [
            DpkgStanza(fields=[("Package", "a"), ("Version", "1.0")]),
            DpkgStanza(fields=[("Package", "b"), ("Version", "2.0")]),
            DpkgStanza(fields=[("Package", "c"), ("Version", "3.0")]),
        ]
        result = format_dpkg_status(stanzas)
        expected = "Package: a\nVersion: 1.0\n\nPackage: b\nVersion: 2.0\n\nPackage: c\nVersion: 3.0\n"
        assert result == expected


class TestFormatStanza:
    """Tests for the _format_stanza helper."""

    def test_single_field(self) -> None:
        stanza = DpkgStanza(fields=[("Package", "bash")])
        assert _format_stanza(stanza) == "Package: bash"

    def test_empty_fields(self) -> None:
        stanza = DpkgStanza(fields=[])
        assert _format_stanza(stanza) == ""


class TestFormatFieldValue:
    """Tests for the _format_field_value helper."""

    def test_simple_value(self) -> None:
        assert _format_field_value("bash") == "bash"

    def test_multiline_value(self) -> None:
        result = _format_field_value("first\nsecond\nthird")
        assert result == "first\n second\n third"

    def test_empty_continuation_line(self) -> None:
        result = _format_field_value("first\n\nthird")
        assert result == "first\n .\n third"

    def test_dot_only_continuation_line(self) -> None:
        """A line containing only '.' is treated as empty separator."""
        result = _format_field_value("first\n.\nthird")
        assert result == "first\n .\n third"
