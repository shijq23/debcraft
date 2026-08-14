"""Kernel configuration service implementation.

Provides layered TOML-based configuration with environment variable overrides,
typed frozen dataclass sections, and plugin registration support.
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from debcraft.platform.contracts.configuration import ConfigurationService
from debcraft.platform.kernel.errors import (
    ConfigurationSyntaxError,
    ConfigurationValidationError,
)

T = TypeVar("T")

_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})
_VALID_LOG_FORMATS = frozenset({"human", "json"})


@dataclass(frozen=True)
class LoggingConfig:
    """Configuration for the logging subsystem.

    Attributes:
        level: Log level threshold (DEBUG, INFO, WARNING, ERROR).
        format: Output format, either "human" or "json".
    """

    level: str = "INFO"
    format: str = "human"

    def __post_init__(self) -> None:
        """Validate logging configuration values."""
        if self.level.upper() not in _VALID_LOG_LEVELS:
            raise ConfigurationValidationError(
                field_name="logging.level",
                value=self.level,
                reason=f"must be one of {sorted(_VALID_LOG_LEVELS)}",
            )
        if self.format not in _VALID_LOG_FORMATS:
            raise ConfigurationValidationError(
                field_name="logging.format",
                value=self.format,
                reason=f"must be one of {sorted(_VALID_LOG_FORMATS)}",
            )


@dataclass(frozen=True)
class ExecutionConfig:
    """Configuration for default execution policies.

    Attributes:
        max_concurrency: Maximum number of concurrent operations.
        retry_count: Number of retry attempts for failed steps.
        retry_backoff_seconds: Initial backoff delay between retries.
        timeout_seconds: Maximum execution time before cancellation.
        fail_fast: Whether to cancel remaining steps on first failure.
    """

    max_concurrency: int = 4
    retry_count: int = 0
    retry_backoff_seconds: float = 1.0
    timeout_seconds: float = 300.0
    fail_fast: bool = True

    def __post_init__(self) -> None:
        """Validate execution configuration values."""
        if self.max_concurrency < 1:
            raise ConfigurationValidationError(
                field_name="execution.max_concurrency",
                value=self.max_concurrency,
                reason="must be at least 1",
            )
        if self.retry_count < 0:
            raise ConfigurationValidationError(
                field_name="execution.retry_count",
                value=self.retry_count,
                reason="must be non-negative",
            )
        if self.retry_backoff_seconds <= 0:
            raise ConfigurationValidationError(
                field_name="execution.retry_backoff_seconds",
                value=self.retry_backoff_seconds,
                reason="must be positive",
            )
        if self.timeout_seconds <= 0:
            raise ConfigurationValidationError(
                field_name="execution.timeout_seconds",
                value=self.timeout_seconds,
                reason="must be positive",
            )


@dataclass(frozen=True)
class PlatformConfig:
    """Top-level platform configuration.

    Attributes:
        logging: Logging subsystem configuration.
        execution: Execution policy defaults.
    """

    logging: LoggingConfig = field(default_factory=LoggingConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge two dictionaries, with override values taking precedence.

    Args:
        base: The base dictionary to merge into.
        override: The dictionary whose values take precedence.

    Returns:
        A new dictionary with merged values.
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _parse_env_vars() -> dict[str, Any]:
    """Parse DEBCRAFT_ prefixed environment variables into a nested dict.

    Environment variable mapping uses the pattern:
    ``DEBCRAFT_SECTION__KEY`` → ``{"section": {"key": value}}``

    The section and key names are case-insensitive (lowercased).
    Boolean strings ("true"/"false") are converted to Python bools.
    Numeric strings are converted to int or float where applicable.

    Returns:
        A nested dictionary of configuration values from environment variables.
    """
    result: dict[str, Any] = {}
    prefix = "DEBCRAFT_"

    for env_key, env_value in os.environ.items():
        if not env_key.startswith(prefix):
            continue

        # Strip prefix and split on double underscore
        remainder = env_key[len(prefix) :]
        parts = remainder.split("__")

        expected_parts = 2
        if len(parts) != expected_parts:
            # Only support DEBCRAFT_SECTION__KEY format
            continue

        section = parts[0].lower()
        key = parts[1].lower()

        # Type coercion
        converted_value: bool | int | float | str = _coerce_env_value(env_value)

        if section not in result:
            result[section] = {}
        result[section][key] = converted_value

    return result


def _coerce_env_value(value: str) -> bool | int | float | str:
    """Coerce a string environment variable value to an appropriate Python type.

    Args:
        value: The raw string value from the environment.

    Returns:
        The value converted to bool, int, float, or left as str.
    """
    # Boolean coercion
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False

    # Integer coercion
    try:
        return int(value)
    except ValueError:
        pass

    # Float coercion
    try:
        return float(value)
    except ValueError:
        pass

    return value


def _load_toml_file(file_path: Path) -> dict[str, Any]:
    """Load and parse a TOML configuration file.

    Args:
        file_path: Path to the TOML file.

    Returns:
        Parsed TOML content as a dictionary.

    Raises:
        ConfigurationSyntaxError: If the file contains invalid TOML syntax.
    """
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError:
        return {}

    try:
        return tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationSyntaxError(
            file_path=str(file_path),
            detail=str(exc),
        ) from exc


def _dataclass_from_dict[DT](section_type: type[DT], data: dict[str, Any], section_key: str) -> DT:
    """Construct a frozen dataclass instance from a dictionary.

    Only passes keys that correspond to fields on the dataclass. Nested
    dataclass fields are recursively constructed from nested dicts.

    Args:
        section_type: The target frozen dataclass type.
        data: Dictionary of configuration values.
        section_key: The section key name (for error messages).

    Returns:
        An instance of the section dataclass.

    Raises:
        ConfigurationValidationError: If value types cannot be coerced or
            validation in __post_init__ fails.
    """
    valid_fields = {f.name: f for f in dataclasses.fields(section_type)}  # type: ignore[arg-type]
    kwargs: dict[str, Any] = {}

    for key, value in data.items():
        if key not in valid_fields:
            continue
        field_obj = valid_fields[key]
        field_type = field_obj.type

        # Handle nested dataclass fields
        if isinstance(value, dict) and isinstance(field_type, type) and dataclasses.is_dataclass(field_type):
            kwargs[key] = _dataclass_from_dict(field_type, value, f"{section_key}.{key}")
        else:
            kwargs[key] = _coerce_field_value(value, field_type, f"{section_key}.{key}")

    try:
        return section_type(**kwargs)
    except (TypeError, ValueError) as exc:
        raise ConfigurationValidationError(
            field_name=section_key,
            value=data,
            reason=str(exc),
        ) from exc


def _coerce_field_value(value: object, field_type: type[object] | str, field_path: str) -> object:
    """Coerce a value to the expected field type.

    Args:
        value: The raw value to coerce.
        field_type: The expected type annotation (as a string or type).
        field_path: Dotted path to the field for error messages.

    Returns:
        The coerced value.

    Raises:
        ConfigurationValidationError: If coercion fails.
    """
    # Resolve string annotations to actual types
    type_map: dict[str, type[object]] = {
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
    }

    target_type: type[object] = type_map.get(field_type, object) if isinstance(field_type, str) else field_type

    if target_type is bool:
        return _coerce_bool(value, field_path)
    if target_type is int:
        return _coerce_int(value, field_path)
    if target_type is float:
        return _coerce_float(value, field_path)
    if target_type is str:
        return str(value)

    # For unknown types, return as-is
    return value


def _coerce_bool(value: object, field_path: str) -> bool:
    """Coerce a value to bool.

    Args:
        value: The raw value.
        field_path: Dotted path for error messages.

    Returns:
        The boolean value.

    Raises:
        ConfigurationValidationError: If value cannot be coerced to bool.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
    raise ConfigurationValidationError(
        field_name=field_path,
        value=value,
        reason="must be a boolean value",
    )


