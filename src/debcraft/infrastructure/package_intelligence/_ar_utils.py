"""Shared utilities for parsing ar archives and decompressing members.

Provides the low-level ar archive parsing logic and member decompression
used by both LocalDebFileReader and ISODebFileReader.
"""

from __future__ import annotations

import bz2
import gzip
import lzma

import zstandard

#: Standard ar archive magic bytes.
AR_MAGIC = b"!<arch>\n"

#: Length of the ar global header.
AR_MAGIC_LEN = 8

#: Length of each ar member header.
AR_HEADER_LEN = 60


def parse_ar_member(data: bytes, deb_path: str, member_prefix: str) -> bytes:
    """Parse an ar archive and return the decompressed content of the matching member.

    Args:
        data: Raw bytes of the ar archive.
        deb_path: Path to the .deb file (used in error messages).
        member_prefix: Prefix of the member name to match (e.g. "control.tar",
            "data.tar", "debian-binary"). If empty, returns the first 8 bytes
            (ar magic header) of the archive.

    Returns:
        Decompressed bytes of the matched member, or raw magic bytes if
        member_prefix is empty.

    Raises:
        ValueError: If the file is not a valid ar archive or no member
            matching the prefix is found.
    """
    if not member_prefix:
        # Return the ar archive magic/header bytes for validation
        return data[:AR_MAGIC_LEN]

    # Parse ar archive to find the requested member
    if len(data) < AR_MAGIC_LEN or data[:AR_MAGIC_LEN] != AR_MAGIC:
        msg = f"Not a valid ar archive: {deb_path}"
        raise ValueError(msg)

    offset = AR_MAGIC_LEN

    while offset < len(data):
        if offset + AR_HEADER_LEN > len(data):
            break

        # Parse 60-byte ar member header
        # Format: name(16) + timestamp(12) + owner(6) + group(6)
        #         + mode(8) + size(10) + magic(2)
        header = data[offset : offset + AR_HEADER_LEN]
        name_raw = header[0:16].decode("ascii", errors="replace").rstrip()
        size_raw = header[48:58].decode("ascii", errors="replace").strip()
        end_magic = header[58:60]

        if end_magic != b"`\n":
            msg = f"Invalid ar member header at offset {offset} in {deb_path}"
            raise ValueError(msg)

        member_size = int(size_raw)
        content_offset = offset + AR_HEADER_LEN
        content_end = content_offset + member_size

        # Strip trailing "/" from member name (ar format convention)
        member_name = name_raw.rstrip("/")

        # Handle extended names (GNU ar long name format starting with "/")
        # For .deb files this is rare but possible

        if member_name.startswith(member_prefix):
            member_bytes = data[content_offset:content_end]
            return decompress(member_name, member_bytes)

        # Advance to next member (padded to even boundary)
        offset = content_end + (member_size % 2)

    msg = f"No member matching prefix '{member_prefix}' found in {deb_path}"
    raise ValueError(msg)


def decompress(member_name: str, raw_bytes: bytes) -> bytes:
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
