"""Token bucket rate limiter for HTTP request throttling.

Provides an async-compatible rate limiter using the token bucket algorithm
to control outgoing HTTP request rates, preventing CDN rate-limiting (429)
during bulk mirror download operations.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

from debcraft.infrastructure.mirror.errors import RateLimitTimeoutError


class TokenBucketRateLimiter:
    """Async token bucket rate limiter for HTTP request throttling.

    Tokens are replenished at a constant rate up to a maximum burst size.
    Each HTTP request must acquire a token before being sent. When no tokens
    are available, requests block until a token is replenished or a timeout
    elapses.
    """

    def __init__(self, rate: float, burst_size: int) -> None:
        """Initialize with tokens-per-second rate and max burst capacity.

        Args:
            rate: Number of tokens replenished per second.
            burst_size: Maximum number of tokens (also the initial count).
        """
        self._tokens: float = float(burst_size)
        self._max_tokens: int = burst_size
        self._rate: float = rate
        self._last_replenish: float = time.monotonic()
        self._lock: asyncio.Lock = asyncio.Lock()
        self._waiters: list[asyncio.Future[None]] = []

    def _replenish(self) -> None:
        """Replenish tokens based on elapsed time since last replenishment."""
        now = time.monotonic()
        elapsed = now - self._last_replenish
        if elapsed > 0:
            self._tokens = min(self._tokens + self._rate * elapsed, float(self._max_tokens))
            self._last_replenish = now

    async def acquire(self, timeout: float = 60.0) -> None:
        """Acquire a single token. Blocks until available or timeout.

        Under the lock, replenishes tokens based on elapsed time. If a token
        is available, decrements and returns immediately. Otherwise, releases
        the lock, sleeps briefly, and retries in a loop until a token is
        available or the timeout expires.

        The current task is tracked in the _waiters list so it can be
        cancelled externally via cancel_waiters().

        Args:
            timeout: Maximum seconds to wait for a token (default 60s).

        Raises:
            RateLimitTimeoutError: If token not acquired within timeout.
        """
        deadline = time.monotonic() + timeout
        waiter = asyncio.get_event_loop().create_future()
        self._waiters.append(waiter)
        try:
            while True:
                async with self._lock:
                    self._replenish()
                    if self._tokens >= 1.0:
                        self._tokens -= 1.0
                        return

                # Check timeout before sleeping
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RateLimitTimeoutError(timeout=timeout)

                # Sleep briefly before retrying — sleep for the time needed
                # to replenish one token, but cap at remaining timeout
                sleep_time = min(1.0 / self._rate, remaining)
                await asyncio.sleep(sleep_time)

                # Check timeout after sleeping
                if time.monotonic() >= deadline:
                    raise RateLimitTimeoutError(timeout=timeout)
        finally:
            # Remove waiter from tracking list
            with contextlib.suppress(ValueError):
                self._waiters.remove(waiter)

    def cancel_waiters(self) -> None:
        """Cancel all tasks currently waiting to acquire a token."""
        for waiter in self._waiters:
            if not waiter.done():
                waiter.cancel()
        self._waiters.clear()
