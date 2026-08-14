"""Unit tests for KernelConfigurationService configuration loading and validation."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path

import pytest

from debcraft.platform.kernel.configuration import (
    ExecutionConfig,
    KernelConfigurationService,
    LoggingConfig,
    _coerce_env_value,
    _deep_merge,
    _load_toml_file,
    _parse_env_vars,
)
from debcraft.platform.kernel.errors import (
    ConfigurationSyntaxError,
    ConfigurationValidationError,
)

# ---------------------------------------------------------------------------
# TOML loading with valid files (Requirement 4.1)
# ---------------------------------------------------------------------------


class TestTomlLoadingValid:
    @pytest.mark.unit
    def test_load_valid_toml_file(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "config.toml"
        toml_file.write_text('[logging]\nlevel = "DEBUG"\nformat = "json"\n')

        result = _load_toml_file(toml_file)

        assert result == {"logging": {"level": "DEBUG", "format": "json"}}

    @pytest.mark.unit
    def test_load_empty_toml_file(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "config.toml"
        toml_file.write_text("")

        result = _load_toml_file(toml_file)

        assert result == {}

    @pytest.mark.unit
    def test_load_nonexistent_file_returns_empty_dict(self, tmp_path: Path) -> None:
        missing_file = tmp_path / "does_not_exist.toml"

        result = _load_toml_file(missing_file)

        assert result == {}

    @pytest.mark.unit
    def test_load_toml_with_nested_sections(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "config.toml"
        toml_file.write_text("[execution]\nmax_concurrency = 8\ntimeout_seconds = 600.0\n")

        result = _load_toml_file(toml_file)

        assert result == {"execution": {"max_concurrency": 8, "timeout_seconds": 600.0}}


# ---------------------------------------------------------------------------
# ConfigurationSyntaxError on malformed TOML (Requirement 4.6)
# ---------------------------------------------------------------------------


class TestConfigurationSyntaxError:
    @pytest.mark.unit
    def test_malformed_toml_raises_syntax_error(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text("[logging\nlevel = broken")

        with pytest.raises(ConfigurationSyntaxError) as exc_info:
            _load_toml_file(toml_file)

        assert exc_info.value.file_path == str(toml_file)
        assert exc_info.value.detail != ""

    @pytest.mark.unit
    def test_syntax_error_message_contains_file_path(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "broken.toml"
        toml_file.write_text("= invalid")

        with pytest.raises(ConfigurationSyntaxError, match=r"broken\.toml"):
            _load_toml_file(toml_file)


# ---------------------------------------------------------------------------
# Precedence ordering: env vars override file values (Requirement 4.2)
# ---------------------------------------------------------------------------


class TestPrecedenceOrdering:
    @pytest.mark.unit
    def test_env_vars_override_file_values(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Create a project-level config file with INFO level
        config_file = tmp_path / ".debcraft.toml"
        config_file.write_text('[logging]\nlevel = "INFO"\n')

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DEBCRAFT_LOGGING__LEVEL", "DEBUG")

        service = KernelConfigurationService()
        section = service.get_section(LoggingConfig)

        assert section.level == "DEBUG"

    @pytest.mark.unit
    def test_cli_overrides_env_vars(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DEBCRAFT_LOGGING__LEVEL", "DEBUG")

        service = KernelConfigurationService()
        service.set_cli_overrides({"logging": {"level": "ERROR"}})
        section = service.get_section(LoggingConfig)

        assert section.level == "ERROR"

    @pytest.mark.unit
    def test_project_file_overrides_user_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Set up user-level config
        user_config_dir = tmp_path / "home" / ".config" / "debcraft"
        user_config_dir.mkdir(parents=True)
        user_config = user_config_dir / "config.toml"
        user_config.write_text('[logging]\nlevel = "WARNING"\n')

        # Set up project-level config
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        project_config = project_dir / ".debcraft.toml"
        project_config.write_text('[logging]\nlevel = "ERROR"\n')

        monkeypatch.chdir(project_dir)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))

        service = KernelConfigurationService()
        section = service.get_section(LoggingConfig)

        assert section.level == "ERROR"


# ---------------------------------------------------------------------------
# Environment variable mapping with double underscore (Requirement 4.3)
# ---------------------------------------------------------------------------


class TestEnvironmentVariableMapping:
    @pytest.mark.unit
    def test_double_underscore_separator_maps_to_nested_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEBCRAFT_LOGGING__LEVEL", "DEBUG")

        result = _parse_env_vars()

        assert result == {"logging": {"level": "DEBUG"}}

    @pytest.mark.unit
    def test_env_var_keys_are_lowercased(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEBCRAFT_EXECUTION__MAX_CONCURRENCY", "16")

        result = _parse_env_vars()

        assert "execution" in result
        assert "max_concurrency" in result["execution"]

    @pytest.mark.unit
    def test_env_var_boolean_coercion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEBCRAFT_EXECUTION__FAIL_FAST", "false")

        result = _parse_env_vars()

        assert result["execution"]["fail_fast"] is False

    @pytest.mark.unit
    def test_env_var_integer_coercion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEBCRAFT_EXECUTION__MAX_CONCURRENCY", "8")

        result = _parse_env_vars()

        assert result["execution"]["max_concurrency"] == 8
        assert isinstance(result["execution"]["max_concurrency"], int)

    @pytest.mark.unit
    def test_env_var_float_coercion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEBCRAFT_EXECUTION__TIMEOUT_SECONDS", "120.5")

        result = _parse_env_vars()

        assert result["execution"]["timeout_seconds"] == 120.5

    @pytest.mark.unit
    def test_single_underscore_key_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEBCRAFT_NODOUBLE", "value")

        result = _parse_env_vars()

        assert result == {}

    @pytest.mark.unit
    def test_non_debcraft_prefix_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTHER_SECTION__KEY", "value")

        result = _parse_env_vars()

        # Should not contain anything from the OTHER_ prefix
        assert "other" not in result


# ---------------------------------------------------------------------------
# Frozen dataclass section production (Requirement 4.5)
# ---------------------------------------------------------------------------


class TestFrozenDataclassProduction:
    @pytest.mark.unit
    def test_get_section_returns_frozen_dataclass(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)

        service = KernelConfigurationService()
        section = service.get_section(LoggingConfig)

        assert dataclasses.is_dataclass(section)
        assert isinstance(section, LoggingConfig)

    @pytest.mark.unit
    def test_frozen_dataclass_is_immutable(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)

        service = KernelConfigurationService()
        section = service.get_section(LoggingConfig)

        with pytest.raises(dataclasses.FrozenInstanceError):
            section.level = "ERROR"  # type: ignore[misc]

    @pytest.mark.unit
    def test_default_logging_config_values(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)

        service = KernelConfigurationService()
        section = service.get_section(LoggingConfig)

        assert section.level == "INFO"
        assert section.format == "human"

    @pytest.mark.unit
    def test_default_execution_config_values(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)

        service = KernelConfigurationService()
        section = service.get_section(ExecutionConfig)

        assert section.max_concurrency == 4
        assert section.retry_count == 0
        assert section.retry_backoff_seconds == 1.0
        assert section.timeout_seconds == 300.0
        assert section.fail_fast is True


# ---------------------------------------------------------------------------
# ConfigurationValidationError on invalid values (Requirement 4.10)
# ---------------------------------------------------------------------------


class TestConfigurationValidationError:
    @pytest.mark.unit
    def test_invalid_log_level_raises_validation_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DEBCRAFT_LOGGING__LEVEL", "INVALID")

        service = KernelConfigurationService()

        with pytest.raises(ConfigurationValidationError) as exc_info:
            service.get_section(LoggingConfig)

        assert exc_info.value.field_name == "logging.level"

    @pytest.mark.unit
    def test_invalid_log_format_raises_validation_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DEBCRAFT_LOGGING__FORMAT", "xml")

        service = KernelConfigurationService()

        with pytest.raises(ConfigurationValidationError) as exc_info:
            service.get_section(LoggingConfig)

        assert exc_info.value.field_name == "logging.format"

    @pytest.mark.unit
    def test_negative_max_concurrency_raises_validation_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DEBCRAFT_EXECUTION__MAX_CONCURRENCY", "0")

        service = KernelConfigurationService()

        with pytest.raises(ConfigurationValidationError):
            service.get_section(ExecutionConfig)

    @pytest.mark.unit
    def test_negative_retry_count_raises_validation_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DEBCRAFT_EXECUTION__RETRY_COUNT", "-1")

        service = KernelConfigurationService()

        with pytest.raises(ConfigurationValidationError):
            service.get_section(ExecutionConfig)

    @pytest.mark.unit
    def test_zero_timeout_raises_validation_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DEBCRAFT_EXECUTION__TIMEOUT_SECONDS", "0")

        service = KernelConfigurationService()

        with pytest.raises(ConfigurationValidationError):
            service.get_section(ExecutionConfig)

    @pytest.mark.unit
    def test_unregistered_section_raises_validation_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        @dataclass(frozen=True)
        class UnknownSection:
            value: str = "default"

        service = KernelConfigurationService()

        with pytest.raises(ConfigurationValidationError):
            service.get_section(UnknownSection)


# ---------------------------------------------------------------------------
# Deep merge utility (Requirement 4.2)
# ---------------------------------------------------------------------------


class TestDeepMerge:
    @pytest.mark.unit
    def test_override_scalar_values(self) -> None:
        base = {"a": 1, "b": 2}
        override = {"b": 3}

        result = _deep_merge(base, override)

        assert result == {"a": 1, "b": 3}

    @pytest.mark.unit
    def test_deep_merge_nested_dicts(self) -> None:
        base = {"section": {"key1": "a", "key2": "b"}}
        override = {"section": {"key2": "c"}}

        result = _deep_merge(base, override)

        assert result == {"section": {"key1": "a", "key2": "c"}}

    @pytest.mark.unit
    def test_override_adds_new_keys(self) -> None:
        base = {"a": 1}
        override = {"b": 2}

        result = _deep_merge(base, override)

        assert result == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# Coerce environment value utility
# ---------------------------------------------------------------------------


class TestCoerceEnvValue:
    @pytest.mark.unit
    def test_coerce_true(self) -> None:
        assert _coerce_env_value("true") is True
        assert _coerce_env_value("True") is True

    @pytest.mark.unit
    def test_coerce_false(self) -> None:
        assert _coerce_env_value("false") is False
        assert _coerce_env_value("False") is False

    @pytest.mark.unit
    def test_coerce_integer(self) -> None:
        assert _coerce_env_value("42") == 42
        assert isinstance(_coerce_env_value("42"), int)

    @pytest.mark.unit
    def test_coerce_float(self) -> None:
        assert _coerce_env_value("3.14") == 3.14
        assert isinstance(_coerce_env_value("3.14"), float)

    @pytest.mark.unit
    def test_coerce_string(self) -> None:
        assert _coerce_env_value("hello") == "hello"


# ---------------------------------------------------------------------------
# Plugin section registration (Requirement 4.4)
# ---------------------------------------------------------------------------


class TestPluginSectionRegistration:
    @pytest.mark.unit
    def test_register_custom_section(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)

        @dataclass(frozen=True)
        class PluginConfig:
            enabled: bool = True
            name: str = "default"

        service = KernelConfigurationService()
        service.register_section("plugin", PluginConfig)

        section = service.get_section(PluginConfig)

        assert section.enabled is True
        assert section.name == "default"

    @pytest.mark.unit
    def test_register_custom_section_with_file_data(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_file = tmp_path / ".debcraft.toml"
        config_file.write_text('[myplugin]\nenabled = false\nname = "custom"\n')
        monkeypatch.chdir(tmp_path)

        @dataclass(frozen=True)
        class MyPluginConfig:
            enabled: bool = True
            name: str = "default"

        service = KernelConfigurationService()
        service.register_section("myplugin", MyPluginConfig)

        section = service.get_section(MyPluginConfig)

        assert section.enabled is False
        assert section.name == "custom"
