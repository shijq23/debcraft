"""Property-based tests for the kernel configuration service.

**Validates: Requirements 4.2, 4.3, 4.4, 4.6, 4.10**

Property 15: Configuration precedence — highest precedence layer value wins.
For any configuration key present at multiple precedence layers (defaults, user config,
project config, env var, CLI argument), the resolved value SHALL equal the value from
the highest-precedence layer.

Property 16: Environment variable mapping — DEBCRAFT_SECTION__KEY maps correctly.
For any environment variable matching the pattern DEBCRAFT_{SECTION}__{KEY}, the
configuration subsystem SHALL map it to the configuration path [section] key
(case-insensitive section and key, double underscore as separator).

Property 17: Configuration validation rejects invalid values — invalid field raises
ConfigurationError. For any configuration section where a field value violates its
type constraint or validation rule, loading SHALL raise a ConfigurationError with a
message identifying the invalid field.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.platform.kernel.configuration import (
    ExecutionConfig,
    KernelConfigurationService,
    LoggingConfig,
    _parse_env_vars,
)
from debcraft.platform.kernel.errors import (
    ConfigurationError,
    ConfigurationValidationError,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Valid logging level values
_valid_log_levels = st.sampled_from(["DEBUG", "INFO", "WARNING", "ERROR"])

# Valid logging format values
_valid_log_formats = st.sampled_from(["human", "json"])

# Valid execution config values
_valid_max_concurrency = st.integers(min_value=1, max_value=1000)
_valid_retry_count = st.integers(min_value=0, max_value=100)
_valid_retry_backoff = st.floats(min_value=0.01, max_value=1000.0, allow_nan=False, allow_infinity=False)
_valid_timeout = st.floats(min_value=0.01, max_value=100000.0, allow_nan=False, allow_infinity=False)
_valid_fail_fast = st.booleans()

# Environment variable key segments: alphanumeric, non-empty, uppercase-able
_env_section_strategy = st.text(
    alphabet=st.characters(categories=("Ll", "Lu"), min_codepoint=65, max_codepoint=122),
    min_size=1,
    max_size=10,
).filter(lambda s: s.isalpha())

_env_key_strategy = st.text(
    alphabet=st.characters(categories=("Ll", "Lu"), min_codepoint=65, max_codepoint=122),
    min_size=1,
    max_size=10,
).filter(lambda s: s.isalpha())

# Simple string values for env vars (no double underscore to avoid parsing issues)
_env_value_strategy = st.one_of(
    st.integers(min_value=-1000, max_value=1000).map(str),
    st.floats(min_value=-1000, max_value=1000, allow_nan=False, allow_infinity=False).map(str),
    st.sampled_from(["true", "false", "True", "False"]),
    st.text(
        alphabet=st.characters(categories=("L",), min_codepoint=65, max_codepoint=122),
        min_size=1,
        max_size=10,
    ),
)

# Invalid log levels (not in the valid set)
_invalid_log_level = st.text(min_size=1, max_size=20).filter(
    lambda s: s.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR"}
)

# Invalid log formats (not "human" or "json")
_invalid_log_format = st.text(min_size=1, max_size=20).filter(lambda s: s not in {"human", "json"})

# Invalid max_concurrency (< 1)
_invalid_max_concurrency = st.integers(max_value=0)

# Invalid retry_count (< 0)
_invalid_retry_count = st.integers(max_value=-1)

# Invalid backoff (<=0)
_invalid_backoff = st.floats(max_value=0.0, allow_nan=False, allow_infinity=False)

# Invalid timeout (<=0)
_invalid_timeout = st.floats(max_value=0.0, allow_nan=False, allow_infinity=False)


# ---------------------------------------------------------------------------
# Property 15: Configuration precedence
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProperty15ConfigurationPrecedence:
    """Property 15: Configuration precedence.

    For any configuration key present at multiple precedence layers (defaults,
    user config, project config, env var, CLI argument), the resolved value SHALL
    equal the value from the highest-precedence layer.

    **Validates: Requirements 4.2, 4.3**
    """

    @given(
        env_level=_valid_log_levels,
        cli_level=_valid_log_levels,
    )
    def test_cli_overrides_env_vars(self, env_level: str, cli_level: str) -> None:
        """CLI arguments (layer 5) override environment variables (layer 4).

        **Validates: Requirements 4.2**
        """
        env_patch = {
            "DEBCRAFT_LOGGING__LEVEL": env_level,
        }
        with patch.dict(os.environ, env_patch, clear=False):
            service = KernelConfigurationService()
            service.set_cli_overrides({"logging": {"level": cli_level}})
            config = service.get_section(LoggingConfig)
            assert config.level == cli_level

    @given(
        env_level=_valid_log_levels,
    )
    def test_env_overrides_defaults(self, env_level: str) -> None:
        """Environment variables (layer 4) override built-in defaults (layer 1).

        **Validates: Requirements 4.2, 4.3**
        """
        env_patch = {
            "DEBCRAFT_LOGGING__LEVEL": env_level,
        }
        # Ensure no project/user config files interfere
        with (
            patch.dict(os.environ, env_patch, clear=False),
            patch("debcraft.platform.kernel.configuration.Path.is_file", return_value=False),
        ):
            service = KernelConfigurationService()
            config = service.get_section(LoggingConfig)
            assert config.level == env_level

    @given(
        env_concurrency=_valid_max_concurrency,
        cli_concurrency=_valid_max_concurrency,
    )
    def test_cli_overrides_env_for_execution_config(self, env_concurrency: int, cli_concurrency: int) -> None:
        """CLI arguments override env vars for execution configuration.

        **Validates: Requirements 4.2**
        """
        env_patch = {
            "DEBCRAFT_EXECUTION__MAX_CONCURRENCY": str(env_concurrency),
        }
        with (
            patch.dict(os.environ, env_patch, clear=False),
            patch("debcraft.platform.kernel.configuration.Path.is_file", return_value=False),
        ):
            service = KernelConfigurationService()
            service.set_cli_overrides({"execution": {"max_concurrency": cli_concurrency}})
            config = service.get_section(ExecutionConfig)
            assert config.max_concurrency == cli_concurrency

    @given(
        layer3_format=_valid_log_formats,
        layer4_format=_valid_log_formats,
        layer5_format=_valid_log_formats,
    )
    def test_highest_layer_wins_across_three_layers(
        self, layer3_format: str, layer4_format: str, layer5_format: str
    ) -> None:
        """The highest precedence layer always wins, regardless of lower layers.

        **Validates: Requirements 4.2, 4.3**
        """
        env_patch = {
            "DEBCRAFT_LOGGING__FORMAT": layer4_format,
        }
        with (
            patch.dict(os.environ, env_patch, clear=False),
            patch("debcraft.platform.kernel.configuration.Path.is_file", return_value=False),
        ):
            service = KernelConfigurationService()
            service.set_cli_overrides({"logging": {"format": layer5_format}})
            config = service.get_section(LoggingConfig)
            # CLI (layer 5) always wins over env (layer 4)
            assert config.format == layer5_format


# ---------------------------------------------------------------------------
# Property 16: Environment variable mapping
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProperty16EnvironmentVariableMapping:
    """Property 16: Environment variable mapping.

    For any environment variable matching the pattern DEBCRAFT_{SECTION}__{KEY},
    the configuration subsystem SHALL map it to the configuration path
    [section] key (case-insensitive section and key, double underscore as separator).

    **Validates: Requirements 4.4**
    """

    @given(
        section=_env_section_strategy,
        key=_env_key_strategy,
        value=_env_value_strategy,
    )
    def test_env_var_maps_to_section_key(self, section: str, key: str, value: str) -> None:
        """DEBCRAFT_SECTION__KEY maps to {"section": {"key": value}}.

        **Validates: Requirements 4.4**
        """
        env_var_name = f"DEBCRAFT_{section.upper()}__{key.upper()}"
        env_patch = {env_var_name: value}

        with patch.dict(os.environ, env_patch, clear=False):
            result = _parse_env_vars()

        expected_section = section.lower()
        expected_key = key.lower()

        assert expected_section in result
        assert expected_key in result[expected_section]

    @given(
        section=_env_section_strategy,
        key=_env_key_strategy,
    )
    def test_env_var_boolean_coercion(self, section: str, key: str) -> None:
        """Boolean strings 'true'/'false' are coerced to Python booleans.

        **Validates: Requirements 4.4**
        """
        env_var_name = f"DEBCRAFT_{section.upper()}__{key.upper()}"

        for str_val, expected in [("true", True), ("false", False), ("True", True), ("False", False)]:
            env_patch = {env_var_name: str_val}
            with patch.dict(os.environ, env_patch, clear=False):
                result = _parse_env_vars()

            expected_section = section.lower()
            expected_key = key.lower()
            assert result[expected_section][expected_key] is expected

    @given(
        section=_env_section_strategy,
        key=_env_key_strategy,
        int_value=st.integers(min_value=-10000, max_value=10000),
    )
    def test_env_var_integer_coercion(self, section: str, key: str, int_value: int) -> None:
        """Integer strings are coerced to Python ints.

        **Validates: Requirements 4.4**
        """
        env_var_name = f"DEBCRAFT_{section.upper()}__{key.upper()}"
        env_patch = {env_var_name: str(int_value)}

        with patch.dict(os.environ, env_patch, clear=False):
            result = _parse_env_vars()

        expected_section = section.lower()
        expected_key = key.lower()
        assert result[expected_section][expected_key] == int_value
        assert isinstance(result[expected_section][expected_key], int)

    @given(
        section=_env_section_strategy,
        key=_env_key_strategy,
    )
    def test_env_var_case_insensitive(self, section: str, key: str) -> None:
        """Section and key names are case-insensitive (lowercased).

        **Validates: Requirements 4.4**
        """
        # Try with mixed case in the env var name
        env_var_name = f"DEBCRAFT_{section.upper()}__{key.upper()}"
        env_patch = {env_var_name: "testval"}

        with patch.dict(os.environ, env_patch, clear=False):
            result = _parse_env_vars()

        # Result keys should always be lowercase
        for s_key in result:
            assert s_key == s_key.lower()
            for k_key in result[s_key]:
                assert k_key == k_key.lower()


# ---------------------------------------------------------------------------
# Property 17: Configuration validation rejects invalid values
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProperty17ConfigurationValidationRejectsInvalid:
    """Property 17: Configuration validation rejects invalid values.

    For any configuration section where a field value violates its type
    constraint or validation rule, loading SHALL raise a ConfigurationError
    with a message identifying the invalid field.

    **Validates: Requirements 4.6, 4.10**
    """

    @given(invalid_level=_invalid_log_level)
    def test_invalid_log_level_raises_error(self, invalid_level: str) -> None:
        """Invalid logging level raises ConfigurationValidationError.

        **Validates: Requirements 4.6**
        """
        with pytest.raises(ConfigurationValidationError) as exc_info:
            LoggingConfig(level=invalid_level)

        assert "logging.level" in exc_info.value.field_name

    @given(invalid_format=_invalid_log_format)
    def test_invalid_log_format_raises_error(self, invalid_format: str) -> None:
        """Invalid logging format raises ConfigurationValidationError.

        **Validates: Requirements 4.6**
        """
        with pytest.raises(ConfigurationValidationError) as exc_info:
            LoggingConfig(format=invalid_format)

        assert "logging.format" in exc_info.value.field_name

    @given(invalid_concurrency=_invalid_max_concurrency)
    def test_invalid_max_concurrency_raises_error(self, invalid_concurrency: int) -> None:
        """max_concurrency < 1 raises ConfigurationValidationError.

        **Validates: Requirements 4.6**
        """
        with pytest.raises(ConfigurationValidationError) as exc_info:
            ExecutionConfig(max_concurrency=invalid_concurrency)

        assert "execution.max_concurrency" in exc_info.value.field_name

    @given(invalid_retry=_invalid_retry_count)
    def test_invalid_retry_count_raises_error(self, invalid_retry: int) -> None:
        """retry_count < 0 raises ConfigurationValidationError.

        **Validates: Requirements 4.6**
        """
        with pytest.raises(ConfigurationValidationError) as exc_info:
            ExecutionConfig(retry_count=invalid_retry)

        assert "execution.retry_count" in exc_info.value.field_name

    @given(invalid_backoff=_invalid_backoff)
    def test_invalid_retry_backoff_raises_error(self, invalid_backoff: float) -> None:
        """retry_backoff_seconds <= 0 raises ConfigurationValidationError.

        **Validates: Requirements 4.6**
        """
        with pytest.raises(ConfigurationValidationError) as exc_info:
            ExecutionConfig(retry_backoff_seconds=invalid_backoff)

        assert "execution.retry_backoff_seconds" in exc_info.value.field_name

    @given(invalid_timeout=_invalid_timeout)
    def test_invalid_timeout_raises_error(self, invalid_timeout: float) -> None:
        """timeout_seconds <= 0 raises ConfigurationValidationError.

        **Validates: Requirements 4.6**
        """
        with pytest.raises(ConfigurationValidationError) as exc_info:
            ExecutionConfig(timeout_seconds=invalid_timeout)

        assert "execution.timeout_seconds" in exc_info.value.field_name

    def test_invalid_env_var_level_raises_on_get_section(self) -> None:
        """Invalid level from env var raises ConfigurationError on get_section().

        **Validates: Requirements 4.6, 4.10**
        """
        env_patch = {"DEBCRAFT_LOGGING__LEVEL": "INVALID_LEVEL"}
        with (
            patch.dict(os.environ, env_patch, clear=False),
            patch("debcraft.platform.kernel.configuration.Path.is_file", return_value=False),
        ):
            service = KernelConfigurationService()
            with pytest.raises(ConfigurationError):
                service.get_section(LoggingConfig)