def _coerce_int(value: object, field_path: str) -> int:
    """Coerce a value to int.

    Args:
        value: The raw value.
        field_path: Dotted path for error messages.

    Returns:
        The integer value.

    Raises:
        ConfigurationValidationError: If value cannot be coerced to int.
    """
    if isinstance(value, bool):
        raise ConfigurationValidationError(
            field_name=field_path,
            value=value,
            reason="must be an integer, not a boolean",
        )
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
    raise ConfigurationValidationError(
        field_name=field_path,
        value=value,
        reason="must be an integer",
    )


def _coerce_float(value: object, field_path: str) -> float:
    """Coerce a value to float.

    Args:
        value: The raw value.
        field_path: Dotted path for error messages.

    Returns:
        The float value.

    Raises:
        ConfigurationValidationError: If value cannot be coerced to float.
    """
    if isinstance(value, bool):
        raise ConfigurationValidationError(
            field_name=field_path,
            value=value,
            reason="must be a number, not a boolean",
        )
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            pass
    raise ConfigurationValidationError(
        field_name=field_path,
        value=value,
        reason="must be a number",
    )


class KernelConfigurationService(ConfigurationService):
    """Layered TOML-based configuration service.

    Loads configuration from multiple sources in precedence order:
    1. Built-in defaults (from frozen dataclass field defaults)
    2. User-level config file (~/.config/debcraft/config.toml)
    3. Project-level config file (.debcraft.toml in cwd)
    4. Environment variables (DEBCRAFT_SECTION__KEY pattern)
    5. CLI arguments (passed via set_cli_overrides)

    Later sources override earlier ones via deep-merge.
    """

    def __init__(self) -> None:
        """Initialize the configuration service with built-in section registry."""
        self._sections: dict[str, type[Any]] = {
            "logging": LoggingConfig,
            "execution": ExecutionConfig,
        }
        self._merged_data: dict[str, Any] = {}
        self._instances: dict[type[Any], Any] = {}
        self._cli_overrides: dict[str, Any] = {}
        self.reload()

    def get_section(self, section_type: type[T]) -> T:
        """Retrieve a typed configuration section.

        Args:
            section_type: The frozen dataclass type for the section.

        Returns:
            An instance of the requested configuration section type.

        Raises:
            ConfigurationValidationError: If the section type is not registered.
        """
        if section_type in self._instances:
            return self._instances[section_type]  # type: ignore[no-any-return]

        # Find the section key for this type
        section_key = self._find_section_key(section_type)
        if section_key is None:
            raise ConfigurationValidationError(
                field_name=section_type.__name__,
                value=None,
                reason="section type is not registered",
            )

        section_data = self._merged_data.get(section_key, {})
        instance = _dataclass_from_dict(section_type, section_data, section_key)
        self._instances[section_type] = instance
        return instance

    def register_section(self, section_key: str, section_type: type[T]) -> None:
        """Register a configuration section type for plugin use.

        Args:
            section_key: The TOML key identifying the section.
            section_type: The frozen dataclass type to map the section into.
        """
        self._sections[section_key] = section_type
        # Invalidate cached instance if previously resolved
        self._instances.pop(section_type, None)

    def reload(self) -> None:
        """Reload configuration from all sources and rebuild sections.

        Reads all configuration sources in precedence order, deep-merges
        them, and invalidates all cached section instances.

        Raises:
            ConfigurationSyntaxError: If a config file has TOML syntax errors.
            ConfigurationValidationError: If config values fail validation.
        """
        # Layer 1: Built-in defaults (empty dict — defaults come from dataclass fields)
        merged: dict[str, Any] = {}

        # Layer 2: User-level config file
        user_config_path = Path.home() / ".config" / "debcraft" / "config.toml"
        if user_config_path.is_file():
            user_data = _load_toml_file(user_config_path)
            merged = _deep_merge(merged, user_data)

        # Layer 3: Project-level config file
        project_config_path = Path.cwd() / ".debcraft.toml"
        if project_config_path.is_file():
            project_data = _load_toml_file(project_config_path)
            merged = _deep_merge(merged, project_data)

        # Layer 4: Environment variables
        env_data = _parse_env_vars()
        merged = _deep_merge(merged, env_data)

        # Layer 5: CLI arguments
        if self._cli_overrides:
            merged = _deep_merge(merged, self._cli_overrides)

        self._merged_data = merged
        # Invalidate all cached instances so they rebuild on next access
        self._instances.clear()

    def set_cli_overrides(self, overrides: dict[str, Any]) -> None:
        """Set CLI argument overrides and reload configuration.

        CLI arguments have the highest precedence in the layered
        configuration system.

        Args:
            overrides: Nested dictionary of CLI override values.
        """
        self._cli_overrides = overrides
        self.reload()

    def get_platform_config(self) -> PlatformConfig:
        """Retrieve the top-level PlatformConfig combining logging and execution.

        Returns:
            A PlatformConfig instance with nested LoggingConfig and ExecutionConfig.
        """
        logging_config = self.get_section(LoggingConfig)
        execution_config = self.get_section(ExecutionConfig)
        return PlatformConfig(logging=logging_config, execution=execution_config)

    def _find_section_key(self, section_type: type[Any]) -> str | None:
        """Find the TOML section key for a given section type.

        Args:
            section_type: The dataclass type to look up.

        Returns:
            The section key string, or None if not registered.
        """
        for key, registered_type in self._sections.items():
            if registered_type is section_type:
                return key
        return None
