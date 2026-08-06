"""Property-based tests for temporary file cleanup.

**Validates: Requirements 1.8, 7.4**

Property 2: For any set of files in the workspace directory where some files
have a ``.tmp`` suffix or match the ``tmp_`` prefix, after
``StorageEngine.initialize()`` completes, all files matching the temporary
naming convention are removed and all other files remain untouched.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.infrastructure.storage.engine import DefaultStorageEngine
from debcraft.infrastructure.storage.providers import LocalStorageProvider


def _is_tmp_file(name: str) -> bool:
    """Return True if the filename matches the temporary naming convention."""
    return name.endswith(".tmp") or name.startswith("tmp_")


def _make_tmp_name(name: str) -> str:
    """Turn a base name into a temporary filename with tmp_ prefix."""
    return f"tmp_{name}"


def _make_tmp_name_suffix(name: str) -> str:
    """Turn a base name into a temporary filename with .tmp suffix."""
    return f"{name}.tmp"


@pytest.mark.unit
@pytest.mark.storage
class TestTemporaryFileCleanupProperty:
    """Property 2: Temporary File Cleanup.

    For any set of files in the workspace directory where some files have a
    ``.tmp`` suffix or match the ``tmp_`` prefix, after initialize() completes,
    all ``.tmp``/``tmp_`` files are removed and all other files remain untouched.
    """

    @settings(max_examples=200)
    @given(
        file_specs=st.lists(
            st.tuples(
                st.text(min_size=1).filter(lambda s: "/" not in s),
                st.booleans(),
            ),
        ),
    )
    def test_tmp_files_removed_and_others_untouched(self, file_specs: list[tuple[str, bool]]) -> None:
        """After initialize(), .tmp/tmp_ files are removed; others remain."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_path = Path(tmp_dir)
            workspace = base_path / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            # Deduplicate filenames to avoid conflicts
            seen_names: set[str] = set()
            created_tmp_files: set[str] = set()
            created_normal_files: set[str] = set()

            for base_name, is_tmp in file_specs:
                # Sanitize: filter out characters problematic for filenames
                sanitized = base_name.replace("\x00", "").strip()
                if not sanitized:
                    continue

                if is_tmp:
                    # Alternate between .tmp suffix and tmp_ prefix
                    if len(created_tmp_files) % 2 == 0:
                        name = _make_tmp_name(sanitized)
                    else:
                        name = _make_tmp_name_suffix(sanitized)
                else:
                    # Normal file - ensure it doesn't accidentally match tmp patterns
                    name = sanitized
                    if _is_tmp_file(name):
                        name = f"keep_{name}"

                if name in seen_names:
                    continue
                seen_names.add(name)

                # Create the file
                file_path = workspace / name
                try:
                    file_path.write_text(f"content of {name}", encoding="utf-8")
                except OSError:
                    # Skip files that can't be created (invalid names on some OS)
                    continue

                if _is_tmp_file(name):
                    created_tmp_files.add(name)
                else:
                    created_normal_files.add(name)

            # Set up provider and engine with patched path resolution
            provider = LocalStorageProvider()
            event_bus = AsyncMock()
            event_bus.publish = AsyncMock()

            engine = DefaultStorageEngine(provider=provider, event_bus=event_bus)

            # Patch resolve_path to use our temp directory structure
            def patched_resolve(purpose: str, relative: str = "") -> Path:
                if purpose == "workspace":
                    base = workspace
                else:
                    base = base_path / purpose
                    base.mkdir(parents=True, exist_ok=True)
                if relative:
                    return base / relative
                return base

            provider.resolve_path = patched_resolve  # type: ignore[assignment]

            # Run initialize
            asyncio.run(engine.initialize())

            # Assert: all .tmp/tmp_ files removed
            remaining_files = {f.name for f in workspace.iterdir() if f.is_file()}

            for tmp_name in created_tmp_files:
                assert tmp_name not in remaining_files, f"Temporary file '{tmp_name}' should have been removed"

            # Assert: all normal files still exist
            for normal_name in created_normal_files:
                assert normal_name in remaining_files, f"Normal file '{normal_name}' should NOT have been removed"
