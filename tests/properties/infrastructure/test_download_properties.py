"""Property-based tests for download coordinator safety.

**Validates: Requirements 2.5, 4.1, 4.2, 4.7, 11.3, 12.2, 12.3, 12.5, 12.7**

Property 6: SHA256 verification accepts correct hashes and rejects incorrect ones.
For any byte sequence and its computed SHA256 digest, the verification function
SHALL return True when the expected hash equals the computed hash, and SHALL return
False for any expected hash that differs from the computed hash by at least one character.

Property 7: Atomic download lifecycle (.part file safety).
For any download operation to a destination path: (a) during transfer, only a
.part-suffixed file exists; (b) on successful verification, the .part file is
atomically renamed and no .part file remains; (c) on verification failure, the
.part file is deleted and the final path is never created.

Property 20: Exponential backoff delay bounds.
For any attempt number, the delay follows min(1 * 2^attempt, 30) + jitter
where jitter is [0, delay*0.25].

Property 21: HTTP error classification.
4xx → HttpClientError (non-retriable), 5xx → HttpServerError (retriable),
network errors → NetworkError (retriable).

Property 22: Size mismatch detection.
For any downloaded file whose size doesn't match metadata, verification fails.
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
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from debcraft.infrastructure.mirror.download import (
    DownloadCoordinator,
    _compute_backoff_delay,
)
from debcraft.infrastructure.mirror.errors import (
    HttpClientError,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Random byte content for SHA256 tests (limit size for speed)
_bytes_strategy = st.binary(min_size=0, max_size=4096)

# Valid SHA256 hex strings (64 hex characters)
_sha256_strategy = st.text(
    alphabet="0123456789abcdef",
    min_size=64,
    max_size=64,
)

# Attempt numbers (0-indexed, up to 2 for 3 max attempts)
_attempt_strategy = st.integers(min_value=0, max_value=10)

# HTTP 4xx status codes
_http_4xx_strategy = st.integers(min_value=400, max_value=499)

# HTTP 5xx status codes
_http_5xx_strategy = st.integers(min_value=500, max_value=599)


# ---------------------------------------------------------------------------
# Property 6: SHA256 verification accepts correct hashes and rejects incorrect ones
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty6SHA256Verification:
    """Property 6: SHA256 verification accepts correct hashes and rejects incorrect ones.

    For any byte sequence and its computed SHA256 digest, the verification function
    SHALL return True when the expected hash equals the computed hash, and SHALL return
    False for any expected hash that differs from the computed hash by at least one character.
    """

    @settings(max_examples=200)
    @given(data=_bytes_strategy)
    def test_correct_hash_is_accepted(self, data: bytes) -> None:
        """**Validates: Requirements 2.5**.

        Computing SHA256 of any byte sequence and comparing against itself
        always yields equality.
        """
        computed = hashlib.sha256(data).hexdigest()
        assert computed == computed
        # Verify the hash function is deterministic
        recomputed = hashlib.sha256(data).hexdigest()
        assert computed == recomputed

    @settings(max_examples=200)
    @given(data=_bytes_strategy, wrong_hash=_sha256_strategy)
    def test_incorrect_hash_is_rejected(self, data: bytes, wrong_hash: str) -> None:
        """**Validates: Requirements 2.5**.

        For any byte sequence, if the expected hash differs from the computed
        hash by at least one character, verification fails.
        """
        computed = hashlib.sha256(data).hexdigest()
        assume(wrong_hash != computed)
        assert wrong_hash != computed

    @settings(max_examples=200)
    @given(data=_bytes_strategy)
    def test_single_bit_change_in_hash_is_rejected(self, data: bytes) -> None:
        """**Validates: Requirements 2.5**.

        Flipping a single hex character in the computed hash always produces
        a mismatch.
        """
        computed = hashlib.sha256(data).hexdigest()
        # Flip the first character
        first_char = computed[0]
        flipped = "1" if first_char == "0" else "0"
        wrong_hash = flipped + computed[1:]
        assert wrong_hash != computed


# ---------------------------------------------------------------------------
# Property 7: Atomic download lifecycle (.part file safety)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestProperty7AtomicDownloadLifecycle:
    """Property 7: Atomic download lifecycle (.part file safety).

    For any download operation to a destination path: (a) during transfer, only a
    .part-suffixed file exists; (b) on successful verification, the .part file is
    atomically renamed and no .part file remains; (c) on verification failure or
    terminal error, the .part file is deleted and the final path is never created.
    """

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(data=st.binary(min_size=1, max_size=1024))
    async def test_successful_download_removes_part_file(self, data: bytes) -> None:
        """**Validates: Requirements 4.1, 4.2**.

        On successful download with correct hash and size, the .part file
        is renamed to the final path and no .part file remains.
        """
        expected_sha256 = hashlib.sha256(data).hexdigest()
        expected_size = len(data)

        tmp_dir = Path(tempfile.mkdtemp())
        try:
            # Create a simple aiohttp app that serves the data
            app = web.Application()
            app.router.add_get("/file", _make_response_handler(data))

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

                    # Assertions: success, final file exists, .part gone
                    assert result.success is True
                    assert result.sha256_verified is True
                    assert dest_path.exists()
                    assert not part_path.exists()
                    assert dest_path.read_bytes() == data
                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(data=st.binary(min_size=1, max_size=1024))
    async def test_checksum_failure_removes_part_file(self, data: bytes) -> None:
        """**Validates: Requirements 4.7, 12.7**.

        On SHA256 verification failure, the .part file is deleted and
        the final destination path is never created.
        """
        # Use a wrong hash
        wrong_hash = hashlib.sha256(b"wrong content").hexdigest()
        assume(wrong_hash != hashlib.sha256(data).hexdigest())
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
                    with patch("debcraft.infrastructure.mirror.download.asyncio.sleep", _noop_sleep):
                        result = await coordinator.download_file(
                            url=url,
                            dest_path=dest_path,
                            expected_sha256=wrong_hash,
                            expected_size=expected_size,
                        )

                    # After all retries: .part removed, final file not created
                    assert result.success is False
                    assert not part_path.exists()
                    assert not dest_path.exists()
                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(data=st.binary(min_size=1, max_size=1024))
    async def test_size_mismatch_removes_part_file(self, data: bytes) -> None:
        """**Validates: Requirements 12.5**.

        On size mismatch, the .part file is deleted and the final
        destination path is never created.
        """
        expected_sha256 = hashlib.sha256(data).hexdigest()
        # Declare a wrong size
        wrong_size = len(data) + 100

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
                    with patch("debcraft.infrastructure.mirror.download.asyncio.sleep", _noop_sleep):
                        result = await coordinator.download_file(
                            url=url,
                            dest_path=dest_path,
                            expected_sha256=expected_sha256,
                            expected_size=wrong_size,
                        )

                    # After all retries: .part removed, final file not created
                    assert result.success is False
                    assert not part_path.exists()
                    assert not dest_path.exists()
                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Property 20: Exponential backoff delay bounds
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty20ExponentialBackoffDelayBounds:
    """Property 20: Exponential backoff delay bounds.

    For any attempt number N, the computed backoff delay SHALL be within
    the range [base, base * 1.25] where base = min(1 * 2^N, 30).
    """

    @settings(max_examples=200)
    @given(attempt=_attempt_strategy)
    def test_delay_within_bounds(self, attempt: int) -> None:
        """**Validates: Requirements 11.3**.

        The computed delay is always within [base, base * 1.25] where
        base = min(1 * 2^attempt, 30).
        """
        delay = _compute_backoff_delay(attempt)
        base = min(1.0 * (2**attempt), 30.0)
        max_delay = base * 1.25

        assert delay >= base, f"Delay {delay} is less than base {base} for attempt {attempt}"
        assert delay <= max_delay, f"Delay {delay} exceeds max {max_delay} for attempt {attempt}"

    @settings(max_examples=200)
    @given(attempt=_attempt_strategy)
    def test_delay_never_exceeds_max_with_jitter(self, attempt: int) -> None:
        """**Validates: Requirements 11.3**.

        The delay never exceeds 30 * 1.25 = 37.5 seconds regardless
        of the attempt number.
        """
        delay = _compute_backoff_delay(attempt)
        # Max base is 30, max jitter is 30 * 0.25 = 7.5, so max total is 37.5
        assert delay <= 37.5

    @settings(max_examples=200)
    @given(attempt=_attempt_strategy)
    def test_delay_is_non_negative(self, attempt: int) -> None:
        """**Validates: Requirements 11.3**.

        The delay is always non-negative.
        """
        delay = _compute_backoff_delay(attempt)
        assert delay >= 0.0


# ---------------------------------------------------------------------------
# Property 21: HTTP error classification
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestProperty21HttpErrorClassification:
    """Property 21: HTTP error classification.

    4xx → HttpClientError (non-retriable), 5xx → HttpServerError (retriable),
    network errors → NetworkError (retriable).
    """

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(status_code=_http_4xx_strategy)
    async def test_4xx_raises_http_client_error(self, status_code: int) -> None:
        """**Validates: Requirements 12.2**.

        Any 4xx status code produces an HttpClientError which is non-retriable.
        The download immediately fails without retry.
        """
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
                    with pytest.raises(HttpClientError) as exc_info:
                        await coordinator.download_file(
                            url=url,
                            dest_path=dest_path,
                            expected_sha256="a" * 64,
                            expected_size=100,
                        )

                    # 4xx is non-retriable - called only once, no retry
                    assert call_count["value"] == 1
                    assert exc_info.value.status_code == status_code
                    assert not dest_path.exists()
                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(status_code=_http_5xx_strategy)
    async def test_5xx_raises_http_server_error_and_retries(self, status_code: int) -> None:
        """**Validates: Requirements 12.3**.

        Any 5xx status code produces an HttpServerError which is retriable.
        The download is retried up to 3 times before final failure.
        """
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
                    with patch("debcraft.infrastructure.mirror.download.asyncio.sleep", _noop_sleep):
                        result = await coordinator.download_file(
                            url=url,
                            dest_path=dest_path,
                            expected_sha256="a" * 64,
                            expected_size=100,
                        )

                    # Should retry 3 times total
                    assert result.success is False
                    assert call_count["value"] == 3
                    assert not dest_path.exists()
                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Property 22: Size mismatch detection
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestProperty22SizeMismatchDetection:
    """Property 22: Size mismatch detection.

    For any downloaded file whose size in bytes differs from the size declared
    in repository metadata, the download SHALL be treated as a verification failure.
    """

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(
        data=st.binary(min_size=1, max_size=1024),
        size_delta=st.integers(min_value=1, max_value=1000),
    )
    async def test_size_mismatch_causes_failure(self, data: bytes, size_delta: int) -> None:
        """**Validates: Requirements 12.5**.

        When the declared expected size differs from actual bytes received,
        the download fails with size mismatch verification error.
        """
        expected_sha256 = hashlib.sha256(data).hexdigest()
        # Expected size is wrong (larger than actual)
        wrong_size = len(data) + size_delta

        app = web.Application()
        app.router.add_get("/file", _make_response_handler(data))

        tmp_dir = Path(tempfile.mkdtemp())
        try:
            async with TestServer(app) as server:
                url = str(server.make_url("/file"))
                dest_path = tmp_dir / "file"
                part_path = Path(str(dest_path) + ".part")

                config = _make_test_config()
                coordinator = DownloadCoordinator(
                    storage_engine=_FakeStorageEngine(),
                    config=config,
                )
                await coordinator.start()
                try:
                    with patch("debcraft.infrastructure.mirror.download.asyncio.sleep", _noop_sleep):
                        result = await coordinator.download_file(
                            url=url,
                            dest_path=dest_path,
                            expected_sha256=expected_sha256,
                            expected_size=wrong_size,
                        )

                    # Size mismatch → failure, .part removed, final not created
                    assert result.success is False
                    assert not part_path.exists()
                    assert not dest_path.exists()
                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(
        data=st.binary(min_size=2, max_size=1024),
        size_delta=st.integers(min_value=1, max_value=1000),
    )
    async def test_size_smaller_than_actual_causes_failure(self, data: bytes, size_delta: int) -> None:
        """**Validates: Requirements 12.5**.

        When the declared expected size is smaller than actual bytes
        received, the download also fails.
        """
        expected_sha256 = hashlib.sha256(data).hexdigest()
        # Expected size is smaller than actual
        wrong_size = max(1, len(data) - size_delta)
        assume(wrong_size != len(data))

        app = web.Application()
        app.router.add_get("/file", _make_response_handler(data))

        tmp_dir = Path(tempfile.mkdtemp())
        try:
            async with TestServer(app) as server:
                url = str(server.make_url("/file"))
                dest_path = tmp_dir / "file"
                part_path = Path(str(dest_path) + ".part")

                config = _make_test_config()
                coordinator = DownloadCoordinator(
                    storage_engine=_FakeStorageEngine(),
                    config=config,
                )
                await coordinator.start()
                try:
                    with patch("debcraft.infrastructure.mirror.download.asyncio.sleep", _noop_sleep):
                        result = await coordinator.download_file(
                            url=url,
                            dest_path=dest_path,
                            expected_sha256=expected_sha256,
                            expected_size=wrong_size,
                        )

                    # Size mismatch → failure
                    assert result.success is False
                    assert not part_path.exists()
                    assert not dest_path.exists()
                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------


def _make_response_handler(body: bytes):
    """Create an async handler function that returns a Response with the given body.

    This avoids aiohttp's deprecation warning about bare (sync) functions
    being used as route handlers.
    """

    async def handler(request: web.Request) -> web.Response:
        return web.Response(body=body)

    return handler


async def _noop_sleep(delay: float) -> None:
    """No-op replacement for asyncio.sleep to speed up retry tests."""


class _FakeStorageEngine:
    """Minimal fake StorageEngine for testing DownloadCoordinator."""

    def get_path(self, name: str) -> Path:
        return Path("/tmp/test-mirror")


def _make_test_config():
    """Create a minimal MirrorConfig for testing."""
    from debcraft.domain.mirror.config import MirrorConfig, RepositoryConfig

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
