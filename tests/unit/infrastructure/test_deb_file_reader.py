"""Tests for the LocalDebFileReader infrastructure adapter."""

import bz2
import gzip
import hashlib
import lzma

import pytest
import zstandard

from debcraft.infrastructure.package_intelligence.file_reader import LocalDebFileReader


def _build_ar_archive(members: list[tuple[str, bytes]]) -> bytes:
    """Build a minimal ar archive with the given members.

    Args:
        members: List of (name, content) tuples.

    Returns:
        Raw bytes of a valid ar archive.
    """
    buf = bytearray(b"!<arch>\n")

    for name, content in members:
        # 60-byte header: name(16) + timestamp(12) + owner(6) + group(6)
        #                 + mode(8) + size(10) + magic(2)
        padded_name = f"{name + '/':16s}"
        header = (
            f"{padded_name}"
            f"{'0':12s}"  # timestamp
            f"{'0':6s}"  # owner
            f"{'0':6s}"  # group
            f"{'100644':8s}"  # mode
            f"{len(content):<10d}"  # size
            "`\n"  # magic
        )
        buf.extend(header.encode("ascii"))
        buf.extend(content)
        # Pad to even boundary
        if len(content) % 2 != 0:
            buf.extend(b"\n")

    return bytes(buf)


@pytest.mark.unit
class TestReadArMember:
    """Tests for read_ar_member method."""

    def test_empty_prefix_returns_magic_bytes(self, tmp_path):
        """Empty prefix returns the first 8 bytes of the file."""
        archive = _build_ar_archive([("debian-binary", b"2.0\n")])
        deb_file = tmp_path / "test.deb"
        deb_file.write_bytes(archive)

        reader = LocalDebFileReader()
        result = reader.read_ar_member(str(deb_file), "")

        assert result == b"!<arch>\n"

    def test_finds_member_by_prefix(self, tmp_path):
        """Finds and returns uncompressed member matching prefix."""
        content = b"2.0\n"
        archive = _build_ar_archive([("debian-binary", content)])
        deb_file = tmp_path / "test.deb"
        deb_file.write_bytes(archive)

        reader = LocalDebFileReader()
        result = reader.read_ar_member(str(deb_file), "debian-binary")

        assert result == content

    def test_finds_member_with_prefix_matching(self, tmp_path):
        """Prefix matching finds control.tar.gz when searching for 'control.tar'."""
        raw_content = b"control file content"
        compressed = gzip.compress(raw_content)
        archive = _build_ar_archive(
            [
                ("debian-binary", b"2.0\n"),
                ("control.tar.gz", compressed),
            ]
        )
        deb_file = tmp_path / "test.deb"
        deb_file.write_bytes(archive)

        reader = LocalDebFileReader()
        result = reader.read_ar_member(str(deb_file), "control.tar")

        assert result == raw_content

    def test_decompresses_gz_member(self, tmp_path):
        """Gzip-compressed members are decompressed."""
        raw_content = b"hello world from gzip"
        compressed = gzip.compress(raw_content)
        archive = _build_ar_archive([("data.tar.gz", compressed)])
        deb_file = tmp_path / "test.deb"
        deb_file.write_bytes(archive)

        reader = LocalDebFileReader()
        result = reader.read_ar_member(str(deb_file), "data.tar")

        assert result == raw_content

    def test_decompresses_xz_member(self, tmp_path):
        """XZ-compressed members are decompressed."""
        raw_content = b"hello world from xz"
        compressed = lzma.compress(raw_content, format=lzma.FORMAT_XZ)
        archive = _build_ar_archive([("data.tar.xz", compressed)])
        deb_file = tmp_path / "test.deb"
        deb_file.write_bytes(archive)

        reader = LocalDebFileReader()
        result = reader.read_ar_member(str(deb_file), "data.tar")

        assert result == raw_content

    def test_decompresses_zst_member(self, tmp_path):
        """Zstandard-compressed members are decompressed."""
        raw_content = b"hello world from zstd"
        cctx = zstandard.ZstdCompressor()
        compressed = cctx.compress(raw_content)
        archive = _build_ar_archive([("data.tar.zst", compressed)])
        deb_file = tmp_path / "test.deb"
        deb_file.write_bytes(archive)

        reader = LocalDebFileReader()
        result = reader.read_ar_member(str(deb_file), "data.tar")

        assert result == raw_content

    def test_decompresses_bz2_member(self, tmp_path):
        """Bzip2-compressed members are decompressed."""
        raw_content = b"hello world from bz2"
        compressed = bz2.compress(raw_content)
        archive = _build_ar_archive([("data.tar.bz2", compressed)])
        deb_file = tmp_path / "test.deb"
        deb_file.write_bytes(archive)

        reader = LocalDebFileReader()
        result = reader.read_ar_member(str(deb_file), "data.tar")

        assert result == raw_content

    def test_decompresses_lzma_member(self, tmp_path):
        """LZMA-compressed members are decompressed."""
        raw_content = b"hello world from lzma"
        compressed = lzma.compress(raw_content, format=lzma.FORMAT_ALONE)
        archive = _build_ar_archive([("data.tar.lzma", compressed)])
        deb_file = tmp_path / "test.deb"
        deb_file.write_bytes(archive)

        reader = LocalDebFileReader()
        result = reader.read_ar_member(str(deb_file), "data.tar")

        assert result == raw_content

    def test_uncompressed_member_returned_raw(self, tmp_path):
        """Members without compression extension are returned as-is."""
        raw_content = b"raw tar content here"
        archive = _build_ar_archive([("data.tar", raw_content)])
        deb_file = tmp_path / "test.deb"
        deb_file.write_bytes(archive)

        reader = LocalDebFileReader()
        result = reader.read_ar_member(str(deb_file), "data.tar")

        assert result == raw_content

    def test_raises_value_error_for_missing_member(self, tmp_path):
        """Raises ValueError when no member matches the prefix."""
        archive = _build_ar_archive([("debian-binary", b"2.0\n")])
        deb_file = tmp_path / "test.deb"
        deb_file.write_bytes(archive)

        reader = LocalDebFileReader()
        with pytest.raises(ValueError, match="No member matching prefix"):
            reader.read_ar_member(str(deb_file), "control.tar")

    def test_raises_value_error_for_invalid_ar_magic(self, tmp_path):
        """Raises ValueError when file is not a valid ar archive."""
        deb_file = tmp_path / "bad.deb"
        deb_file.write_bytes(b"not an ar archive at all")

        reader = LocalDebFileReader()
        with pytest.raises(ValueError, match="Not a valid ar archive"):
            reader.read_ar_member(str(deb_file), "debian-binary")

    def test_raises_os_error_for_nonexistent_file(self, tmp_path):
        """Raises OSError for missing files."""
        reader = LocalDebFileReader()
        with pytest.raises(OSError):
            reader.read_ar_member(str(tmp_path / "nonexistent.deb"), "control.tar")

    def test_multiple_members_returns_correct_one(self, tmp_path):
        """When multiple members exist, returns the one matching the prefix."""
        control_content = b"control tar content"
        data_content = b"data tar content"
        archive = _build_ar_archive(
            [
                ("debian-binary", b"2.0\n"),
                ("control.tar", control_content),
                ("data.tar", data_content),
            ]
        )
        deb_file = tmp_path / "test.deb"
        deb_file.write_bytes(archive)

        reader = LocalDebFileReader()
        assert reader.read_ar_member(str(deb_file), "control.tar") == control_content
        assert reader.read_ar_member(str(deb_file), "data.tar") == data_content
        assert reader.read_ar_member(str(deb_file), "debian-binary") == b"2.0\n"


