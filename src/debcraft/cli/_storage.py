"""Shared minimal storage engine for CLI modules.

Provides XDG-compliant path resolution without requiring full platform
bootstrap. Used by CLI commands that need config or mirror paths early
in their lifecycle.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from debcraft.platform.contracts.storage import StorageEngine

if TYPE_CHECKING:
    from debcraft.platform.contracts.storage import StoragePurpose


class MinimalStorageEngine(StorageEngine):
    """XDG-compliant storage engine for CLI context (no full platform bootstrap).

    Provides just enough of the StorageEngine interface (get_path)
    for ConfigReader and MirrorEngine to resolve paths without requiring
    full platform bootstrap.

    Supports the ``config`` and ``mirror`` storage purposes only.
    """

    def __init__(self) -> None:
        xdg_config = os.environ.get("XDG_CONFIG_HOME", "")
        if xdg_config:
            self._config_dir = Path(xdg_config) / "debcraft"
        else:
            self._config_dir = Path.home() / ".config" / "debcraft"

        xdg_cache = os.environ.get("XDG_CACHE_HOME", "")
        if xdg_cache:
            self._cache_dir = Path(xdg_cache) / "debcraft"
        else:
            self._cache_dir = Path.home() / ".cache" / "debcraft"

    async def initialize(self) -> None:
        """No-op for CLI context."""

    async def shutdown(self) -> None:
        """No-op for CLI context."""

    def get_path(self, purpose: StoragePurpose, relative: str = "") -> Path:
        """Resolve path for a storage purpose.

        Args:
            purpose: The named storage purpose ('config' or 'mirror').
            relative: Optional relative path within the purpose directory.

        Returns:
            Absolute path to the resolved location.

        Raises:
            ValueError: If purpose is not 'config' or 'mirror'.
        """
        if purpose == "config":
            base = self._config_dir
        elif purpose == "mirror":
            base = self._cache_dir / "mirror"
        else:
            msg = f"Unsupported storage purpose for CLI: {purpose}"
            raise ValueError(msg)

        if relative:
            return base / relative
        return base

    async def __aenter__(self) -> MinimalStorageEngine:
        """Enter async context."""
        await self.initialize()
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Exit async context."""
        await self.shutdown()
