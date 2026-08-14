"""Shared pytest configuration and fixtures for DebCraft tests."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch
from hypothesis import HealthCheck, settings

# ---------------------------------------------------------------------------
# Hypothesis profiles: use HYPOTHESIS_PROFILE=ci for thorough runs in CI,
# defaults to "dev" (fewer examples) for fast local iteration.
# ---------------------------------------------------------------------------
settings.register_profile("ci", max_examples=100)
settings.register_profile("dev", max_examples=5, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "dev"))


@pytest.fixture(scope="session")
def monkeypatch_session() -> Generator[MonkeyPatch]:
    """Provide a session-scoped monkeypatch fixture."""
    mp = MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(autouse=True, scope="session")
def _isolate_xdg_paths() -> Generator[None]:
    """Redirect all XDG paths to a temp directory for the entire test session.

    Prevents unit tests from creating or modifying real user data files
    (e.g., ~/.local/share/debcraft/mirror.db). Any code that calls
    resolve_xdg_path without explicit environ/platform args will resolve
    paths under this temporary tree instead.
    """
    tmp_root = tempfile.mkdtemp(prefix="debcraft-test-")
    original_env = {}
    xdg_vars = ("XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME")

    for var in xdg_vars:
        original_env[var] = os.environ.get(var)
        os.environ[var] = str(Path(tmp_root) / var.lower())

    yield

    # Restore original environment
    for var in xdg_vars:
        if original_env[var] is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = original_env[var]


@pytest.fixture
def tmp_working_dir(tmp_path: Path) -> Path:
    """Provide a temporary working directory for tests that need filesystem access."""
    return tmp_path


@pytest.fixture(autouse=True, scope="session")
def no_http_requests(monkeypatch_session: MonkeyPatch):
    """Block real HTTP from aiohttp during tests."""
    import aiohttp

    allowed_hosts = {"localhost", "127.0.0.1", "::1"}
    original_connect = aiohttp.TCPConnector._create_connection

    async def blocked_connect(self, req, traces, timeout):
        host = req.url.host
        if host in allowed_hosts:
            return await original_connect(self, req, traces, timeout)
        raise RuntimeError(f"Test tried to make a real HTTP request to {req.method} {req.url}")

    monkeypatch_session.setattr("aiohttp.TCPConnector._create_connection", blocked_connect)
