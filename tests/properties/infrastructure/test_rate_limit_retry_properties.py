"""Property-based tests for HTTP 429 retry behavior in DownloadCoordinator.

**Validates: Requirements 3.1, 3.3, 3.4, 3.5, 4.1, 4.3**

Property 6: HTTP 429 is retriable with 3 total attempts.
For any download URL that consistently returns HTTP 429, the DownloadCoordinator
SHALL make exactly 3 HTTP requests (1 initial + 2 retries), proving 429 is
classified as retriable. In contrast, for any non-429 4xx status code, exactly
1 request SHALL be made.

Property 7: Exhausted 429 retries produce correct failure result.
For any download URL that returns HTTP 429 on all 3 attempts, the returned
DownloadResult SHALL have success=False, status_code=429, a non-empty error
message describing rate-limit failure, and retry_count equal to 2.

Property 8: Successful 429 retry reports correct attempt number.
For any download URL where the server returns 429 for the first N attempts
(N in {1, 2}) and then succeeds, the returned DownloadResult SHALL have
success=True and retry_count equal to N.

Property 11: Rate limiter acquire is called before every HTTP request.
For any batch of download tasks with a total of M HTTP requests (including
retries), the rate limiter's acquire method SHALL be called exactly M times,
ensuring all requests — both initial attempts and retries — are throttled.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from debcraft.domain.mirror.config import MirrorConfig
from debcraft.infrastructure.mirror.download import DownloadCoordinator
from debcraft.infrastructure.mirror.errors import HttpClientError
from debcraft.infrastructure.mirror.rate_limiter import TokenBucketRateLimiter

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Non-429 4xx status codes (400-499 excluding 429)
non_403_4xx_strategy = st.sampled_from([s for s in range(400, 500) if s != 429])

# Random URL paths for variation
url_path_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Nd"), whitelist_characters="/-_"),
    min_size=1,
    max_size=50,
).map(lambda p: f"http://cdn.example.com/{p.lstrip('/')}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(status: int) -> MagicMock:
    """Create a mock aiohttp response with the given status code."""
    response = MagicMock()
    response.status = status
    response.headers = {}
    # The response context manager
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


def _create_coordinator_with_mocked_session(
    responses: list[MagicMock],
) -> tuple[DownloadCoordinator, AsyncMock]:
    """Create a DownloadCoordinator with a mocked aiohttp session.

    Returns the coordinator and the mock session's get method for assertion.
    """
    config = MirrorConfig(
        repositories=[],
        download_timeout=30,
        max_connections_per_repo=5,
        max_total_connections=10,
        rate_limit_rps=100.0,
        rate_limit_burst=10,
    )

    storage_engine = MagicMock()
    rate_limiter = TokenBucketRateLimiter(rate=100.0, burst_size=10)

    coordinator = DownloadCoordinator(
        storage_engine=storage_engine,
        config=config,
        rate_limiter=rate_limiter,
    )

    # Mock the session
    mock_session = MagicMock()
    mock_get = MagicMock(side_effect=responses)
    mock_session.get = mock_get
    coordinator._session = mock_session

    return coordinator, mock_get


# ---------------------------------------------------------------------------
# Property 6: HTTP 429 is retriable with 3 total attempts
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestProperty6Http429RetriableWith3Attempts:
    """Property 6: HTTP 429 is retriable with 3 total attempts.

    For any download URL that consistently returns HTTP 429, the
    DownloadCoordinator SHALL make exactly 3 HTTP requests (1 initial +
    2 retries), proving 429 is classified as retriable. In contrast, for
    any non-429 4xx status code, exactly 1 request SHALL be made.
    """

    @settings(
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(url=url_path_strategy)
    async def test_429_triggers_exactly_3_requests(self, url: str, tmp_path: Path) -> None:
        """Validate Requirements 3.1, 3.3.

        HTTP 429 is classified as retriable and triggers exactly 3 total
        HTTP requests (1 initial + 2 retries).
        """
        # Create 3 mock responses all returning 429
        responses = [_make_mock_response(429) for _ in range(3)]
        coordinator, mock_get = _create_coordinator_with_mocked_session(responses)

        dest_path = tmp_path / "test_file.deb"

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await coordinator.download_file(
                url=url,
                dest_path=dest_path,
                expected_sha256="a" * 64,
                expected_size=1024,
            )

        # Verify exactly 3 requests were made
        assert mock_get.call_count == 3, f"Expected exactly 3 HTTP requests for 429, got {mock_get.call_count}"
        # Verify the result indicates failure
        assert result.success is False
        assert result.status_code == 429

    @settings(
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(status_code=non_403_4xx_strategy, url=url_path_strategy)
    async def test_non_429_4xx_triggers_exactly_1_request(self, status_code: int, url: str, tmp_path: Path) -> None:
        """Validate Requirements 3.1, 3.3.

        Non-429 4xx status codes are NOT retriable and trigger exactly 1
        HTTP request (no retries).
        """
        # Create a single mock response with the non-429 4xx code
        responses = [_make_mock_response(status_code)]
        coordinator, mock_get = _create_coordinator_with_mocked_session(responses)

        dest_path = tmp_path / "test_file.deb"

        with pytest.raises(HttpClientError) as exc_info:
            await coordinator.download_file(
                url=url,
                dest_path=dest_path,
                expected_sha256="a" * 64,
                expected_size=1024,
            )

        # Verify exactly 1 request was made (no retries)
        assert mock_get.call_count == 1, f"Expected exactly 1 HTTP request for {status_code}, got {mock_get.call_count}"
        # Verify the error has the correct status code
        assert exc_info.value.status_code == status_code


# ---------------------------------------------------------------------------
# Property 7: Exhausted 429 retries produce correct failure result
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestProperty7Exhausted429RetriesProduceCorrectFailureResult:
    """Property 7: Exhausted 429 retries produce correct failure result.

    For any download URL that returns HTTP 429 on all 3 attempts, the returned
    DownloadResult SHALL have success=False, status_code=429, a non-empty error
    message describing rate-limit failure, and retry_count equal to the number
    of retries performed (2).

    **Validates: Requirements 3.4**
    """

    @settings(
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(url=url_path_strategy)
    async def test_exhausted_429_retries_produce_correct_failure_result(
        self,
        url: str,
        tmp_path: Path,
    ) -> None:
        """**Validates: Requirements 3.4**.

        When all 3 attempts for a 429 response are exhausted without success,
        the returned DownloadResult SHALL have:
        - success=False
        - status_code=429
        - non-empty error message
        - retry_count=2 (2 retries were performed)
        """
        # Create 3 mock responses all returning 429
        responses = [_make_mock_response(429) for _ in range(3)]
        coordinator, _mock_get = _create_coordinator_with_mocked_session(responses)

        dest_path = tmp_path / "test_file.deb"

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await coordinator.download_file(
                url=url,
                dest_path=dest_path,
                expected_sha256="a" * 64,
                expected_size=1024,
            )

        # success=False
        assert result.success is False, (
            f"Expected success=False after exhausted 429 retries, got success={result.success}"
        )

        # status_code=429
        assert result.status_code == 429, f"Expected status_code=429, got status_code={result.status_code}"

        # non-empty error message
        assert result.error is not None and len(result.error) > 0, (
            f"Expected non-empty error message, got error={result.error!r}"
        )

        # retry_count=2 (2 retries performed, 3 total attempts)
        assert result.retry_count == 2, f"Expected retry_count=2 (2 retries), got retry_count={result.retry_count}"


# ---------------------------------------------------------------------------
# Property 8: Successful 429 retry reports correct attempt number
# ---------------------------------------------------------------------------


def _make_mock_success_response(content: bytes) -> MagicMock:
    """Create a mock aiohttp response for a successful 200 download.

    The response streams the provided content back as a single chunk,
    simulating a successful HTTP response with iter_chunked support.
    """
    response = MagicMock()
    response.status = 200
    response.headers = {}
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)

    # Mock the content.iter_chunked() async iterator
    async def _iter_chunked(chunk_size: int):
        yield content

    response.content = MagicMock()
    response.content.iter_chunked = _iter_chunked

    return response


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestProperty8Successful429RetryReportsCorrectAttemptNumber:
    """Property 8: Successful 429 retry reports correct attempt number.

    For any download URL where the server returns 429 for the first N attempts
    (N in {1, 2}) and then succeeds, the returned DownloadResult SHALL have
    success=True and retry_count equal to N (the zero-indexed attempt on which
    success occurred).

    **Validates: Requirements 3.5**
    """

    @settings(
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        num_403s=st.integers(min_value=1, max_value=2),
        url=url_path_strategy,
    )
    async def test_successful_429_retry_reports_correct_attempt_number(
        self,
        num_403s: int,
        url: str,
        tmp_path: Path,
    ) -> None:
        """**Validates: Requirements 3.5**.

        When a 429 retry succeeds on a subsequent attempt, the DownloadResult
        SHALL have success=True and retry_count=N where N is the zero-indexed
        attempt number on which success occurred.
        """
        import hashlib as _hashlib

        # Create file content with known SHA256
        content = b"test file content for property 8"
        expected_sha256 = _hashlib.sha256(content).hexdigest()
        expected_size = len(content)

        # Build responses: N 429s followed by one success
        responses: list[MagicMock] = [_make_mock_response(429) for _ in range(num_403s)]
        responses.append(_make_mock_success_response(content))

        coordinator, _mock_get = _create_coordinator_with_mocked_session(responses)

        dest_path = tmp_path / "test_file.deb"

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await coordinator.download_file(
                url=url,
                dest_path=dest_path,
                expected_sha256=expected_sha256,
                expected_size=expected_size,
            )

        # success=True
        assert result.success is True, (
            f"Expected success=True after {num_403s} failed 429(s) then success, "
            f"got success={result.success}, error={result.error}"
        )

        # retry_count equals the attempt number on which success occurred (N)
        assert result.retry_count == num_403s, (
            f"Expected retry_count={num_403s} (success on attempt {num_403s}), got retry_count={result.retry_count}"
        )


# ---------------------------------------------------------------------------
# Property 11: Rate limiter acquire is called before every HTTP request
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestProperty11RateLimiterAcquireCalledBeforeEveryRequest:
    """Property 11: Rate limiter acquire is called before every HTTP request.

    For any batch of download tasks with a total of M HTTP requests (including
    retries), the rate limiter's acquire method SHALL be called exactly M times,
    ensuring all requests — both initial attempts and retries — are throttled.

    **Validates: Requirements 4.1, 4.3**
    """

    @settings(
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        num_403s=st.integers(min_value=0, max_value=2),
        url=url_path_strategy,
    )
    async def test_acquire_called_exactly_m_times_for_m_requests(
        self,
        num_403s: int,
        url: str,
        tmp_path: Path,
    ) -> None:
        """**Validates: Requirements 4.1, 4.3**.

        Verify that acquire is called exactly M times for M total HTTP
        requests (including retries). When num_403s=0, there is 1 request
        and 1 acquire call. When num_403s > 0, there are num_403s + 1 total
        requests (the 403 retries plus the final success), and acquire must
        be called that many times.
        """
        import hashlib as _hashlib

        # Create file content with known SHA256
        content = b"test file content for property 11"
        expected_sha256 = _hashlib.sha256(content).hexdigest()
        expected_size = len(content)

        # Build responses: num_403s 429 responses followed by one success
        responses: list[MagicMock] = [_make_mock_response(429) for _ in range(num_403s)]
        responses.append(_make_mock_success_response(content))

        expected_total_requests = num_403s + 1

        config = MirrorConfig(
            repositories=[],
            download_timeout=30,
            max_connections_per_repo=5,
            max_total_connections=10,
            rate_limit_rps=100.0,
            rate_limit_burst=10,
        )

        storage_engine = MagicMock()

        # Use a mock rate limiter to track acquire calls
        mock_rate_limiter = AsyncMock()
        mock_rate_limiter.acquire = AsyncMock()

        coordinator = DownloadCoordinator(
            storage_engine=storage_engine,
            config=config,
            rate_limiter=mock_rate_limiter,
        )

        # Mock the session
        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=responses)
        coordinator._session = mock_session

        dest_path = tmp_path / "test_file.deb"

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await coordinator.download_file(
                url=url,
                dest_path=dest_path,
                expected_sha256=expected_sha256,
                expected_size=expected_size,
            )

        # Verify acquire was called exactly M times for M total HTTP requests
        assert mock_rate_limiter.acquire.call_count == expected_total_requests, (
            f"Expected acquire to be called {expected_total_requests} times "
            f"for {expected_total_requests} HTTP requests (including "
            f"{num_403s} retries), but got {mock_rate_limiter.acquire.call_count}"
        )
        assert result.success is True

    @settings(
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(url=url_path_strategy)
    async def test_acquire_called_for_all_failed_attempts(
        self,
        url: str,
        tmp_path: Path,
    ) -> None:
        """**Validates: Requirements 4.1, 4.3**.

        When all 3 attempts fail with 429, acquire SHALL be called exactly
        3 times (once per HTTP request including retries).
        """
        # Create 3 mock responses all returning 429
        responses = [_make_mock_response(429) for _ in range(3)]
        expected_total_requests = 3

        config = MirrorConfig(
            repositories=[],
            download_timeout=30,
            max_connections_per_repo=5,
            max_total_connections=10,
            rate_limit_rps=100.0,
            rate_limit_burst=10,
        )

        storage_engine = MagicMock()

        # Use a mock rate limiter to track acquire calls
        mock_rate_limiter = AsyncMock()
        mock_rate_limiter.acquire = AsyncMock()

        coordinator = DownloadCoordinator(
            storage_engine=storage_engine,
            config=config,
            rate_limiter=mock_rate_limiter,
        )

        # Mock the session
        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=responses)
        coordinator._session = mock_session

        dest_path = tmp_path / "test_file.deb"

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await coordinator.download_file(
                url=url,
                dest_path=dest_path,
                expected_sha256="a" * 64,
                expected_size=1024,
            )

        # Verify acquire was called exactly 3 times for 3 total HTTP requests
        assert mock_rate_limiter.acquire.call_count == expected_total_requests, (
            f"Expected acquire to be called {expected_total_requests} times "
            f"for {expected_total_requests} HTTP requests (all 429 failures), "
            f"but got {mock_rate_limiter.acquire.call_count}"
        )
        assert result.success is False
        assert result.status_code == 429
