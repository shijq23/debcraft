"""Local filesystem storage provider implementation.

Implements the StorageProvider contract using pathlib.Path for all
filesystem operations. All blocking I/O calls are wrapped in
``asyncio.to_thread()`` to maintain async compatibility.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import TYPE_CHECKING

from debcraft.infrastructure.errors import StorageError
from debcraft.infrastructure.storage.paths import resolve_xdg_path
from debcraft.platform.contracts.storage import StorageProvider, StoragePurpose

if TYPE_CHECKING:
    from pathlib import Path


class LocalStorageProvider(StorageProvider):
    """Storage provider backed by the local filesystem.

    Delegates path resolution to ``resolve_xdg_path()`` and wraps all
    blocking pathlib/shutil/os calls in ``asyncio.to_thread()`` so that
    the event loop is never blocked by filesystem I/O.
    """

    async def create_directory(self, path: Path) -> None:
        """Create directory and all parents; no-op if it already exists.

        Args:
            path: The directory path to create.

        Raises:
            StorageError: If a permission error prevents directory creation.
        """
        try:
            await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(
                f"Cannot create directory '{path}': {exc}",
                cause=exc,
            ) from exc

    async def remove_matching(self, directory: Path, pattern: str) -> None:
        """Remove files/dirs in directory matching glob pattern.

        Uses ``Path.glob(pattern)`` to find matches, then removes each
        match using ``shutil.rmtree`` for directories or ``Path.unlink``
        for files.

        Args:
            directory: The directory to search within.
            pattern: Glob pattern to match files and directories for removal.
        """
        matches = await asyncio.to_thread(lambda: list(directory.glob(pattern)))
        for match in matches:
            if match.is_dir():
                await asyncio.to_thread(shutil.rmtree, match)
            else:
                await asyncio.to_thread(match.unlink)

    def resolve_path(self, purpose: StoragePurpose, relative: str = "") -> Path:
        """Return absolute Path for a storage purpose.

        Delegates to ``resolve_xdg_path()`` for platform-appropriate
        path resolution. If a relative path is provided, it is appended
        to the base directory.

        Args:
            purpose: The named storage purpose to resolve.
            relative: Optional relative path to append within the purpose directory.

        Returns:
            Absolute path to the resolved location.
        """
        base = resolve_xdg_path(purpose)
        if relative:
            return base / relative
        return base

    async def check_writable(self, path: Path) -> bool:
        """Return True if path is writable by the current process.

        Uses ``os.access(path, os.W_OK)`` wrapped in ``asyncio.to_thread()``.

        Args:
            path: The filesystem path to check.

        Returns:
            True if the path is writable, False otherwise.
        """
        return await asyncio.to_thread(os.access, path, os.W_OK)
