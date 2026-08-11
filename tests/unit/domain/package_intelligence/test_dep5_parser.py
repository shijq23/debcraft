"""Unit tests for DEP5Parser.

Tests cover edge cases: empty input, missing fields, continuation lines,
lone-dot markers, multiline values, Files glob parsing, standalone License
paragraphs, Comment fields, and extra fields.
"""

from __future__ import annotations

import pytest

from debcraft.domain.package_intelligence.dep5_parser import DEP5Parser
from debcraft.domain.package_intelligence.errors import DEP5ParseError


@pytest.mark.unit
class TestDEP5ParserEmptyInput:
    """Verify empty/whitespace input raises DEP5ParseError."""

    def setup_method(self):
        self.parser = DEP5Parser()

    def test_empty_string(self):
        """Empty string raises DEP5ParseError."""
        with pytest.raises(DEP5ParseError, match="No content found"):
            self.parser.parse("")

    def test_whitespace_only(self):
        """Whitespace-only string raises DEP5ParseError."""
        with pytest.raises(DEP5ParseError, match="No content found"):
            self.parser.parse("   \n  \n  ")

    def test_newlines_only(self):
        """Newlines-only string raises DEP5ParseError."""
        with pytest.raises(DEP5ParseError, match="No content found"):
            self.parser.parse("\n\n\n")

    def test_tabs_only(self):
        """Tabs-only string raises DEP5ParseError."""
        with pytest.raises(DEP5ParseError, match="No content found"):
            self.parser.parse("\t\t\t")


@pytest.mark.unit
class TestDEP5ParserMissingFormat:
    """Verify missing Format field raises appropriate error."""

    def setup_method(self):
        self.parser = DEP5Parser()

    def test_missing_format_field(self):
        """First paragraph without Format field raises error."""
        text = "Upstream-Name: foo\nSource: https://example.com\n"
        with pytest.raises(DEP5ParseError) as exc_info:
            self.parser.parse(text)
        assert exc_info.value.paragraph_index == 0
        assert "Format" in str(exc_info.value)

    def test_format_field_in_wrong_paragraph(self):
        """Format field not in first paragraph raises error."""
        text = "Upstream-Name: foo\n\nFormat: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n"
        with pytest.raises(DEP5ParseError) as exc_info:
            self.parser.parse(text)
        assert exc_info.value.paragraph_index == 0


@pytest.mark.unit
class TestDEP5ParserMissingFilesFields:
    """Verify missing required fields in Files paragraph."""

    def setup_method(self):
        self.parser = DEP5Parser()

    def test_missing_copyright_in_files_paragraph(self):
        """Files paragraph without Copyright raises error."""
        text = "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n\nFiles: *\nLicense: MIT\n"
        with pytest.raises(DEP5ParseError) as exc_info:
            self.parser.parse(text)
        assert exc_info.value.paragraph_index == 1
        assert "Copyright" in str(exc_info.value)

    def test_missing_license_in_files_paragraph(self):
        """Files paragraph without License raises error."""
        text = (
            "Format: https://www.debian.org/doc/packaging-manuals/"
            "copyright-format/1.0/\n"
            "\n"
            "Files: *\n"
            "Copyright: 2024 Author\n"
        )
        with pytest.raises(DEP5ParseError) as exc_info:
            self.parser.parse(text)
        assert exc_info.value.paragraph_index == 1
        assert "License" in str(exc_info.value)


@pytest.mark.unit
class TestDEP5ParserHeader:
    """Verify header paragraph parsing."""

    def setup_method(self):
        self.parser = DEP5Parser()

    def test_minimal_header(self):
        """Header with only Format field is valid."""
        text = "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n"
        doc = self.parser.parse(text)
        assert doc.header.format_url == ("https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/")
        assert doc.header.upstream_name is None
        assert doc.header.upstream_contact is None
        assert doc.header.source is None
        assert doc.header.comment is None

    def test_full_header(self):
        """Header with all standard fields parsed correctly."""
        text = (
            "Format: https://www.debian.org/doc/packaging-manuals/"
            "copyright-format/1.0/\n"
            "Upstream-Name: MyProject\n"
            "Upstream-Contact: dev@example.com\n"
            "Source: https://github.com/example/myproject\n"
            "Comment: This is a comment\n"
        )
        doc = self.parser.parse(text)
        assert doc.header.format_url == ("https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/")
        assert doc.header.upstream_name == "MyProject"
        assert doc.header.upstream_contact == "dev@example.com"
        assert doc.header.source == "https://github.com/example/myproject"
        assert doc.header.comment == "This is a comment"

    def test_header_extra_fields(self):
        """Unknown fields in header are captured as extra_fields."""
        text = "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\nX-Custom: custom value\n"
        doc = self.parser.parse(text)
        assert "x-custom" in doc.header.extra_fields
        assert doc.header.extra_fields["x-custom"] == "custom value"


