"""Property-based tests for MinimalStorageEngine path resolution.

**Validates: Requirements 2.4**

# Feature: pylint-cleanup, Property 3: MinimalStorageEngine path resolution correctness

For any storage purpose in {"config", "mirror"} and any relative path string
containing only valid path characters, MinimalStorageEngine.get_path(purpose, relative)
returns a path whose parent chain starts with the XDG base directory for that purpose
and, if relative is non-empty, the path ends with the relative suffix.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.cli._storage import MinimalStorageEngine

# Fixed XDG env vars for deterministic testing
_XDG_CONFIG = "/tmp/test_xdg_config"
_XDG_CACHE = "/tmp/test_xdg_cache"


@pytest.mark.unit
@pytest.mark.storage
class TestMinimalStorageEnginePathResolution:
    """Property 3: MinimalStorageEngine path resolution correctness.

    For any storage purpose in {"config", "mirror"} and any relative path
    containing valid path characters, get_path returns a path under the
    XDG base directory for that purpose and contains the relative suffix.
    """

    @given(
        purpose=st.sampled_from(["config", "mirror"]),
        relative=st.from_regex(r"[a-zA-Z0-9_/\-]{0,50}", fullmatch=True).filter(
            lambda s: not s.startswith("/") and "//" not in s and not s.endswith("/")
        ),
    )
    def test_path_is_under_xdg_base_dir(self, purpose: str, relative: str) -> None:
        """**Validates: Requirements 2.4**.

        The returned path's parent chain starts with the XDG base directory
        for the given purpose, and if relative is non-empty the path ends
        with the relative suffix.
        """
        # Use fixed XDG env vars for deterministic testing
        env_patch = {
            "XDG_CONFIG_HOME": _XDG_CONFIG,
            "XDG_CACHE_HOME": _XDG_CACHE,
        }
        with patch.dict(os.environ, env_patch):
            engine = MinimalStorageEngine()
            result = engine.get_path(purpose, relative)  # type: ignore[arg-type]

        # Determine expected base directory
        if purpose == "config":
            expected_base = Path(_XDG_CONFIG) / "debcraft"
        else:  # mirror
            expected_base = Path(_XDG_CACHE) / "debcraft" / "mirror"

        # The result must be under the expected base directory
        assert result.is_relative_to(expected_base), f"Expected path to be under {expected_base}, got {result}"

        # If relative is non-empty, the path must end with the relative suffix
        if relative:
            # Path normalizes trailing slashes, so compare with normalized form
            expected_suffix = str(Path(relative))
            assert str(result).endswith(expected_suffix), f"Expected path to end with '{expected_suffix}', got {result}"
