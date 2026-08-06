"""Property-based tests for preservation of non-Windows behavior.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

Property 2: Preservation — Non-Windows Behavior Unchanged

For any test execution where the host platform is Linux or macOS,
the fixed test code SHALL produce exactly the same behavior and results
as the original code, preserving all existing test semantics.

These tests MUST PASS on the current unfixed code (they verify baseline
behavior on Linux) and MUST STILL PASS after the fix is applied
(confirming no regressions).
"""

from __future__ import annotations

import re
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


def _environ_for_platform(
    platform: str,
) -> st.SearchStrategy[dict[str, str]]:
    """Generate an environ dict with absolute POSIX paths for a platform."""
    if platform == "linux":
        return st.fixed_dictionaries(
            {"HOME": _safe_path_segment().map(lambda s: f"/home/{s}")},
            optional={
                "XDG_CACHE_HOME": _safe_path_segment().map(lambda s: f"/opt/cache/{s}"),
                "XDG_DATA_HOME": _safe_path_segment().map(lambda s: f"/opt/data/{s}"),
                "XDG_CONFIG_HOME": _safe_path_segment().map(lambda s: f"/opt/config/{s}"),
            },
        )
    if platform == "darwin":
        return st.fixed_dictionaries(
            {"HOME": _safe_path_segment().map(lambda s: f"/Users/{s}")},
            optional={
                "XDG_CACHE_HOME": _safe_path_segment().map(lambda s: f"/opt/cache/{s}"),
                "XDG_DATA_HOME": _safe_path_segment().map(lambda s: f"/opt/data/{s}"),
                "XDG_CONFIG_HOME": _safe_path_segment().map(lambda s: f"/opt/config/{s}"),
            },
        )
    # win32: use POSIX-style absolute paths (since we're on a Linux host)
    return st.fixed_dictionaries(
        {
            "USERPROFILE": _safe_path_segment().map(lambda s: f"/users/{s}"),
        },
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
class TestPreservationProperties:
    """Property 2: Preservation — Non-Windows Behavior Unchanged.

    On the current Linux host, for all platform/purpose/environ
    combinations, the behavior we want to preserve is already working.
    These tests confirm baseline correctness and detect regressions
    after the fix is applied.
    """

    @settings(max_examples=200)
    @given(
        platform=st.sampled_from(["linux", "darwin", "win32"]),
        purpose=st.sampled_from(_ALL_PURPOSES),
        data=st.data(),
    )
    def test_posix_path_absoluteness_on_linux_host(self, platform: str, purpose: str, data: st.DataObject) -> None:
        """On Linux host, PurePosixPath(str(result)).is_absolute() is True.

        This validates that the new assertion (PurePosixPath) is equivalent
        to the old one (result.is_absolute()) on non-Windows hosts.
        """
        environ = data.draw(_environ_for_platform(platform))
        result = resolve_xdg_path(
            purpose,
            environ=environ,
            platform=platform,  # type: ignore[arg-type]
        )
        # On a Linux host, Path always creates PosixPath, so both
        # result.is_absolute() and PurePosixPath(str(result)).is_absolute()
        # should return True for absolute POSIX paths.
        assert PurePosixPath(result.as_posix()).is_absolute(), (
            f"PurePosixPath('{result}').is_absolute() returned False for platform={platform}, purpose={purpose}"
        )

    @settings(max_examples=200)
    @given(
        platform=st.sampled_from(["linux", "darwin", "win32"]),
        purpose=st.sampled_from(_ALL_PURPOSES),
        data=st.data(),
    )
    def test_result_contains_debcraft(self, platform: str, purpose: str, data: st.DataObject) -> None:
        """For all valid platforms and purposes, result contains 'debcraft'.

        This ensures the application name is always part of the resolved
        path, regardless of platform or purpose.
        """
        environ = data.draw(_environ_for_platform(platform))
        result = resolve_xdg_path(
            purpose,
            environ=environ,
            platform=platform,  # type: ignore[arg-type]
        )
        assert "debcraft" in result.parts, (
            f"'debcraft' not in path parts {result.parts} for platform={platform}, purpose={purpose}"
        )

    def test_re_escape_path_on_linux_produces_matching_pattern(
        self,
    ) -> None:
        """re.escape(str(Path('/fake/config'))) matches str(Path('/fake/config')).

        This confirms that the platform-aware regex approach produces a
        pattern that matches the actual platform path representation.
        """
        path_str = str(Path("/fake/config"))
        escaped = re.escape(path_str)

        # The escaped pattern should match the platform's own string representation
        assert re.search(escaped, path_str) is not None, (
            f"re.escape(str(Path('/fake/config'))) = '{escaped}' does not match '{path_str}'"
        )

        # On Linux specifically, the string form is identical to the literal
        if sys.platform == "linux":
            assert path_str == "/fake/config", (
                f"str(Path('/fake/config')) = '{path_str}' (expected '/fake/config' on Linux)"
            )
