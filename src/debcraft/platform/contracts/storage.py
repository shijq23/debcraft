"""Storage contracts defining filesystem layout, lifecycle, and path resolution interfaces."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal

StoragePurpose = Literal["mirror", "workspace", "outputs", "logs", "cache", "database", "config"]
"""Named storage purposes mapping to platform-specific directories."""


class StorageProvider(ABC):
    """Abstraction over the physical storage backend.

    Delegates filesystem operations (directory creation, file removal, path
    resolution, writability checks) to a concrete implementation, allowing
    the StorageEngine to remain decoupled from the underlying OS primitives.
    """

    @abstractmethod
    async def create_directory(self, path: Path) -> None:
        """Create directory and all parents; no-op if it already exists.

        Args:
            path: The directory path to create.
        """
        ...

    @abstractmethod
    async def remove_matching(self, directory: Path, pattern: str) -> None:
        """Remove files/dirs in directory matching glob pattern.

        Args:
            directory: The directory to search within.
            pattern: Glob pattern to match files and directories for removal.
        """
        ...

    @abstractmethod
    def resolve_path(self, purpose: StoragePurpose, relative: str = "") -> Path:
        """Return absolute Path for a storage purpose.

        Args:
            purpose: The named storage purpose to resolve.
            relative: Optional relative path to append within the purpose directory.

        Returns:
            Absolute path to the resolved location.
        """
        ...

    @abstractmethod
    async def check_writable(self, path: Path) -> bool:
        """Return True if path is writable by the current process.

        Args:
            path: The filesystem path to check.

        Returns:
            True if the path is writable, False otherwise.
        """
        ...


class StorageEngine(ABC):
    """Manages filesystem layout, lifecycle, and path resolution.

    The StorageEngine is the top-level coordinator for all persistent storage
    concerns. It creates the XDG-compliant directory layout on initialization,
    removes temporary files, verifies permissions, and provides path resolution
    for all storage purposes.
    """

    @abstractmethod
    async def initialize(self) -> None:
        """Create directories, remove temporaries, verify permissions."""
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        """Flush pending writes and release resources (30-second limit)."""
        ...

    @abstractmethod
    def get_path(self, purpose: StoragePurpose, relative: str = "") -> Path:
        """Return absolute Path for a named storage purpose.

        Args:
            purpose: The named storage purpose to resolve.
            relative: Optional relative path to append within the purpose directory.

        Returns:
            Absolute path to the resolved location.
        """
        ...

    @abstractmethod
    async def __aenter__(self) -> "StorageEngine":
        """Enter async context: calls initialize()."""
        ...

    @abstractmethod
    async def __aexit__(self, *exc: object) -> None:
        """Exit async context: calls shutdown()."""
        ...
