"""Unit tests for the LocalFileReader infrastructure component."""

from __future__ import annotations

import bz2
import gzip
import lzma
from pathlib import Path

import pytest

from debcraft.infrastructure.indexer.file_reader import LocalFileReader


@pytest.fixture
def reader() -> LocalFileReader:
    return LocalFileReader()


@pytest.mark.unit
class TestLocalFileReader:
    """Tests for LocalFileReader read_file method."""

    @pytest.mark.asyncio
    async def test_reads_plain_text_file(self, reader: LocalFileReader, tmp_path: Path) -> None:
        """Plain text files are read without decompression."""
        content = "Package: libfoo\nVersion: 1.0\n"
        file_path = tmp_path / "Packages"
        file_path.write_bytes(content.encode("utf-8"))

        result = await reader.read_file(str(file_path))

        assert result == content

    @pytest.mark.asyncio
    async def test_reads_gz_compressed_file(self, reader: LocalFileReader, tmp_path: Path) -> None:
        """Gzip-compressed files are decompressed transparently."""
        content = "Package: libbar\nVersion: 2.0\n"
        file_path = tmp_path / "Packages.gz"
        file_path.write_bytes(gzip.compress(content.encode("utf-8")))

        result = await reader.read_file(str(file_path))

        assert result == content

    @pytest.mark.asyncio
    async def test_reads_xz_compressed_file(self, reader: LocalFileReader, tmp_path: Path) -> None:
        """XZ-compressed files are decompressed transparently."""
        content = "Package: libbaz\nVersion: 3.0\n"
        file_path = tmp_path / "Packages.xz"
        file_path.write_bytes(lzma.compress(content.encode("utf-8")))

        result = await reader.read_file(str(file_path))

        assert result == content

    @pytest.mark.asyncio
    async def test_reads_bz2_compressed_file(self, reader: LocalFileReader, tmp_path: Path) -> None:
        """Bzip2-compressed files are decompressed transparently."""
        content = "Package: libqux\nVersion: 4.0\n"
        file_path = tmp_path / "Sources.bz2"
        file_path.write_bytes(bz2.compress(content.encode("utf-8")))

        result = await reader.read_file(str(file_path))

        assert result == content

    @pytest.mark.asyncio
    async def test_raises_ioerror_for_missing_file(self, reader: LocalFileReader, tmp_path: Path) -> None:
        """Missing files raise OSError."""
        file_path = tmp_path / "nonexistent.gz"

        with pytest.raises(OSError, match="Failed to read file"):
            await reader.read_file(str(file_path))

    @pytest.mark.asyncio
    async def test_raises_ioerror_for_corrupt_gz(self, reader: LocalFileReader, tmp_path: Path) -> None:
        """Corrupt gzip data raises OSError."""
        file_path = tmp_path / "corrupt.gz"
        file_path.write_bytes(b"this is not gzip data")

        with pytest.raises(OSError, match="Failed to decompress file"):
            await reader.read_file(str(file_path))

    @pytest.mark.asyncio
    async def test_raises_ioerror_for_corrupt_xz(self, reader: LocalFileReader, tmp_path: Path) -> None:
        """Corrupt XZ data raises OSError."""
        file_path = tmp_path / "corrupt.xz"
        file_path.write_bytes(b"this is not xz data")

        with pytest.raises(OSError, match="Failed to decompress file"):
            await reader.read_file(str(file_path))

    @pytest.mark.asyncio
    async def test_raises_ioerror_for_corrupt_bz2(self, reader: LocalFileReader, tmp_path: Path) -> None:
        """Corrupt bz2 data raises OSError."""
        file_path = tmp_path / "corrupt.bz2"
        file_path.write_bytes(b"this is not bz2 data")

        with pytest.raises(OSError, match="Failed to decompress file"):
            await reader.read_file(str(file_path))

    @pytest.mark.asyncio
    async def test_extension_case_insensitive(self, reader: LocalFileReader, tmp_path: Path) -> None:
        """File extension matching is case-insensitive."""
        content = "Package: casefoo\nVersion: 1.0\n"
        file_path = tmp_path / "Packages.GZ"
        file_path.write_bytes(gzip.compress(content.encode("utf-8")))

        result = await reader.read_file(str(file_path))

        assert result == content

    @pytest.mark.asyncio
    async def test_handles_empty_file(self, reader: LocalFileReader, tmp_path: Path) -> None:
        """Empty plain text files return an empty string."""
        file_path = tmp_path / "empty"
        file_path.write_bytes(b"")

        result = await reader.read_file(str(file_path))

        assert result == ""
