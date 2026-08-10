"""Unit tests for SourcesParser format method round-trip correctness."""

import pytest

from debcraft.domain.indexer.sources_parser import SourcesParser
from debcraft.domain.indexer.values import SourcePackageMetadata


@pytest.mark.unit
class TestSourcesParserFormat:
    """Verify the format method produces valid stanza output and round-trips correctly."""

    def setup_method(self):
        self.parser = SourcesParser()

    def test_format_required_fields_only(self):
        """Format with only required fields produces Package and Version lines."""
        metadata = SourcePackageMetadata(name="hello", version="2.10-3")
        result = self.parser.format(metadata)

        assert "Package: hello" in result
        assert "Version: 2.10-3" in result
        # Required fields should be the first two lines
        lines = result.split("\n")
        assert lines[0] == "Package: hello"
        assert lines[1] == "Version: 2.10-3"

    def test_format_all_optional_fields(self):
        """Format includes all non-empty optional fields."""
        metadata = SourcePackageMetadata(
            name="libfoo",
            version="1.0-1",
            maintainer="John Doe <john@example.com>",
            uploaders=["Alice <alice@example.com>", "Bob <bob@example.com>"],
            section="libs",
            homepage="https://example.com",
            build_depends="debhelper (>= 12), libbar-dev",
            binary_packages=["libfoo0", "libfoo-dev"],
        )
        result = self.parser.format(metadata)

        assert "Maintainer: John Doe <john@example.com>" in result
        assert "Uploaders: Alice <alice@example.com>, Bob <bob@example.com>" in result
        assert "Section: libs" in result
        assert "Homepage: https://example.com" in result
        assert "Build-Depends: debhelper (>= 12), libbar-dev" in result
        assert "Binary: libfoo0, libfoo-dev" in result

    def test_format_omits_none_fields(self):
        """Format does not include fields that are None or empty lists."""
        metadata = SourcePackageMetadata(
            name="minimal",
            version="0.1",
            maintainer=None,
            uploaders=[],
            section=None,
            homepage=None,
            build_depends=None,
            binary_packages=[],
        )
        result = self.parser.format(metadata)

        assert "Maintainer" not in result
        assert "Uploaders" not in result
        assert "Section" not in result
        assert "Homepage" not in result
        assert "Build-Depends" not in result
        assert "Binary" not in result

    def test_round_trip_minimal(self):
        """Format then parse produces equivalent metadata (minimal case)."""
        original = SourcePackageMetadata(name="hello", version="2.10-3")
        stanza = self.parser.format(original)
        parsed = self.parser.parse(stanza)

        assert len(parsed) == 1
        assert parsed[0] == original

    def test_round_trip_full(self):
        """Format then parse produces equivalent metadata (all fields)."""
        original = SourcePackageMetadata(
            name="libfoo",
            version="1.0-1",
            maintainer="John Doe <john@example.com>",
            uploaders=["Alice <alice@example.com>", "Bob <bob@example.com>"],
            section="libs",
            homepage="https://example.com",
            build_depends="debhelper (>= 12), libbar-dev",
            binary_packages=["libfoo0", "libfoo-dev"],
        )
        stanza = self.parser.format(original)
        parsed = self.parser.parse(stanza)

        assert len(parsed) == 1
        assert parsed[0] == original

    def test_round_trip_with_single_uploader(self):
        """Round-trip with a single uploader entry."""
        original = SourcePackageMetadata(
            name="pkg",
            version="2.0",
            uploaders=["Jane <jane@example.com>"],
        )
        stanza = self.parser.format(original)
        parsed = self.parser.parse(stanza)

        assert len(parsed) == 1
        assert parsed[0] == original

    def test_round_trip_with_single_binary(self):
        """Round-trip with a single binary package."""
        original = SourcePackageMetadata(
            name="src-pkg",
            version="3.1-2",
            binary_packages=["bin-pkg"],
        )
        stanza = self.parser.format(original)
        parsed = self.parser.parse(stanza)

        assert len(parsed) == 1
        assert parsed[0] == original