@pytest.mark.unit
class TestDEP5ParserFilesParagraph:
    """Verify Files paragraph parsing."""

    def setup_method(self):
        self.parser = DEP5Parser()

    def test_single_files_paragraph(self):
        """Single Files paragraph with all required fields."""
        text = (
            "Format: https://www.debian.org/doc/packaging-manuals/"
            "copyright-format/1.0/\n"
            "\n"
            "Files: *\n"
            "Copyright: 2024 Author\n"
            "License: MIT\n"
        )
        doc = self.parser.parse(text)
        assert len(doc.files_paragraphs) == 1
        fp = doc.files_paragraphs[0]
        assert fp.files == ["*"]
        assert fp.copyright == "2024 Author"
        assert fp.license_name == "MIT"
        assert fp.license_text is None

    def test_multiple_file_patterns(self):
        """Files field with multiple space-separated patterns."""
        text = (
            "Format: https://www.debian.org/doc/packaging-manuals/"
            "copyright-format/1.0/\n"
            "\n"
            "Files: src/*.py tests/*.py docs/*\n"
            "Copyright: 2024 Author\n"
            "License: Apache-2.0\n"
        )
        doc = self.parser.parse(text)
        fp = doc.files_paragraphs[0]
        assert fp.files == ["src/*.py", "tests/*.py", "docs/*"]

    def test_license_with_body_text(self):
        """License field with continuation lines for body text."""
        text = (
            "Format: https://www.debian.org/doc/packaging-manuals/"
            "copyright-format/1.0/\n"
            "\n"
            "Files: *\n"
            "Copyright: 2024 Author\n"
            "License: MIT\n"
            " Permission is hereby granted, free of charge.\n"
            " .\n"
            ' THE SOFTWARE IS PROVIDED "AS IS".\n'
        )
        doc = self.parser.parse(text)
        fp = doc.files_paragraphs[0]
        assert fp.license_name == "MIT"
        assert fp.license_text == ('Permission is hereby granted, free of charge.\n\nTHE SOFTWARE IS PROVIDED "AS IS".')

    def test_files_paragraph_comment(self):
        """Comment field in Files paragraph is preserved."""
        text = (
            "Format: https://www.debian.org/doc/packaging-manuals/"
            "copyright-format/1.0/\n"
            "\n"
            "Files: *\n"
            "Copyright: 2024 Author\n"
            "License: MIT\n"
            "Comment: This is a note\n"
        )
        doc = self.parser.parse(text)
        assert doc.files_paragraphs[0].comment == "This is a note"

    def test_files_paragraph_extra_fields(self):
        """Extra fields in Files paragraph are captured."""
        text = (
            "Format: https://www.debian.org/doc/packaging-manuals/"
            "copyright-format/1.0/\n"
            "\n"
            "Files: *\n"
            "Copyright: 2024 Author\n"
            "License: MIT\n"
            "X-Source: upstream\n"
        )
        doc = self.parser.parse(text)
        assert "x-source" in doc.files_paragraphs[0].extra_fields
        assert doc.files_paragraphs[0].extra_fields["x-source"] == "upstream"


@pytest.mark.unit
class TestDEP5ParserLicenseParagraph:
    """Verify standalone License paragraph parsing."""

    def setup_method(self):
        self.parser = DEP5Parser()

    def test_standalone_license_paragraph(self):
        """Standalone License paragraph with full body text."""
        text = (
            "Format: https://www.debian.org/doc/packaging-manuals/"
            "copyright-format/1.0/\n"
            "\n"
            "License: MIT\n"
            " Permission is hereby granted.\n"
        )
        doc = self.parser.parse(text)
        assert len(doc.license_paragraphs) == 1
        lp = doc.license_paragraphs[0]
        assert lp.license_name == "MIT"
        assert lp.license_text == "Permission is hereby granted."

    def test_license_paragraph_with_comment(self):
        """Standalone License paragraph with Comment field."""
        text = (
            "Format: https://www.debian.org/doc/packaging-manuals/"
            "copyright-format/1.0/\n"
            "\n"
            "License: GPL-2.0\n"
            " Full GPL text here.\n"
            "Comment: See /usr/share/common-licenses/GPL-2\n"
        )
        doc = self.parser.parse(text)
        lp = doc.license_paragraphs[0]
        assert lp.comment == "See /usr/share/common-licenses/GPL-2"

    def test_license_paragraph_extra_fields(self):
        """Extra fields in License paragraph are captured."""
        text = (
            "Format: https://www.debian.org/doc/packaging-manuals/"
            "copyright-format/1.0/\n"
            "\n"
            "License: BSD-3-Clause\n"
            " Redistribution permitted.\n"
            "X-Note: custom note\n"
        )
        doc = self.parser.parse(text)
        assert "x-note" in doc.license_paragraphs[0].extra_fields


