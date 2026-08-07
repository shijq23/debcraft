"""Unit tests for ConfigReader.

Verifies TOML parsing, default config fallback, validation error reporting,
and invalid syntax error handling with line numbers.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from debcraft.domain.mirror.config import MirrorConfig, RepositoryConfig
from debcraft.infrastructure.mirror.config_reader import DEFAULT_CONFIG, ConfigReader
from debcraft.infrastructure.mirror.errors import MirrorConfigurationError


def _make_storage_engine(config_path: Path) -> MagicMock:
    """Create a mock StorageEngine that returns the given path for config lookups."""
    storage_engine = MagicMock()
    storage_engine.get_path.return_value = config_path
    return storage_engine


VALID_TOML = """\
[settings]
download_timeout = 600
max_connections_per_repo = 10
max_total_connections = 30

[[repository]]
name = "debian"
base_url = "https://deb.debian.org/debian"
suites = ["bookworm"]
components = ["main", "contrib"]
architectures = ["amd64"]
"""

MULTI_REPO_TOML = """\
[settings]
download_timeout = 300

[[repository]]
name = "debian"
base_url = "https://deb.debian.org/debian"
suites = ["bookworm"]
components = ["main"]
architectures = ["amd64"]

[[repository]]
name = "elxr"
base_url = "https://mirror.elxr.dev/elxr"
suites = ["elxr3"]
components = ["main"]
architectures = ["amd64", "arm64"]
"""


@pytest.mark.unit
@pytest.mark.mirror
class TestConfigReaderValidToml:
    """When config file exists with valid TOML, returns parsed MirrorConfig."""

    def test_parses_single_repository(self, tmp_path: Path) -> None:
        config_file = tmp_path / "mirrors.toml"
        config_file.write_text(VALID_TOML)
        storage_engine = _make_storage_engine(config_file)

        reader = ConfigReader(storage_engine)
        config = reader.read()

        assert isinstance(config, MirrorConfig)
        assert len(config.repositories) == 1
        repo = config.repositories[0]
        assert repo.name == "debian"
        assert repo.base_url == "https://deb.debian.org/debian"
        assert repo.suites == ["bookworm"]
        assert repo.components == ["main", "contrib"]
        assert repo.architectures == ["amd64"]

    def test_parses_settings(self, tmp_path: Path) -> None:
        config_file = tmp_path / "mirrors.toml"
        config_file.write_text(VALID_TOML)
        storage_engine = _make_storage_engine(config_file)

        reader = ConfigReader(storage_engine)
        config = reader.read()

        assert config.download_timeout == 600
        assert config.max_connections_per_repo == 10
        assert config.max_total_connections == 30

    def test_parses_multiple_repositories(self, tmp_path: Path) -> None:
        config_file = tmp_path / "mirrors.toml"
        config_file.write_text(MULTI_REPO_TOML)
        storage_engine = _make_storage_engine(config_file)

        reader = ConfigReader(storage_engine)
        config = reader.read()

        assert len(config.repositories) == 2
        assert config.repositories[0].name == "debian"
        assert config.repositories[1].name == "elxr"

    def test_resolves_config_path_via_storage_engine(self, tmp_path: Path) -> None:
        config_file = tmp_path / "mirrors.toml"
        config_file.write_text(VALID_TOML)
        storage_engine = _make_storage_engine(config_file)

        reader = ConfigReader(storage_engine)
        reader.read()

        storage_engine.get_path.assert_called_once_with("config", "mirrors.toml")


@pytest.mark.unit
@pytest.mark.mirror
class TestConfigReaderDefaultFallback:
    """When config file doesn't exist, returns DEFAULT_CONFIG."""

    def test_returns_default_config_when_file_missing(self, tmp_path: Path) -> None:
        config_file = tmp_path / "nonexistent" / "mirrors.toml"
        storage_engine = _make_storage_engine(config_file)

        reader = ConfigReader(storage_engine)
        config = reader.read()

        assert config is DEFAULT_CONFIG

    def test_default_config_has_elxr_repository(self) -> None:
        assert len(DEFAULT_CONFIG.repositories) == 1
        repo = DEFAULT_CONFIG.repositories[0]
        assert repo.name == "elxr"
        assert repo.base_url == "https://mirror.elxr.dev/elxr"
        assert repo.suites == ["elxr3"]
        assert repo.components == ["main"]
        assert repo.architectures == ["amd64", "arm64"]

    def test_default_config_timeout(self) -> None:
        assert DEFAULT_CONFIG.download_timeout == 300


