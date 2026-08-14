"""SquashfsReader implementation backed by PySquashfsImage.

Provides read access to squashfs filesystem images from in-memory byte
data. Operates without mount operations or root privileges.
"""

from __future__ import annotations

from PySquashfsImage import SquashFsImage


class PySquashfsImageReader:
    """SquashfsReader implementation backed by PySquashfsImage.

    Wraps the PySquashfsImage library to conform to the SquashfsReader
    Protocol interface. Accepts raw squashfs image bytes, parses the
    filesystem structure, and provides file reading and directory listing.
    """

    def __init__(self) -> None:
        """Initialize the reader with no squashfs image open."""
        self._image: SquashFsImage | None = None
        self._open: bool = False

    def open(self, data: bytes) -> None:
        """Open a squashfs image from raw bytes.

        Validates that data is non-empty and parses it as a squashfs 4.0
        filesystem image.

        Args:
            data: Raw bytes of the squashfs image.

        Raises:
            OSError: If the reader already has an image open, if data is
                empty, or if data is not a valid squashfs image.
        """
        if self._open:
            raise OSError("Reader already has an image open")
        if not data:
            raise OSError("Cannot open empty squashfs data")
        try:
            self._image = SquashFsImage.from_bytes(data)
        except OSError:
            raise
        except Exception as exc:
            raise OSError(str(exc)) from exc
        self._open = True

    def read_file(self, path: str) -> bytes:
        """Read a file's contents from the squashfs filesystem.

        Normalizes the path by stripping leading slashes, then navigates
        the squashfs inode tree to locate the file.

        Args:
            path: Path to the file within the squashfs filesystem.

        Returns:
            Raw bytes content of the file.

        Raises:
            FileNotFoundError: If the file does not exist or the path
                points to a directory.
        """
        normalized = self._normalize_path(path)
        node = self._image.root.select(normalized)  # type: ignore[union-attr]
        if node is None:
            raise FileNotFoundError(f"File not found in squashfs: {path}")
        if node.is_dir:
            raise FileNotFoundError(f"Path is a directory, not a file: {path}")
        return node.read_bytes()  # type: ignore[no-any-return]

    def list_dir(self, path: str) -> list[str]:
        """List entries in a directory within the squashfs filesystem.

        Normalizes the path by stripping leading slashes. An empty string
        is treated as the root directory.

        Args:
            path: Path within the squashfs filesystem to list.

        Returns:
            List of entry basenames in the directory.

        Raises:
            FileNotFoundError: If the path does not exist or points to
                a file rather than a directory.
        """
        normalized = self._normalize_path(path)
        node = self._image.root if not normalized else self._image.root.select(normalized)  # type: ignore[union-attr]
        if node is None:
            raise FileNotFoundError(f"Directory not found in squashfs: {path}")
        if not node.is_dir:
            raise FileNotFoundError(f"Path is a file, not a directory: {path}")
        return [child.name for child in node.iterdir()]

    def close(self) -> None:
        """Close the squashfs image and release resources.

        Safe to call multiple times or without a prior open.
        """
        self._image = None
        self._open = False

    @staticmethod
    def _normalize_path(path: str) -> str:
        """Normalize a user-provided path by stripping leading slashes.

        Args:
            path: User-provided path, with or without leading slash.

        Returns:
            Path with leading slashes removed. Empty string represents root.
        """
        return path.lstrip("/")
