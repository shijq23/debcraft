"""Unit tests for SPDXPrinter.

Tests cover formatting of each node type: SimpleNode, WithNode, AndNode, OrNode,
including or-later suffix and parenthesization of Or children within And nodes.
"""

from __future__ import annotations

import pytest

from debcraft.domain.package_intelligence.spdx_printer import SPDXPrinter
from debcraft.domain.package_intelligence.values import (
    AndNode,
    OrNode,
    SimpleNode,
    WithNode,
)


@pytest.mark.unit
class TestSPDXPrinterSimpleNode:
    """Verify SimpleNode formatting."""

    def setup_method(self):
        self.printer = SPDXPrinter()

    def test_simple_identifier(self):
        """SimpleNode prints as the identifier."""
        node = SimpleNode(identifier="MIT")
        assert self.printer.print(node) == "MIT"

    def test_identifier_with_version(self):
        """SimpleNode with version prints correctly."""
        node = SimpleNode(identifier="GPL-2.0-only")
        assert self.printer.print(node) == "GPL-2.0-only"

    def test_or_later_suffix(self):
        """SimpleNode with or_later=True appends +."""
        node = SimpleNode(identifier="GPL-2.0", or_later=True)
        assert self.printer.print(node) == "GPL-2.0+"

    def test_or_later_false(self):
        """SimpleNode with or_later=False has no + suffix."""
        node = SimpleNode(identifier="Apache-2.0", or_later=False)
        assert self.printer.print(node) == "Apache-2.0"

    def test_license_ref(self):
        """LicenseRef identifier prints as-is."""
        node = SimpleNode(identifier="LicenseRef-custom-1")
        assert self.printer.print(node) == "LicenseRef-custom-1"

    def test_document_ref(self):
        """DocumentRef identifier prints as-is."""
        node = SimpleNode(identifier="DocumentRef-ext:LicenseRef-Custom")
        assert self.printer.print(node) == "DocumentRef-ext:LicenseRef-Custom"


@pytest.mark.unit
class TestSPDXPrinterWithNode:
    """Verify WithNode formatting."""

    def setup_method(self):
        self.printer = SPDXPrinter()

    def test_with_expression(self):
        """WithNode prints as '<license> WITH <exception>'."""
        node = WithNode(
            license=SimpleNode(identifier="GPL-2.0-only"),
            exception="Classpath-exception-2.0",
        )
        assert self.printer.print(node) == "GPL-2.0-only WITH Classpath-exception-2.0"

    def test_with_or_later_license(self):
        """WithNode with or-later license prints correctly."""
        node = WithNode(
            license=SimpleNode(identifier="GPL-2.0", or_later=True),
            exception="Bison-exception-2.2",
        )
        assert self.printer.print(node) == "GPL-2.0+ WITH Bison-exception-2.2"


@pytest.mark.unit
class TestSPDXPrinterAndNode:
    """Verify AndNode formatting and parenthesization."""

    def setup_method(self):
        self.printer = SPDXPrinter()

    def test_simple_and(self):
        """AndNode with simple children prints '<left> AND <right>'."""
        node = AndNode(
            left=SimpleNode(identifier="MIT"),
            right=SimpleNode(identifier="Apache-2.0"),
        )
        assert self.printer.print(node) == "MIT AND Apache-2.0"

    def test_and_wraps_or_child_left(self):
        """AndNode wraps left OrNode child in parentheses."""
        node = AndNode(
            left=OrNode(
                left=SimpleNode(identifier="MIT"),
                right=SimpleNode(identifier="ISC"),
            ),
            right=SimpleNode(identifier="Apache-2.0"),
        )
        assert self.printer.print(node) == "(MIT OR ISC) AND Apache-2.0"

    def test_and_wraps_or_child_right(self):
        """AndNode wraps right OrNode child in parentheses."""
        node = AndNode(
            left=SimpleNode(identifier="Apache-2.0"),
            right=OrNode(
                left=SimpleNode(identifier="MIT"),
                right=SimpleNode(identifier="ISC"),
            ),
        )
        assert self.printer.print(node) == "Apache-2.0 AND (MIT OR ISC)"

    def test_and_wraps_both_or_children(self):
        """AndNode wraps both OrNode children in parentheses."""
        node = AndNode(
            left=OrNode(
                left=SimpleNode(identifier="MIT"),
                right=SimpleNode(identifier="ISC"),
            ),
            right=OrNode(
                left=SimpleNode(identifier="Apache-2.0"),
                right=SimpleNode(identifier="BSD-2-Clause"),
            ),
        )
        assert self.printer.print(node) == "(MIT OR ISC) AND (Apache-2.0 OR BSD-2-Clause)"

    def test_and_does_not_wrap_and_child(self):
        """AndNode does not wrap nested AndNode children."""
        node = AndNode(
            left=AndNode(
                left=SimpleNode(identifier="MIT"),
                right=SimpleNode(identifier="ISC"),
            ),
            right=SimpleNode(identifier="Apache-2.0"),
        )
        assert self.printer.print(node) == "MIT AND ISC AND Apache-2.0"

    def test_and_does_not_wrap_with_child(self):
        """AndNode does not wrap WithNode children."""
        node = AndNode(
            left=WithNode(
                license=SimpleNode(identifier="GPL-2.0-only"),
                exception="Classpath-exception-2.0",
            ),
            right=SimpleNode(identifier="MIT"),
        )
        assert self.printer.print(node) == "GPL-2.0-only WITH Classpath-exception-2.0 AND MIT"


