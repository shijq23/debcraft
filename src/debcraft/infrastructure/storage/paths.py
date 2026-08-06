"""Cross-platform XDG path resolution for storage purposes.

Resolves filesystem paths according to the XDG Base Directory Specification
on Linux, with platform-appropriate fallbacks for macOS and Windows.
All paths are returned as absolute ``pathlib.Path`` instances.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from debcraft.platform.contracts.storage import StoragePurpose

# Application name used in all platform path constructions.
_APP_NAME = "debcraft"


def resolve_xdg_path(
    purpose: StoragePurpose,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> Path:
    """Resolve an absolute path for the given storage purpose.

    Uses XDG Base Directory environment variables on Linux, with
    platform-specific fallbacks on macOS (``~/Library/...``) and
    Windows (``%LOCALAPPDATA%`` / ``%APPDATA%``).

    Args:
        purpose: The named storage purpose to resolve a path for.
        environ: Environment variable mapping; defaults to ``os.environ``.
        platform: Platform identifier (``sys.platform`` value); defaults
            to the current runtime platform.

    Returns:
        An absolute ``pathlib.Path`` to the directory for the given purpose.
    """
    if environ is None:
        environ = os.environ
    if platform is None:
        platform = sys.platform

    if platform == "linux":
        return _resolve_linux(purpose, environ)
    if platform == "darwin":
        return _resolve_darwin(purpose, environ)
    if platform == "win32":
        return _resolve_windows(purpose, environ)

    # Fallback: treat unknown platforms like Linux.
    return _resolve_linux(purpose, environ)


def _resolve_linux(purpose: StoragePurpose, environ: Mapping[str, str]) -> Path:
    """Resolve paths using XDG variables with Linux defaults."""
    home = Path(environ.get("HOME", "~")).expanduser()

    if purpose in ("mirror", "workspace", "outputs", "logs", "cache"):
        xdg_cache = environ.get("XDG_CACHE_HOME", "")
        base = Path(xdg_cache) if xdg_cache else home / ".cache"
        return base / _APP_NAME / purpose

    if purpose == "database":
        xdg_data = environ.get("XDG_DATA_HOME", "")
        base = Path(xdg_data) if xdg_data else home / ".local" / "share"
        return base / _APP_NAME

    # purpose == "config"
    xdg_config = environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg_config) if xdg_config else home / ".config"
    return base / _APP_NAME


def _resolve_darwin(purpose: StoragePurpose, environ: Mapping[str, str]) -> Path:
    """Resolve paths using macOS Library conventions.

    On macOS, XDG variables are still honored when set. If absent,
    macOS-specific Library subdirectories are used as fallbacks.
    """
    home = Path(environ.get("HOME", "~")).expanduser()

    if purpose in ("mirror", "workspace", "outputs", "logs", "cache"):
        xdg_cache = environ.get("XDG_CACHE_HOME", "")
        base = Path(xdg_cache) if xdg_cache else home / "Library" / "Caches"
        return base / _APP_NAME / purpose

    if purpose == "database":
        xdg_data = environ.get("XDG_DATA_HOME", "")
        base = Path(xdg_data) if xdg_data else home / "Library" / "Application Support"
        return base / _APP_NAME

    # purpose == "config"
    xdg_config = environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg_config) if xdg_config else home / "Library" / "Preferences"
    return base / _APP_NAME


def _resolve_windows(purpose: StoragePurpose, environ: Mapping[str, str]) -> Path:
    """Resolve paths using Windows LOCALAPPDATA/APPDATA conventions.

    On Windows, XDG variables are honored when set. If absent,
    ``%LOCALAPPDATA%`` is used for cache purposes and ``%APPDATA%``
    for data and config purposes.
    """
    home = Path(environ.get("USERPROFILE", "~")).expanduser()

    if purpose in ("mirror", "workspace", "outputs", "logs", "cache"):
        xdg_cache = environ.get("XDG_CACHE_HOME", "")
        if xdg_cache:
            base = Path(xdg_cache)
        else:
            localappdata = environ.get("LOCALAPPDATA", "")
            base = Path(localappdata) if localappdata else home / "AppData" / "Local"
        return base / _APP_NAME / "cache" / purpose

    if purpose == "database":
        xdg_data = environ.get("XDG_DATA_HOME", "")
        if xdg_data:
            base = Path(xdg_data)
        else:
            appdata = environ.get("APPDATA", "")
            base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / _APP_NAME

    # purpose == "config"
    xdg_config = environ.get("XDG_CONFIG_HOME", "")
    if xdg_config:
        base = Path(xdg_config)
    else:
        appdata = environ.get("APPDATA", "")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
    return base / _APP_NAME / "config"
