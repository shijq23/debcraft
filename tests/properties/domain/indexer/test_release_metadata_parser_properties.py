"""Property-based tests for ReleaseMetadataParser.

# Feature: repository-indexer, Property 9: Release metadata extraction with suite fallback

**Validates: Requirements 4.1, 4.2**
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.domain.indexer.errors import ReleaseParseError
from debcraft.domain.indexer.release_metadata_parser import ReleaseMetadataParser

# ===========================================================================
# Strategies for generating Release file content
# ===========================================================================

# Valid suite/codename values: short text without newlines, colons, or leading/trailing whitespace
_FIELD_VALUE_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789-."


def _release_field_value() -> st.SearchStrategy[str]:
    """Generate a valid Release field value (short text, no newlines/colons)."""
    return st.text(
        alphabet=_FIELD_VALUE_CHARS,
        min_size=1,
        max_size=30,
    ).filter(lambda s: s.strip() == s and s != "")


def _build_release_content(
    suite: str | None = None,
    codename: str | None = None,
) -> str:
    """Build Release file content with optional Suite and Codename fields."""
    lines: list[str] = []
    # Always include Origin and Label for realism
    lines.append("Origin: Debian")
    lines.append("Label: Debian")
    if suite is not None:
        lines.append(f"Suite: {suite}")
    if codename is not None:
        lines.append(f"Codename: {codename}")
    lines.append("Architectures: amd64 arm64 i386")
    lines.append("Components: main contrib non-free")
    lines.append("Date: Thu, 01 Jan 2024 00:00:00 UTC")
    return "\n".join(lines)


# ===========================================================================
# Property 9: Release metadata extraction with suite fallback
# ===========================================================================


@pytest.mark.unit
class TestProperty9ReleaseMetadataExtractionWithSuiteFallback:
    """Property 9: Release metadata extraction with suite fallback.

    For any Release file content containing at least one of Suite or Codename,
    parsing SHALL produce a RepositoryIdentity where `suite` equals the Suite
    field value if present, otherwise the Codename field value.

    **Validates: Requirements 4.1, 4.2**
    """

    @given(
        scenario=st.sampled_from(["both", "suite_only", "codename_only"]),
        suite_value=_release_field_value(),
        codename_value=_release_field_value(),
    )
    def test_suite_field_logic(
        self,
        scenario: str,
        suite_value: str,
        codename_value: str,
    ) -> None:
        """Suite field follows fallback rules based on scenario.

        - both: suite == Suite field value, codename == Codename field value
        - suite_only: suite == Suite value, codename is None
        - codename_only: suite == Codename value (fallback)
        """
        parser = ReleaseMetadataParser()

        if scenario == "both":
            content = _build_release_content(suite=suite_value, codename=codename_value)
            result = parser.parse(content)
            assert result.suite == suite_value, (
                f"With both fields, suite should equal Suite value '{suite_value}', got '{result.suite}'"
            )
            assert result.codename == codename_value, (
                f"With both fields, codename should equal Codename value '{codename_value}', got '{result.codename}'"
            )

        elif scenario == "suite_only":
            content = _build_release_content(suite=suite_value, codename=None)
            result = parser.parse(content)
            assert result.suite == suite_value, (
                f"With only Suite, suite should equal Suite value '{suite_value}', got '{result.suite}'"
            )
            assert result.codename is None, f"With only Suite, codename should be None, got '{result.codename}'"

        else:  # codename_only
            content = _build_release_content(suite=None, codename=codename_value)
            result = parser.parse(content)
            assert result.suite == codename_value, (
                f"With only Codename, suite should fall back to Codename value '{codename_value}', got '{result.suite}'"
            )

    @given(
        suite_value=_release_field_value(),
        codename_value=_release_field_value(),
    )
    def test_both_present_suite_takes_precedence(
        self,
        suite_value: str,
        codename_value: str,
    ) -> None:
        """When both Suite and Codename are present, suite equals Suite value."""
        parser = ReleaseMetadataParser()
        content = _build_release_content(suite=suite_value, codename=codename_value)
        result = parser.parse(content)

        assert result.suite == suite_value
        assert result.codename == codename_value

    @given(suite_value=_release_field_value())
    def test_suite_only_no_codename(self, suite_value: str) -> None:
        """When only Suite is present, suite equals Suite value and codename is None."""
        parser = ReleaseMetadataParser()
        content = _build_release_content(suite=suite_value, codename=None)
        result = parser.parse(content)

        assert result.suite == suite_value
        assert result.codename is None

    @given(codename_value=_release_field_value())
    def test_codename_only_falls_back(self, codename_value: str) -> None:
        """When only Codename is present, suite falls back to Codename value."""
        parser = ReleaseMetadataParser()
        content = _build_release_content(suite=None, codename=codename_value)
        result = parser.parse(content)

        assert result.suite == codename_value

    def test_neither_present_raises_error(self) -> None:
        """When neither Suite nor Codename is present, ReleaseParseError is raised."""
        parser = ReleaseMetadataParser()
        content = _build_release_content(suite=None, codename=None)

        with pytest.raises(ReleaseParseError):
            parser.parse(content)
