"""ISOReader implementation backed by pycdlib with Rock Ridge support.

Provides read access to ISO 9660 images using Rock Ridge extensions for
POSIX long filename support. Operates without mount operations or root
privileges.
"""

from __future__ import annotations

import pycdlib
from pycdlib.pycdlibexception import PyCdlibInvalidInput, PyCdlibInvalidISO


class PyCdlibISOReader:
    """ISOReader implementation backed by pycdlib with Rock Ridge support.

    Wraps the pycdlib library to conform to the ISOReader Protocol interface.
    Uses Rock Ridge path resolution for all filesystem operations, enabling
    support for Linux distribution ISOs with long filenames.
    """

    def __init__(self) -> None:
        """Initialize the reader with no ISO image open."""
        self._iso: pycdlib.PyCdlib | None = None

    def open(self, path: str) -> None:
        """Open an ISO 9660 image for reading.

        Creates a new PyCdlib instance and opens the specified file path.
        Rock Ridge extensions are automatically detected during parsing.

        Args:
            path: Filesystem path to the ISO image file.

        Raises:
            OSError: If the file cannot be opened or is not valid ISO 9660.
        """
        try:
            iso = pycdlib.PyCdlib()
            iso.open(path)
        except PyCdlibInvalidISO as exc:
            raise OSError(str(exc)) from exc
        except OSError:
            raise
        except Exception as exc:
            raise OSError(str(exc)) from exc
        self._iso = iso

    def list_dir(self, path: str) -> list[str]:
        """List entries in a directory within the ISO filesystem.

        Normalizes the path to an absolute Rock Ridge path and returns
        decoded basenames, excluding "." and ".." entries.

        Args:
            path: Path within the ISO filesystem to list. An empty string
                is treated as the root directory.

        Returns:
            List of entry basenames in the directory.

        Raises:
            FileNotFoundError: If the path does not exist or is a file.
        """
        rr_path = self._normalize_path(path)
        try:
            children = list(self._iso.list_children(rr_path=rr_path))  # type: ignore[union-attr]
        except PyCdlibInvalidInput as exc:
            raise FileNotFoundError(str(exc)) from exc
        except (IndexError, AttributeError) as exc:
            raise FileNotFoundError(f"Path not found in ISO: {path}") from exc

        entries: list[str] = []
        for child in children:
            if child.is_dot() or child.is_dotdot():
                continue
            if child.rock_ridge is not None:
                name = child.rock_ridge.name().decode("utf-8")
            else:
                name = child.file_identifier.decode("utf-8")
            entries.append(name)
        return entries

    def read_file(self, path: str) -> bytes:
        """Read a file's contents from the ISO filesystem.

        Normalizes the path to an absolute Rock Ridge path and reads
        the complete file contents.

        Args:
            path: Path to the file within the ISO filesystem.

        Returns:
            Raw bytes content of the file.

        Raises:
            FileNotFoundError: If the file does not exist or is a directory.
        """
        rr_path = self._normalize_path(path)
        try:
            with self._iso.open_file_from_iso(rr_path=rr_path) as f:  # type: ignore[union-attr]
                return f.read()  # type: ignore[no-any-return]
        except PyCdlibInvalidInput as exc:
            raise FileNotFoundError(str(exc)) from exc
        except (IndexError, AttributeError) as exc:
            raise FileNotFoundError(f"Path not found in ISO: {path}") from exc

    def close(self) -> None:
        """Close the ISO image and release resources.

        Safe to call multiple times or without a prior open.
        """
        if self._iso is not None:
            self._iso.close()
            self._iso = None

    @staticmethod
    def _normalize_path(path: str) -> str:
        """Normalize a user-provided path to an absolute Rock Ridge path.

        Args:
            path: User-provided path, with or without leading slash.

        Returns:
            Absolute path suitable for Rock Ridge operations (starts with "/").
        """
        stripped = path.strip("/")
        if not stripped:
            return "/"
        return "/" + stripped
