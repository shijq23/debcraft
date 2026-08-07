"""Configuration reader for mirrors.toml.

Reads and validates the TOML configuration file for repository mirroring,
falling back to a default eLxr configuration when no file exists.
"""

from __future__ import annotations

import logging
import tomllib
from typing import TYPE_CHECKING

from debcraft.domain.mirror.config import (
    MirrorConfig,
    RepositoryConfig,
    validate_mirror_config,
)
from debcraft.infrastructure.mirror.errors import MirrorConfigurationError

if TYPE_CHECKING:
    from pathlib import Path

    from debcraft.platform.contracts.storage import StorageEngine

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = MirrorConfig(
    repositories=[
        RepositoryConfig(
            name="elxr",
            base_url="https://mirror.elxr.dev/elxr",
            suites=["elxr3"],
            components=["main"],
            architectures=["amd64", "arm64"],
        )
    ],
    download_timeout=300,
    max_connections_per_repo=20,
    max_total_connections=60,
)
"""Default configuration used when mirrors.toml does not exist."""


class ConfigReader:
    """Reads and validates mirrors.toml configuration.

    Uses StorageEngine to resolve the XDG-compliant config path,
    parses TOML content, constructs MirrorConfig, and validates
    all fields using domain validation rules.
    """

    def __init__(self, storage_engine: StorageEngine) -> None:
        """Initialize ConfigReader.

        Args:
            storage_engine: Storage engine for resolving config file paths.
        """
        self._storage_engine = storage_engine

    def read(self) -> MirrorConfig:
        """Read configuration from XDG config path.

        Falls back to default eLxr configuration if file doesn't exist.

        Returns:
            Validated MirrorConfig instance.

        Raises:
            MirrorConfigurationError: If TOML is invalid or fields fail validation.
        """
        config_path = self._storage_engine.get_path("config", "mirrors.toml")

        if not config_path.exists():
            logger.debug(
                "Config file not found, using default configuration",
                extra={"config_path": str(config_path)},
            )
            return DEFAULT_CONFIG

        config = self._parse_toml(config_path)
        errors = self.validate(config)
        if errors:
            raise MirrorConfigurationError(
                message=f"Configuration validation failed: {'; '.join(errors)}",
            )

        logger.info(
            "Configuration loaded",
            extra={
                "config_path": str(config_path),
                "repositories": len(config.repositories),
            },
        )
        return config

    def validate(self, config: MirrorConfig) -> list[str]:
        """Validate config entries, returning list of error messages.

        Delegates to domain-layer validation logic.

        Args:
            config: The MirrorConfig to validate.

        Returns:
            List of error message strings. Empty if valid.
        """
        return validate_mirror_config(config)

    def _parse_toml(self, config_path: Path) -> MirrorConfig:
        """Parse TOML file content into a MirrorConfig.

        Args:
            config_path: Path to the mirrors.toml file.

        Returns:
            MirrorConfig constructed from parsed TOML data.

        Raises:
            MirrorConfigurationError: If TOML syntax is invalid.
        """
        try:
            with config_path.open("rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            line_number = _extract_line_number(e)
            raise MirrorConfigurationError(
                message=f"Invalid TOML syntax in {config_path}",
                line_number=line_number,
                cause=e,
            ) from e

        return self._build_config(data)

    def _build_config(self, data: dict) -> MirrorConfig:
        """Build a MirrorConfig from parsed TOML dictionary.

        Args:
            data: Parsed TOML data as a dictionary.

        Returns:
            Constructed MirrorConfig instance.
        """
        settings = data.get("settings", {})
        repositories_data = data.get("repository", [])

        repositories = [
            RepositoryConfig(
                name=repo.get("name", ""),
                base_url=repo.get("base_url", ""),
                suites=repo.get("suites", []),
                components=repo.get("components", []),
                architectures=repo.get("architectures", []),
            )
            for repo in repositories_data
        ]

        return MirrorConfig(
            repositories=repositories,
            download_timeout=settings.get("download_timeout", 300),
            max_connections_per_repo=settings.get("max_connections_per_repo", 20),
            max_total_connections=settings.get("max_total_connections", 60),
        )


def _extract_line_number(error: tomllib.TOMLDecodeError) -> int | None:
    """Extract line number from a TOML decode error.

    The tomllib error message typically includes position information
    in the format "... (at line X, column Y)" or similar patterns.

    Args:
        error: The TOMLDecodeError to extract line info from.

    Returns:
        The line number if found, None otherwise.
    """
    msg = str(error)
    # tomllib error messages include "at line X, column Y"
    # or "(line X, column Y)" patterns
    import re

    match = re.search(r"line\s+(\d+)", msg)
    if match:
        return int(match.group(1))
    return None
