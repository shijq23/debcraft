"""Property-based tests for XDG path resolution correctness.

**Validates: Requirements 1.4, 1.6**

Property 1: For any combination of platform identifier, environment variables,
and storage purpose, resolve_xdg_path() returns a path rooted in the expected
platform-specific base directory with the correct subdirectory suffix.
"""

import sys
from pathlib import Path, PurePosixPath
from typing import get_args

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.infrastructure.storage.paths import resolve_xdg_path
from debcraft.platform.contracts.storage import StoragePurpose

# All valid StoragePurpose values.
_ALL_PURPOSES: list[str] = list(get_args(StoragePurpose))

# Cache-type purposes that share the XDG_CACHE_HOME base on Linux.
_CACHE_PURPOSES = ("mirror", "workspace", "outputs", "logs", "cache")


def _safe_path_segment() -> st.SearchStrategy[str]:
    """Generate text safe for use in path segments."""
    return st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N"),
            blacklist_characters="\x00/\\:",
        ),
        min_size=1,
        max_size=20,
    )


def _linux_env_strategy() -> st.SearchStrategy[dict[str, str]]:
    """Generate environment dictionaries appropriate for Linux testing."""
    return st.fixed_dictionaries(
        # HOME is always present so paths are deterministic and absolute.
        {"HOME": _safe_path_segment().map(lambda s: f"/home/{s}")},
        optional={
            "XDG_CACHE_HOME": _safe_path_segment().map(lambda s: f"/opt/cache/{s}"),
            "XDG_DATA_HOME": _safe_path_segment().map(lambda s: f"/opt/data/{s}"),
            "XDG_CONFIG_HOME": _safe_path_segment().map(lambda s: f"/opt/config/{s}"),
        },
    )


def _darwin_env_strategy() -> st.SearchStrategy[dict[str, str]]:
    """Generate environment dictionaries appropriate for macOS testing."""
    return st.fixed_dictionaries(
        {"HOME": _safe_path_segment().map(lambda s: f"/Users/{s}")},
        optional={
            "XDG_CACHE_HOME": _safe_path_segment().map(lambda s: f"/opt/cache/{s}"),
            "XDG_DATA_HOME": _safe_path_segment().map(lambda s: f"/opt/data/{s}"),
            "XDG_CONFIG_HOME": _safe_path_segment().map(lambda s: f"/opt/config/{s}"),
        },
    )


def _windows_env_strategy() -> st.SearchStrategy[dict[str, str]]:
    r"""Generate environment dictionaries appropriate for Windows testing.

    On a non-Windows host, pathlib.Path produces PosixPaths, so Windows-style
    absolute paths (C:\\...) won't register as absolute. We use forward-slash
    absolute paths for testability since the implementation uses pathlib.Path
    which always returns the host platform's path type.
    """
    return st.fixed_dictionaries(
        {"USERPROFILE": _safe_path_segment().map(lambda s: f"/users/{s}")},
        optional={
            "XDG_CACHE_HOME": _safe_path_segment().map(lambda s: f"/xdg_cache/{s}"),
            "XDG_DATA_HOME": _safe_path_segment().map(lambda s: f"/xdg_data/{s}"),
            "XDG_CONFIG_HOME": _safe_path_segment().map(lambda s: f"/xdg_config/{s}"),
            "LOCALAPPDATA": _safe_path_segment().map(lambda s: f"/localappdata/{s}"),
            "APPDATA": _safe_path_segment().map(lambda s: f"/appdata/{s}"),
        },
    )


