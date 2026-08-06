"""Unit tests for XDG path resolution.

Verifies that resolve_xdg_path returns correct platform-specific paths
for all storage purposes, with and without XDG environment variables.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from debcraft.infrastructure.storage.paths import resolve_xdg_path


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.cross_platform
class TestLinuxDefaultPaths:
    """Test Linux paths when no XDG environment variables are set."""

    def _env(self) -> dict[str, str]:
        return {"HOME": "/home/testuser"}

    def test_mirror_default(self) -> None:
        result = resolve_xdg_path("mirror", environ=self._env(), platform="linux")
        assert result == Path("/home/testuser/.cache/debcraft/mirror")

    def test_workspace_default(self) -> None:
        result = resolve_xdg_path("workspace", environ=self._env(), platform="linux")
        assert result == Path("/home/testuser/.cache/debcraft/workspace")

    def test_outputs_default(self) -> None:
        result = resolve_xdg_path("outputs", environ=self._env(), platform="linux")
        assert result == Path("/home/testuser/.cache/debcraft/outputs")

    def test_logs_default(self) -> None:
        result = resolve_xdg_path("logs", environ=self._env(), platform="linux")
        assert result == Path("/home/testuser/.cache/debcraft/logs")

    def test_cache_default(self) -> None:
        result = resolve_xdg_path("cache", environ=self._env(), platform="linux")
        assert result == Path("/home/testuser/.cache/debcraft/cache")

    def test_database_default(self) -> None:
        result = resolve_xdg_path("database", environ=self._env(), platform="linux")
        assert result == Path("/home/testuser/.local/share/debcraft")

    def test_config_default(self) -> None:
        result = resolve_xdg_path("config", environ=self._env(), platform="linux")
        assert result == Path("/home/testuser/.config/debcraft")

    def test_all_paths_are_absolute(self) -> None:
        env = self._env()
        for purpose in ("mirror", "workspace", "outputs", "logs", "cache", "database", "config"):
            result = resolve_xdg_path(purpose, environ=env, platform="linux")  # type: ignore[arg-type]
            assert result.is_absolute(), f"{purpose} path is not absolute: {result}"


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.cross_platform
class TestLinuxCustomXDGPaths:
    """Test Linux paths with custom XDG_CACHE_HOME and XDG_DATA_HOME set."""

    def test_custom_xdg_cache_home_for_mirror(self) -> None:
        env = {"HOME": "/home/testuser", "XDG_CACHE_HOME": "/custom/cache"}
        result = resolve_xdg_path("mirror", environ=env, platform="linux")
        assert result == Path("/custom/cache/debcraft/mirror")

    def test_custom_xdg_cache_home_for_workspace(self) -> None:
        env = {"HOME": "/home/testuser", "XDG_CACHE_HOME": "/custom/cache"}
        result = resolve_xdg_path("workspace", environ=env, platform="linux")
        assert result == Path("/custom/cache/debcraft/workspace")

    def test_custom_xdg_cache_home_for_outputs(self) -> None:
        env = {"HOME": "/home/testuser", "XDG_CACHE_HOME": "/custom/cache"}
        result = resolve_xdg_path("outputs", environ=env, platform="linux")
        assert result == Path("/custom/cache/debcraft/outputs")

    def test_custom_xdg_cache_home_for_logs(self) -> None:
        env = {"HOME": "/home/testuser", "XDG_CACHE_HOME": "/custom/cache"}
        result = resolve_xdg_path("logs", environ=env, platform="linux")
        assert result == Path("/custom/cache/debcraft/logs")

    def test_custom_xdg_cache_home_for_cache(self) -> None:
        env = {"HOME": "/home/testuser", "XDG_CACHE_HOME": "/custom/cache"}
        result = resolve_xdg_path("cache", environ=env, platform="linux")
        assert result == Path("/custom/cache/debcraft/cache")

    def test_custom_xdg_data_home_for_database(self) -> None:
        env = {"HOME": "/home/testuser", "XDG_DATA_HOME": "/custom/data"}
        result = resolve_xdg_path("database", environ=env, platform="linux")
        assert result == Path("/custom/data/debcraft")

    def test_custom_xdg_config_home_for_config(self) -> None:
        env = {"HOME": "/home/testuser", "XDG_CONFIG_HOME": "/custom/config"}
        result = resolve_xdg_path("config", environ=env, platform="linux")
        assert result == Path("/custom/config/debcraft")

    def test_xdg_cache_home_does_not_affect_database(self) -> None:
        """XDG_CACHE_HOME should not change the database path."""
        env = {"HOME": "/home/testuser", "XDG_CACHE_HOME": "/custom/cache"}
        result = resolve_xdg_path("database", environ=env, platform="linux")
        assert result == Path("/home/testuser/.local/share/debcraft")

    def test_xdg_data_home_does_not_affect_cache_purposes(self) -> None:
        """XDG_DATA_HOME should not change mirror/workspace/etc paths."""
        env = {"HOME": "/home/testuser", "XDG_DATA_HOME": "/custom/data"}
        result = resolve_xdg_path("mirror", environ=env, platform="linux")
        assert result == Path("/home/testuser/.cache/debcraft/mirror")


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.cross_platform
class TestMacOSFallbackPaths:
    """Test macOS fallback paths when no XDG variables are set."""

    def _env(self) -> dict[str, str]:
        return {"HOME": "/Users/testuser"}

    def test_mirror_fallback(self) -> None:
        result = resolve_xdg_path("mirror", environ=self._env(), platform="darwin")
        assert result == Path("/Users/testuser/Library/Caches/debcraft/mirror")

    def test_workspace_fallback(self) -> None:
        result = resolve_xdg_path("workspace", environ=self._env(), platform="darwin")
        assert result == Path("/Users/testuser/Library/Caches/debcraft/workspace")

    def test_outputs_fallback(self) -> None:
        result = resolve_xdg_path("outputs", environ=self._env(), platform="darwin")
        assert result == Path("/Users/testuser/Library/Caches/debcraft/outputs")

    def test_logs_fallback(self) -> None:
        result = resolve_xdg_path("logs", environ=self._env(), platform="darwin")
        assert result == Path("/Users/testuser/Library/Caches/debcraft/logs")

    def test_cache_fallback(self) -> None:
        result = resolve_xdg_path("cache", environ=self._env(), platform="darwin")
        assert result == Path("/Users/testuser/Library/Caches/debcraft/cache")

    def test_database_fallback(self) -> None:
        result = resolve_xdg_path("database", environ=self._env(), platform="darwin")
        assert result == Path("/Users/testuser/Library/Application Support/debcraft")

    def test_config_fallback(self) -> None:
        result = resolve_xdg_path("config", environ=self._env(), platform="darwin")
        assert result == Path("/Users/testuser/Library/Preferences/debcraft")

    def test_xdg_vars_override_macos_defaults(self) -> None:
        """MacOS still honors XDG vars when explicitly set."""
        env = {"HOME": "/Users/testuser", "XDG_CACHE_HOME": "/custom/cache"}
        result = resolve_xdg_path("mirror", environ=env, platform="darwin")
        assert result == Path("/custom/cache/debcraft/mirror")

    def test_all_paths_are_absolute(self) -> None:
        env = self._env()
        for purpose in ("mirror", "workspace", "outputs", "logs", "cache", "database", "config"):
            result = resolve_xdg_path(purpose, environ=env, platform="darwin")  # type: ignore[arg-type]
            assert result.is_absolute(), f"{purpose} path is not absolute: {result}"


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.cross_platform
class TestWindowsFallbackPaths:
    """Test Windows fallback paths when no XDG variables are set.

    Since these tests may run on Linux/macOS where backslash paths are not
    treated as absolute, we use forward-slash paths that are portable across
    all platforms while still testing the Windows path resolution logic.
    """

    def _env(self) -> dict[str, str]:
        return {
            "USERPROFILE": "/Users/testuser",
            "LOCALAPPDATA": "/Users/testuser/AppData/Local",
            "APPDATA": "/Users/testuser/AppData/Roaming",
        }

    def test_mirror_fallback(self) -> None:
        result = resolve_xdg_path("mirror", environ=self._env(), platform="win32")
        expected = Path("/Users/testuser/AppData/Local") / "debcraft" / "cache" / "mirror"
        assert result == expected

    def test_workspace_fallback(self) -> None:
        result = resolve_xdg_path("workspace", environ=self._env(), platform="win32")
        expected = Path("/Users/testuser/AppData/Local") / "debcraft" / "cache" / "workspace"
        assert result == expected

    def test_outputs_fallback(self) -> None:
        result = resolve_xdg_path("outputs", environ=self._env(), platform="win32")
        expected = Path("/Users/testuser/AppData/Local") / "debcraft" / "cache" / "outputs"
        assert result == expected

    def test_logs_fallback(self) -> None:
        result = resolve_xdg_path("logs", environ=self._env(), platform="win32")
        expected = Path("/Users/testuser/AppData/Local") / "debcraft" / "cache" / "logs"
        assert result == expected

    def test_cache_fallback(self) -> None:
        result = resolve_xdg_path("cache", environ=self._env(), platform="win32")
        expected = Path("/Users/testuser/AppData/Local") / "debcraft" / "cache" / "cache"
        assert result == expected

    def test_database_fallback(self) -> None:
        result = resolve_xdg_path("database", environ=self._env(), platform="win32")
        expected = Path("/Users/testuser/AppData/Roaming") / "debcraft"
        assert result == expected

    def test_config_fallback(self) -> None:
        result = resolve_xdg_path("config", environ=self._env(), platform="win32")
        expected = Path("/Users/testuser/AppData/Roaming") / "debcraft" / "config"
        assert result == expected

    def test_xdg_vars_override_windows_defaults(self) -> None:
        """Windows still honors XDG vars when explicitly set."""
        env = {**self._env(), "XDG_CACHE_HOME": "/custom/cache"}
        result = resolve_xdg_path("mirror", environ=env, platform="win32")
        assert result == Path("/custom/cache") / "debcraft" / "cache" / "mirror"

    def test_windows_without_localappdata_uses_userprofile_fallback(self) -> None:
        """When LOCALAPPDATA is not set, falls back to USERPROFILE/AppData/Local."""
        env = {"USERPROFILE": "/Users/testuser"}
        result = resolve_xdg_path("mirror", environ=env, platform="win32")
        expected = Path("/Users/testuser") / "AppData" / "Local" / "debcraft" / "cache" / "mirror"
        assert result == expected

    def test_windows_without_appdata_uses_userprofile_fallback(self) -> None:
        """When APPDATA is not set, falls back to USERPROFILE/AppData/Roaming."""
        env = {"USERPROFILE": "/Users/testuser"}
        result = resolve_xdg_path("database", environ=env, platform="win32")
        expected = Path("/Users/testuser") / "AppData" / "Roaming" / "debcraft"
        assert result == expected

    def test_all_paths_are_absolute(self) -> None:
        env = self._env()
        for purpose in ("mirror", "workspace", "outputs", "logs", "cache", "database", "config"):
            result = resolve_xdg_path(purpose, environ=env, platform="win32")  # type: ignore[arg-type]
            assert result.is_absolute(), f"{purpose} path is not absolute: {result}"
