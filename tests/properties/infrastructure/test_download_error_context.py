"""Property-based test for download error context propagation (Bug Condition Exploration).

**Validates: Requirements 1.3, 1.4**

Property 2: Bug Condition - Download Error Context Propagation

For any download attempt where all retries are exhausted and the final exception
is an HttpServerError, the returned DownloadResult SHALL include the HTTP status_code
from the exception.

This test is EXPECTED TO FAIL on unfixed code because DownloadResult has no
status_code field. The failure confirms the bug exists.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from debcraft.domain.mirror.config import MirrorConfig, RepositoryConfig
from debcraft.domain.mirror.values import DownloadResult
from debcraft.infrastructure.mirror.download import DownloadCoordinator

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# HTTP 5xx status codes (server errors that trigger retries)
_http_5xx_strategy = st.integers(min_value=500, max_value=599)


# ---------------------------------------------------------------------------
# Property 2: Bug Condition - Download Error Context Propagation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestProperty2DownloadErrorContextPropagation:
    """Property 2: Bug Condition - Download Error Context Propagation.

    For any HTTP 5xx status code returned by the server on all retry attempts,
    the returned DownloadResult SHALL have a status_code field equal to that
    HTTP status code.

    On UNFIXED code, this test MUST FAIL because DownloadResult lacks a
    status_code field (AttributeError or TypeError).
    """

    @settings(
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(status_code=_http_5xx_strategy)
    async def test_download_result_has_status_code_after_exhausted_retries(self, status_code: int) -> None:
        """**Validates: Requirements 1.3, 1.4**.

        When all retries are exhausted due to HTTP 5xx errors, the returned
        DownloadResult must carry the status_code from the final error.

        Bug condition: DownloadResult has no status_code field, so this
        assertion will fail with AttributeError on unfixed code.
        """

        # Create a handler that always returns the given 5xx status
        async def handler(request: web.Request) -> web.Response:
            return web.Response(status=status_code, body=b"server error")

        app = web.Application()
        app.router.add_get("/file", handler)

        tmp_dir = Path(tempfile.mkdtemp())
        try:
            async with TestServer(app) as server:
                url = str(server.make_url("/file"))
                dest_path = tmp_dir / "downloaded_file"

                config = _make_test_config()
                coordinator = DownloadCoordinator(
                    storage_engine=_FakeStorageEngine(),
                    config=config,
                )
                await coordinator.start()
                try:
                    # Patch asyncio.sleep to avoid waiting during retries
                    with patch(
                        "debcraft.infrastructure.mirror.download.asyncio.sleep",
                        _noop_sleep,
                    ):
                        result = await coordinator.download_file(
                            url=url,
                            dest_path=dest_path,
                            expected_sha256="a" * 64,
                            expected_size=100,
                        )

                    # The download should have failed after exhausting retries
                    assert result.success is False
                    assert isinstance(result, DownloadResult)

                    # BUG CONDITION: DownloadResult lacks status_code field
                    # This assertion will fail with AttributeError on unfixed code
                    assert hasattr(result, "status_code"), (
                        f"DownloadResult has no 'status_code' attribute. "
                        f"Available fields: {[f.name for f in result.__dataclass_fields__.values()]}"
                    )
                    assert result.status_code == status_code, (
                        f"Expected status_code={status_code}, got status_code={result.status_code}"
                    )
                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------


async def _noop_sleep(delay: float) -> None:
    """No-op replacement for asyncio.sleep to speed up retry tests."""


class _FakeStorageEngine:
    """Minimal fake StorageEngine for testing DownloadCoordinator."""

    def get_path(self, name: str) -> Path:
        return Path("/tmp/test-mirror")


def _make_test_config() -> MirrorConfig:
    """Create a minimal MirrorConfig for testing."""
    return MirrorConfig(
        repositories=[
            RepositoryConfig(
                name="test",
                base_url="http://localhost",
                suites=["test"],
                components=["main"],
                architectures=["amd64"],
            )
        ],
        download_timeout=30,
        max_connections_per_repo=5,
        max_total_connections=10,
    )
