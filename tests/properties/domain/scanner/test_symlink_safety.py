"""Property-based tests for symlink containment safety.

# Feature: m6-artifact-scanners, Property 6: Symlink Containment

**Validates: Requirements 4.7**
"""

from __future__ import annotations

import os
import shutil
import tempfile
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.infrastructure.scanners.directory import DirectoryScanner

pytestmark = [pytest.mark.unit]


# ===========================================================================
# Test helpers
# ===========================================================================


def _make_scanner() -> DirectoryScanner:
    """Create a DirectoryScanner with mock ports."""
    contents_port = MagicMock()
    package_port = MagicMock()
    return DirectoryScanner(
        contents_port=contents_port,
        package_port=package_port,
    )


# ===========================================================================
# Strategies: symlink target generation
# ===========================================================================

# Characters valid for directory/file names in tests (avoid special shell chars)
_FILENAME_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789_-"


@st.composite
def st_relative_escape_path(draw: st.DrawFn) -> str:
    """Generate a relative path that escapes via ../ traversal.

    Produces paths like '../secret', '../../etc/passwd', etc.
    """
    depth = draw(st.integers(min_value=1, max_value=5))
    suffix = draw(st.text(alphabet=_FILENAME_CHARS, min_size=1, max_size=20))
    return "../" * depth + suffix


@st.composite
def st_internal_relative_path(draw: st.DrawFn) -> str:
    """Generate a relative path that stays within the root.

    Produces paths like 'subdir/file', 'a/b/c', etc.
    """
    segments = draw(st.integers(min_value=1, max_value=3))
    parts = []
    for _ in range(segments):
        part = draw(st.text(alphabet=_FILENAME_CHARS, min_size=1, max_size=10))
        parts.append(part)
    return "/".join(parts)


# ===========================================================================
# Property 6: Symlink Containment
# ===========================================================================


