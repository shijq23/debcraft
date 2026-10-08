"""Deterministic table tests for tar member name normalization.

Pins the full normalization table from the bug report so the difference
between character-set stripping (``lstrip("./")``) and prefix removal
(``removeprefix("./")``) cannot be re-introduced unnoticed.
"""

from __future__ import annotations

import pytest

from debcraft.domain._archive_paths import normalize_tar_member_name

pytestmark = [pytest.mark.unit]

#: (raw member name, expected normalized name) — the authoritative table.
NORMALIZATION_TABLE = [
    ("usr/bin/foo", "usr/bin/foo"),
    ("./usr/bin/foo", "usr/bin/foo"),
    ("/usr/bin/foo", "usr/bin/foo"),
    ("//usr/bin/foo", "usr/bin/foo"),
    # The ``./`` prefix is removed first, then every leading ``/``; the OCI
    # scanner's former ``startswith``/``elif`` form left a leading slash here.
    (".//usr/bin/foo", "usr/bin/foo"),
    ("./.dockerenv", ".dockerenv"),
    (".config/app.conf", ".config/app.conf"),
    ("./.config/app.conf", ".config/app.conf"),
    ("./..data/x", "..data/x"),
    (".", "."),
    ("./", ""),
    ("...", "..."),
    ("./.bashrc", ".bashrc"),
    # The ./ prefix is removed exactly once: this is prefix normalization,
    # not canonicalization (no posixpath.normpath, no loop).
    ("././usr/bin/foo", "./usr/bin/foo"),
    ("./.wh..dockerenv", ".wh..dockerenv"),
    ("", ""),
]

#: Rows the old ``lstrip("./")`` implementation mangled.
MANGLED_BY_LSTRIP = [
    "./.dockerenv",
    ".config/app.conf",
    "./.config/app.conf",
    "./..data/x",
    ".",
    "...",
    "./.bashrc",
    "./.wh..dockerenv",
]


class TestNormalizeTarMemberName:
    """The normalization table is pinned row by row."""

    @pytest.mark.parametrize(("raw", "expected"), NORMALIZATION_TABLE)
    def test_normalization_table(self, raw: str, expected: str) -> None:
        assert normalize_tar_member_name(raw) == expected

    @pytest.mark.parametrize("raw", MANGLED_BY_LSTRIP)
    def test_differs_from_character_set_stripping(self, raw: str) -> None:
        """Names the buggy ``lstrip("./")`` mangled now normalize differently.

        This names the regression directly: ``lstrip`` strips a set of
        characters, so every leading dot and slash went away.
        """
        assert normalize_tar_member_name(raw) != raw.lstrip("./")

    def test_dot_prefixed_first_component_is_preserved(self) -> None:
        """A dotfile-rooted path keeps its leading dot."""
        assert normalize_tar_member_name("./.dockerenv") == ".dockerenv"
        assert normalize_tar_member_name("./.wh..dockerenv").startswith(".wh.")

    def test_parent_segments_are_not_resolved(self) -> None:
        """``..`` is left alone — this is not a canonicalizer."""
        assert normalize_tar_member_name("./usr/../etc/passwd") == "usr/../etc/passwd"