@pytest.mark.unit
class TestSPDXPrinterOrNode:
    """Verify OrNode formatting."""

    def setup_method(self):
        self.printer = SPDXPrinter()

    def test_simple_or(self):
        """OrNode with simple children prints '<left> OR <right>'."""
        node = OrNode(
            left=SimpleNode(identifier="MIT"),
            right=SimpleNode(identifier="Apache-2.0"),
        )
        assert self.printer.print(node) == "MIT OR Apache-2.0"

    def test_or_does_not_wrap_and_child(self):
        """OrNode does not wrap AndNode children (AND binds tighter)."""
        node = OrNode(
            left=AndNode(
                left=SimpleNode(identifier="MIT"),
                right=SimpleNode(identifier="ISC"),
            ),
            right=SimpleNode(identifier="Apache-2.0"),
        )
        assert self.printer.print(node) == "MIT AND ISC OR Apache-2.0"

    def test_or_does_not_wrap_nested_or(self):
        """OrNode does not wrap nested OrNode children."""
        node = OrNode(
            left=OrNode(
                left=SimpleNode(identifier="MIT"),
                right=SimpleNode(identifier="ISC"),
            ),
            right=SimpleNode(identifier="Apache-2.0"),
        )
        assert self.printer.print(node) == "MIT OR ISC OR Apache-2.0"


@pytest.mark.unit
class TestSPDXPrinterComplex:
    """Verify complex expression formatting."""

    def setup_method(self):
        self.printer = SPDXPrinter()

    def test_nested_and_or_precedence(self):
        """Complex expression with mixed AND/OR preserves precedence."""
        # (MIT OR ISC) AND Apache-2.0 OR BSD-2-Clause
        node = OrNode(
            left=AndNode(
                left=OrNode(
                    left=SimpleNode(identifier="MIT"),
                    right=SimpleNode(identifier="ISC"),
                ),
                right=SimpleNode(identifier="Apache-2.0"),
            ),
            right=SimpleNode(identifier="BSD-2-Clause"),
        )
        assert self.printer.print(node) == "(MIT OR ISC) AND Apache-2.0 OR BSD-2-Clause"

    def test_with_inside_and(self):
        """WithNode inside AndNode does not get parenthesized."""
        node = AndNode(
            left=WithNode(
                license=SimpleNode(identifier="GPL-2.0-only"),
                exception="Classpath-exception-2.0",
            ),
            right=WithNode(
                license=SimpleNode(identifier="Apache-2.0"),
                exception="LLVM-exception",
            ),
        )
        assert (
            self.printer.print(node) == "GPL-2.0-only WITH Classpath-exception-2.0 AND Apache-2.0 WITH LLVM-exception"
        )

    def test_or_later_in_compound(self):
        """Or-later suffix in a compound expression."""
        node = OrNode(
            left=SimpleNode(identifier="GPL-2.0", or_later=True),
            right=SimpleNode(identifier="MIT"),
        )
        assert self.printer.print(node) == "GPL-2.0+ OR MIT"
