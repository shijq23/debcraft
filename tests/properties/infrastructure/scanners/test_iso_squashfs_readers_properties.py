"""Property-based tests for ISO & SquashFS reader adapters.

# Feature: iso-squashfs-readers
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.infrastructure.scanners.iso_reader_pycdlib import PyCdlibISOReader
from debcraft.infrastructure.scanners.squashfs_reader_pysquashfsimage import PySquashfsImageReader

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_ISO_FIXTURE = _PROJECT_ROOT / "fixtures" / "images" / "test.iso"

# ---------------------------------------------------------------------------
# Module-level directory collection for ISO
# ---------------------------------------------------------------------------


def _collect_iso_directories() -> list[str]:
    """Walk all directories in the fixture ISO and return their paths."""
    if not _ISO_FIXTURE.exists():
        return []
    reader = PyCdlibISOReader()
    reader.open(str(_ISO_FIXTURE))
    try:
        all_dirs: list[str] = [""]
        queue: list[str] = [""]
        while queue:
            current = queue.pop(0)
            entries = reader.list_dir(current)
            for entry in entries:
                child_path = current + "/" + entry if current else entry
                try:
                    reader.list_dir(child_path)
                    all_dirs.append(child_path)
                    queue.append(child_path)
                except FileNotFoundError:
                    pass  # it's a file, not a directory
    finally:
        reader.close()
    return all_dirs


ALL_ISO_DIRECTORIES: list[str] = _collect_iso_directories()

# Skip the entire module if fixtures were not built
_SQUASHFS_FIXTURE_PATH = _PROJECT_ROOT / "fixtures" / "images" / "test.squashfs"
pytestmark = pytest.mark.skipif(
    not _ISO_FIXTURE.exists() or not _SQUASHFS_FIXTURE_PATH.exists(),
    reason="Test fixtures not found. Run: make -C fixtures images",
)


# ---------------------------------------------------------------------------
# Property 1: ISO directory listing entries are bare names
# ---------------------------------------------------------------------------


@pytest.mark.property
@pytest.mark.iso
class TestProperty1ISODirectoryListingBareNames:
    """Property 1: ISO directory listing entries are bare names.

    For any directory within an ISO image, all entries returned by
    PyCdlibISOReader.list_dir() SHALL be bare basenames containing no "/"
    character, and SHALL never include "." or ".." entries.

    **Validates: Requirements 2.5, 10.3**
    """

    def test_all_entries_are_bare_names(self) -> None:
        """Validates Requirements 2.5, 10.3.

        Walk all directories in fixture ISO and verify every entry is a bare
        name with no "/" character and is never "." or "..".
        """
        reader = PyCdlibISOReader()
        reader.open(str(_ISO_FIXTURE))
        try:
            from collections import deque

            dirs_to_visit: deque[str] = deque([""])
            total_entries = 0

            while dirs_to_visit:
                current_dir = dirs_to_visit.popleft()
                entries = reader.list_dir(current_dir)

                for entry in entries:
                    total_entries += 1

                    # Property: no "/" in entry name
                    assert "/" not in entry, f"Entry '{entry}' in directory '{current_dir}' contains '/'"

                    # Property: never "." or ".."
                    assert entry != ".", f"Entry '.' found in directory '{current_dir}'"
                    assert entry != "..", f"Entry '..' found in directory '{current_dir}'"

                    # Queue subdirectories for further exploration
                    child_path = f"{current_dir}/{entry}" if current_dir else entry
                    try:
                        reader.list_dir(child_path)
                        dirs_to_visit.append(child_path)
                    except FileNotFoundError:
                        pass

            # Sanity check: we should have found at least one entry
            assert total_entries > 0, "No entries found in fixture ISO — fixture may be empty"
        finally:
            reader.close()


# ---------------------------------------------------------------------------
# Property 2: ISO path round-trip composability
# ---------------------------------------------------------------------------


@pytest.mark.property
@pytest.mark.iso
class TestProperty2ISOPathRoundTripComposability:
    """Property 2: ISO path round-trip composability.

    For any directory D within an ISO image and for any entry E returned by
    PyCdlibISOReader.list_dir(D), the composed path (D + "/" + E when D is
    non-empty, or E when D is empty) SHALL be a valid argument to either
    read_file() (returning bytes without raising) or list_dir() (returning
    a list without raising).

    **Validates: Requirements 10.1**
    """

    @given(directory=st.sampled_from(ALL_ISO_DIRECTORIES))
    def test_composed_paths_are_valid_arguments(self, directory: str) -> None:
        """Validates Requirements 10.1.

        For a randomly selected directory, compose paths with each entry
        and verify the composed path works with either read_file() or
        list_dir().
        """
        reader = PyCdlibISOReader()
        reader.open(str(_ISO_FIXTURE))
        try:
            entries = reader.list_dir(directory)
            for entry in entries:
                composed = directory + "/" + entry if directory else entry

                # The composed path must be valid for either read_file or list_dir
                is_valid = False
                try:
                    result = reader.read_file(composed)
                    assert isinstance(result, bytes)
                    is_valid = True
                except FileNotFoundError:
                    pass

                if not is_valid:
                    try:
                        result_list = reader.list_dir(composed)
                        assert isinstance(result_list, list)
                        is_valid = True
                    except FileNotFoundError:
                        pass

                assert is_valid, (
                    f"Composed path {composed!r} (from directory={directory!r}, "
                    f"entry={entry!r}) is not valid for read_file() or list_dir()"
                )
        finally:
            reader.close()


# ---------------------------------------------------------------------------
# Squashfs fixture and helpers
# ---------------------------------------------------------------------------

_SQUASHFS_FIXTURE = _PROJECT_ROOT / "fixtures" / "images" / "test.squashfs"


def _walk_all_squashfs_directories(reader: PySquashfsImageReader) -> list[str]:
    """Walk the squashfs image and return all directory paths."""
    dirs: list[str] = [""]
    queue: list[str] = [""]
    while queue:
        current = queue.pop(0)
        entries = reader.list_dir(current)
        for entry in entries:
            child_path = f"{current}/{entry}" if current else entry
            try:
                reader.list_dir(child_path)
            except FileNotFoundError:
                # Not a directory, skip
                continue
            dirs.append(child_path)
            queue.append(child_path)
    return dirs


# ---------------------------------------------------------------------------
# Property 3: Squashfs directory listing entries are bare names
# ---------------------------------------------------------------------------


@pytest.mark.property
@pytest.mark.iso
class TestProperty3SquashfsDirListingBareNames:
    """Property 3: Squashfs directory listing entries are bare names.

    For any directory within a squashfs image, all entries returned by
    PySquashfsImageReader.list_dir() SHALL be bare basenames containing
    no "/" character.

    **Validates: Requirements 10.4**
    """

    def test_all_entries_are_bare_names(self) -> None:
        """Validates: Requirements 10.4.

        Walk all directories in the fixture squashfs and assert that every
        entry returned by list_dir has no "/" character.
        """
        data = _SQUASHFS_FIXTURE.read_bytes()
        reader = PySquashfsImageReader()
        reader.open(data)

        try:
            all_dirs = _walk_all_squashfs_directories(reader)

            for dir_path in all_dirs:
                entries = reader.list_dir(dir_path)
                for entry in entries:
                    assert "/" not in entry, (
                        f"Entry '{entry}' in directory '{dir_path}' contains '/' — "
                        f"expected bare basename with no path separators"
                    )
        finally:
            reader.close()


# ---------------------------------------------------------------------------
# Property 6: Squashfs invalid data rejection
# ---------------------------------------------------------------------------


@pytest.mark.property
@pytest.mark.iso
class TestProperty6SquashfsInvalidDataRejection:
    """Property 6: Squashfs invalid data rejection.

    For any byte sequence that does not begin with a valid squashfs superblock
    magic number, PySquashfsImageReader.open(data) SHALL raise an OSError.

    **Validates: Requirements 4.2**
    """

    @given(data=st.binary(min_size=0, max_size=1024).filter(lambda d: not d.startswith(b"hsqs")))
    def test_invalid_data_raises_oserror(self, data: bytes) -> None:
        """Validates: Requirements 4.2.

        Generate random byte sequences that don't start with valid squashfs
        magic bytes, and verify that open(data) raises OSError.
        """
        reader = PySquashfsImageReader()
        with pytest.raises(OSError):
            reader.open(data)


# ---------------------------------------------------------------------------
# Module-level directory collection for Squashfs
# ---------------------------------------------------------------------------


def _collect_squashfs_directories() -> list[str]:
    """Walk all directories in the fixture squashfs and return their paths."""
    if not _SQUASHFS_FIXTURE.exists():
        return []
    data = _SQUASHFS_FIXTURE.read_bytes()
    reader = PySquashfsImageReader()
    reader.open(data)
    try:
        return _walk_all_squashfs_directories(reader)
    finally:
        reader.close()


ALL_SQUASHFS_DIRECTORIES: list[str] = _collect_squashfs_directories()


# ---------------------------------------------------------------------------
# Property 4: Squashfs path round-trip composability
# ---------------------------------------------------------------------------


@pytest.mark.property
@pytest.mark.iso
class TestProperty4SquashfsPathRoundTripComposability:
    """Property 4: Squashfs path round-trip composability.

    For any directory D within a squashfs image and for any entry E returned by
    PySquashfsImageReader.list_dir(D), the composed path (D + "/" + E when D is
    non-empty, or E when D is empty) SHALL be a valid argument to either
    read_file() (returning bytes without raising) or list_dir() (returning
    a list without raising).

    **Validates: Requirements 10.2**
    """

    @given(directory=st.sampled_from(ALL_SQUASHFS_DIRECTORIES))
    def test_composed_paths_are_valid_arguments(self, directory: str) -> None:
        """Validates: Requirements 10.2.

        For a randomly selected directory, compose paths with each entry
        and verify the composed path works with either read_file() or
        list_dir().
        """
        data = _SQUASHFS_FIXTURE.read_bytes()
        reader = PySquashfsImageReader()
        reader.open(data)
        try:
            entries = reader.list_dir(directory)
            for entry in entries:
                composed = f"{directory}/{entry}" if directory else entry

                # The composed path must be valid for either read_file or list_dir
                is_valid = False
                try:
                    result = reader.read_file(composed)
                    assert isinstance(result, bytes)
                    is_valid = True
                except FileNotFoundError:
                    pass

                if not is_valid:
                    try:
                        result_list = reader.list_dir(composed)
                        assert isinstance(result_list, list)
                        is_valid = True
                    except FileNotFoundError:
                        pass

                assert is_valid, (
                    f"Composed path {composed!r} (from directory={directory!r}, "
                    f"entry={entry!r}) is not valid for read_file() or list_dir()"
                )
        finally:
            reader.close()


# ---------------------------------------------------------------------------
# Module-level collection of ALL paths (files and directories) in squashfs
# ---------------------------------------------------------------------------


def _collect_all_squashfs_paths() -> list[str]:
    """Walk the squashfs fixture and return all file and directory paths."""
    if not _SQUASHFS_FIXTURE.exists():
        return []
    data = _SQUASHFS_FIXTURE.read_bytes()
    reader = PySquashfsImageReader()
    reader.open(data)
    try:
        all_paths: list[str] = [""]  # root is a valid directory path
        queue: list[str] = [""]
        while queue:
            current = queue.pop(0)
            entries = reader.list_dir(current)
            for entry in entries:
                child_path = f"{current}/{entry}" if current else entry
                all_paths.append(child_path)
                try:
                    reader.list_dir(child_path)
                    queue.append(child_path)
                except FileNotFoundError:
                    pass  # it's a file, not a directory
    finally:
        reader.close()
    return all_paths


ALL_SQUASHFS_PATHS: list[str] = _collect_all_squashfs_paths()


# ---------------------------------------------------------------------------
# Property 5: Squashfs leading-slash path normalization
# ---------------------------------------------------------------------------


@pytest.mark.property
@pytest.mark.iso
class TestProperty5SquashfsLeadingSlashNormalization:
    """Property 5: Squashfs leading-slash path normalization.

    For any valid file path P within a squashfs image, read_file("/" + P) SHALL
    return the same bytes as read_file(P), and for any valid directory path P,
    list_dir("/" + P) SHALL return the same entries as list_dir(P).

    **Validates: Requirements 5.5, 6.5**
    """

    @given(path=st.sampled_from(ALL_SQUASHFS_PATHS))
    def test_leading_slash_equivalence(self, path: str) -> None:
        """Validates: Requirements 5.5, 6.5.

        For a randomly selected path (file or directory) in the squashfs
        fixture, verify that prefixing with "/" produces identical results.
        """
        data = _SQUASHFS_FIXTURE.read_bytes()
        reader = PySquashfsImageReader()
        reader.open(data)
        try:
            slash_path = "/" + path

            # Try as a directory first
            try:
                result_without = reader.list_dir(path)
                result_with = reader.list_dir(slash_path)
                assert result_with == result_without, (
                    f"list_dir('/{path}') returned {result_with!r} but list_dir('{path}') returned {result_without!r}"
                )
            except FileNotFoundError:
                # Not a directory — must be a file
                result_bytes_without = reader.read_file(path)
                result_bytes_with = reader.read_file(slash_path)
                assert result_bytes_with == result_bytes_without, (
                    f"read_file('/{path}') returned different bytes than read_file('{path}')"
                )
        finally:
            reader.close()
