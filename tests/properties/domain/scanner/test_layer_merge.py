"""Property-based tests for layer merge with whiteout handling.

**Validates: Requirements 5.2, 5.3, 6.6**

Property 7: Layer Merge with Whiteouts
  Generate layer sequences with regular files and whiteout markers; assert
  whited-out files do not appear in merged filesystem; assert opaque whiteouts
  clear entire directory from lower layers; assert non-targeted files are
  preserved; assert same-layer files survive opaque whiteouts.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.infrastructure.scanners.docker import DockerScanner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_docker_scanner() -> DockerScanner:
    """Create a DockerScanner instance with dummy ports (not used for whiteouts)."""

    class _DummyPort:
        pass

    return DockerScanner(
        contents_port=_DummyPort(),  # type: ignore[arg-type]
        package_port=_DummyPort(),  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Valid path segments: simple lowercase names without special chars
_PATH_SEGMENT = st.text(
    st.characters(whitelist_categories=("Ll", "Nd"), whitelist_characters="_"),
    min_size=1,
    max_size=12,
)


@st.composite
def st_file_path(draw: st.DrawFn) -> str:
    """Generate a simple file path like 'dir/subdir/file'.

    Paths have 1-3 directory segments plus a filename segment.
    Names never start with '.wh.' to avoid accidental whiteout markers.
    """
    depth = draw(st.integers(min_value=0, max_value=2))
    segments = [draw(_PATH_SEGMENT) for _ in range(depth + 1)]
    # Ensure no segment accidentally matches a whiteout prefix
    segments = [s if not s.startswith(".wh.") else "x" + s for s in segments]
    return "/".join(segments)


@st.composite
def st_directory_path(draw: st.DrawFn) -> str:
    """Generate a directory path (1-2 segments)."""
    depth = draw(st.integers(min_value=1, max_value=2))
    segments = [draw(_PATH_SEGMENT) for _ in range(depth)]
    segments = [s if not s.startswith(".wh.") else "x" + s for s in segments]
    return "/".join(segments)


@st.composite
def st_base_vfs(draw: st.DrawFn) -> dict[str, bytes]:
    """Generate a base virtual filesystem with 1-20 files."""
    num_files = draw(st.integers(min_value=1, max_value=20))
    vfs: dict[str, bytes] = {}
    for _ in range(num_files):
        path = draw(st_file_path())
        content = draw(st.binary(min_size=0, max_size=64))
        vfs[path] = content
    return vfs


@st.composite
def st_directory_with_files(draw: st.DrawFn) -> tuple[str, dict[str, bytes]]:
    """Generate a directory and files under it.

    Returns a tuple of (directory_path, vfs_dict_of_files_under_that_dir).
    """
    dir_path = draw(st_directory_path())
    num_files = draw(st.integers(min_value=1, max_value=8))
    vfs: dict[str, bytes] = {}
    for _ in range(num_files):
        filename = draw(_PATH_SEGMENT)
        file_path = f"{dir_path}/{filename}"
        vfs[file_path] = draw(st.binary(min_size=0, max_size=32))
    return dir_path, vfs


# ---------------------------------------------------------------------------
# Property 7: Layer Merge with Whiteouts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProperty7LayerMergeWhiteouts:
    """Property 7: Layer Merge with Whiteouts.

    Tests the _apply_whiteouts method from DockerScanner for correct
    OCI/Docker whiteout semantics.
    """

    @given(data=st.data())
    def test_regular_whiteout_removes_target_file(self, data: st.DataObject) -> None:
        """After applying .wh.X, file X is NOT in the merged vfs.

        **Validates: Requirements 5.3, 6.6**
        """
        scanner = _make_docker_scanner()

        # Generate a base VFS with at least one file
        vfs = data.draw(st_base_vfs())
        if not vfs:
            return

        # Pick a file from the VFS to whiteout
        target_path = data.draw(st.sampled_from(sorted(vfs.keys())))

        # Build the whiteout entry for the target
        if "/" in target_path:
            parent_dir = target_path.rsplit("/", 1)[0]
            target_name = target_path.rsplit("/", 1)[1]
            whiteout_entry = f"{parent_dir}/.wh.{target_name}"
        else:
            whiteout_entry = f".wh.{target_path}"

        # Layer entries include: just the whiteout marker
        layer_entries = [whiteout_entry]

        # Apply whiteouts
        scanner._apply_whiteouts(vfs, layer_entries)

        # Assert the target file is removed
        assert target_path not in vfs, (
            f"File '{target_path}' should have been removed by whiteout '{whiteout_entry}' but is still present in vfs"
        )

    @given(data=st.data())
    def test_opaque_whiteout_clears_directory_from_lower_layers(self, data: st.DataObject) -> None:
        """After applying .wh..wh..opq in a directory, ALL files from lower layers are removed.

        **Validates: Requirements 5.3, 6.6**
        """
        scanner = _make_docker_scanner()

        # Generate a directory with files (simulating lower layer content)
        dir_path, dir_files = data.draw(st_directory_with_files())

        # Create VFS with these files (from "lower" layer)
        vfs: dict[str, bytes] = dict(dir_files)

        # Also add some files outside this directory (should be preserved)
        other_vfs = data.draw(st_base_vfs())
        # Filter out files that happen to be under our target directory
        other_prefix = dir_path + "/"
        other_files = {k: v for k, v in other_vfs.items() if not k.startswith(other_prefix)}
        vfs.update(other_files)

        # The opaque whiteout marker for this directory
        opaque_entry = f"{dir_path}/.wh..wh..opq"

        # Layer entries: just the opaque whiteout (no new files in this layer)
        layer_entries = [opaque_entry]

        # Apply whiteouts
        scanner._apply_whiteouts(vfs, layer_entries)

        # Assert ALL files that were under dir_path are gone
        prefix = dir_path + "/"
        remaining_in_dir = [k for k in vfs if k.startswith(prefix)]
        assert remaining_in_dir == [], (
            f"Expected all files under '{dir_path}/' to be removed by opaque whiteout, but found: {remaining_in_dir}"
        )

        # Assert files outside the directory are preserved
        for path in other_files:
            assert path in vfs, (
                f"File '{path}' outside the opaque whiteout directory should be preserved but was removed"
            )

    @given(data=st.data())
    def test_non_targeted_files_preserved(self, data: st.DataObject) -> None:
        """Files NOT targeted by whiteouts remain in the vfs.

        **Validates: Requirements 5.2, 5.3**
        """
        scanner = _make_docker_scanner()

        # Generate a VFS with multiple files
        vfs = data.draw(st_base_vfs())
        if len(vfs) < 2:
            return

        keys = sorted(vfs.keys())

        # Pick one file to whiteout
        target_path = data.draw(st.sampled_from(keys))

        # Build the whiteout entry
        if "/" in target_path:
            parent_dir = target_path.rsplit("/", 1)[0]
            target_name = target_path.rsplit("/", 1)[1]
            whiteout_entry = f"{parent_dir}/.wh.{target_name}"
        else:
            whiteout_entry = f".wh.{target_path}"

        # Save a snapshot of files that should NOT be affected
        non_targeted = {k: v for k, v in vfs.items() if k != target_path}

        # Apply whiteout
        layer_entries = [whiteout_entry]
        scanner._apply_whiteouts(vfs, layer_entries)

        # Assert non-targeted files are still present with same content
        for path, content in non_targeted.items():
            assert path in vfs, f"Non-targeted file '{path}' should be preserved but was removed"
            assert vfs[path] == content, f"Non-targeted file '{path}' content was modified"

    @given(data=st.data())
    def test_same_layer_files_survive_opaque_whiteout(self, data: st.DataObject) -> None:
        """Files added in the same layer as an opaque whiteout ARE preserved.

        **Validates: Requirements 5.3, 6.6**
        """
        scanner = _make_docker_scanner()

        # Generate a directory path
        dir_path = data.draw(st_directory_path())

        # Create lower-layer files under this directory
        num_lower_files = data.draw(st.integers(min_value=1, max_value=5))
        lower_files: dict[str, bytes] = {}
        for i in range(num_lower_files):
            filename = f"lower_{i}"
            lower_files[f"{dir_path}/{filename}"] = b"lower content"

        # Create same-layer files under this directory
        num_same_layer_files = data.draw(st.integers(min_value=1, max_value=5))
        same_layer_files: dict[str, bytes] = {}
        same_layer_entries: list[str] = []
        for i in range(num_same_layer_files):
            filename = f"new_{i}"
            path = f"{dir_path}/{filename}"
            same_layer_files[path] = b"new content"
            same_layer_entries.append(path)

        # Build VFS with lower-layer files and same-layer files
        vfs: dict[str, bytes] = {}
        vfs.update(lower_files)
        vfs.update(same_layer_files)

        # Layer entries: opaque whiteout + same-layer new files
        opaque_entry = f"{dir_path}/.wh..wh..opq"
        layer_entries = [opaque_entry, *same_layer_entries]

        # Apply whiteouts
        scanner._apply_whiteouts(vfs, layer_entries)

        # Assert lower-layer files are GONE
        for path in lower_files:
            assert path not in vfs, f"Lower-layer file '{path}' should be removed by opaque whiteout"

        # Assert same-layer files are PRESERVED
        for path in same_layer_files:
            assert path in vfs, f"Same-layer file '{path}' should survive the opaque whiteout but was removed"
            assert vfs[path] == same_layer_files[path], f"Same-layer file '{path}' content was modified"
