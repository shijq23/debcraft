"""Unit tests for infrastructure/mirror/download.py DownloadCoordinator."""

import asyncio
import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from debcraft.domain.mirror.config import MirrorConfig
from debcraft.infrastructure.mirror.download import (
    DownloadCoordinator,
    DownloadTask,
    _compute_backoff_delay,
)
from debcraft.infrastructure.mirror.errors import (
    HttpClientError,
)

# ---------------------------------------------------------------------------
# Helpers for async route handlers (avoids aiohttp bare function deprecation)
# ---------------------------------------------------------------------------


def _make_response_handler(*, body: bytes = b"", status: int = 200):
    """Create an async handler that returns a Response with the given body/status."""

    async def handler(request: web.Request) -> web.Response:
        return web.Response(body=body, status=status)

    return handler


@pytest.fixture
def mirror_config():
    return MirrorConfig(
        repositories=[],
        download_timeout=30,
        max_connections_per_repo=5,
        max_total_connections=10,
    )


@pytest.fixture
def storage_engine():
    mock = MagicMock()
    mock.get_path.return_value = Path("/var/tmp/mirror")
    return mock


@pytest.fixture
def coordinator(storage_engine, mirror_config):
    return DownloadCoordinator(storage_engine=storage_engine, config=mirror_config)


@pytest.mark.unit
@pytest.mark.mirror
class TestDownloadCoordinatorSessionManagement:
    """Tests for session start/close lifecycle."""

    @pytest.mark.asyncio
    async def test_start_creates_session(self, coordinator):
        await coordinator.start()
        assert coordinator._session is not None
        assert coordinator._connector is not None
        await coordinator.close()

    @pytest.mark.asyncio
    async def test_close_cleans_up_session(self, coordinator):
        await coordinator.start()
        await coordinator.close()
        assert coordinator._session is None
        assert coordinator._connector is None

    @pytest.mark.asyncio
    async def test_close_without_start_is_safe(self, coordinator):
        await coordinator.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_download_file_without_start_raises(self, coordinator, tmp_path):
        with pytest.raises(RuntimeError, match="Session not started"):
            await coordinator.download_file(
                url="http://example.com/file",
                dest_path=tmp_path / "file.deb",
                expected_sha256="abc",
                expected_size=100,
            )


