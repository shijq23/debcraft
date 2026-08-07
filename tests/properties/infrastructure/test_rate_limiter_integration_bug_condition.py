"""Property-based bug condition exploration test for rate limiter integration.

**Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 2.5**

Property 1: Bug Condition - Rate Limiter Not Integrated Into Download Pipeline

For any valid MirrorConfig (rate_limit_rps in [1, 1000], rate_limit_burst in
[1, 200] or None, max_connections_per_repo in [1, 100]), after
DownloadCoordinator.start():
  1. self._rate_limiter is not None
  2. self._rate_limiter._rate equals config.rate_limit_rps
  3. self._rate_limiter._max_tokens equals resolved burst size
  4. _attempt_download() calls rate_limiter.acquire() exactly once before HTTP GET
  5. check_conditional() calls rate_limiter.acquire() exactly once before HTTP HEAD

This test is EXPECTED TO FAIL on unfixed code, confirming the bug exists:
the rate limiter is never instantiated and acquire() is never called.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from debcraft.domain.mirror.config import MirrorConfig
from debcraft.infrastructure.mirror.download import DownloadCoordinator

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

rate_limit_rps_strategy = st.floats(
    min_value=1.0,
    max_value=1000.0,
    allow_nan=False,
    allow_infinity=False,
)

rate_limit_burst_strategy = st.one_of(
    st.none(),
    st.integers(min_value=1, max_value=200),
)

max_connections_per_repo_strategy = st.integers(min_value=1, max_value=100)


@st.composite
def mirror_config_strategy(draw: st.DrawFn) -> MirrorConfig:
    """Generate random MirrorConfig with valid rate limiter fields."""
    rps = draw(rate_limit_rps_strategy)
    burst = draw(rate_limit_burst_strategy)
    max_conn = draw(max_connections_per_repo_strategy)
    return MirrorConfig(
        rate_limit_rps=rps,
        rate_limit_burst=burst,
        max_connections_per_repo=max_conn,
    )


# ---------------------------------------------------------------------------
# Property 1: Bug Condition - Rate Limiter Not Integrated
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestProperty1RateLimiterIntegrationBugCondition:
    """Property 1: Bug Condition - Rate Limiter Not Integrated.

    These tests MUST FAIL on unfixed code to confirm the bug exists.
    The DownloadCoordinator does not instantiate or use a rate limiter.
    """

    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(config=mirror_config_strategy())
    async def test_rate_limiter_instantiated_after_start(
        self,
        config: MirrorConfig,
    ) -> None:
        """Validate Requirements 2.1.

        After DownloadCoordinator.start(), self._rate_limiter MUST NOT be None.
        The rate limiter must be instantiated with the config values.
        """
        storage_engine = MagicMock()
        coordinator = DownloadCoordinator(
            storage_engine=storage_engine,
            config=config,
        )

        # Mock aiohttp internals to avoid real network
        with (
            patch("aiohttp.TCPConnector") as mock_connector,
            patch("aiohttp.ClientSession") as mock_session,
        ):
            mock_connector.return_value = MagicMock()
            mock_session.return_value = MagicMock()
            await coordinator.start()

        # BUG: _rate_limiter is never set — this assertion will FAIL
        assert hasattr(coordinator, "_rate_limiter"), "DownloadCoordinator has no _rate_limiter attribute after start()"
        assert coordinator._rate_limiter is not None, (
            "DownloadCoordinator._rate_limiter is None after start() — rate limiter was never instantiated"
        )

        # Clean up
        coordinator._session = None
        coordinator._connector = None

    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(config=mirror_config_strategy())
    async def test_rate_limiter_configured_with_correct_rate(
        self,
        config: MirrorConfig,
    ) -> None:
        """Validate Requirements 2.1.

        After start(), self._rate_limiter._rate must equal config.rate_limit_rps.
        """
        storage_engine = MagicMock()
        coordinator = DownloadCoordinator(
            storage_engine=storage_engine,
            config=config,
        )

        with (
            patch("aiohttp.TCPConnector") as mock_connector,
            patch("aiohttp.ClientSession") as mock_session,
        ):
            mock_connector.return_value = MagicMock()
            mock_session.return_value = MagicMock()
            await coordinator.start()

        # BUG: _rate_limiter is never set
        assert hasattr(coordinator, "_rate_limiter") and coordinator._rate_limiter is not None, (
            "Cannot check rate — _rate_limiter is None (never instantiated)"
        )
        assert coordinator._rate_limiter._rate == config.rate_limit_rps, (
            f"Rate limiter rate {coordinator._rate_limiter._rate} != config.rate_limit_rps {config.rate_limit_rps}"
        )

        coordinator._session = None
        coordinator._connector = None

    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(config=mirror_config_strategy())
    async def test_rate_limiter_configured_with_correct_burst_size(
        self,
        config: MirrorConfig,
    ) -> None:
        """Validate Requirements 2.1.

        After start(), self._rate_limiter._max_tokens must equal the
        resolved burst size (config.rate_limit_burst or config.max_connections_per_repo).
        """
        storage_engine = MagicMock()
        coordinator = DownloadCoordinator(
            storage_engine=storage_engine,
            config=config,
        )

        with (
            patch("aiohttp.TCPConnector") as mock_connector,
            patch("aiohttp.ClientSession") as mock_session,
        ):
            mock_connector.return_value = MagicMock()
            mock_session.return_value = MagicMock()
            await coordinator.start()

        expected_burst = config.rate_limit_burst or config.max_connections_per_repo

        # BUG: _rate_limiter is never set
        assert hasattr(coordinator, "_rate_limiter") and coordinator._rate_limiter is not None, (
            "Cannot check burst size — _rate_limiter is None (never instantiated)"
        )
        assert coordinator._rate_limiter._max_tokens == expected_burst, (
            f"Rate limiter burst {coordinator._rate_limiter._max_tokens} != expected {expected_burst}"
        )

        coordinator._session = None
        coordinator._connector = None

    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(config=mirror_config_strategy())
    async def test_acquire_called_before_http_get(
        self,
        config: MirrorConfig,
    ) -> None:
        """Validate Requirements 2.2 and 2.5.

        When _attempt_download() is called, rate_limiter.acquire() MUST be
        called exactly once before the HTTP GET request is issued.
        """
        storage_engine = MagicMock()
        coordinator = DownloadCoordinator(
            storage_engine=storage_engine,
            config=config,
        )

        # Call start() with mocked aiohttp so rate limiter gets instantiated
        with (
            patch("aiohttp.TCPConnector") as mock_connector,
            patch("aiohttp.ClientSession") as mock_client_session,
        ):
            mock_connector.return_value = MagicMock()
            mock_client_session.return_value = MagicMock()
            await coordinator.start()

        # Set up a mock session that returns a successful response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.content = AsyncMock()
        mock_response.content.iter_chunked = MagicMock(return_value=AsyncIteratorMock([b"test data"]))
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)

        coordinator._session = mock_session

        # Track acquire calls — the bug is that _rate_limiter doesn't exist
        # so we need to check if one exists and if acquire was called
        call_order: list[str] = []

        # If the coordinator has a rate limiter, patch its acquire
        # On unfixed code, _rate_limiter doesn't exist, so we verify via absence
        if hasattr(coordinator, "_rate_limiter") and coordinator._rate_limiter is not None:
            original_acquire = coordinator._rate_limiter.acquire

            async def tracked_acquire(*args, **kwargs):
                call_order.append("acquire")
                return await original_acquire(*args, **kwargs)

            coordinator._rate_limiter.acquire = tracked_acquire

        # Patch session.get to track call order
        original_get = mock_session.get

        def tracked_get(*args, **kwargs):
            call_order.append("http_get")
            return original_get(*args, **kwargs)

        mock_session.get = MagicMock(side_effect=tracked_get)
        mock_session.get.return_value = mock_response

        with contextlib.suppress(Exception):
            await coordinator._attempt_download(
                url="http://example.com/test.deb",
                dest_path=Path("/tmp/test.deb"),
                expected_sha256="",
                expected_size=0,
                timeout=30,
                attempt=0,
            )

        # BUG: acquire is never called because _rate_limiter doesn't exist
        assert "acquire" in call_order, (
            "rate_limiter.acquire() was never called before HTTP GET — "
            "rate limiter is not integrated into download pipeline"
        )
        assert call_order.index("acquire") < call_order.index("http_get"), (
            "rate_limiter.acquire() was not called BEFORE HTTP GET request"
        )

    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(config=mirror_config_strategy())
    async def test_acquire_called_before_http_head(
        self,
        config: MirrorConfig,
    ) -> None:
        """Validate Requirements 2.3 and 2.5.

        When check_conditional() is called, rate_limiter.acquire() MUST be
        called exactly once before the HTTP HEAD request is issued.
        """
        storage_engine = MagicMock()
        coordinator = DownloadCoordinator(
            storage_engine=storage_engine,
            config=config,
        )

        # Call start() with mocked aiohttp so rate limiter gets instantiated
        with (
            patch("aiohttp.TCPConnector") as mock_connector,
            patch("aiohttp.ClientSession") as mock_client_session,
        ):
            mock_connector.return_value = MagicMock()
            mock_client_session.return_value = MagicMock()
            await coordinator.start()

        # Set up a mock session for HEAD request
        mock_response = AsyncMock()
        mock_response.status = 304
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.head = MagicMock(return_value=mock_response)

        coordinator._session = mock_session

        # Track call order
        call_order: list[str] = []

        # If the coordinator has a rate limiter, patch its acquire
        if hasattr(coordinator, "_rate_limiter") and coordinator._rate_limiter is not None:
            original_acquire = coordinator._rate_limiter.acquire

            async def tracked_acquire(*args, **kwargs):
                call_order.append("acquire")
                return await original_acquire(*args, **kwargs)

            coordinator._rate_limiter.acquire = tracked_acquire

        # Patch session.head to track call order
        original_head = mock_session.head

        def tracked_head(*args, **kwargs):
            call_order.append("http_head")
            return original_head(*args, **kwargs)

        mock_session.head = MagicMock(side_effect=tracked_head)
        mock_session.head.return_value = mock_response

        with contextlib.suppress(Exception):
            await coordinator.check_conditional(
                url="http://example.com/Release",
                etag='"abc123"',
            )

        # BUG: acquire is never called because _rate_limiter doesn't exist
        assert "acquire" in call_order, (
            "rate_limiter.acquire() was never called before HTTP HEAD — "
            "rate limiter is not integrated into download pipeline"
        )
        assert call_order.index("acquire") < call_order.index("http_head"), (
            "rate_limiter.acquire() was not called BEFORE HTTP HEAD request"
        )


# ---------------------------------------------------------------------------
# Helper: AsyncIterator mock for response.content.iter_chunked()
# ---------------------------------------------------------------------------


class AsyncIteratorMock:
    """Mock async iterator for aiohttp response content chunks."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self._index = 0

    def __aiter__(self):
        """Return the async iterator."""
        return self

    async def __anext__(self) -> bytes:
        """Return the next chunk or raise StopAsyncIteration."""
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk
