"""ISO filesystem adapter implementing the DebFileReader protocol.

Reads ar archive members from .deb files within an ISO filesystem,
handling decompression for gz, xz, zst, bz2, lzma, and uncompressed
members. Also provides SHA256 hashing for cache-key computation.

Uses an ISOReader instance for all I/O operations, enabling .deb
extraction without mounting the ISO or copying files to the local
filesystem.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from debcraft.infrastructure.package_intelligence._ar_utils import parse_ar_member

if TYPE_CHECKING:
    from debcraft.infrastructure.scanners.iso import ISOReader


class ISODebFileReader:
    """Reads .deb archive members and computes file hashes from an ISO filesystem.

    Implements the DebFileReader protocol defined in the domain layer.
    Parses the standard ar archive format and decompresses members
    based on their file extension. All file I/O is performed through
    an ISOReader instance rather than the local filesystem.
    """

    def __init__(self, iso_reader: ISOReader) -> None:
        """Initialize with an ISOReader instance.

        Args:
            iso_reader: An ISOReader that has already been opened on an ISO image.
        """
        self._iso_reader = iso_reader

    def read_ar_member(self, deb_path: str, member_prefix: str) -> bytes:
        """Read and return raw bytes of an ar archive member matching the given prefix.

        Reads the full .deb file from the ISO filesystem via the ISOReader,
        then performs ar archive parsing in-memory.

        Args:
            deb_path: Path within the ISO filesystem to the .deb file
                (e.g. "pool/main/l/libc6/libc6_2.40-1_amd64.deb").
            member_prefix: Prefix of the member name to match (e.g. "control.tar",
                "data.tar", "debian-binary"). If empty, returns the first 8 bytes
                (ar magic header) of the archive.

        Returns:
            Decompressed bytes of the matched member, or raw magic bytes if
            member_prefix is empty.

        Raises:
            FileNotFoundError: If the .deb file does not exist in the ISO.
            ValueError: If the file is not a valid ar archive or no member
                matching the prefix is found.
        """
        data = self._iso_reader.read_file(deb_path)
        return parse_ar_member(data, deb_path, member_prefix)

    def compute_sha256(self, file_path: str) -> str:
        """Compute and return the SHA256 hex digest of a file within the ISO.

        Reads the complete file content from the ISO filesystem and computes
        the hash in one pass.

        Args:
            file_path: Path to the file within the ISO filesystem.

        Returns:
            Lowercase hexadecimal SHA256 digest string.

        Raises:
            FileNotFoundError: If the file does not exist in the ISO.
        """
        data = self._iso_reader.read_file(file_path)
        return hashlib.sha256(data).hexdigest()
