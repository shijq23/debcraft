"""Unit tests for PySquashfsImageReader adapter.

Validates requirements:
4.1, 4.2, 4.3, 4.4, 4.5, 5.1, 5.2, 5.3, 5.4, 5.5,
6.1, 6.2, 6.3, 6.4, 6.5, 10.2, 10.4
"""

from __future__ import annotations

from pathlib import Path

import pytest

from debcraft.infrastructure.scanners.squashfs_reader_pysquashfsimage import (
    PySquashfsImageReader,
)

pytestmark = [pytest.mark.unit, pytest.mark.iso]

FIXTURE_PATH = Path(__file__).resolve().parents[3] / "fixtures" / "images" / "test.squashfs"


@pytest.fixture
def squashfs_data() -> bytes:
    """Load the squashfs fixture image as raw bytes."""
    return FIXTURE_PATH.read_bytes()


@pytest.fixture
def reader(squashfs_data: bytes) -> PySquashfsImageReader:
    """Return an opened PySquashfsImageReader backed by the test fixture."""
    r = PySquashfsImageReader()
    r.open(squashfs_data)
    yield r  # type: ignore[misc]
    r.close()


# --- open / close tests ---


class TestOpen:
    """Tests for open() behaviour (Req 4.1, 4.2, 4.5)."""

    def test_open_valid_squashfs(self, squashfs_data: bytes) -> None:
        """Valid squashfs bytes open without exception (Req 4.1)."""
        r = PySquashfsImageReader()
        r.open(squashfs_data)
        r.close()

    def test_open_empty_bytes_raises_oserror(self) -> None:
        """Empty bytes raise OSError (Req 4.2)."""
        r = PySquashfsImageReader()
        with pytest.raises(OSError):
            r.open(b"")

    def test_open_invalid_bytes_raises_oserror(self) -> None:
        """Random invalid bytes raise OSError (Req 4.2)."""
        r = PySquashfsImageReader()
        with pytest.raises(OSError):
            r.open(b"this is not a squashfs image at all")

    def test_open_when_already_open_raises_oserror(self, squashfs_data: bytes) -> None:
        """Double-open raises OSError without altering state (Req 4.5)."""
        r = PySquashfsImageReader()
        r.open(squashfs_data)
        try:
            with pytest.raises(OSError, match="already"):
                r.open(squashfs_data)
        finally:
            r.close()


class TestClose:
    """Tests for close() behaviour (Req 4.3, 4.4)."""

    def test_close_without_open(self) -> None:
        """Close without prior open completes without exception (Req 4.4)."""
        r = PySquashfsImageReader()
        r.close()  # Should not raise

    def test_close_releases_resources(self, squashfs_data: bytes) -> None:
        """After close, internal state is cleared (Req 4.3)."""
        r = PySquashfsImageReader()
        r.open(squashfs_data)
        r.close()
        assert r._image is None
        assert r._open is False


# --- list_dir tests ---


class TestListDir:
    """Tests for list_dir() behaviour (Req 6.1, 6.2, 6.3, 6.4, 6.5, 10.4)."""

    def test_list_dir_root(self, reader: PySquashfsImageReader) -> None:
        """Empty string lists root entries (Req 6.2)."""
        entries = reader.list_dir("")
        assert isinstance(entries, list)
        assert "var" in entries
        assert "usr" in entries
        assert "etc" in entries

    def test_list_dir_subdirectory(self, reader: PySquashfsImageReader) -> None:
        """Subdirectory path returns expected children (Req 6.1)."""
        entries = reader.list_dir("var/lib")
        assert "dpkg" in entries

    def test_list_dir_nonexistent_raises(self, reader: PySquashfsImageReader) -> None:
        """Non-existent directory raises FileNotFoundError (Req 6.3)."""
        with pytest.raises(FileNotFoundError):
            reader.list_dir("nonexistent/path")

    def test_list_dir_on_file_raises(self, reader: PySquashfsImageReader) -> None:
        """Path pointing to a file raises FileNotFoundError (Req 6.4)."""
        with pytest.raises(FileNotFoundError):
            reader.list_dir("etc/hostname")

    def test_list_dir_entries_are_bare_names(self, reader: PySquashfsImageReader) -> None:
        """All entries are bare basenames without path separators (Req 10.4)."""
        entries = reader.list_dir("")
        for entry in entries:
            assert "/" not in entry


# --- read_file tests ---


class TestReadFile:
    """Tests for read_file() behaviour (Req 5.1, 5.2, 5.3, 5.4, 5.5)."""

    def test_read_file_valid(self, reader: PySquashfsImageReader) -> None:
        """Valid file path returns expected bytes (Req 5.1)."""
        content = reader.read_file("etc/hostname")
        assert content == b"debcraft-test\n"

    def test_read_file_nonexistent_raises(self, reader: PySquashfsImageReader) -> None:
        """Non-existent path raises FileNotFoundError (Req 5.2)."""
        with pytest.raises(FileNotFoundError):
            reader.read_file("nonexistent/file.txt")

    def test_read_file_on_directory_raises(self, reader: PySquashfsImageReader) -> None:
        """Path that is a directory raises FileNotFoundError (Req 5.4)."""
        with pytest.raises(FileNotFoundError):
            reader.read_file("var/lib/dpkg")

    def test_leading_slash_equivalence(self, reader: PySquashfsImageReader) -> None:
        """Leading slash resolves the same as without (Req 5.5)."""
        with_slash = reader.read_file("/var/lib/dpkg/status")
        without_slash = reader.read_file("var/lib/dpkg/status")
        assert with_slash == without_slash

    def test_read_file_dpkg_status(self, reader: PySquashfsImageReader) -> None:
        """dpkg/status file is readable and non-empty (Req 5.1, 5.3)."""
        content = reader.read_file("var/lib/dpkg/status")
        assert b"Package: base-files" in content
