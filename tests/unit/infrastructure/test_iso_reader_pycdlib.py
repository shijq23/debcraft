"""Unit tests for PyCdlibISOReader adapter.

Tests the pycdlib-backed ISO 9660 reader against a real fixture ISO image
created with genisoimage and Rock Ridge extensions.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from debcraft.infrastructure.scanners.iso_reader_pycdlib import PyCdlibISOReader

pytestmark = [pytest.mark.unit, pytest.mark.iso]

# Locate fixture ISO relative to this test file
FIXTURE_ISO = Path(__file__).resolve().parents[3] / "fixtures" / "images" / "test.iso"


@pytest.fixture
def reader() -> PyCdlibISOReader:
    """Provide a fresh PyCdlibISOReader instance."""
    return PyCdlibISOReader()


@pytest.fixture
def opened_reader() -> PyCdlibISOReader:
    """Provide a PyCdlibISOReader with the fixture ISO already open."""
    r = PyCdlibISOReader()
    r.open(str(FIXTURE_ISO))
    yield r  # type: ignore[misc]
    r.close()


class TestOpen:
    """Tests for PyCdlibISOReader.open()."""

    def test_open_valid_iso(self, reader: PyCdlibISOReader) -> None:
        """Opening a valid ISO succeeds without exception."""
        reader.open(str(FIXTURE_ISO))
        reader.close()

    def test_open_nonexistent_path_raises_oserror(self, reader: PyCdlibISOReader) -> None:
        """Opening a nonexistent path raises OSError."""
        with pytest.raises(OSError):
            reader.open("/nonexistent/path/to/file.iso")

    def test_open_invalid_file_raises_oserror(self, reader: PyCdlibISOReader) -> None:
        """Opening a file that is not valid ISO raises OSError."""
        with tempfile.NamedTemporaryFile(suffix=".iso") as f:
            f.write(b"this is not an ISO image at all" * 100)
            f.flush()
            with pytest.raises(OSError):
                reader.open(f.name)


class TestClose:
    """Tests for PyCdlibISOReader.close()."""

    def test_close_without_open(self, reader: PyCdlibISOReader) -> None:
        """Closing without a prior open completes without exception."""
        reader.close()

    def test_close_then_list_dir_raises(self, reader: PyCdlibISOReader) -> None:
        """After close, list_dir raises an exception."""
        reader.open(str(FIXTURE_ISO))
        reader.close()
        with pytest.raises(FileNotFoundError):
            reader.list_dir("")

    def test_close_then_read_file_raises(self, reader: PyCdlibISOReader) -> None:
        """After close, read_file raises an exception."""
        reader.open(str(FIXTURE_ISO))
        reader.close()
        with pytest.raises(FileNotFoundError):
            reader.read_file("var/lib/dpkg/status")


class TestListDir:
    """Tests for PyCdlibISOReader.list_dir()."""

    def test_list_dir_root(self, opened_reader: PyCdlibISOReader) -> None:
        """Listing the root directory returns top-level entries."""
        entries = opened_reader.list_dir("")
        assert "var" in entries

    def test_list_dir_subdirectory(self, opened_reader: PyCdlibISOReader) -> None:
        """Listing a subdirectory returns expected entries."""
        entries = opened_reader.list_dir("var/lib")
        assert "dpkg" in entries

    def test_list_dir_nonexistent_raises(self, opened_reader: PyCdlibISOReader) -> None:
        """Listing a nonexistent directory raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            opened_reader.list_dir("nonexistent/path")

    def test_list_dir_on_file_raises(self, opened_reader: PyCdlibISOReader) -> None:
        """Listing a file path (not a directory) raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            opened_reader.list_dir("var/lib/dpkg/status")


class TestReadFile:
    """Tests for PyCdlibISOReader.read_file()."""

    def test_read_file_valid(self, opened_reader: PyCdlibISOReader) -> None:
        """Reading a valid file returns its contents as bytes."""
        data = opened_reader.read_file("var/lib/dpkg/status")
        assert isinstance(data, bytes)
        assert b"Package: base-files" in data
        assert b"Version: 13.5" in data

    def test_read_file_nonexistent_raises(self, opened_reader: PyCdlibISOReader) -> None:
        """Reading a nonexistent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            opened_reader.read_file("nonexistent/file.txt")

    def test_read_file_on_directory_raises(self, opened_reader: PyCdlibISOReader) -> None:
        """Reading a directory path raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            opened_reader.read_file("var/lib/dpkg")


class TestPathNormalization:
    """Tests for path with and without leading slash equivalence."""

    def test_list_dir_with_leading_slash(self, opened_reader: PyCdlibISOReader) -> None:
        """list_dir with leading slash is equivalent to without."""
        entries_no_slash = opened_reader.list_dir("var/lib")
        entries_with_slash = opened_reader.list_dir("/var/lib")
        assert entries_no_slash == entries_with_slash

    def test_read_file_with_leading_slash(self, opened_reader: PyCdlibISOReader) -> None:
        """read_file with leading slash is equivalent to without."""
        data_no_slash = opened_reader.read_file("var/lib/dpkg/status")
        data_with_slash = opened_reader.read_file("/var/lib/dpkg/status")
        assert data_no_slash == data_with_slash


class TestPartialRockRidgeHandling:
    """Tests for pycdlib IndexError handling on ISOs with partial Rock Ridge.

    When pycdlib's _find_rr_record navigates the directory tree, it can throw
    IndexError if a path component exists in ISO 9660 directory records but has
    no corresponding Rock Ridge entry. This is common in ISOs that mix Joliet +
    Rock Ridge or have partial RR support. The adapter must catch these and
    convert to FileNotFoundError.
    """

    def test_read_file_index_error_maps_to_file_not_found(self, reader: PyCdlibISOReader) -> None:
        """IndexError from pycdlib during read_file raises FileNotFoundError."""
        reader.open(str(FIXTURE_ISO))
        try:
            # Replace _iso with a mock that raises IndexError from open_file_from_iso,
            # simulating what happens when _find_rr_record hits a path component
            # without a corresponding Rock Ridge entry.
            mock_iso = MagicMock()
            mock_iso.open_file_from_iso.side_effect = IndexError("list index out of range")
            reader._iso = mock_iso

            with pytest.raises(FileNotFoundError, match="Path not found in ISO"):
                reader.read_file("some/partial/rr/path")
        finally:
            reader._iso = None

    def test_list_dir_index_error_maps_to_file_not_found(self, reader: PyCdlibISOReader) -> None:
        """IndexError from pycdlib during list_dir raises FileNotFoundError."""
        reader.open(str(FIXTURE_ISO))
        try:
            # Mock list_children to raise IndexError, simulating partial RR
            # navigation failure in _find_rr_record.
            mock_iso = MagicMock()
            mock_iso.list_children.side_effect = IndexError("list index out of range")
            reader._iso = mock_iso

            with pytest.raises(FileNotFoundError, match="Path not found in ISO"):
                reader.list_dir("some/partial/rr/dir")
        finally:
            reader._iso = None

    def test_read_file_attribute_error_maps_to_file_not_found(self, reader: PyCdlibISOReader) -> None:
        """AttributeError from pycdlib during read_file raises FileNotFoundError."""
        reader.open(str(FIXTURE_ISO))
        try:
            # AttributeError can occur when pycdlib tries to access .rock_ridge
            # on a record that doesn't have one in certain code paths.
            mock_iso = MagicMock()
            mock_iso.open_file_from_iso.side_effect = AttributeError("'NoneType' has no attribute 'name'")
            reader._iso = mock_iso

            with pytest.raises(FileNotFoundError, match="Path not found in ISO"):
                reader.read_file("another/broken/rr/path")
        finally:
            reader._iso = None

    def test_list_dir_attribute_error_maps_to_file_not_found(self, reader: PyCdlibISOReader) -> None:
        """AttributeError from pycdlib during list_dir raises FileNotFoundError."""
        reader.open(str(FIXTURE_ISO))
        try:
            mock_iso = MagicMock()
            mock_iso.list_children.side_effect = AttributeError("'NoneType' has no attribute 'rock_ridge'")
            reader._iso = mock_iso

            with pytest.raises(FileNotFoundError, match="Path not found in ISO"):
                reader.list_dir("broken/rr/directory")
        finally:
            reader._iso = None

    def test_read_file_index_error_preserves_original_path_in_message(self, reader: PyCdlibISOReader) -> None:
        """The FileNotFoundError message includes the original requested path."""
        reader.open(str(FIXTURE_ISO))
        try:
            mock_iso = MagicMock()
            mock_iso.open_file_from_iso.side_effect = IndexError("list index out of range")
            reader._iso = mock_iso

            with pytest.raises(FileNotFoundError, match=r"install/filesystem\.squashfs"):
                reader.read_file("install/filesystem.squashfs")
        finally:
            reader._iso = None

    def test_list_dir_index_error_preserves_original_path_in_message(self, reader: PyCdlibISOReader) -> None:
        """The FileNotFoundError message includes the original requested path."""
        reader.open(str(FIXTURE_ISO))
        try:
            mock_iso = MagicMock()
            mock_iso.list_children.side_effect = IndexError("list index out of range")
            reader._iso = mock_iso

            with pytest.raises(FileNotFoundError, match="casper"):
                reader.list_dir("casper")
        finally:
            reader._iso = None