@pytest.mark.unit
@pytest.mark.mirror
class TestDownloadFile:
    """Tests for single file download with verification."""

    @pytest.mark.asyncio
    async def test_successful_download(self, tmp_path):
        content = b"hello world package content"
        sha256 = hashlib.sha256(content).hexdigest()
        size = len(content)

        app = web.Application()
        app.router.add_get("/file.deb", _make_response_handler(body=content))

        async with TestServer(app) as server:
            config = MirrorConfig(
                repositories=[],
                download_timeout=10,
                max_connections_per_repo=5,
                max_total_connections=10,
            )
            coord = DownloadCoordinator(storage_engine=MagicMock(), config=config)
            await coord.start()
            try:
                dest = tmp_path / "file.deb"
                result = await coord.download_file(
                    url=f"http://localhost:{server.port}/file.deb",
                    dest_path=dest,
                    expected_sha256=sha256,
                    expected_size=size,
                )
                assert result.success is True
                assert result.sha256_verified is True
                assert result.bytes_transferred == size
                assert dest.exists()
                assert dest.read_bytes() == content
                # .part file should not remain
                assert not Path(str(dest) + ".part").exists()
            finally:
                await coord.close()

    @pytest.mark.asyncio
    async def test_sha256_mismatch_retries_and_fails(self, tmp_path):
        content = b"some content"
        size = len(content)

        app = web.Application()
        app.router.add_get("/file.deb", _make_response_handler(body=content))

        async with TestServer(app) as server:
            config = MirrorConfig(
                repositories=[],
                download_timeout=10,
                max_connections_per_repo=5,
                max_total_connections=10,
            )
            coord = DownloadCoordinator(storage_engine=MagicMock(), config=config)
            await coord.start()
            try:
                dest = tmp_path / "file.deb"
                with patch(
                    "debcraft.infrastructure.mirror.download._compute_backoff_delay",
                    return_value=0.0,
                ):
                    result = await coord.download_file(
                        url=f"http://localhost:{server.port}/file.deb",
                        dest_path=dest,
                        expected_sha256="0" * 64,
                        expected_size=size,
                    )
                assert result.success is False
                assert "SHA256 mismatch" in (result.error or "")
                assert not dest.exists()
                assert not Path(str(dest) + ".part").exists()
            finally:
                await coord.close()

    @pytest.mark.asyncio
    async def test_size_mismatch_retries_and_fails(self, tmp_path):
        content = b"some content"
        sha256 = hashlib.sha256(content).hexdigest()

        app = web.Application()
        app.router.add_get("/file.deb", _make_response_handler(body=content))

        async with TestServer(app) as server:
            config = MirrorConfig(
                repositories=[],
                download_timeout=10,
                max_connections_per_repo=5,
                max_total_connections=10,
            )
            coord = DownloadCoordinator(storage_engine=MagicMock(), config=config)
            await coord.start()
            try:
                dest = tmp_path / "file.deb"
                with patch(
                    "debcraft.infrastructure.mirror.download._compute_backoff_delay",
                    return_value=0.0,
                ):
                    result = await coord.download_file(
                        url=f"http://localhost:{server.port}/file.deb",
                        dest_path=dest,
                        expected_sha256=sha256,
                        expected_size=999999,
                    )
                assert result.success is False
                assert "Size mismatch" in (result.error or "")
            finally:
                await coord.close()

    @pytest.mark.asyncio
    async def test_404_raises_http_client_error(self, tmp_path):
        app = web.Application()
        app.router.add_get("/missing.deb", _make_response_handler(status=404))

        async with TestServer(app) as server:
            config = MirrorConfig(
                repositories=[],
                download_timeout=10,
                max_connections_per_repo=5,
                max_total_connections=10,
            )
            coord = DownloadCoordinator(storage_engine=MagicMock(), config=config)
            await coord.start()
            try:
                dest = tmp_path / "missing.deb"
                with pytest.raises(HttpClientError) as exc_info:
                    await coord.download_file(
                        url=f"http://localhost:{server.port}/missing.deb",
                        dest_path=dest,
                        expected_sha256="abc",
                        expected_size=100,
                    )
                assert exc_info.value.status_code == 404
            finally:
                await coord.close()

    @pytest.mark.asyncio
    async def test_500_retries_and_returns_failure(self, tmp_path):
        app = web.Application()
        app.router.add_get("/error.deb", _make_response_handler(status=500))

        async with TestServer(app) as server:
            config = MirrorConfig(
                repositories=[],
                download_timeout=10,
                max_connections_per_repo=5,
                max_total_connections=10,
            )
            coord = DownloadCoordinator(storage_engine=MagicMock(), config=config)
            await coord.start()
            try:
                dest = tmp_path / "error.deb"
                with patch(
                    "debcraft.infrastructure.mirror.download._compute_backoff_delay",
                    return_value=0.0,
                ):
                    result = await coord.download_file(
                        url=f"http://localhost:{server.port}/error.deb",
                        dest_path=dest,
                        expected_sha256="abc",
                        expected_size=100,
                    )
                assert result.success is False
                assert "HTTP 500" in (result.error or "")
                assert result.retry_count == 2
            finally:
                await coord.close()

    @pytest.mark.asyncio
    async def test_part_file_deleted_before_new_download(self, tmp_path):
        content = b"fresh content"
        sha256 = hashlib.sha256(content).hexdigest()
        size = len(content)

        app = web.Application()
        app.router.add_get("/file.deb", _make_response_handler(body=content))

        async with TestServer(app) as server:
            config = MirrorConfig(
                repositories=[],
                download_timeout=10,
                max_connections_per_repo=5,
                max_total_connections=10,
            )
            coord = DownloadCoordinator(storage_engine=MagicMock(), config=config)
            await coord.start()
            try:
                dest = tmp_path / "file.deb"
                part = Path(str(dest) + ".part")
                part.write_bytes(b"stale partial data")

                result = await coord.download_file(
                    url=f"http://localhost:{server.port}/file.deb",
                    dest_path=dest,
                    expected_sha256=sha256,
                    expected_size=size,
                )
                assert result.success is True
                assert dest.read_bytes() == content
            finally:
                await coord.close()

    @pytest.mark.asyncio
    async def test_creates_parent_directories(self, tmp_path):
        content = b"nested file"
        sha256 = hashlib.sha256(content).hexdigest()
        size = len(content)

        app = web.Application()
        app.router.add_get("/file.deb", _make_response_handler(body=content))

        async with TestServer(app) as server:
            config = MirrorConfig(
                repositories=[],
                download_timeout=10,
                max_connections_per_repo=5,
                max_total_connections=10,
            )
            coord = DownloadCoordinator(storage_engine=MagicMock(), config=config)
            await coord.start()
            try:
                dest = tmp_path / "deep" / "nested" / "dir" / "file.deb"
                result = await coord.download_file(
                    url=f"http://localhost:{server.port}/file.deb",
                    dest_path=dest,
                    expected_sha256=sha256,
                    expected_size=size,
                )
                assert result.success is True
                assert dest.exists()
            finally:
                await coord.close()


