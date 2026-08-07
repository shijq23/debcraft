"""Property-based preservation tests for rate limiter integration bugfix.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**

Property 2: Preservation - Download Behavior Unchanged For Non-Rate-Limiting Paths

These tests capture the EXISTING download behavior that must remain unchanged
after the rate limiter is wired into the download pipeline. They verify:
- 4xx errors fail immediately without retry
- 5xx errors retry exactly _MAX_ATTEMPTS - 1 times
- Successful downloads produce correct DownloadResult with checksum verification
- Backoff computation produces correct bounded delays with jitter
- Atomic .part → final rename happens on success
"""

from __future__ import annotations

import hashlib
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
from debcraft.infrastructure.mirror.download import (
    _BASE_BACKOFF,
    _JITTER_FACTOR,
    _MAX_ATTEMPTS,
    _MAX_BACKOFF,
    DownloadCoordinator,
    _compute_backoff_delay,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# HTTP 4xx status codes (client errors, non-retriable)
_http_4xx_strategy = st.integers(min_value=400, max_value=499)

# HTTP 5xx status codes (server errors, retriable)
_http_5xx_strategy = st.integers(min_value=500, max_value=599)

# Attempt numbers for backoff computation
_attempt_strategy = st.integers(min_value=0, max_value=10)

# Random file content for download tests
_file_content_strategy = st.binary(min_size=1, max_size=2048)


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------


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


class _FakeStorageEngine:
    """Minimal fake StorageEngine for testing DownloadCoordinator."""

    def get_path(self, name: str) -> Path:
        return Path("/tmp/test-mirror")


def _make_response_handler(body: bytes):
    """Create an async handler that returns a Response with the given body."""

    async def handler(request: web.Request) -> web.Response:
        return web.Response(body=body)

    return handler


async def _noop_sleep(delay: float) -> None:
    """No-op replacement for asyncio.sleep to speed up retry tests."""


# ---------------------------------------------------------------------------
# Property 2a: For all HTTP 4xx status codes, download_file fails immediately
#              without retry (retry_count=0 in result)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestPreservation4xxImmediateFailure:
    """For all HTTP 4xx status codes (400-499): download_file fails immediately without retry.

    **Validates: Requirements 3.3**
    """

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(status_code=_http_4xx_strategy)
    async def test_4xx_fails_immediately_no_retry(self, status_code: int) -> None:
        """For any 4xx HTTP status code, the download coordinator raises HttpClientError immediately."""
        call_count = {"value": 0}

        async def handler(request: web.Request) -> web.Response:
            call_count["value"] += 1
            return web.Response(status=status_code, body=b"error")

        app = web.Application()
        app.router.add_get("/file", handler)

        tmp_dir = Path(tempfile.mkdtemp())
        try:
            async with TestServer(app) as server:
                url = str(server.make_url("/file"))
                dest_path = tmp_dir / "file"

                config = _make_test_config()
                coordinator = DownloadCoordinator(
                    storage_engine=_FakeStorageEngine(),
                    config=config,
                )
                await coordinator.start()
                try:
                    from debcraft.infrastructure.mirror.errors import (
                        HttpClientError,
                    )

                    with pytest.raises(HttpClientError) as exc_info:
                        await coordinator.download_file(
                            url=url,
                            dest_path=dest_path,
                            expected_sha256="a" * 64,
                            expected_size=100,
                        )

                    # Only called once - no retry for 4xx
                    assert call_count["value"] == 1
                    assert exc_info.value.status_code == status_code
                    # No retry occurred
                    assert exc_info.value.retry_count == 0
                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Property 2b: For all HTTP 5xx status codes, download_file retries exactly
#              _MAX_ATTEMPTS - 1 times (total of _MAX_ATTEMPTS calls)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestPreservation5xxRetryBehavior:
    """For all HTTP 5xx status codes (500-599): download_file retries exactly _MAX_ATTEMPTS - 1 times.

    **Validates: Requirements 3.2**
    """

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(status_code=_http_5xx_strategy)
    async def test_5xx_retries_max_attempts_times(self, status_code: int) -> None:
        """For any 5xx HTTP status code, the download coordinator retries _MAX_ATTEMPTS - 1 times."""
        call_count = {"value": 0}

        async def handler(request: web.Request) -> web.Response:
            call_count["value"] += 1
            return web.Response(status=status_code, body=b"server error")

        app = web.Application()
        app.router.add_get("/file", handler)

        tmp_dir = Path(tempfile.mkdtemp())
        try:
            async with TestServer(app) as server:
                url = str(server.make_url("/file"))
                dest_path = tmp_dir / "file"

                config = _make_test_config()
                coordinator = DownloadCoordinator(
                    storage_engine=_FakeStorageEngine(),
                    config=config,
                )
                await coordinator.start()
                try:
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

                    # Total HTTP calls should be _MAX_ATTEMPTS
                    assert call_count["value"] == _MAX_ATTEMPTS
                    assert result.success is False
                    assert result.retry_count == _MAX_ATTEMPTS - 1
                    assert result.status_code == status_code
                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Property 2c: For all valid downloads, checksum and size verification occur
#              and atomic .part → final rename happens
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestPreservationSuccessfulDownload:
    """For all valid downloads: checksum/size verification and atomic .part rename occur.

    **Validates: Requirements 3.5**
    """

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(data=_file_content_strategy)
    async def test_successful_download_verifies_and_renames(self, data: bytes) -> None:
        """For any valid file content, a successful download verifies SHA256, size, and renames atomically."""
        expected_sha256 = hashlib.sha256(data).hexdigest()
        expected_size = len(data)

        app = web.Application()
        app.router.add_get("/file", _make_response_handler(data))

        tmp_dir = Path(tempfile.mkdtemp())
        try:
            async with TestServer(app) as server:
                url = str(server.make_url("/file"))
                dest_path = tmp_dir / "downloaded_file"
                part_path = Path(str(dest_path) + ".part")

                config = _make_test_config()
                coordinator = DownloadCoordinator(
                    storage_engine=_FakeStorageEngine(),
                    config=config,
                )
                await coordinator.start()
                try:
                    result = await coordinator.download_file(
                        url=url,
                        dest_path=dest_path,
                        expected_sha256=expected_sha256,
                        expected_size=expected_size,
                    )

                    # Successful result
                    assert result.success is True
                    assert result.sha256_verified is True
                    assert result.bytes_transferred == expected_size
                    assert result.retry_count == 0
                    # Atomic rename happened
                    assert dest_path.exists()
                    assert not part_path.exists()
                    # Content is correct
                    assert dest_path.read_bytes() == data
                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Property 2d: For all backoff computations, _compute_backoff_delay(attempt)
#              produces min(1 * 2^attempt, 30) + jitter where jitter in
#              [0, delay * 0.25]
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestPreservationBackoffComputation:
    """For all backoff computations: _compute_backoff_delay(attempt) produces bounded delays with jitter.

    **Validates: Requirements 3.2**
    """

    @settings(max_examples=200)
    @given(attempt=_attempt_strategy)
    def test_backoff_delay_within_expected_bounds(self, attempt: int) -> None:
        """For any attempt number, the computed delay is within [base, base * 1.25]."""
        delay = _compute_backoff_delay(attempt)
        base = min(_BASE_BACKOFF * (2**attempt), _MAX_BACKOFF)
        max_with_jitter = base + base * _JITTER_FACTOR

        assert delay >= base, f"Delay {delay} < base {base} for attempt {attempt}"
        assert delay <= max_with_jitter, f"Delay {delay} > max {max_with_jitter} for attempt {attempt}"

    @settings(max_examples=200)
    @given(attempt=_attempt_strategy)
    def test_backoff_delay_never_exceeds_absolute_max(self, attempt: int) -> None:
        """The delay never exceeds _MAX_BACKOFF * (1 + _JITTER_FACTOR) regardless of attempt number."""
        delay = _compute_backoff_delay(attempt)
        absolute_max = _MAX_BACKOFF * (1 + _JITTER_FACTOR)
        assert delay <= absolute_max, f"Delay {delay} exceeds absolute max {absolute_max}"

    @settings(max_examples=200)
    @given(attempt=_attempt_strategy)
    def test_backoff_delay_is_non_negative(self, attempt: int) -> None:
        """The delay is always non-negative."""
        delay = _compute_backoff_delay(attempt)
        assert delay >= 0.0


# ---------------------------------------------------------------------------
# Property 2e: Checksum mismatch retries and size mismatch retries
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestPreservationChecksumAndSizeRetry:
    """Checksum/size mismatch errors are retriable up to _MAX_ATTEMPTS times before failing.

    **Validates: Requirements 3.5**
    """

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(data=_file_content_strategy)
    async def test_checksum_mismatch_retries(self, data: bytes) -> None:
        """When the server returns content with a wrong checksum, the coordinator retries."""
        # Use a wrong hash that doesn't match data
        wrong_sha256 = hashlib.sha256(b"definitely wrong").hexdigest()
        expected_size = len(data)

        call_count = {"value": 0}

        async def handler(request: web.Request) -> web.Response:
            call_count["value"] += 1
            return web.Response(body=data)

        app = web.Application()
        app.router.add_get("/file", handler)

        tmp_dir = Path(tempfile.mkdtemp())
        try:
            async with TestServer(app) as server:
                url = str(server.make_url("/file"))
                dest_path = tmp_dir / "file"

                config = _make_test_config()
                coordinator = DownloadCoordinator(
                    storage_engine=_FakeStorageEngine(),
                    config=config,
                )
                await coordinator.start()
                try:
                    with patch(
                        "debcraft.infrastructure.mirror.download.asyncio.sleep",
                        _noop_sleep,
                    ):
                        result = await coordinator.download_file(
                            url=url,
                            dest_path=dest_path,
                            expected_sha256=wrong_sha256,
                            expected_size=expected_size,
                        )

                    # Should retry _MAX_ATTEMPTS times total
                    assert call_count["value"] == _MAX_ATTEMPTS
                    assert result.success is False
                    assert result.retry_count == _MAX_ATTEMPTS - 1
                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(data=_file_content_strategy)
    async def test_size_mismatch_retries(self, data: bytes) -> None:
        """When the server returns content with a wrong size, the coordinator retries."""
        expected_sha256 = hashlib.sha256(data).hexdigest()
        wrong_size = len(data) + 100  # Deliberate mismatch

        call_count = {"value": 0}

        async def handler(request: web.Request) -> web.Response:
            call_count["value"] += 1
            return web.Response(body=data)

        app = web.Application()
        app.router.add_get("/file", handler)

        tmp_dir = Path(tempfile.mkdtemp())
        try:
            async with TestServer(app) as server:
                url = str(server.make_url("/file"))
                dest_path = tmp_dir / "file"

                config = _make_test_config()
                coordinator = DownloadCoordinator(
                    storage_engine=_FakeStorageEngine(),
                    config=config,
                )
                await coordinator.start()
                try:
                    with patch(
                        "debcraft.infrastructure.mirror.download.asyncio.sleep",
                        _noop_sleep,
                    ):
                        result = await coordinator.download_file(
                            url=url,
                            dest_path=dest_path,
                            expected_sha256=expected_sha256,
                            expected_size=wrong_size,
                        )

                    # Should retry _MAX_ATTEMPTS times total
                    assert call_count["value"] == _MAX_ATTEMPTS
                    assert result.success is False
                    assert result.retry_count == _MAX_ATTEMPTS - 1
                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Property 2f: Network error retries with backoff
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestPreservationNetworkErrorRetry:
    """Network errors are retriable up to _MAX_ATTEMPTS times with backoff before failing.

    **Validates: Requirements 3.2**
    """

    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(data=st.binary(min_size=1, max_size=64))
    async def test_network_error_retries(self, data: bytes) -> None:
        """When the server connection is refused/reset, the coordinator retries _MAX_ATTEMPTS times."""
        call_count = {"value": 0}

        async def handler(request: web.Request) -> web.Response:
            call_count["value"] += 1
            # Force a connection reset by raising an error
            raise web.HTTPServiceUnavailable()

        app = web.Application()
        app.router.add_get("/file", handler)

        tmp_dir = Path(tempfile.mkdtemp())
        try:
            async with TestServer(app) as server:
                url = str(server.make_url("/file"))
                dest_path = tmp_dir / "file"

                config = _make_test_config()
                coordinator = DownloadCoordinator(
                    storage_engine=_FakeStorageEngine(),
                    config=config,
                )
                await coordinator.start()
                try:
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

                    # Should retry _MAX_ATTEMPTS times total
                    assert call_count["value"] == _MAX_ATTEMPTS
                    assert result.success is False
                    assert result.retry_count == _MAX_ATTEMPTS - 1
                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Property 2g: download_batch() API signature and behavior unchanged
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestPreservationDownloadBatch:
    """download_batch() API signature and behavior unchanged.

    It accepts a list of DownloadTasks and max_concurrent, returns list of DownloadResults.

    **Validates: Requirements 3.6**
    """

    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(
        data_list=st.lists(
            st.binary(min_size=1, max_size=512),
            min_size=1,
            max_size=3,
        )
    )
    async def test_download_batch_returns_results_for_all_tasks(self, data_list: list[bytes]) -> None:
        """download_batch returns one DownloadResult per input task."""
        from debcraft.infrastructure.mirror.download import DownloadTask

        # Build handlers for each file
        handlers: dict[str, bytes] = {}
        for i, data in enumerate(data_list):
            handlers[f"/file{i}"] = data

        async def dynamic_handler(request: web.Request) -> web.Response:
            path = request.path
            if path in handlers:
                return web.Response(body=handlers[path])
            return web.Response(status=404)

        app = web.Application()
        app.router.add_get("/{path:.*}", dynamic_handler)

        tmp_dir = Path(tempfile.mkdtemp())
        try:
            async with TestServer(app) as server:
                tasks = []
                for i, data in enumerate(data_list):
                    url = str(server.make_url(f"/file{i}"))
                    dest = tmp_dir / f"file{i}"
                    sha256 = hashlib.sha256(data).hexdigest()
                    tasks.append(
                        DownloadTask(
                            url=url,
                            dest_path=dest,
                            expected_sha256=sha256,
                            expected_size=len(data),
                        )
                    )

                config = _make_test_config()
                coordinator = DownloadCoordinator(
                    storage_engine=_FakeStorageEngine(),
                    config=config,
                )
                await coordinator.start()
                try:
                    results = await coordinator.download_batch(tasks=tasks, max_concurrent=5)

                    # One result per task
                    assert len(results) == len(tasks)
                    # All should succeed
                    for result in results:
                        assert result.success is True
                        assert result.sha256_verified is True
                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