@pytest.mark.unit
class TestComputeSha256:
    """Tests for compute_sha256 method."""

    def test_computes_correct_hash(self, tmp_path):
        """Returns correct SHA256 hex digest."""
        content = b"hello world"
        test_file = tmp_path / "test.bin"
        test_file.write_bytes(content)

        reader = LocalDebFileReader()
        result = reader.compute_sha256(str(test_file))

        expected = hashlib.sha256(content).hexdigest()
        assert result == expected

    def test_empty_file_hash(self, tmp_path):
        """Returns correct hash for empty file."""
        test_file = tmp_path / "empty.bin"
        test_file.write_bytes(b"")

        reader = LocalDebFileReader()
        result = reader.compute_sha256(str(test_file))

        expected = hashlib.sha256(b"").hexdigest()
        assert result == expected

    def test_large_file_buffered_read(self, tmp_path):
        """Handles files larger than the buffer size correctly."""
        # Create a file larger than 64KiB buffer
        content = b"x" * 200000
        test_file = tmp_path / "large.bin"
        test_file.write_bytes(content)

        reader = LocalDebFileReader()
        result = reader.compute_sha256(str(test_file))

        expected = hashlib.sha256(content).hexdigest()
        assert result == expected

    def test_raises_os_error_for_nonexistent_file(self, tmp_path):
        """Raises OSError for missing files."""
        reader = LocalDebFileReader()
        with pytest.raises(OSError):
            reader.compute_sha256(str(tmp_path / "nonexistent.bin"))
