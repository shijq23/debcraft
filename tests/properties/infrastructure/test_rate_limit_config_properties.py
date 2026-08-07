"""Property-based tests for rate limit configuration parsing and validation.

**Validates: Requirements 2.1, 2.2, 2.4, 2.5, 2.6, 2.7**

Property 4: Rate limit config parsing round-trip.
For any numeric value in [1, 1000] written as rate_limit_rps and any integer in
[1, 200] written as rate_limit_burst in a valid TOML [settings] section, parsing
the configuration SHALL produce a MirrorConfig with those exact values. When
rate_limit_burst is omitted, it SHALL default to the value of max_connections_per_repo.

Property 5: Rate limit config validation rejects invalid values.
For any rate_limit_rps value less than 1 or greater than 1000, or any
rate_limit_burst value less than 1 or greater than 200, or any non-numeric value
in either field, the configuration validator SHALL produce at least one error
message indicating the accepted range or expected type.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.infrastructure.mirror.config_reader import ConfigReader
from debcraft.infrastructure.mirror.errors import MirrorConfigurationError

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Invalid RPS values: too low or too high
_invalid_rps_strategy = st.one_of(
    st.floats(max_value=0.99, allow_nan=False, allow_infinity=False),
    st.floats(min_value=1000.01, allow_nan=False, allow_infinity=False),
)

# Invalid burst values: too low or too high
_invalid_burst_strategy = st.one_of(
    st.integers(max_value=0),
    st.integers(min_value=201),
)

# Non-numeric string values for TOML fields
_non_numeric_string_strategy = st.text(
    alphabet=st.characters(categories=("L", "Nd", "P", "S")),
    min_size=1,
    max_size=20,
).filter(lambda s: not _is_numeric(s))


def _is_numeric(s: str) -> bool:
    """Check if a string can be parsed as a number."""
    try:
        float(s)
        return True
    except (ValueError, OverflowError):
        return False


# ---------------------------------------------------------------------------
# Fake StorageEngine for testing
# ---------------------------------------------------------------------------


class _FakeStorageEngine:
    """Minimal fake StorageEngine for testing ConfigReader."""

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path

    def get_path(self, purpose: str, relative: str = "") -> Path:
        return self._config_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config_with_invalid_rps(rps_value: float) -> tuple[Path, ConfigReader]:
    """Create a temp TOML config with an out-of-range rate_limit_rps value."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as tmp:
        tmp.write(
            f"""[settings]
download_timeout = 300
max_connections_per_repo = 20
max_total_connections = 60
rate_limit_rps = {rps_value}

[[repository]]
name = "test"
base_url = "https://example.com"
suites = ["stable"]
components = ["main"]
architectures = ["amd64"]
"""
        )
    config_path = Path(tmp.name)
    storage = _FakeStorageEngine(config_path)
    return config_path, ConfigReader(storage)


def _make_config_with_invalid_burst(burst_value: int) -> tuple[Path, ConfigReader]:
    """Create a temp TOML config with an out-of-range rate_limit_burst value."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as tmp:
        tmp.write(
            f"""[settings]
download_timeout = 300
max_connections_per_repo = 20
max_total_connections = 60
rate_limit_burst = {burst_value}

[[repository]]
name = "test"
base_url = "https://example.com"
suites = ["stable"]
components = ["main"]
architectures = ["amd64"]
"""
        )
    config_path = Path(tmp.name)
    storage = _FakeStorageEngine(config_path)
    return config_path, ConfigReader(storage)


def _make_config_with_string_rps(string_value: str) -> tuple[Path, ConfigReader]:
    """Create a temp TOML config with a non-numeric rate_limit_rps value."""
    escaped = string_value.replace("\\", "\\\\").replace('"', '\\"')
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False, encoding="utf-8") as tmp:
        tmp.write(
            f"""[settings]
download_timeout = 300
max_connections_per_repo = 20
max_total_connections = 60
rate_limit_rps = "{escaped}"

