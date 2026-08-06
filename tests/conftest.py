"""Shared pytest configuration and fixtures for DebCraft tests."""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_working_dir(tmp_path: Path) -> Path:
    """Provide a temporary working directory for tests that need filesystem access."""
    return tmp_path
