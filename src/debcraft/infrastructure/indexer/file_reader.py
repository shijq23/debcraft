"""Local filesystem reader for cached repository metadata files.

Reads and decompresses .gz, .xz, and .bz2 files from the mirror
cache directory, returning decoded UTF-8 text content.
"""

from __future__ import annotations

import asyncio
import bz2
import gzip
import logging
import lzma
from pathlib import Path

logger = logging.getLogger(__name__)


class LocalFileReader:
    """Reads and decompresses cached metadata files from the local filesystem."""

    async def read_file(self, local_path: str) -> str:
        """Read a cached file, decompressing .gz/.xz/.bz2 as needed.

        Args:
            local_path: Path to the file on the local filesystem.

        Returns:
            The decompressed file content as a string (UTF-8 decoded).

        Raises:
            OSError: If the file cannot be read or decompressed.
        """
        path = Path(local_path)
        suffix = path.suffix.lower()

        logger.debug("Reading file", extra={"path": local_path, "suffix": suffix})

        try:
            raw_bytes = await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            msg = f"Failed to read file: {local_path}"
            raise OSError(msg) from exc

        try:
            if suffix == ".gz":
                content = gzip.decompress(raw_bytes)
            elif suffix == ".xz":
                content = lzma.decompress(raw_bytes)
            elif suffix == ".bz2":
                content = bz2.decompress(raw_bytes)
            else:
                logger.debug("No decompression needed", extra={"path": local_path})
                return raw_bytes.decode("utf-8")
        except (gzip.BadGzipFile, lzma.LZMAError, OSError, ValueError) as exc:
            msg = f"Failed to decompress file: {local_path}"
            raise OSError(msg) from exc

        logger.debug(
            "Decompressed file",
            extra={"path": local_path, "compressed_size": len(raw_bytes), "decompressed_size": len(content)},
        )

        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            msg = f"Failed to decode file as UTF-8: {local_path}"
            raise OSError(msg) from exc
