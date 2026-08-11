"""Property-based tests for DEP-5 round-trip and printer invariants.

# Feature: package-intelligence, Property 1: DEP-5 Parse–Print Round-Trip
# Feature: package-intelligence, Property 2: DEP-5 Printer Trailing Newline Invariant

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.7, 3.1, 3.2, 3.3, 3.4, 3.5**
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.domain.package_intelligence.dep5_parser import DEP5Parser
from debcraft.domain.package_intelligence.dep5_printer import DEP5Printer
from debcraft.domain.package_intelligence.values import (
    DEP5Document,
    DEP5FilesParagraph,
    DEP5Header,
    DEP5LicenseParagraph,
)

# ===========================================================================
# Strategies for generating valid DEP5Document objects
# ===========================================================================

# Characters safe for single-line field values (no newlines, no leading space/tab)
_SAFE_FIELD_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 -_./:@+()[]<>,;="


def _single_line_text(min_size: int = 1, max_size: int = 40) -> st.SearchStrategy[str]:
    """Generate non-empty single-line text suitable for DEP-5 field values.

    Must not have leading or trailing whitespace (parser strips these).
    """
    return st.text(
        alphabet=st.sampled_from(sorted(_SAFE_FIELD_CHARS)),
        min_size=min_size,
        max_size=max_size,
    ).filter(lambda s: s.strip() != "" and s == s.strip())


def _multiline_text() -> st.SearchStrategy[str]:
    """Generate multiline text with continuation lines.

    Each line is non-empty (empty lines become ' .' in DEP-5 format).
    Lines do not start with space or tab (the printer adds those).
    A lone '.' is excluded because ' .' in continuation means empty line.
    """
    line = _single_line_text(min_size=1, max_size=30).filter(lambda s: s != ".")
    return st.lists(
        line,
        min_size=2,
        max_size=4,
    ).map("\n".join)


def _license_name() -> st.SearchStrategy[str]:
    """Generate a short license identifier string."""
    return st.sampled_from(
        [
            "MIT",
            "Apache-2.0",
            "GPL-2.0-only",
            "GPL-3.0-or-later",
            "BSD-2-Clause",
            "BSD-3-Clause",
            "ISC",
            "MPL-2.0",
            "LGPL-2.1-only",
            "Artistic-2.0",
        ]
    )


def _file_pattern() -> st.SearchStrategy[str]:
    """Generate a file glob pattern for Files paragraphs."""
    return st.sampled_from(
        [
            "*",
            "src/*",
            "debian/*",
            "*.py",
            "docs/*.rst",
            "lib/**/*.c",
            "tests/*",
        ]
    )


def _format_url() -> st.SearchStrategy[str]:
    """Generate a valid Format URL for the DEP-5 header."""
    return st.sampled_from(
        [
            "https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/",
            "http://www.debian.org/doc/packaging-manuals/copyright-format/1.0/",
        ]
    )


@st.composite
def dep5_header_strategy(draw: st.DrawFn) -> DEP5Header:
    """Generate a valid DEP5Header."""
    format_url = draw(_format_url())
    upstream_name = draw(st.one_of(st.none(), _single_line_text()))
    upstream_contact = draw(st.one_of(st.none(), _single_line_text()))
    source = draw(st.one_of(st.none(), _single_line_text()))
    comment = draw(st.one_of(st.none(), _single_line_text()))

    return DEP5Header(
        format_url=format_url,
        upstream_name=upstream_name,
        upstream_contact=upstream_contact,
        source=source,
        comment=comment,
    )


@st.composite
def dep5_files_paragraph_strategy(draw: st.DrawFn) -> DEP5FilesParagraph:
    """Generate a valid DEP5FilesParagraph."""
    files = draw(st.lists(_file_pattern(), min_size=1, max_size=3))
    copyright_text = draw(_single_line_text())
    license_name = draw(_license_name())
    license_text = draw(st.one_of(st.none(), _multiline_text()))
    comment = draw(st.one_of(st.none(), _single_line_text()))

    return DEP5FilesParagraph(
        files=files,
        copyright=copyright_text,
        license_name=license_name,
        license_text=license_text,
        comment=comment,
    )


@st.composite
def dep5_license_paragraph_strategy(draw: st.DrawFn) -> DEP5LicenseParagraph:
    """Generate a valid DEP5LicenseParagraph."""
    license_name = draw(_license_name())
    license_text = draw(_multiline_text())
    comment = draw(st.one_of(st.none(), _single_line_text()))

    return DEP5LicenseParagraph(
        license_name=license_name,
        license_text=license_text,
        comment=comment,
    )


@st.composite
def dep5_document_strategy(draw: st.DrawFn) -> DEP5Document:
    """Generate a valid DEP5Document with header, files, and license paragraphs."""
    header = draw(dep5_header_strategy())
    files_paragraphs = draw(st.lists(dep5_files_paragraph_strategy(), min_size=0, max_size=3))
    license_paragraphs = draw(st.lists(dep5_license_paragraph_strategy(), min_size=0, max_size=2))

    return DEP5Document(
        header=header,
        files_paragraphs=files_paragraphs,
        license_paragraphs=license_paragraphs,
    )


# ===========================================================================
# Property 1: DEP-5 Parse–Print Round-Trip
# ===========================================================================


@pytest.mark.unit
@pytest.mark.package
class TestProperty1DEP5ParsePrintRoundTrip:
    """Property 1: DEP-5 Parse–Print Round-Trip.

    For any valid DEP5Document, printing it with DEP5Printer and then
    parsing the resulting text with DEP5Parser SHALL produce a DEP5Document
    that is structurally equal to the original (same paragraph types in
    same order, same field names and values in each paragraph).

    **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.7, 3.1, 3.2, 3.3, 3.4**
    """

    @settings(max_examples=100)
    @given(doc=dep5_document_strategy())
    def test_dep5_round_trip(self, doc: DEP5Document) -> None:
        """Print → parse produces structurally equal document."""
        printer = DEP5Printer()
        parser = DEP5Parser()

        printed = printer.print(doc)
        parsed = parser.parse(printed)

        assert parsed == doc, (
            f"Round-trip failed!\nOriginal doc: {doc!r}\nPrinted text:\n{printed}\nParsed back: {parsed!r}"
        )


# ===========================================================================
# Property 2: DEP-5 Printer Trailing Newline Invariant
# ===========================================================================


@pytest.mark.unit
@pytest.mark.package
class TestProperty2DEP5PrinterTrailingNewline:
    r"""Property 2: DEP-5 Printer Trailing Newline Invariant.

    For any valid DEP5Document, the output of DEP5Printer SHALL end with
    exactly one newline character (`\\n`) and SHALL NOT end with two or more
    consecutive newline characters.

    **Validates: Requirements 3.5**
    """

    @settings(max_examples=100)
    @given(doc=dep5_document_strategy())
    def test_dep5_printer_trailing_newline(self, doc: DEP5Document) -> None:
        """Output ends with exactly one newline, not two or more."""
        printer = DEP5Printer()
        output = printer.print(doc)

        assert output.endswith("\n"), f"Output must end with a newline character.\nOutput tail: {output[-20:]!r}"
        assert not output.endswith("\n\n"), f"Output must not end with double newline.\nOutput tail: {output[-20:]!r}"
