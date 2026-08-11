"""Local filesystem adapter implementing the DebFileReader protocol.

Reads ar archive members from .deb files, handling decompression for
gz, xz, zst, bz2, lzma, and uncompressed members. Also provides
SHA256 hashing for cache-key computation.
"""

from __future__ import annotations

import bz2
import gzip
import hashlib
import logging
import lzma
from pathlib import Path

import zstandard

logger = logging.getLogger(__name__)

#: Standard ar archive magic bytes.
_AR_MAGIC = b"!<arch>\n"

#: Length of the ar global header.
_AR_MAGIC_LEN = 8

#: Length of each ar member header.
_AR_HEADER_LEN = 60

#: Buffer size for SHA256 computation (64 KiB).
_HASH_BUFFER_SIZE = 65536


class LocalDebFileReader:
    """Reads .deb archive members and computes file hashes.

    Implements the DebFileReader protocol defined in the domain layer.
    Parses the standard ar archive format and decompresses members
    based on their file extension.
    """

    def read_ar_member(self, deb_path: str, member_prefix: str) -> bytes:
        """Read and return raw bytes of an ar archive member matching the given prefix.

        Args:
            deb_path: Path to the .deb file on the filesystem.
            member_prefix: Prefix of the member name to match (e.g. "control.tar",
                "data.tar", "debian-binary"). If empty, returns the first 8 bytes
                (ar magic header) of the file.

        Returns:
            Decompressed bytes of the matched member, or raw magic bytes if
            member_prefix is empty.

        Raises:
            OSError: If the file cannot be read.
            ValueError: If no member matching the prefix is found.
        """
        path = Path(deb_path)
        data = path.read_bytes()

        if not member_prefix:
            # Return the ar archive magic/header bytes for validation
            return data[:_AR_MAGIC_LEN]

        # Parse ar archive to find the requested member
        if len(data) < _AR_MAGIC_LEN or data[:_AR_MAGIC_LEN] != _AR_MAGIC:
            msg = f"Not a valid ar archive: {deb_path}"
            raise ValueError(msg)

        offset = _AR_MAGIC_LEN

        while offset < len(data):
            if offset + _AR_HEADER_LEN > len(data):
                break

            # Parse 60-byte ar member header
            # Format: name(16) + timestamp(12) + owner(6) + group(6)
            #         + mode(8) + size(10) + magic(2)
            header = data[offset : offset + _AR_HEADER_LEN]
            name_raw = header[0:16].decode("ascii", errors="replace").rstrip()
            size_raw = header[48:58].decode("ascii", errors="replace").strip()
            end_magic = header[58:60]

            if end_magic != b"`\n":
                msg = f"Invalid ar member header at offset {offset} in {deb_path}"
                raise ValueError(msg)

            member_size = int(size_raw)
            content_offset = offset + _AR_HEADER_LEN
            content_end = content_offset + member_size

            # Strip trailing "/" from member name (ar format convention)
            member_name = name_raw.rstrip("/")

            # Handle extended names (GNU ar long name format starting with "/")
            # For .deb files this is rare but possible

            if member_name.startswith(member_prefix):
                member_bytes = data[content_offset:content_end]
                return self._decompress(member_name, member_bytes)

            # Advance to next member (padded to even boundary)
            offset = content_end + (member_size % 2)

        msg = f"No member matching prefix '{member_prefix}' found in {deb_path}"
        raise ValueError(msg)

    def compute_sha256(self, file_path: str) -> str:
        """Compute and return the SHA256 hex digest of the file at the given path.

        Uses buffered reading for efficient handling of large files.

        Args:
            file_path: Path to the file to hash.

        Returns:
            Lowercase hexadecimal SHA256 digest string.

        Raises:
            OSError: If the file cannot be read.
        """
        sha256 = hashlib.sha256()
        path = Path(file_path)

        with path.open("rb") as f:
            while True:
                chunk = f.read(_HASH_BUFFER_SIZE)
                if not chunk:
                    break
                sha256.update(chunk)

        return sha256.hexdigest()

    def _decompress(self, member_name: str, raw_bytes: bytes) -> bytes:
        """Decompress member content based on the member name extension.

        Supports: .gz, .xz, .zst, .bz2, .lzma, and uncompressed.

        Args:
            member_name: The ar member name (used to detect compression).
            raw_bytes: Raw (possibly compressed) member content.

        Returns:
            Decompressed bytes.

        Raises:
            OSError: If decompression fails.
        """
        if member_name.endswith(".gz"):
            return gzip.decompress(raw_bytes)
        if member_name.endswith(".xz"):
            return lzma.decompress(raw_bytes)
        if member_name.endswith(".zst"):
            dctx = zstandard.ZstdDecompressor()
            return dctx.decompress(raw_bytes)
        if member_name.endswith(".bz2"):
            return bz2.decompress(raw_bytes)
        if member_name.endswith(".lzma"):
            return lzma.decompress(raw_bytes)

        # Uncompressed
        return raw_bytes
