"""Property-based tests for token bucket rate limiter.

**Validates: Requirements 1.2, 1.3, 1.4, 1.5, 1.6**

Property 1: Token bucket replenishment computes correctly.
For any valid rate (1–1000), burst_size (1–200), initial token count, and
non-negative elapsed time, the number of tokens after replenishment SHALL equal
`min(current_tokens + rate * elapsed_time, burst_size)`. Additionally, upon
initialization, the token count SHALL equal `burst_size`.

Property 2: Token bucket acquire blocks when empty and times out.
For any token bucket with zero available tokens and a configured rate,
calling acquire SHALL block until at least one token is replenished. If
the replenishment time exceeds the 60-second timeout, acquire SHALL raise
RateLimitTimeoutError. When tokens are available (count >= 1), acquire
SHALL return in under 1 millisecond.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from debcraft.infrastructure.mirror.errors import RateLimitTimeoutError
from debcraft.infrastructure.mirror.rate_limiter import TokenBucketRateLimiter

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Rate config values for replenishment tests
rate_strategy = st.floats(
    min_value=1.0,
    max_value=1000.0,
    allow_nan=False,
    allow_infinity=False,
)
burst_strategy = st.integers(min_value=1, max_value=200)
elapsed_time_strategy = st.floats(
    min_value=0.0,
    max_value=120.0,
    allow_nan=False,
    allow_infinity=False,
)
# Initial tokens expressed as a fraction of burst_size
token_fraction_strategy = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)

# ---------------------------------------------------------------------------
# Property 1: Token bucket replenishment computes correctly
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty1TokenBucketReplenishment:
    """Property 1: Token bucket replenishment computes correctly.

    For any valid rate (1–1000), burst_size (1–200), initial token count, and
    non-negative elapsed time, the number of tokens after replenishment SHALL
    equal `min(current_tokens + rate * elapsed_time, burst_size)`. Additionally,
    upon initialization, the token count SHALL equal `burst_size`.
    """

    @settings(max_examples=200)
    @given(rate=rate_strategy, burst_size=burst_strategy)
    def test_initialization_sets_tokens_to_burst_size(self, rate: float, burst_size: int) -> None:
        """Validate Requirements 1.6.

        Upon initialization, the token count SHALL equal burst_size.
        """
        limiter = TokenBucketRateLimiter(rate=rate, burst_size=burst_size)
        assert limiter._tokens == float(burst_size)
        assert limiter._max_tokens == burst_size

    @settings(max_examples=200)
    @given(
        rate=rate_strategy,
        burst_size=burst_strategy,
        token_fraction=token_fraction_strategy,
        elapsed=elapsed_time_strategy,
    )
    def test_replenishment_computes_correctly(
        self,
        rate: float,
        burst_size: int,
        token_fraction: float,
        elapsed: float,
    ) -> None:
        """Validate Requirements 1.5 and 1.6.

        For any rate, burst_size, initial tokens, and elapsed time,
        replenished count equals min(current + rate * elapsed, burst_size).
        """
        limiter = TokenBucketRateLimiter(rate=rate, burst_size=burst_size)

        # Set initial tokens to a fraction of burst_size
        initial_tokens = token_fraction * burst_size
        limiter._tokens = initial_tokens

        # Use a fixed base_time to avoid floating-point cancellation errors
        # that occur when subtracting two large monotonic clock values
        base_time = 1000.0
        limiter._last_replenish = base_time

        with patch(
            "debcraft.infrastructure.mirror.rate_limiter.time.monotonic",
            return_value=base_time + elapsed,
        ):
            limiter._replenish()

        expected = min(initial_tokens + rate * elapsed, float(burst_size))
        # Use relative tolerance for floating point comparison since
        # IEEE 754 arithmetic can produce tiny rounding differences
        tolerance = max(1e-7, abs(expected) * 1e-7)
        assert abs(limiter._tokens - expected) < tolerance, (
            f"Expected {expected}, got {limiter._tokens} "
            f"(rate={rate}, burst={burst_size}, initial={initial_tokens}, elapsed={elapsed})"
        )

    @settings(max_examples=200)
    @given(
        rate=rate_strategy,
        burst_size=burst_strategy,
        elapsed=elapsed_time_strategy,
    )
    def test_replenishment_never_exceeds_burst_size(
        self,
        rate: float,
        burst_size: int,
        elapsed: float,
    ) -> None:
        """Validate Requirements 1.5.

        Token count after replenishment never exceeds burst_size, regardless
        of elapsed time or rate.
        """
        limiter = TokenBucketRateLimiter(rate=rate, burst_size=burst_size)

        # Use a fixed base_time to avoid floating-point cancellation errors
        base_time = 1000.0
        limiter._last_replenish = base_time

        with patch(
            "debcraft.infrastructure.mirror.rate_limiter.time.monotonic",
            return_value=base_time + elapsed,
        ):
            limiter._replenish()

        assert limiter._tokens <= float(burst_size), (
            f"Tokens {limiter._tokens} exceeded burst_size {burst_size} (rate={rate}, elapsed={elapsed})"
        )


# ---------------------------------------------------------------------------
# Strategies for Property 2 (acquire blocking/timeout)
# ---------------------------------------------------------------------------

# Rates that allow relatively fast replenishment for blocking tests
# Using rates high enough that one token replenishes quickly (< 1s)
_fast_rate_strategy = st.floats(
    min_value=10.0,
    max_value=1000.0,
    allow_nan=False,
    allow_infinity=False,
)

# Burst sizes for testing
_burst_strategy = st.integers(min_value=1, max_value=50)

# Rates so slow that timeout will be triggered with a short timeout
# At 0.001 tokens/sec, one token takes 1000s to replenish
_slow_rate_strategy = st.floats(
    min_value=0.001,
    max_value=0.01,
    allow_nan=False,
    allow_infinity=False,
)

# Short timeouts for timeout tests (to avoid slow tests)
_short_timeout_strategy = st.floats(
    min_value=0.05,
    max_value=0.15,
    allow_nan=False,
    allow_infinity=False,
)

# Rates and burst sizes for immediate-return tests
_available_rate_strategy = st.floats(
    min_value=1.0,
    max_value=1000.0,
    allow_nan=False,
    allow_infinity=False,
)
_available_burst_strategy = st.integers(min_value=1, max_value=200)


# ---------------------------------------------------------------------------
# Property 2: Token bucket acquire blocks when empty and times out
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestProperty2AcquireBlockingAndTimeout:
    """Property 2: Token bucket acquire blocks when empty and times out.

    For any token bucket with zero available tokens and a configured rate,
    calling acquire SHALL block until at least one token is replenished.
    If the replenishment time exceeds the timeout, acquire SHALL raise
    RateLimitTimeoutError. When tokens are available (count >= 1),
    acquire SHALL return in under 1 millisecond.
    """

    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(rate=_fast_rate_strategy, burst_size=_burst_strategy)
    async def test_acquire_blocks_when_empty_returns_after_replenish(self, rate: float, burst_size: int) -> None:
        """Validate Requirements 1.2.

        When tokens are exhausted, acquire blocks until replenishment
        makes a token available, then returns successfully.
        """
        limiter = TokenBucketRateLimiter(rate=rate, burst_size=burst_size)

        # Exhaust all tokens
        for _ in range(burst_size):
            await limiter.acquire(timeout=5.0)

        # Now bucket is empty. Next acquire should block briefly then
        # return after replenishment (at rate tokens/sec, one token
        # appears after 1/rate seconds)
        start = time.monotonic()
        await limiter.acquire(timeout=5.0)
        elapsed = time.monotonic() - start

        # It should have blocked for approximately 1/rate seconds
        # Allow generous tolerance for scheduling jitter
        expected_wait = 1.0 / rate
        assert elapsed >= expected_wait * 0.5, (
            f"Acquire returned too quickly: {elapsed:.4f}s, expected at least ~{expected_wait:.4f}s"
        )

    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(rate=_slow_rate_strategy, timeout=_short_timeout_strategy)
    async def test_acquire_raises_timeout_when_wait_exceeds_timeout(self, rate: float, timeout: float) -> None:
        """Validate Requirements 1.3.

        When the replenishment time exceeds the timeout, acquire raises
        RateLimitTimeoutError.
        """
        # Use burst_size=1 so we only need to exhaust 1 token
        limiter = TokenBucketRateLimiter(rate=rate, burst_size=1)

        # Exhaust the single token
        await limiter.acquire(timeout=5.0)

        # At rate=0.001-0.01, one token takes 100-1000s to replenish.
        # With timeout=0.05-0.15s, this should always time out.
        with pytest.raises(RateLimitTimeoutError) as exc_info:
            await limiter.acquire(timeout=timeout)

        assert exc_info.value.timeout == timeout

    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(rate=_available_rate_strategy, burst_size=_available_burst_strategy)
    async def test_acquire_returns_within_1ms_when_tokens_available(self, rate: float, burst_size: int) -> None:
        """Validate Requirements 1.4.

        When tokens are available (count >= 1), acquire returns in
        under 1 millisecond.
        """
        limiter = TokenBucketRateLimiter(rate=rate, burst_size=burst_size)

        # Tokens are available (initialized to burst_size)
        start = time.monotonic()
        await limiter.acquire(timeout=5.0)
        elapsed = time.monotonic() - start

        # Should return in under 1 millisecond
        assert elapsed < 0.001, f"Acquire took {elapsed * 1000:.3f}ms with tokens available, expected < 1ms"


# ---------------------------------------------------------------------------
# Strategies for Property 3
# ---------------------------------------------------------------------------

# Use relatively high rates (50-1000 rps) and small burst sizes to keep
# tests fast while still verifying the throughput bound.
_p3_rate_strategy = st.floats(
    min_value=50.0,
    max_value=1000.0,
    allow_nan=False,
    allow_infinity=False,
)
_p3_burst_strategy = st.integers(min_value=1, max_value=10)

# Number of extra requests beyond burst that must wait for tokens
_p3_extra_requests_strategy = st.integers(min_value=1, max_value=20)


# ---------------------------------------------------------------------------
# Property 3: Token bucket enforces maximum request rate
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestProperty3MaximumRequestRateEnforcement:
    """Property 3: Token bucket enforces maximum request rate.

    For any configured rate R and burst_size B, if N requests are issued
    where N > B, the minimum time to complete all N acquire calls SHALL
    be at least `(N - B) / R` seconds, ensuring the sustained request
    rate never exceeds R requests per second.
    """

    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        rate=_p3_rate_strategy,
        burst_size=_p3_burst_strategy,
        extra_requests=_p3_extra_requests_strategy,
    )
    async def test_n_requests_take_at_least_n_minus_b_over_r_seconds(
        self,
        rate: float,
        burst_size: int,
        extra_requests: int,
    ) -> None:
        """Validate Requirements 1.1.

        N > B requests issued sequentially take at least (N - B) / R
        seconds to complete, proving that the sustained request rate
        never exceeds R rps.
        """
        n_requests = burst_size + extra_requests
        limiter = TokenBucketRateLimiter(rate=rate, burst_size=burst_size)

        # Issue N acquire calls sequentially and measure wall-clock time
        start = time.monotonic()
        for _ in range(n_requests):
            await limiter.acquire()
        elapsed = time.monotonic() - start

        # The minimum expected time: the first B requests are free
        # (burst), the remaining (N - B) requests each need 1/R
        # seconds to refill.
        min_expected = extra_requests / rate

        # Allow a small tolerance for scheduling jitter (5ms or 5%)
        tolerance = max(0.005, min_expected * 0.05)
        assert elapsed >= min_expected - tolerance, (
            f"Expected at least {min_expected:.4f}s for "
            f"{n_requests} requests (rate={rate}, "
            f"burst={burst_size}), but only took {elapsed:.4f}s"
        )

    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        rate=_p3_rate_strategy,
        burst_size=_p3_burst_strategy,
        extra_requests=_p3_extra_requests_strategy,
    )
    async def test_concurrent_requests_respect_rate_limit(
        self,
        rate: float,
        burst_size: int,
        extra_requests: int,
    ) -> None:
        """Validate Requirements 1.1.

        Even when N > B requests are issued concurrently, the total
        time to complete all of them is at least (N - B) / R seconds.
        """
        n_requests = burst_size + extra_requests
        limiter = TokenBucketRateLimiter(rate=rate, burst_size=burst_size)

        async def acquire_one() -> None:
            await limiter.acquire()

        start = time.monotonic()
        # Launch all requests concurrently
        await asyncio.gather(*[acquire_one() for _ in range(n_requests)])
        elapsed = time.monotonic() - start

        min_expected = extra_requests / rate

        # Allow a small tolerance for scheduling jitter (5ms or 5%)
        tolerance = max(0.005, min_expected * 0.05)
        assert elapsed >= min_expected - tolerance, (
            f"Expected at least {min_expected:.4f}s for "
            f"{n_requests} concurrent requests (rate={rate}, "
            f"burst={burst_size}), but only took {elapsed:.4f}s"
        )
