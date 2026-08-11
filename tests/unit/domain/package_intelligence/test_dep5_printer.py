"""Unit tests for DEP5Printer.

Tests cover: single-line fields, multiline continuation lines, space-dot
empty line markers, paragraph separation, trailing newline invariant,
field ordering, extra field title-casing, Files pattern joining, and
License field formatting.
"""

from __future__ import annotations

import pytest

from debcraft.domain.package_intelligence.dep5_parser import DEP5Parser
from debcraft.domain.package_intelligence.dep5_printer import DEP5Printer
from debcraft.domain.package_intelligence.values import (
    DEP5Document,
    DEP5FilesParagraph,
    DEP5Header,
    DEP5LicenseParagraph,
)


@pytest.mark.unit
class TestDEP5PrinterSingleLineFields:
    """Verify single-line field formatting."""

    def setup_method(self):
        self.printer = DEP5Printer()

    def test_single_line_format_field(self):
        r"""Single-line field emits 'Field-Name: value\n'."""
        doc = DEP5Document(
            header=DEP5Header(format_url="https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/"),
            files_paragraphs=[],
            license_paragraphs=[],
        )
        output = self.printer.print(doc)
        assert output == ("Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n")

    def test_all_header_fields(self):
        """All standard header fields emitted in correct order."""
        doc = DEP5Document(
            header=DEP5Header(
                format_url="https://example.com/format",
                upstream_name="MyProject",
                upstream_contact="dev@example.com",
                source="https://example.com/source",
                comment="A comment",
            ),
            files_paragraphs=[],
            license_paragraphs=[],
        )
        output = self.printer.print(doc)
        lines = output.split("\n")
        assert lines[0] == "Format: https://example.com/format"
        assert lines[1] == "Upstream-Name: MyProject"
        assert lines[2] == "Upstream-Contact: dev@example.com"
        assert lines[3] == "Source: https://example.com/source"
        assert lines[4] == "Comment: A comment"


@pytest.mark.unit
class TestDEP5PrinterMultilineValues:
    """Verify multiline field value formatting."""

    def setup_method(self):
        self.printer = DEP5Printer()

    def test_multiline_copyright(self):
        """Multiline copyright uses space-prefixed continuation lines."""
        doc = DEP5Document(
            header=DEP5Header(format_url="https://example.com/format"),
            files_paragraphs=[
                DEP5FilesParagraph(
                    files=["*"],
                    copyright="2020 Author One\n2021 Author Two",
                    license_name="MIT",
                )
            ],
            license_paragraphs=[],
        )
        output = self.printer.print(doc)
        assert "Copyright: 2020 Author One\n 2021 Author Two\n" in output

    def test_empty_line_becomes_space_dot(self):
        r"""Empty lines within multiline values become ' .\n'."""
        doc = DEP5Document(
            header=DEP5Header(format_url="https://example.com/format"),
            files_paragraphs=[],
            license_paragraphs=[
                DEP5LicenseParagraph(
                    license_name="MIT",
                    license_text="Para one.\n\nPara two.",
                )
            ],
        )
        output = self.printer.print(doc)
        assert " .\n" in output
        assert "License: MIT\n Para one.\n .\n Para two.\n" in output

    def test_multiple_empty_lines(self):
        r"""Multiple consecutive empty lines each become ' .\n'."""
        doc = DEP5Document(
            header=DEP5Header(format_url="https://example.com/format"),
            files_paragraphs=[],
            license_paragraphs=[
                DEP5LicenseParagraph(
                    license_name="GPL-2",
                    license_text="A\n\n\nB",
                )
            ],
        )
        output = self.printer.print(doc)
        assert " A\n .\n .\n B\n" in output


@pytest.mark.unit
class TestDEP5PrinterParagraphSeparation:
    """Verify paragraphs are separated by exactly one blank line."""

    def setup_method(self):
        self.printer = DEP5Printer()

    def test_header_and_files_separated(self):
        """Header and Files paragraph separated by one blank line."""
        doc = DEP5Document(
            header=DEP5Header(format_url="https://example.com/format"),
            files_paragraphs=[
                DEP5FilesParagraph(
                    files=["*"],
                    copyright="2024 Author",
                    license_name="MIT",
                )
            ],
            license_paragraphs=[],
        )
        output = self.printer.print(doc)
        # Between header and files there should be exactly \n\n
        assert "format\n\nFiles:" in output

    def test_multiple_paragraphs_each_separated(self):
        """Multiple paragraphs each separated by exactly one blank line."""
        doc = DEP5Document(
            header=DEP5Header(format_url="https://example.com/format"),
            files_paragraphs=[
                DEP5FilesParagraph(
                    files=["src/*"],
                    copyright="2024 Author",
                    license_name="MIT",
                ),
                DEP5FilesParagraph(
                    files=["debian/*"],
                    copyright="2024 Maintainer",
                    license_name="GPL-2+",
                ),
            ],
            license_paragraphs=[
                DEP5LicenseParagraph(
                    license_name="MIT",
                    license_text="License text here.",
                )
            ],
        )
        output = self.printer.print(doc)
        # No triple newlines (that would be double blank lines)
        assert "\n\n\n" not in output
        # Count blank line separators (each is one \n\n in the middle)
        paragraph_count = output.count("\n\n") + 1
        assert paragraph_count == 4  # header + 2 files + 1 license