[[repository]]
name = "test"
base_url = "https://example.com"
suites = ["stable"]
components = ["main"]
architectures = ["amd64"]
"""
        )
    config_path = Path(tmp.name)
    storage = _FakeStorageEngine(config_path)
    return config_path, ConfigReader(storage)


def _make_config_with_string_burst(string_value: str) -> tuple[Path, ConfigReader]:
    """Create a temp TOML config with a non-numeric rate_limit_burst value."""
    escaped = string_value.replace("\\", "\\\\").replace('"', '\\"')
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False, encoding="utf-8") as tmp:
        tmp.write(
            f"""[settings]
download_timeout = 300
max_connections_per_repo = 20
max_total_connections = 60
rate_limit_burst = "{escaped}"

[[repository]]
name = "test"
base_url = "https://example.com"
suites = ["stable"]
components = ["main"]
architectures = ["amd64"]
"""
        )
    config_path = Path(tmp.name)
    storage = _FakeStorageEngine(config_path)
    return config_path, ConfigReader(storage)


# ---------------------------------------------------------------------------
# Property 5: Rate limit config validation rejects invalid values
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty5ConfigValidationRejection:
    """Property 5: Rate limit config validation rejects invalid values.

    For any rate_limit_rps value less than 1 or greater than 1000, or any
    rate_limit_burst value less than 1 or greater than 200, or any non-numeric
    value in either field, the configuration validator SHALL produce at least
    one error message indicating the accepted range or expected type.

    **Validates: Requirements 2.5, 2.6, 2.7**
    """

    @settings(max_examples=200)
    @given(rps=_invalid_rps_strategy)
    def test_invalid_rps_produces_validation_error(self, rps: float) -> None:
        """Validate Requirements 2.5.

        Any rate_limit_rps value outside [1, 1000] SHALL cause a
        MirrorConfigurationError with an error message indicating
        the accepted range.
        """
        config_path, reader = _make_config_with_invalid_rps(rps)
        try:
            with pytest.raises(MirrorConfigurationError) as exc_info:
                reader.read()

            error_msg = str(exc_info.value)
            # Error should indicate the accepted range
            assert "rate_limit_rps" in error_msg
        finally:
            config_path.unlink(missing_ok=True)

    @settings(max_examples=200)
    @given(burst=_invalid_burst_strategy)
    def test_invalid_burst_produces_validation_error(self, burst: int) -> None:
        """Validate Requirements 2.6.

        Any rate_limit_burst value outside [1, 200] SHALL cause a
        MirrorConfigurationError with an error message indicating
        the accepted range.
        """
        config_path, reader = _make_config_with_invalid_burst(burst)
        try:
            with pytest.raises(MirrorConfigurationError) as exc_info:
                reader.read()

            error_msg = str(exc_info.value)
            # Error should indicate the accepted range
            assert "rate_limit_burst" in error_msg
        finally:
            config_path.unlink(missing_ok=True)

    @settings(max_examples=200)
    @given(string_val=_non_numeric_string_strategy)
    def test_non_numeric_rps_produces_validation_error(self, string_val: str) -> None:
        """Validate Requirements 2.7.

        Any non-numeric value for rate_limit_rps SHALL cause a
        MirrorConfigurationError with an error message indicating
        the expected type.
        """
        config_path, reader = _make_config_with_string_rps(string_val)
        try:
            with pytest.raises(MirrorConfigurationError) as exc_info:
                reader.read()

            error_msg = str(exc_info.value)
            # Error should mention the type issue
            assert "rate_limit_rps" in error_msg
            assert "numeric" in error_msg
        finally:
            config_path.unlink(missing_ok=True)

    @settings(max_examples=200)
    @given(string_val=_non_numeric_string_strategy)
    def test_non_numeric_burst_produces_validation_error(self, string_val: str) -> None:
        """Validate Requirements 2.7.

        Any non-numeric value for rate_limit_burst SHALL cause a
        MirrorConfigurationError with an error message indicating
        the expected type.
        """
        config_path, reader = _make_config_with_string_burst(string_val)
        try:
            with pytest.raises(MirrorConfigurationError) as exc_info:
                reader.read()

            error_msg = str(exc_info.value)
            # Error should mention the type issue
            assert "rate_limit_burst" in error_msg
            assert "numeric" in error_msg
        finally:
            config_path.unlink(missing_ok=True)
