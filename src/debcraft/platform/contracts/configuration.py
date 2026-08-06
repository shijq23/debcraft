"""Configuration service contract for typed, layered configuration management."""

from abc import ABC, abstractmethod
from typing import TypeVar

T = TypeVar("T")


class ConfigurationService(ABC):
    """Loads, merges, and provides typed configuration.

    The configuration service manages a layered configuration system where
    multiple sources are merged in precedence order. It produces typed,
    frozen dataclass instances for each registered section.
    """

    @abstractmethod
    def get_section(self, section_type: type[T]) -> T:
        """Retrieve a typed configuration section.

        Args:
            section_type: The frozen dataclass type for the section.

        Returns:
            An instance of the requested configuration section type.

        Raises:
            ConfigurationError: If the section type is not registered.
        """
        ...

    @abstractmethod
    def register_section(self, section_key: str, section_type: type[T]) -> None:
        """Register a configuration section type for plugin use.

        Args:
            section_key: The TOML key identifying the section.
            section_type: The frozen dataclass type to map the section into.
        """
        ...

    @abstractmethod
    def reload(self) -> None:
        """Reload configuration from all sources (startup only).

        Raises:
            ConfigurationSyntaxError: If a config file has TOML syntax errors.
            ConfigurationValidationError: If config values fail validation.
        """
        ...
