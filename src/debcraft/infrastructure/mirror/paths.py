"""Mirror cache path derivation utilities.

Provides functions to derive local filesystem paths for mirrored repository
files from base URLs and relative paths, ensuring:
- XDG-compliant cache directory layout
- Hostname + URL path isolation per repository
- Exact relative path preservation from repository metadata
- Standard apt-compatible directory structure
"""

from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from pathlib import Path

    from debcraft.platform.contracts.storage import StorageEngine

# File mode: owner read/write, group read, others read (0o644)
_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH


def derive_mirror_root(storage_engine: StorageEngine, base_url: str) -> Path:
    """Derive the local mirror root path from a repository base URL.

    Maps a repository base URL to a local path under the XDG-compliant
    mirror cache directory:
        {XDG_CACHE_HOME}/debcraft/mirror/{hostname}/{url_path}/

    This ensures separate top-level directories per repository, preventing
    path collisions between different repositories.

    Args:
        storage_engine: StorageEngine providing the base mirror cache path.
        base_url: The repository's base URL (e.g., "https://mirror.elxr.dev/elxr").

    Returns:
        Absolute path to the repository's local mirror root directory.

    Examples:
        >>> # With mirror base = ~/.cache/debcraft/mirror
        >>> derive_mirror_root(engine, "https://mirror.elxr.dev/elxr")
        PosixPath('~/.cache/debcraft/mirror/mirror.elxr.dev/elxr')

        >>> derive_mirror_root(engine, "https://deb.debian.org/debian")
        PosixPath('~/.cache/debcraft/mirror/deb.debian.org/debian')
    """
    parsed = urlparse(base_url)
    hostname = parsed.hostname or "unknown"
    url_path = parsed.path.strip("/")
    mirror_base = storage_engine.get_path("mirror")
    if url_path:
        return mirror_base / hostname / url_path
    return mirror_base / hostname


def derive_file_path(mirror_root: Path, relative_path: str) -> Path:
    """Construct the full local path for a mirrored file.

    Preserves the exact relative path structure from the repository
    metadata, appending it to the mirror root. This maintains the
    standard Debian repository layout (dists/, pool/, etc.) so that
    apt can use the local mirror directly via file:// URIs.

    Args:
        mirror_root: The repository's local mirror root (from derive_mirror_root).
        relative_path: The file's relative path as declared in repository
            metadata (e.g., "dists/elxr3/InRelease" or
            "pool/main/l/libssl3/libssl3_3.0.2-0ubuntu1_amd64.deb").

    Returns:
        Absolute path where the file should be stored locally.

    Examples:
        >>> root = Path("/home/user/.cache/debcraft/mirror/mirror.elxr.dev/elxr")
        >>> derive_file_path(root, "dists/elxr3/InRelease")
        PosixPath('/home/user/.cache/debcraft/mirror/mirror.elxr.dev/elxr/dists/elxr3/InRelease')

        >>> derive_file_path(root, "pool/main/l/libssl3/libssl3_3.0.2-0ubuntu1_amd64.deb")
        PosixPath('/home/user/.cache/debcraft/mirror/mirror.elxr.dev/elxr/pool/main/l/libssl3/libssl3_3.0.2-0ubuntu1_amd64.deb')
    """
    # Strip leading slash/separator to prevent path joining issues
    clean_relative = relative_path.lstrip("/")
    return mirror_root / clean_relative


def set_file_mode(path: Path) -> None:
    """Set file permissions to 0o644 (rw-r--r--).

    Ensures that downloaded files are readable by apt and other tools
    when the mirror is used as an apt source via file:// URI.

    Args:
        path: Path to the file whose mode should be set.
    """
    os.chmod(path, _FILE_MODE)
