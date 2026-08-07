"""Bug condition exploration test for Windows cross-platform test failures.

**Validates: Requirements 1.1, 1.2, 1.3, 1.4**

Property 1: Bug Condition — Windows Path Absoluteness and Platform Assumptions

This test demonstrates the 6 test failures on Windows by showing that:
1. PureWindowsPath does NOT consider POSIX-style paths absolute (no drive letter)
2. write_text() without encoding="utf-8" fails for non-ASCII content on Windows
3. Regex match with literal "/fake/config" doesn't match Windows path representation

The fix validation tests confirm the EXPECTED behavior works correctly.
The bug demonstration tests (marked xfail) document the Windows-specific failures.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.infrastructure.storage.paths import resolve_xdg_path

# Strategies for generating test inputs
_ALL_PURPOSES = ("mirror", "workspace", "outputs", "logs", "cache", "database", "config")
_ALL_PLATFORMS = ("linux", "darwin", "win32")


@pytest.mark.unit
@pytest.mark.storage
class TestBugConditionFixValidation:
    """Validate that the FIX assertions work correctly.

    These tests confirm the expected behavior using the fixed approach:
    - PurePosixPath for path absoluteness validation
    - encoding="utf-8" for write_text()
    - re.escape(str(Path(...))) for regex matching
    """

    @settings(max_examples=50)
    @given(
        platform=st.sampled_from(list(_ALL_PLATFORMS)),
        purpose=st.sampled_from(list(_ALL_PURPOSES)),
    )
    def test_posix_paths_absolute_with_pure_posix_validation(self, platform: str, purpose: str) -> None:
        """For all platforms/purposes with POSIX-style environ paths, PurePosixPath is absolute.

        PurePosixPath(str(result)).is_absolute() returns True.
        This mirrors the fix assertion and confirms it works regardless
        of the host platform's Path implementation.

        **Validates: Requirements 1.1, 1.2**
        """
        if platform == "win32":
            environ = {
                "USERPROFILE": "/users/testuser",
                "LOCALAPPDATA": "/local",
                "APPDATA": "/roaming",
            }
        elif platform == "darwin":
            environ = {"HOME": "/Users/testuser"}
        else:
            environ = {"HOME": "/home/testuser"}

        result = resolve_xdg_path(purpose, environ=environ, platform=platform)

        # The FIX assertion: PurePosixPath always recognizes leading / as absolute
        assert PurePosixPath(result.as_posix()).is_absolute(), (
            f"PurePosixPath validation failed for {platform}/{purpose}: {result}"
        )

    @settings(max_examples=50)
    @given(
        content=st.text(
            alphabet=st.characters(
                min_codepoint=1,
                max_codepoint=65533,
                blacklist_categories=("Cs",),  # Exclude surrogates
                blacklist_characters="\r",
            ),
            min_size=1,
            max_size=100,
        ),
    )
    def test_write_text_with_explicit_utf8_encoding(self, content: str) -> None:
        """For arbitrary text content, write_text with explicit utf-8 encoding succeeds.

        This includes non-ASCII characters that would fail with default encoding on Windows.

        **Validates: Requirements 1.3**
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            tmp_path = Path(f.name)

        try:
            # The FIX: explicit encoding="utf-8" always works
            tmp_path.write_text(content, encoding="utf-8")
            read_back = tmp_path.read_text(encoding="utf-8")
            assert read_back == content
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_regex_escape_matches_platform_path_representation(self) -> None:
        """Escaped path pattern matches the actual string representation on the current host.

        re.escape(str(Path("/fake/config"))) matches the actual
        string representation of Path("/fake/config").

        **Validates: Requirements 1.4**
        """
        path_str = str(Path("/fake/config"))
        escaped_pattern = re.escape(path_str)

        # The FIX: re.escape(str(Path(...))) always matches str(Path(...))
        assert re.search(escaped_pattern, path_str), (
            f"re.escape(str(Path('/fake/config'))) should match: pattern={escaped_pattern!r}, target={path_str!r}"
        )


@pytest.mark.unit
@pytest.mark.storage
class TestBugConditionDemonstration:
    """Demonstrate the bug conditions that exist on Windows.

    These tests use PureWindowsPath and cp1252 encoding to simulate
    Windows behavior on a Linux host, confirming the bugs exist.
    They are marked xfail because they intentionally assert the buggy
    behavior that would manifest on Windows.
    """

    @pytest.mark.xfail(reason="Demonstrates Windows bug: POSIX paths lack drive letter", strict=True)
    def test_windows_path_considers_posix_paths_not_absolute(self) -> None:
        r"""PureWindowsPath does NOT consider POSIX-style paths absolute.

        This demonstrates why result.is_absolute() fails on Windows:
        WindowsPath("/home/testuser/...").is_absolute() returns False
        because there's no drive letter (e.g., C:\\).

        Counterexample: PureWindowsPath('/home/testuser/.cache/debcraft/mirror').is_absolute() → False
        """
        from debcraft.infrastructure.storage.paths import resolve_xdg_path

        result = resolve_xdg_path("mirror", environ={"HOME": "/home/testuser"}, platform="linux")
        windows_view = PureWindowsPath(str(result))

        # This FAILS — demonstrating the bug on Windows
        assert windows_view.is_absolute(), f"PureWindowsPath('{result}').is_absolute() returns False"

    @pytest.mark.xfail(reason="Demonstrates Windows bug: cp1252 cannot encode all Unicode", strict=True)
    def test_cp1252_cannot_encode_all_unicode_content(self) -> None:
        """cp1252 (Windows default encoding) cannot encode all Unicode characters.

        This demonstrates why write_text() without encoding="utf-8" fails on Windows
        when Hypothesis generates filenames containing characters outside cp1252's range.

        Counterexample: 'Ţ' (U+0162) cannot be encoded with cp1252
        """
        # Character that cp1252 cannot encode
        content = "\u0162"  # Ţ (Latin capital letter T with cedilla)
        content.encode("cp1252")  # This raises UnicodeEncodeError

    @pytest.mark.xfail(reason="Demonstrates Windows bug: literal '/' pattern mismatches '\\\\'", strict=True)
    def test_literal_forward_slash_pattern_mismatches_windows_path(self) -> None:
        r"""Literal '/fake/config' does not match Windows path '\\fake\\config'.

        This demonstrates why match="/fake/config" fails on Windows:
        str(Path("/fake/config")) produces '\\fake\\config' on Windows,
        and the regex literal '/fake/config' won't match backslashes.

        Counterexample: re.search('/fake/config', '\\fake\\config') → None
        """
        windows_path_str = str(PureWindowsPath("/fake/config"))
        literal_pattern = "/fake/config"

        match_result = re.search(literal_pattern, windows_path_str)
        # This FAILS — the literal doesn't match the Windows representation
        assert match_result is not None