@pytest.mark.unit
class TestDEP5PrinterTrailingNewline:
    """Verify output ends with exactly one trailing newline."""

    def setup_method(self):
        self.printer = DEP5Printer()

    def test_single_paragraph_trailing_newline(self):
        """Single paragraph (header only) ends with one newline."""
        doc = DEP5Document(
            header=DEP5Header(format_url="https://example.com/format"),
            files_paragraphs=[],
            license_paragraphs=[],
        )
        output = self.printer.print(doc)
        assert output.endswith("\n")
        assert not output.endswith("\n\n")

    def test_multiple_paragraphs_trailing_newline(self):
        """Multiple paragraphs end with one newline, not two."""
        doc = DEP5Document(
            header=DEP5Header(format_url="https://example.com/format"),
            files_paragraphs=[
                DEP5FilesParagraph(
                    files=["*"],
                    copyright="2024 Author",
                    license_name="MIT",
                )
            ],
            license_paragraphs=[
                DEP5LicenseParagraph(
                    license_name="MIT",
                    license_text="Full text.",
                )
            ],
        )
        output = self.printer.print(doc)
        assert output.endswith("\n")
        assert not output.endswith("\n\n")


@pytest.mark.unit
class TestDEP5PrinterLicenseField:
    """Verify License field formatting."""

    def setup_method(self):
        self.printer = DEP5Printer()

    def test_license_name_only(self):
        """License without body text emits name only."""
        doc = DEP5Document(
            header=DEP5Header(format_url="https://example.com/format"),
            files_paragraphs=[
                DEP5FilesParagraph(
                    files=["*"],
                    copyright="2024 Author",
                    license_name="MIT",
                    license_text=None,
                )
            ],
            license_paragraphs=[],
        )
        output = self.printer.print(doc)
        assert "License: MIT\n" in output
        # No continuation lines after License
        license_idx = output.index("License: MIT\n")
        after = output[license_idx + len("License: MIT\n") :]
        # Next char should not be a space (no continuation)
        if after:
            assert not after.startswith(" ")

    def test_license_name_with_body(self):
        """License with body text emits name then continuation lines."""
        doc = DEP5Document(
            header=DEP5Header(format_url="https://example.com/format"),
            files_paragraphs=[
                DEP5FilesParagraph(
                    files=["*"],
                    copyright="2024 Author",
                    license_name="Apache-2.0",
                    license_text="Licensed under the Apache License.",
                )
            ],
            license_paragraphs=[],
        )
        output = self.printer.print(doc)
        assert "License: Apache-2.0\n Licensed under the Apache License.\n" in output


@pytest.mark.unit
class TestDEP5PrinterFilesField:
    """Verify Files field pattern joining."""

    def setup_method(self):
        self.printer = DEP5Printer()

    def test_single_pattern(self):
        """Single file pattern emitted as-is."""
        doc = DEP5Document(
            header=DEP5Header(format_url="https://example.com/format"),
            files_paragraphs=[
                DEP5FilesParagraph(
                    files=["*"],
                    copyright="2024 Author",
                    license_name="MIT",
                )
            ],
            license_paragraphs=[],
        )
        output = self.printer.print(doc)
        assert "Files: *\n" in output

    def test_multiple_patterns_joined_with_space(self):
        """Multiple file patterns joined with spaces."""
        doc = DEP5Document(
            header=DEP5Header(format_url="https://example.com/format"),
            files_paragraphs=[
                DEP5FilesParagraph(
                    files=["src/*.py", "lib/*.py", "bin/*"],
                    copyright="2024 Author",
                    license_name="MIT",
                )
            ],
            license_paragraphs=[],
        )
        output = self.printer.print(doc)
        assert "Files: src/*.py lib/*.py bin/*\n" in output


@pytest.mark.unit
class TestDEP5PrinterExtraFields:
    """Verify extra field title-casing."""

    def setup_method(self):
        self.printer = DEP5Printer()

    def test_extra_field_title_cased(self):
        """Extra fields keys are emitted in Title-Case."""
        doc = DEP5Document(
            header=DEP5Header(
                format_url="https://example.com/format",
                extra_fields={"x-custom-field": "value"},
            ),
            files_paragraphs=[],
            license_paragraphs=[],
        )
        output = self.printer.print(doc)
        assert "X-Custom-Field: value\n" in output

    def test_single_word_extra_field(self):
        """Single-word extra field key is capitalized."""
        doc = DEP5Document(
            header=DEP5Header(
                format_url="https://example.com/format",
                extra_fields={"disclaimer": "none"},
            ),
            files_paragraphs=[],
            license_paragraphs=[],
        )
        output = self.printer.print(doc)
        assert "Disclaimer: none\n" in output


@pytest.mark.unit
class TestDEP5PrinterRoundTrip:
    """Verify round-trip correctness with the parser."""

    def setup_method(self):
        self.parser = DEP5Parser()
        self.printer = DEP5Printer()

    def test_round_trip_full_document(self):
        """Parse → print → parse produces equal document."""
        text = (
            "Format: https://www.debian.org/doc/packaging-manuals/"
            "copyright-format/1.0/\n"
            "Upstream-Name: Example\n"
            "Source: https://example.com\n"
            "\n"
            "Files: *\n"
            "Copyright: 2024 Example Corp\n"
            "License: MIT\n"
            " Permission is hereby granted.\n"
            " .\n"
            " THE SOFTWARE IS PROVIDED AS IS.\n"
            "\n"
            "Files: debian/*\n"
            "Copyright: 2024 Maintainer\n"
            "License: GPL-2+\n"
            "\n"
            "License: GPL-2+\n"
            " This program is free software.\n"
        )
        doc1 = self.parser.parse(text)
        printed = self.printer.print(doc1)
        doc2 = self.parser.parse(printed)
        assert doc1 == doc2

    def test_round_trip_minimal(self):
        """Minimal document round-trips correctly."""
        doc = DEP5Document(
            header=DEP5Header(format_url="https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/"),
            files_paragraphs=[],
            license_paragraphs=[],
        )
        printed = self.printer.print(doc)
        doc2 = self.parser.parse(printed)
        assert doc == doc2