@pytest.mark.unit
@pytest.mark.mirror
class TestDownloadBatch:
    """Tests for batch download with concurrency."""

    @pytest.mark.asyncio
    async def test_batch_downloads_multiple_files(self, tmp_path):
        files = {f"/file{i}.deb": f"content_{i}".encode() for i in range(3)}

        app = web.Application()
        for path, content in files.items():
            app.router.add_get(path, _make_response_handler(body=content))

        async with TestServer(app) as server:
            config = MirrorConfig(
                repositories=[],
                download_timeout=10,
                max_connections_per_repo=5,
                max_total_connections=10,
            )
            coord = DownloadCoordinator(storage_engine=MagicMock(), config=config)
            await coord.start()
            try:
                tasks = []
                for path, content in files.items():
                    dest = tmp_path / path.lstrip("/")
                    tasks.append(
                        DownloadTask(
                            url=f"http://localhost:{server.port}{path}",
                            dest_path=dest,
                            expected_sha256=hashlib.sha256(content).hexdigest(),
                            expected_size=len(content),
                        )
                    )

                results = await coord.download_batch(tasks, max_concurrent=2)
                assert len(results) == 3
                assert all(r.success for r in results)
            finally:
                await coord.close()

    @pytest.mark.asyncio
    async def test_batch_respects_concurrency_limit(self, tmp_path):
        """Verify semaphore limits concurrent downloads."""
        max_concurrent_observed = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def slow_handler(request):
            nonlocal max_concurrent_observed, current_concurrent
            async with lock:
                current_concurrent += 1
                max_concurrent_observed = max(max_concurrent_observed, current_concurrent)
            await asyncio.sleep(0.05)
            async with lock:
                current_concurrent -= 1
            content = b"data"
            return web.Response(body=content)

        app = web.Application()
        for i in range(5):
            app.router.add_get(f"/file{i}.deb", slow_handler)

        async with TestServer(app) as server:
            config = MirrorConfig(
                repositories=[],
                download_timeout=10,
                max_connections_per_repo=10,
                max_total_connections=20,
            )
            coord = DownloadCoordinator(storage_engine=MagicMock(), config=config)
            await coord.start()
            try:
                content = b"data"
                sha = hashlib.sha256(content).hexdigest()
                tasks = [
                    DownloadTask(
                        url=f"http://localhost:{server.port}/file{i}.deb",
                        dest_path=tmp_path / f"file{i}.deb",
                        expected_sha256=sha,
                        expected_size=len(content),
                    )
                    for i in range(5)
                ]

                results = await coord.download_batch(tasks, max_concurrent=2)
                assert all(r.success for r in results)
                assert max_concurrent_observed <= 2
            finally:
                await coord.close()