@pytest.mark.unit
class TestDEP5ParserContinuationLines:
    """Verify continuation line and multiline value handling."""

    def setup_method(self):
        self.parser = DEP5Parser()

    def test_space_continuation(self):
        """Continuation lines with leading space are joined."""
        text = (
            "Format: https://www.debian.org/doc/packaging-manuals/"
            "copyright-format/1.0/\n"
            "\n"
            "Files: *\n"
            "Copyright: 2024 Author\n"
            " 2023 Other Author\n"
            "License: MIT\n"
        )
        doc = self.parser.parse(text)
        assert doc.files_paragraphs[0].copyright == "2024 Author\n2023 Other Author"

    def test_tab_continuation(self):
        """Continuation lines with leading tab are joined."""
        text = (
            "Format: https://www.debian.org/doc/packaging-manuals/"
            "copyright-format/1.0/\n"
            "\n"
            "Files: *\n"
            "Copyright: 2024 Author\n"
            "\t2023 Other Author\n"
            "License: MIT\n"
        )
        doc = self.parser.parse(text)
        assert doc.files_paragraphs[0].copyright == "2024 Author\n2023 Other Author"

    def test_lone_dot_empty_line_marker(self):
        """Lone dot on continuation line becomes empty line in value."""
        text = (
            "Format: https://www.debian.org/doc/packaging-manuals/"
            "copyright-format/1.0/\n"
            "\n"
            "License: MIT\n"
            " First paragraph.\n"
            " .\n"
            " Second paragraph.\n"
        )
        doc = self.parser.parse(text)
        lp = doc.license_paragraphs[0]
        assert lp.license_text == "First paragraph.\n\nSecond paragraph."

    def test_multiple_lone_dots(self):
        """Multiple lone dots produce multiple empty lines."""
        text = (
            "Format: https://www.debian.org/doc/packaging-manuals/"
            "copyright-format/1.0/\n"
            "\n"
            "License: MIT\n"
            " Para one.\n"
            " .\n"
            " .\n"
            " Para two.\n"
        )
        doc = self.parser.parse(text)
        lp = doc.license_paragraphs[0]
        assert lp.license_text == "Para one.\n\n\nPara two."


@pytest.mark.unit
class TestDEP5ParserFullDocument:
    """Verify parsing of complete DEP-5 documents."""

    def setup_method(self):
        self.parser = DEP5Parser()

    def test_complete_document(self):
        """Full DEP-5 document with header, files, and license paragraphs."""
        text = (
            "Format: https://www.debian.org/doc/packaging-manuals/"
            "copyright-format/1.0/\n"
            "Upstream-Name: Example\n"
            "Source: https://example.com\n"
            "\n"
            "Files: *\n"
            "Copyright: 2024 Example Corp\n"
            "License: MIT\n"
            "\n"
            "Files: debian/*\n"
            "Copyright: 2024 Debian Maintainer\n"
            "License: GPL-2.0+\n"
            "\n"
            "License: MIT\n"
            " Permission is hereby granted.\n"
            "\n"
            "License: GPL-2.0+\n"
            " This program is free software.\n"
        )
        doc = self.parser.parse(text)
        assert doc.header.upstream_name == "Example"
        assert len(doc.files_paragraphs) == 2
        assert len(doc.license_paragraphs) == 2
        assert doc.files_paragraphs[0].files == ["*"]
        assert doc.files_paragraphs[1].files == ["debian/*"]
        assert doc.license_paragraphs[0].license_name == "MIT"
        assert doc.license_paragraphs[1].license_name == "GPL-2.0+"

    def test_multiple_blank_lines_between_paragraphs(self):
        """Multiple blank lines between paragraphs are treated as one separator."""
        text = (
            "Format: https://www.debian.org/doc/packaging-manuals/"
            "copyright-format/1.0/\n"
            "\n"
            "\n"
            "\n"
            "Files: *\n"
            "Copyright: 2024 Author\n"
            "License: MIT\n"
        )
        doc = self.parser.parse(text)
        assert len(doc.files_paragraphs) == 1


@pytest.mark.unit
class TestDEP5ParserVersion:
    """Verify PARSER_VERSION class attribute."""

    def test_parser_version(self):
        """Parser has PARSER_VERSION = 1."""
        assert DEP5Parser.PARSER_VERSION == 1
