"""Local filesystem adapter implementing the DebFileReader protocol.

Reads ar archive members from .deb files, handling decompression for
gz, xz, zst, bz2, lzma, and uncompressed members. Also provides
SHA256 hashing for cache-key computation.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from debcraft.infrastructure.package_intelligence._ar_utils import parse_ar_member

logger = logging.getLogger(__name__)

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
        return parse_ar_member(data, deb_path, member_prefix)

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