@pytest.mark.unit
@pytest.mark.mirror
class TestConfigReaderValidationErrors:
    """When config has validation errors, raises MirrorConfigurationError."""

    def test_empty_name_raises_configuration_error(self, tmp_path: Path) -> None:
        toml_content = """\
[[repository]]
name = ""
base_url = "https://example.com"
suites = ["stable"]
components = ["main"]
architectures = ["amd64"]
"""
        config_file = tmp_path / "mirrors.toml"
        config_file.write_text(toml_content)
        storage_engine = _make_storage_engine(config_file)

        reader = ConfigReader(storage_engine)
        with pytest.raises(MirrorConfigurationError) as exc_info:
            reader.read()

        assert "validation failed" in str(exc_info.value).lower()

    def test_invalid_url_raises_configuration_error(self, tmp_path: Path) -> None:
        toml_content = """\
[[repository]]
name = "test"
base_url = "ftp://invalid.example.com"
suites = ["stable"]
components = ["main"]
architectures = ["amd64"]
"""
        config_file = tmp_path / "mirrors.toml"
        config_file.write_text(toml_content)
        storage_engine = _make_storage_engine(config_file)

        reader = ConfigReader(storage_engine)
        with pytest.raises(MirrorConfigurationError):
            reader.read()

    def test_empty_suites_raises_configuration_error(self, tmp_path: Path) -> None:
        toml_content = """\
[[repository]]
name = "test"
base_url = "https://example.com"
suites = []
components = ["main"]
architectures = ["amd64"]
"""
        config_file = tmp_path / "mirrors.toml"
        config_file.write_text(toml_content)
        storage_engine = _make_storage_engine(config_file)

        reader = ConfigReader(storage_engine)
        with pytest.raises(MirrorConfigurationError):
            reader.read()


@pytest.mark.unit
@pytest.mark.mirror
class TestConfigReaderInvalidTomlSyntax:
    """When config has invalid TOML syntax, raises MirrorConfigurationError with line_number."""

    def test_invalid_toml_raises_configuration_error(self, tmp_path: Path) -> None:
        invalid_toml = """\
[settings]
download_timeout = 300

[[repository]
name = "broken"
"""
        config_file = tmp_path / "mirrors.toml"
        config_file.write_text(invalid_toml)
        storage_engine = _make_storage_engine(config_file)

        reader = ConfigReader(storage_engine)
        with pytest.raises(MirrorConfigurationError) as exc_info:
            reader.read()

        assert "Invalid TOML syntax" in str(exc_info.value)

    def test_invalid_toml_includes_line_number(self, tmp_path: Path) -> None:
        # Malformed value on a known line
        invalid_toml = """\
[settings]
download_timeout = 300

[[repository]]
name = "test
"""
        config_file = tmp_path / "mirrors.toml"
        config_file.write_text(invalid_toml)
        storage_engine = _make_storage_engine(config_file)

        reader = ConfigReader(storage_engine)
        with pytest.raises(MirrorConfigurationError) as exc_info:
            reader.read()

        err = exc_info.value
        assert err.line_number is not None
        assert err.line_number > 0

    def test_invalid_toml_preserves_cause(self, tmp_path: Path) -> None:
        invalid_toml = "[[broken"
        config_file = tmp_path / "mirrors.toml"
        config_file.write_text(invalid_toml)
        storage_engine = _make_storage_engine(config_file)

        reader = ConfigReader(storage_engine)
        with pytest.raises(MirrorConfigurationError) as exc_info:
            reader.read()

        import tomllib

        assert exc_info.value.__cause__ is not None
        assert isinstance(exc_info.value.__cause__, tomllib.TOMLDecodeError)


@pytest.mark.unit
@pytest.mark.mirror
class TestConfigReaderValidateMethod:
    """Test that validate() delegates to domain validation correctly."""

    def test_validate_returns_empty_list_for_valid_config(self) -> None:
        storage_engine = MagicMock()
        reader = ConfigReader(storage_engine)

        config = MirrorConfig(
            repositories=[
                RepositoryConfig(
                    name="test",
                    base_url="https://example.com",
                    suites=["stable"],
                    components=["main"],
                    architectures=["amd64"],
                )
            ],
            download_timeout=300,
        )

        errors = reader.validate(config)
        assert errors == []

    def test_validate_returns_errors_for_invalid_config(self) -> None:
        storage_engine = MagicMock()
        reader = ConfigReader(storage_engine)

        config = MirrorConfig(
            repositories=[
                RepositoryConfig(
                    name="",
                    base_url="https://example.com",
                    suites=["stable"],
                    components=["main"],
                    architectures=["amd64"],
                )
            ],
            download_timeout=300,
        )

        errors = reader.validate(config)
        assert len(errors) > 0
        assert any("name" in e.lower() for e in errors)

    def test_validate_reports_timeout_out_of_range(self) -> None:
        storage_engine = MagicMock()
        reader = ConfigReader(storage_engine)

        config = MirrorConfig(
            repositories=[
                RepositoryConfig(
                    name="test",
                    base_url="https://example.com",
                    suites=["stable"],
                    components=["main"],
                    architectures=["amd64"],
                )
            ],
            download_timeout=10,  # Below minimum of 30
        )

        errors = reader.validate(config)
        assert len(errors) > 0
        assert any("download_timeout" in e for e in errors)
