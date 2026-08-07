"""Bug condition exploration test for download_batch() ExceptionGroup crash.

**Validates: Requirements 1.1, 1.2, 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4**

This test confirms that when download_batch() encounters an HTTP 4xx response
in any task, the current code raises an ExceptionGroup (wrapping HttpClientError)
instead of returning a complete list of DownloadResult objects.

This test is EXPECTED TO FAIL on unfixed code, confirming the bug exists.
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
    DownloadCoordinator,
    DownloadTask,
)
from debcraft.infrastructure.mirror.errors import HttpClientError

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Number of successful tasks in the batch (at least 1 success alongside failures)
_num_success_tasks = st.integers(min_value=1, max_value=4)

# Number of tasks that will trigger 4xx (at least 1)
_num_failing_tasks = st.integers(min_value=1, max_value=3)

# HTTP 4xx status codes
_http_4xx_status = st.integers(min_value=400, max_value=499)

# Random content for successful downloads
_content_strategy = st.binary(min_size=1, max_size=512)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Bug Condition Exploration Test
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestDownloadBatchBugExploration:
    """Bug exploration: download_batch() crashes with ExceptionGroup on 4xx.

    **Validates: Requirements 1.1, 1.2, 2.1, 2.2**

    When at least one download task in a batch triggers an HTTP 4xx response,
    the current (buggy) code raises an ExceptionGroup instead of returning
    a complete list of DownloadResult objects.

    This test asserts the EXPECTED (correct) behavior: download_batch() should
    return a list of DownloadResult with the same length as the input, where
    failed tasks have success=False. On unfixed code, this test FAILS because
    the ExceptionGroup is raised instead.
    """

    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(
        num_success=_num_success_tasks,
        num_fail=_num_failing_tasks,
        status_code=_http_4xx_status,
        content=_content_strategy,
    )
    async def test_batch_returns_results_without_exception_group(
        self,
        num_success: int,
        num_fail: int,
        status_code: int,
        content: bytes,
    ) -> None:
        """download_batch() must return results for all tasks, not raise.

        **Validates: Requirements 1.1, 1.2, 2.1, 2.2**

        Generates a batch with some successful tasks and at least one task
        that returns a 4xx status code. Asserts that:
        1. No ExceptionGroup is raised
        2. The results list has the same length as the input tasks
        3. Failed tasks have success=False with an error message
        4. Successful tasks have success=True
        """
        sha256 = hashlib.sha256(content).hexdigest()
        size = len(content)

        # Build aiohttp app with routes for success and failure
        app = web.Application()

        # Add success routes
        for i in range(num_success):

            async def success_handler(request: web.Request, _content: bytes = content) -> web.Response:
                return web.Response(body=_content)

            app.router.add_get(f"/success_{i}.deb", success_handler)

        # Add failure routes
        for i in range(num_fail):

            async def fail_handler(request: web.Request, _status: int = status_code) -> web.Response:
                return web.Response(status=_status, body=b"not found")

            app.router.add_get(f"/fail_{i}.deb", fail_handler)

        tmp_dir = Path(tempfile.mkdtemp())
        try:
            async with TestServer(app) as server:
                port = server.port
                config = _make_test_config()
                coordinator = DownloadCoordinator(
                    storage_engine=_FakeStorageEngine(),
                    config=config,
                )
                await coordinator.start()
                try:
                    # Build task list
                    tasks: list[DownloadTask] = []

                    for i in range(num_success):
                        tasks.append(
                            DownloadTask(
                                url=f"http://localhost:{port}/success_{i}.deb",
                                dest_path=tmp_dir / f"success_{i}.deb",
                                expected_sha256=sha256,
                                expected_size=size,
                            )
                        )

                    for i in range(num_fail):
                        tasks.append(
                            DownloadTask(
                                url=f"http://localhost:{port}/fail_{i}.deb",
                                dest_path=tmp_dir / f"fail_{i}.deb",
                                expected_sha256="a" * 64,
                                expected_size=100,
                            )
                        )

                    total_tasks = len(tasks)

                    # This should NOT raise ExceptionGroup — it should
                    # return a complete results list
                    results = await coordinator.download_batch(tasks, max_concurrent=3)

                    # Assert results list is complete
                    assert len(results) == total_tasks, f"Expected {total_tasks} results, got {len(results)}"

                    # Assert successful tasks succeeded
                    for i in range(num_success):
                        assert results[i].success is True, f"Task {i} should have succeeded"

                    # Assert failed tasks have success=False with error
                    for i in range(num_fail):
                        idx = num_success + i
                        assert results[idx].success is False, f"Task {idx} should have failed"
                        assert results[idx].error is not None, f"Task {idx} should have an error message"

                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_sleep(delay: float) -> None:
    """No-op replacement for asyncio.sleep to speed up retry tests."""


# ---------------------------------------------------------------------------
# Regression Test: Multiple 4xx failures produce independent failed results
# ---------------------------------------------------------------------------

# Strategy for a list of distinct 4xx codes (2-4 codes)
_multi_4xx_codes = st.lists(
    st.integers(min_value=400, max_value=499),
    min_size=2,
    max_size=4,
)


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestBatchMultiple4xxFailures:
    """Multiple 4xx failures in a batch each produce independent failed results.

    **Validates: Requirements 2.3, 3.1**

    When multiple tasks in a batch encounter different 4xx responses, each
    produces an independent DownloadResult with success=False, while other
    tasks that succeed remain unaffected.
    """

    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(
        status_codes=_multi_4xx_codes,
        content=_content_strategy,
        num_success=st.integers(min_value=1, max_value=3),
    )
    async def test_multiple_4xx_failures_independent(
        self,
        status_codes: list[int],
        content: bytes,
        num_success: int,
    ) -> None:
        """Each 4xx failure is recorded independently; successes unaffected.

        **Validates: Requirements 2.3, 3.1**
        """
        sha256 = hashlib.sha256(content).hexdigest()
        size = len(content)

        app = web.Application()

        # Add success routes
        for i in range(num_success):

            async def success_handler(request: web.Request, _content: bytes = content) -> web.Response:
                return web.Response(body=_content)

            app.router.add_get(f"/ok_{i}.deb", success_handler)

        # Add failure routes with different status codes
        for i, code in enumerate(status_codes):

            async def fail_handler(request: web.Request, _status: int = code) -> web.Response:
                return web.Response(status=_status, body=b"error")

            app.router.add_get(f"/err_{i}.deb", fail_handler)

        tmp_dir = Path(tempfile.mkdtemp())
        try:
            async with TestServer(app) as server:
                port = server.port
                config = _make_test_config()
                coordinator = DownloadCoordinator(
                    storage_engine=_FakeStorageEngine(),
                    config=config,
                )
                await coordinator.start()
                try:
                    tasks: list[DownloadTask] = []

                    # Successful tasks first
                    for i in range(num_success):
                        tasks.append(
                            DownloadTask(
                                url=f"http://localhost:{port}/ok_{i}.deb",
                                dest_path=tmp_dir / f"ok_{i}.deb",
                                expected_sha256=sha256,
                                expected_size=size,
                            )
                        )

                    # Failing tasks
                    for i, _code in enumerate(status_codes):
                        tasks.append(
                            DownloadTask(
                                url=f"http://localhost:{port}/err_{i}.deb",
                                dest_path=tmp_dir / f"err_{i}.deb",
                                expected_sha256="a" * 64,
                                expected_size=100,
                            )
                        )

                    results = await coordinator.download_batch(tasks, max_concurrent=4)

                    # All tasks get a result
                    assert len(results) == num_success + len(status_codes)

                    # Successful tasks are unaffected
                    for i in range(num_success):
                        assert results[i].success is True
                        assert results[i].sha256_verified is True

                    # Each failed task is independent with its own error
                    for i, code in enumerate(status_codes):
                        idx = num_success + i
                        assert results[idx].success is False
                        assert results[idx].error is not None
                        assert str(code) in results[idx].error

                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Regression Test: Direct download_file() still raises HttpClientError
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestDirectDownloadFileStillRaises:
    """Direct download_file() calls still raise HttpClientError for 4xx.

    **Validates: Requirements 3.3**

    The batch-level catch does NOT affect the direct API — calling
    download_file() directly with a 4xx URL still raises HttpClientError.
    """

    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(status_code=_http_4xx_status)
    async def test_download_file_raises_http_client_error(
        self,
        status_code: int,
    ) -> None:
        """download_file() raises HttpClientError on 4xx, not a DownloadResult.

        **Validates: Requirements 3.3**
        """

        async def handler(request: web.Request) -> web.Response:
            return web.Response(status=status_code, body=b"client error")

        app = web.Application()
        app.router.add_get("/file.deb", handler)

        tmp_dir = Path(tempfile.mkdtemp())
        try:
            async with TestServer(app) as server:
                url = str(server.make_url("/file.deb"))
                dest_path = tmp_dir / "file.deb"

                config = _make_test_config()
                coordinator = DownloadCoordinator(
                    storage_engine=_FakeStorageEngine(),
                    config=config,
                )
                await coordinator.start()
                try:
                    with pytest.raises(HttpClientError) as exc_info:
                        await coordinator.download_file(
                            url=url,
                            dest_path=dest_path,
                            expected_sha256="a" * 64,
                            expected_size=100,
                        )

                    assert exc_info.value.status_code == status_code
                    assert not dest_path.exists()
                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Regression Test: 5xx/network errors in batch still trigger retries
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestBatch5xxRetriesBeforeFailing:
    """5xx errors in a batch still trigger retries before failing.

    **Validates: Requirements 3.2**

    When a task in a batch gets a 5xx response, it retries with backoff,
    exhausts retries, and returns a failed DownloadResult — without crashing
    the batch or affecting other tasks.
    """

    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(
        status_code=st.integers(min_value=500, max_value=599),
        content=_content_strategy,
    )
    async def test_5xx_retries_and_fails_gracefully_in_batch(
        self,
        status_code: int,
        content: bytes,
    ) -> None:
        """5xx task retries, fails, but batch still completes with other results.

        **Validates: Requirements 3.2**
        """
        sha256 = hashlib.sha256(content).hexdigest()
        size = len(content)
        call_count = {"value": 0}

        async def success_handler(request: web.Request, _content: bytes = content) -> web.Response:
            return web.Response(body=_content)

        async def server_error_handler(request: web.Request) -> web.Response:
            call_count["value"] += 1
            return web.Response(status=status_code, body=b"server error")

        app = web.Application()
        app.router.add_get("/good.deb", success_handler)
        app.router.add_get("/bad.deb", server_error_handler)

        tmp_dir = Path(tempfile.mkdtemp())
        try:
            async with TestServer(app) as server:
                port = server.port
                config = _make_test_config()
                coordinator = DownloadCoordinator(
                    storage_engine=_FakeStorageEngine(),
                    config=config,
                )
                await coordinator.start()
                try:
                    tasks = [
                        DownloadTask(
                            url=f"http://localhost:{port}/good.deb",
                            dest_path=tmp_dir / "good.deb",
                            expected_sha256=sha256,
                            expected_size=size,
                        ),
                        DownloadTask(
                            url=f"http://localhost:{port}/bad.deb",
                            dest_path=tmp_dir / "bad.deb",
                            expected_sha256="a" * 64,
                            expected_size=100,
                        ),
                    ]

                    with patch(
                        "debcraft.infrastructure.mirror.download.asyncio.sleep",
                        _noop_sleep,
                    ):
                        results = await coordinator.download_batch(tasks, max_concurrent=2)

                    # Batch completed without crashing
                    assert len(results) == 2

                    # Successful task is unaffected
                    assert results[0].success is True
                    assert results[0].sha256_verified is True

                    # 5xx task retried (3 attempts) and failed
                    assert results[1].success is False
                    assert results[1].error is not None
                    assert str(status_code) in results[1].error
                    # Confirm retries happened (3 attempts total)
                    assert call_count["value"] == 3

                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