@pytest.mark.unit
@pytest.mark.mirror
class TestCheckConditional:
    """Tests for conditional HTTP request handling."""

    @pytest.mark.asyncio
    async def test_returns_true_on_304(self):
        app = web.Application()

        async def handler(request):
            if request.headers.get("If-None-Match") == '"etag123"':
                return web.Response(status=304)
            return web.Response(status=200, body=b"content")

        app.router.add_head("/release", handler)

        async with TestServer(app) as server:
            config = MirrorConfig(
                repositories=[],
                download_timeout=10,
                max_connections_per_repo=5,
                max_total_connections=10,
            )
            coord = DownloadCoordinator(storage_engine=MagicMock(), config=config)
            await coord.start()
            try:
                unchanged = await coord.check_conditional(
                    url=f"http://localhost:{server.port}/release",
                    etag='"etag123"',
                )
                assert unchanged is True
            finally:
                await coord.close()

    @pytest.mark.asyncio
    async def test_returns_false_on_200(self):
        app = web.Application()
        app.router.add_head("/release", _make_response_handler(status=200))

        async with TestServer(app) as server:
            config = MirrorConfig(
                repositories=[],
                download_timeout=10,
                max_connections_per_repo=5,
                max_total_connections=10,
            )
            coord = DownloadCoordinator(storage_engine=MagicMock(), config=config)
            await coord.start()
            try:
                unchanged = await coord.check_conditional(
                    url=f"http://localhost:{server.port}/release",
                    etag='"old-etag"',
                )
                assert unchanged is False
            finally:
                await coord.close()

    @pytest.mark.asyncio
    async def test_sends_if_modified_since_header(self):
        received_headers = {}

        async def handler(request):
            received_headers.update(dict(request.headers))
            return web.Response(status=304)

        app = web.Application()
        app.router.add_head("/release", handler)

        async with TestServer(app) as server:
            config = MirrorConfig(
                repositories=[],
                download_timeout=10,
                max_connections_per_repo=5,
                max_total_connections=10,
            )
            coord = DownloadCoordinator(storage_engine=MagicMock(), config=config)
            await coord.start()
            try:
                await coord.check_conditional(
                    url=f"http://localhost:{server.port}/release",
                    last_modified="Thu, 01 Jan 2024 00:00:00 GMT",
                )
                assert received_headers.get("If-Modified-Since") == "Thu, 01 Jan 2024 00:00:00 GMT"
            finally:
                await coord.close()

    @pytest.mark.asyncio
    async def test_returns_false_on_network_error(self, coordinator):
        await coordinator.start()
        try:
            result = await coordinator.check_conditional(
                url="http://localhost:1/nonexistent",
                etag='"test"',
            )
            assert result is False
        finally:
            await coordinator.close()


@pytest.mark.unit
@pytest.mark.mirror
class TestExponentialBackoff:
    """Tests for the backoff delay computation."""

    def test_attempt_0_base_delay(self):
        # delay = min(1 * 2^0, 30) = 1, + jitter in [0, 0.25]
        with patch("debcraft.infrastructure.mirror.download.random.uniform", return_value=0.0):
            delay = _compute_backoff_delay(0)
        assert delay == 1.0

    def test_attempt_1_doubles(self):
        # delay = min(1 * 2^1, 30) = 2, + jitter in [0, 0.5]
        with patch("debcraft.infrastructure.mirror.download.random.uniform", return_value=0.0):
            delay = _compute_backoff_delay(1)
        assert delay == 2.0

    def test_attempt_4_capped_at_30(self):
        # delay = min(1 * 2^4, 30) = 16, + jitter
        with patch("debcraft.infrastructure.mirror.download.random.uniform", return_value=0.0):
            delay = _compute_backoff_delay(4)
        assert delay == 16.0

    def test_attempt_5_capped_at_30(self):
        # delay = min(1 * 2^5, 30) = 30
        with patch("debcraft.infrastructure.mirror.download.random.uniform", return_value=0.0):
            delay = _compute_backoff_delay(5)
        assert delay == 30.0

    def test_attempt_10_still_capped_at_30(self):
        # delay = min(1 * 2^10, 30) = 30
        with patch("debcraft.infrastructure.mirror.download.random.uniform", return_value=0.0):
            delay = _compute_backoff_delay(10)
        assert delay == 30.0

    def test_jitter_within_bounds(self):
        # For attempt=2: base delay = 4, max jitter = 4 * 0.25 = 1.0
        for _ in range(100):
            delay = _compute_backoff_delay(2)
            assert 4.0 <= delay <= 5.0
