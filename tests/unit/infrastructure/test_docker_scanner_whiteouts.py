"""Deterministic regression tests for shared whiteout path semantics.

Layer entry names come from tar members and are always forward-slash
separated; the virtual filesystem is keyed by those names verbatim. These
tests pin that contract with fixed, nested examples so a platform-dependent
separator (``os.path`` is ``ntpath`` on Windows) cannot pass unnoticed.

**Validates: Requirements 5.2, 5.3, 6.6**
"""

from __future__ import annotations

import pytest

from debcraft.domain._whiteouts import apply_whiteouts

pytestmark = [pytest.mark.unit]


def _assert_posix_keys(vfs: dict[str, bytes]) -> None:
    """Assert the vfs is keyed by forward-slash separated tar member names."""
    assert not any("\\" in key for key in vfs), f"vfs keys must stay forward-slash separated, got: {sorted(vfs)}"


class TestDockerRegularWhiteout:
    """Regular ``.wh.<name>`` markers remove exactly their nested target."""

    def test_nested_whiteout_removes_target_only(self) -> None:
        """``usr/share/doc/.wh.README`` removes ``usr/share/doc/README``."""
        vfs = {
            "usr/share/doc/README": b"a",
            "usr/share/doc/keep": b"b",
            "usr/share/man/README": b"c",
        }

        apply_whiteouts(vfs, ["usr/share/doc/.wh.README"])

        assert set(vfs) == {"usr/share/doc/keep", "usr/share/man/README"}
        _assert_posix_keys(vfs)
        # The backslash-joined key an ntpath.join would compute must never appear.
        assert "usr/share/doc\\README" not in vfs

    def test_whiteout_marker_itself_is_removed_from_vfs(self) -> None:
        """A marker that was also stored as a file does not survive the merge."""
        marker = "usr/share/doc/.wh.README"
        vfs = {
            "usr/share/doc/README": b"a",
            marker: b"",
            "usr/share/doc/keep": b"b",
        }

        apply_whiteouts(vfs, [marker])

        assert set(vfs) == {"usr/share/doc/keep"}
        _assert_posix_keys(vfs)


class TestDockerOpaqueWhiteout:
    """Opaque ``.wh..wh..opq`` markers clear their nested directory."""

    def test_opaque_removes_lower_layer_and_keeps_siblings(self) -> None:
        """Only entries under the marker's directory are cleared."""
        vfs = {
            "usr/share/doc/README": b"a",
            "usr/share/doc/nested/LICENSE": b"b",
            "usr/share/man/x": b"c",
        }

        apply_whiteouts(vfs, ["usr/share/doc/.wh..wh..opq"])

        assert set(vfs) == {"usr/share/man/x"}
        _assert_posix_keys(vfs)

    def test_opaque_preserves_same_layer_additions(self) -> None:
        """Entries written by the same layer survive the opaque marker."""
        vfs = {
            "usr/share/doc/old": b"lower",
            "usr/share/doc/new": b"same-layer",
            "usr/share/man/x": b"c",
        }

        apply_whiteouts(
            vfs,
            ["usr/share/doc/.wh..wh..opq", "usr/share/doc/new"],
        )

        assert set(vfs) == {"usr/share/doc/new", "usr/share/man/x"}
        assert vfs["usr/share/doc/new"] == b"same-layer"
        _assert_posix_keys(vfs)


class TestDockerWhiteoutSeparatorGuard:
    """A backslash in a member name is a filename, not a separator."""

    def test_backslash_in_name_is_not_a_directory_separator(self) -> None:
        r"""``a\b`` is one legal POSIX filename and must not be split."""
        vfs = {
            "dir/a\\b": b"payload",
            "dir/keep": b"keep",
        }

        # Whiteout targeting a *different* name must leave 'a\b' alone.
        apply_whiteouts(vfs, ["dir/.wh.keep"])
        assert set(vfs) == {"dir/a\\b"}

        # Whiteout targeting the backslash-containing name removes exactly it.
        apply_whiteouts(vfs, ["dir/.wh.a\\b"])
        assert vfs == {}
