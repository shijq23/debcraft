"""Property-based tests for .part file cleanup on HTTP 429 retry.

**Validates: Requirements 3.6**

Property 9: 429 retry cleans up .part file before retrying.
For any download that receives an HTTP 429 response, the .part file created
during that failed attempt SHALL be deleted before the backoff wait begins.
After all retries are exhausted without success, no .part file SHALL remain
on disk.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from debcraft.domain.mirror.config import MirrorConfig
from debcraft.infrastructure.mirror.download import DownloadCoordinator
from debcraft.infrastructure.mirror.rate_limiter import TokenBucketRateLimiter

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Random URL paths for variation
url_path_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Nd"), whitelist_characters="/-_"),
    min_size=1,
    max_size=50,
).map(lambda p: f"http://cdn.example.com/{p.lstrip('/')}")

# Number of 429 responses before exhaustion (1 to 3 means all attempts fail)
num_403_attempts_strategy = st.integers(min_value=1, max_value=3)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(status: int) -> MagicMock:
    """Create a mock aiohttp response with the given status code."""
    response = MagicMock()
    response.status = status
    response.headers = {}
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


def _create_coordinator_with_mocked_session(
    responses: list[MagicMock],
) -> tuple[DownloadCoordinator, MagicMock]:
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
# Property 9: 429 retry cleans up .part file before retrying
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestProperty9PartFileCleanupOn429:
    """Property 9: 429 retry cleans up .part file before retrying.

    For any download that receives an HTTP 429 response, the .part file created
    during that failed attempt SHALL be deleted before the backoff wait begins.
    After all retries are exhausted without success, no .part file SHALL remain
    on disk.

    **Validates: Requirements 3.6**
    """

    @settings(
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(url=url_path_strategy)
    async def test_part_file_deleted_before_backoff_on_429(self, url: str, tmp_path: Path) -> None:
        """**Validates: Requirements 3.6**.

        Verify .part file is deleted before the backoff wait starts on 429.
        We track the state of the .part file at the point asyncio.sleep is called.
        If .part exists when sleep is called, it means cleanup didn't happen first.
        """
        responses = [_make_mock_response(429) for _ in range(3)]
        coordinator, _mock_get = _create_coordinator_with_mocked_session(responses)

        dest_path = tmp_path / "test_file.deb"
        part_path = Path(str(dest_path) + ".part")

        # Track whether .part file exists when asyncio.sleep is called
        part_existed_during_sleep: list[bool] = []

        async def track_sleep(delay: float) -> None:
            """Record whether .part file exists at the moment sleep is called."""
            part_existed_during_sleep.append(part_path.exists())

        with patch("asyncio.sleep", side_effect=track_sleep):
            result = await coordinator.download_file(
                url=url,
                dest_path=dest_path,
                expected_sha256="a" * 64,
                expected_size=1024,
            )

        # Verify result is failure (all 3 attempts returned 429)
        assert result.success is False
        assert result.status_code == 429

        # Verify .part file did NOT exist when sleep was called (cleanup before backoff)
        assert len(part_existed_during_sleep) == 2, (
            f"Expected 2 backoff sleeps for 3 attempts, got {len(part_existed_during_sleep)}"
        )
        for i, existed in enumerate(part_existed_during_sleep):
            assert not existed, (
                f".part file existed when backoff sleep was called at retry {i + 1}; "
                f"cleanup should happen BEFORE the backoff wait"
            )

    @settings(
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(url=url_path_strategy)
    async def test_no_part_file_remains_after_all_retries_exhausted(self, url: str, tmp_path: Path) -> None:
        """**Validates: Requirements 3.6**.

        After all 429 retries are exhausted without success, no .part file
        SHALL remain on disk.
        """
        responses = [_make_mock_response(429) for _ in range(3)]
        coordinator, _mock_get = _create_coordinator_with_mocked_session(responses)

        dest_path = tmp_path / "test_file.deb"
        part_path = Path(str(dest_path) + ".part")

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await coordinator.download_file(
                url=url,
                dest_path=dest_path,
                expected_sha256="a" * 64,
                expected_size=1024,
            )

        # Verify failure
        assert result.success is False
        assert result.status_code == 429

        # Verify no .part file remains on disk
        assert not part_path.exists(), f".part file still exists at {part_path} after all retries exhausted"