class TestProperty6SymlinkContainment:
    """Property 6: Symlink Containment.

    THE Directory_Scanner SHALL NOT follow symbolic links that resolve to a
    path outside the artifact root directory; such links SHALL be silently
    skipped during scanning to prevent path traversal attacks.

    **Validates: Requirements 4.7**
    """

    @settings(max_examples=50)
    @given(escape_path=st_relative_escape_path())
    def test_symlinks_outside_root_are_rejected(
        self,
        escape_path: str,
    ) -> None:
        """Symlinks resolving outside the artifact root are detected as unsafe."""
        scanner = _make_scanner()
        tmp_dir = tempfile.mkdtemp()
        try:
            # Create a subdirectory to act as the artifact root
            root = os.path.join(tmp_dir, "artifact_root")
            os.makedirs(root)

            # Create a target outside the root
            outside_dir = os.path.join(tmp_dir, "outside")
            os.makedirs(outside_dir, exist_ok=True)
            outside_target = os.path.join(outside_dir, "secret_file")
            with open(outside_target, "w") as f:
                f.write("sensitive data")

            # Create a symlink inside root that points outside
            symlink_path = os.path.join(root, "escape_link")
            os.symlink(outside_target, symlink_path)

            # _is_safe_path should return False for this symlink
            assert scanner._is_safe_path(root, symlink_path) is False
        finally:
            shutil.rmtree(tmp_dir)

    @settings(max_examples=50)
    @given(internal_path=st_internal_relative_path())
    def test_symlinks_within_root_are_accepted(
        self,
        internal_path: str,
    ) -> None:
        """Symlinks resolving within the artifact root are safe."""
        scanner = _make_scanner()
        tmp_dir = tempfile.mkdtemp()
        try:
            # Create the artifact root
            root = os.path.join(tmp_dir, "artifact_root")
            os.makedirs(root)

            # Create a target inside root
            target_path = os.path.join(root, "real_file")
            with open(target_path, "w") as f:
                f.write("legitimate content")

            # Create a symlink inside root that points to the target
            link_parent = os.path.join(root, "links")
            os.makedirs(link_parent, exist_ok=True)
            symlink_path = os.path.join(link_parent, "internal_link")
            os.symlink(target_path, symlink_path)

            # _is_safe_path should return True for this symlink
            assert scanner._is_safe_path(root, symlink_path) is True
        finally:
            shutil.rmtree(tmp_dir)

    @settings(max_examples=50)
    @given(
        depth=st.integers(min_value=1, max_value=4),
        filename=st.text(alphabet=_FILENAME_CHARS, min_size=1, max_size=15),
    )
    def test_dotdot_traversal_symlinks_rejected(
        self,
        depth: int,
        filename: str,
    ) -> None:
        """Symlinks using ../ traversal to escape root are always rejected."""
        scanner = _make_scanner()
        tmp_dir = tempfile.mkdtemp()
        try:
            # Create nested root structure
            root = os.path.join(tmp_dir, "root")
            nested = root
            for i in range(depth):
                nested = os.path.join(nested, f"level{i}")
            os.makedirs(nested)

            # Create the escape target outside root
            escape_dir = os.path.join(tmp_dir, "escaped")
            os.makedirs(escape_dir, exist_ok=True)
            escape_target = os.path.join(escape_dir, filename)
            with open(escape_target, "w") as f:
                f.write("escaped content")

            # Create a symlink that points to the escape target
            symlink_path = os.path.join(nested, "escape")
            os.symlink(escape_target, symlink_path)

            # Must be rejected
            assert scanner._is_safe_path(root, symlink_path) is False
        finally:
            shutil.rmtree(tmp_dir)

    @settings(max_examples=50)
    @given(filename=st.text(alphabet=_FILENAME_CHARS, min_size=1, max_size=15))
    def test_regular_files_always_accepted(
        self,
        filename: str,
    ) -> None:
        """Regular files (non-symlinks) within the root are always accepted."""
        scanner = _make_scanner()
        tmp_dir = tempfile.mkdtemp()
        try:
            root = os.path.join(tmp_dir, "root")
            os.makedirs(root)

            # Create a regular file inside root
            file_path = os.path.join(root, filename)
            with open(file_path, "w") as f:
                f.write("content")

            # _is_safe_path should return True for regular files
            assert scanner._is_safe_path(root, file_path) is True
        finally:
            shutil.rmtree(tmp_dir)

    @settings(max_examples=30)
    @given(filename=st.text(alphabet=_FILENAME_CHARS, min_size=1, max_size=15))
    def test_absolute_symlinks_outside_root_rejected(
        self,
        filename: str,
    ) -> None:
        """Symlinks with absolute targets outside root are rejected."""
        scanner = _make_scanner()
        tmp_dir = tempfile.mkdtemp()
        try:
            root = os.path.join(tmp_dir, "root")
            os.makedirs(root)

            # Create a target outside root using absolute path
            outside_dir = os.path.join(tmp_dir, "outside")
            os.makedirs(outside_dir, exist_ok=True)
            outside = os.path.join(outside_dir, filename)
            with open(outside, "w") as f:
                f.write("outside data")

            # Create symlink with absolute target
            symlink_path = os.path.join(root, "abs_escape")
            os.symlink(outside, symlink_path)

            assert scanner._is_safe_path(root, symlink_path) is False
        finally:
            shutil.rmtree(tmp_dir)

    @settings(max_examples=30)
    @given(filename=st.text(alphabet=_FILENAME_CHARS, min_size=1, max_size=15))
    def test_chained_symlinks_escaping_root_rejected(
        self,
        filename: str,
    ) -> None:
        """Chains of symlinks that ultimately escape root are rejected.

        Even if intermediate links stay within root, the final resolved
        path must be within root for the path to be considered safe.
        """
        scanner = _make_scanner()
        tmp_dir = tempfile.mkdtemp()
        try:
            root = os.path.join(tmp_dir, "root")
            os.makedirs(root)

            # Create target outside root
            outside_dir = os.path.join(tmp_dir, "outside")
            os.makedirs(outside_dir, exist_ok=True)
            outside = os.path.join(outside_dir, filename)
            with open(outside, "w") as f:
                f.write("escaped via chain")

            # Create intermediate symlink inside root pointing outside
            intermediate = os.path.join(root, "intermediate")
            os.symlink(outside, intermediate)

            # Create another symlink pointing to the intermediate
            final_link = os.path.join(root, "final_link")
            os.symlink(intermediate, final_link)

            # The chain resolves outside root, so both should be unsafe
            assert scanner._is_safe_path(root, intermediate) is False
            assert scanner._is_safe_path(root, final_link) is False
        finally:
            shutil.rmtree(tmp_dir)