@pytest.mark.unit
@pytest.mark.storage
class TestXdgPathResolutionProperty:
    """Property 1: XDG Path Resolution Correctness.

    For any combination of platform, environment variables, and storage purpose,
    the resolved path is absolute, rooted in the expected base directory, and
    has the correct subdirectory suffix appended.
    """

    @settings(max_examples=200)
    @given(
        platform=st.sampled_from(["linux", "darwin", "win32"]),
        purpose=st.sampled_from(_ALL_PURPOSES),
    )
    def test_result_is_absolute_path(self, platform: str, purpose: str) -> None:
        """Resolved path is always absolute when HOME/USERPROFILE is set."""
        # Use a deterministic absolute HOME so the result is always absolute.
        if platform == "win32" and sys.platform != "win32":
            # On non-Windows host, use POSIX-style paths for USERPROFILE.
            environ = {"USERPROFILE": "/users/testuser", "LOCALAPPDATA": "/local", "APPDATA": "/roaming"}
        elif platform == "darwin":
            environ = {"HOME": "/Users/testuser"}
        else:
            environ = {"HOME": "/home/testuser"}

        result = resolve_xdg_path(purpose, environ=environ, platform=platform)  # type: ignore[arg-type]
        assert PurePosixPath(result.as_posix()).is_absolute()

    @settings(max_examples=200)
    @given(
        platform=st.sampled_from(["linux", "darwin", "win32"]),
        purpose=st.sampled_from(_ALL_PURPOSES),
    )
    def test_result_contains_app_name(self, platform: str, purpose: str) -> None:
        """Resolved path always contains the application name 'debcraft'."""
        if platform == "win32" and sys.platform != "win32":
            environ = {"USERPROFILE": "/users/testuser", "LOCALAPPDATA": "/local", "APPDATA": "/roaming"}
        elif platform == "darwin":
            environ = {"HOME": "/Users/testuser"}
        else:
            environ = {"HOME": "/home/testuser"}

        result = resolve_xdg_path(purpose, environ=environ, platform=platform)  # type: ignore[arg-type]
        assert "debcraft" in result.parts

    @settings(max_examples=200)
    @given(
        environ=_linux_env_strategy(),
        purpose=st.sampled_from(list(_CACHE_PURPOSES)),
    )
    def test_linux_cache_purposes_rooted_in_xdg_cache(self, environ: dict[str, str], purpose: str) -> None:
        """On Linux, cache-type purposes are rooted in XDG_CACHE_HOME or ~/.cache."""
        result = resolve_xdg_path(purpose, environ=environ, platform="linux")  # type: ignore[arg-type]

        if "XDG_CACHE_HOME" in environ:
            expected_base = Path(environ["XDG_CACHE_HOME"])
        else:
            expected_base = Path(environ["HOME"]) / ".cache"

        assert result.is_relative_to(expected_base)
        # Must end with debcraft/<purpose>
        assert result.name == purpose
        assert result.parent.name == "debcraft"

    @settings(max_examples=200)
    @given(environ=_linux_env_strategy())
    def test_linux_database_rooted_in_xdg_data(self, environ: dict[str, str]) -> None:
        """On Linux, database purpose is rooted in XDG_DATA_HOME or ~/.local/share."""
        result = resolve_xdg_path("database", environ=environ, platform="linux")

        if "XDG_DATA_HOME" in environ:
            expected_base = Path(environ["XDG_DATA_HOME"])
        else:
            expected_base = Path(environ["HOME"]) / ".local" / "share"

        assert result.is_relative_to(expected_base)
        assert result.name == "debcraft"

    @settings(max_examples=200)
    @given(environ=_linux_env_strategy())
    def test_linux_config_rooted_in_xdg_config(self, environ: dict[str, str]) -> None:
        """On Linux, config purpose is rooted in XDG_CONFIG_HOME or ~/.config."""
        result = resolve_xdg_path("config", environ=environ, platform="linux")

        if "XDG_CONFIG_HOME" in environ:
            expected_base = Path(environ["XDG_CONFIG_HOME"])
        else:
            expected_base = Path(environ["HOME"]) / ".config"

        assert result.is_relative_to(expected_base)
        assert result.name == "debcraft"

    @settings(max_examples=200)
    @given(
        environ=_darwin_env_strategy(),
        purpose=st.sampled_from(list(_CACHE_PURPOSES)),
    )
    def test_darwin_cache_purposes_rooted_correctly(self, environ: dict[str, str], purpose: str) -> None:
        """On macOS, cache-type purposes use XDG_CACHE_HOME or ~/Library/Caches."""
        result = resolve_xdg_path(purpose, environ=environ, platform="darwin")  # type: ignore[arg-type]

        if "XDG_CACHE_HOME" in environ:
            expected_base = Path(environ["XDG_CACHE_HOME"])
        else:
            expected_base = Path(environ["HOME"]) / "Library" / "Caches"

        assert result.is_relative_to(expected_base)
        assert result.name == purpose
        assert result.parent.name == "debcraft"

    @settings(max_examples=200)
    @given(environ=_darwin_env_strategy())
    def test_darwin_database_rooted_correctly(self, environ: dict[str, str]) -> None:
        """On macOS, database uses XDG_DATA_HOME or ~/Library/Application Support."""
        result = resolve_xdg_path("database", environ=environ, platform="darwin")

        if "XDG_DATA_HOME" in environ:
            expected_base = Path(environ["XDG_DATA_HOME"])
        else:
            expected_base = Path(environ["HOME"]) / "Library" / "Application Support"

        assert result.is_relative_to(expected_base)
        assert result.name == "debcraft"

    @settings(max_examples=200)
    @given(environ=_darwin_env_strategy())
    def test_darwin_config_rooted_correctly(self, environ: dict[str, str]) -> None:
        """On macOS, config uses XDG_CONFIG_HOME or ~/Library/Preferences."""
        result = resolve_xdg_path("config", environ=environ, platform="darwin")

        if "XDG_CONFIG_HOME" in environ:
            expected_base = Path(environ["XDG_CONFIG_HOME"])
        else:
            expected_base = Path(environ["HOME"]) / "Library" / "Preferences"

        assert result.is_relative_to(expected_base)
        assert result.name == "debcraft"

    @settings(max_examples=200)
    @given(
        environ=_windows_env_strategy(),
        purpose=st.sampled_from(list(_CACHE_PURPOSES)),
    )
    def test_windows_cache_purposes_rooted_correctly(self, environ: dict[str, str], purpose: str) -> None:
        """On Windows, cache-type purposes use XDG_CACHE_HOME or LOCALAPPDATA."""
        result = resolve_xdg_path(purpose, environ=environ, platform="win32")  # type: ignore[arg-type]

        if "XDG_CACHE_HOME" in environ:
            expected_base = Path(environ["XDG_CACHE_HOME"])
        else:
            if "LOCALAPPDATA" in environ:
                expected_base = Path(environ["LOCALAPPDATA"])
            else:
                expected_base = Path(environ["USERPROFILE"]) / "AppData" / "Local"

        assert result.is_relative_to(expected_base)
        # Windows cache path ends with <purpose>
        assert result.name == purpose
        assert "debcraft" in result.parts

    @settings(max_examples=200)
    @given(environ=_windows_env_strategy())
    def test_windows_database_rooted_correctly(self, environ: dict[str, str]) -> None:
        """On Windows, database uses XDG_DATA_HOME or APPDATA."""
        result = resolve_xdg_path("database", environ=environ, platform="win32")

        if "XDG_DATA_HOME" in environ:
            expected_base = Path(environ["XDG_DATA_HOME"])
        else:
            if "APPDATA" in environ:
                expected_base = Path(environ["APPDATA"])
            else:
                expected_base = Path(environ["USERPROFILE"]) / "AppData" / "Roaming"

        assert result.is_relative_to(expected_base)
        assert result.name == "debcraft"

    @settings(max_examples=200)
    @given(environ=_windows_env_strategy())
    def test_windows_config_rooted_correctly(self, environ: dict[str, str]) -> None:
        """On Windows, config uses XDG_CONFIG_HOME or APPDATA with /config suffix."""
        result = resolve_xdg_path("config", environ=environ, platform="win32")

        if "XDG_CONFIG_HOME" in environ:
            expected_base = Path(environ["XDG_CONFIG_HOME"])
        else:
            if "APPDATA" in environ:
                expected_base = Path(environ["APPDATA"])
            else:
                expected_base = Path(environ["USERPROFILE"]) / "AppData" / "Roaming"

        assert result.is_relative_to(expected_base)
        # Windows config always ends with debcraft/config
        assert result.name == "config"
        assert result.parent.name == "debcraft"

    @settings(max_examples=200)
    @given(
        platform=st.sampled_from(["linux", "darwin", "win32"]),
        purpose=st.sampled_from(_ALL_PURPOSES),
    )
    def test_result_has_correct_subdirectory_suffix(self, platform: str, purpose: str) -> None:
        """The resolved path ends with the expected purpose-specific suffix."""
        # Use deterministic absolute environ for each platform.
        if platform == "linux":
            environ = {"HOME": "/home/testuser"}
        elif platform == "darwin":
            environ = {"HOME": "/Users/testuser"}
        else:
            environ: dict[str, str] = {
                "USERPROFILE": "/users/testuser",
                "LOCALAPPDATA": "/local",
                "APPDATA": "/roaming",
            }

        result = resolve_xdg_path(purpose, environ=environ, platform=platform)  # type: ignore[arg-type]

        if purpose in _CACHE_PURPOSES:
            # Path should end with <purpose> and contain 'debcraft'
            assert result.name == purpose
            if platform == "win32":
                # Windows: <base>/debcraft/cache/<purpose>
                assert result.parent.name == "cache"
                assert "debcraft" in result.parts
            else:
                # Linux/macOS: <base>/debcraft/<purpose>
                assert result.parent.name == "debcraft"
        elif purpose == "database":
            # Path should end with debcraft/
            assert result.name == "debcraft"
        elif purpose == "config":
            if platform == "win32":
                # Windows config (no XDG): debcraft/config
                assert result.name == "config"
                assert result.parent.name == "debcraft"
            else:
                # Linux/macOS config (no XDG): .../debcraft
                assert result.name == "debcraft"
