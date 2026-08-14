"""Property-based tests for rate-limit backoff delay bounds.

**Validates: Requirements 5.1, 5.2, 5.3**

Property 10: Rate-limit backoff delay bounds.
For any attempt number N (0-indexed), the computed backoff delay for a 429
rate-limit response SHALL fall within the range
[min(5 * 2^N, 60), min(min(5 * 2^N, 60) * 1.5, 60)] seconds.
The base delay SHALL be 5 seconds, the maximum SHALL be 60 seconds, and
jitter SHALL be between 0 and 50% of the computed base delay.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.infrastructure.mirror.download import _compute_backoff_delay

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Attempt numbers: 0, 1, 2 (the 3 allowed attempts for rate-limit retries)
# Also test higher values to ensure cap behavior holds
attempt_strategy = st.integers(min_value=0, max_value=10)

# Rate-limit backoff parameters as specified in design
_RATE_LIMIT_BASE = 5.0
_RATE_LIMIT_MAXIMUM = 60.0
_RATE_LIMIT_JITTER_FACTOR = 0.5


# ---------------------------------------------------------------------------
# Property 10: Rate-limit backoff delay bounds
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty10BackoffDelayBounds:
    """Property 10: Rate-limit backoff delay bounds.

    For any attempt number N, verify computed delay falls within
    [min(5*2^N, 60), min(min(5*2^N, 60)*1.5, 60)].

    **Validates: Requirements 5.1, 5.2, 5.3**
    """

    @given(attempt=attempt_strategy)
    def test_rate_limit_backoff_delay_lower_bound(self, attempt: int) -> None:
        """**Validates: Requirements 5.1, 5.2, 5.3**.

        The computed delay SHALL be at least min(5 * 2^attempt, 60).
        This verifies the base delay is 5 seconds with exponential growth
        capped at 60 seconds.
        """
        delay = _compute_backoff_delay(
            attempt,
            base=_RATE_LIMIT_BASE,
            maximum=_RATE_LIMIT_MAXIMUM,
            jitter_factor=_RATE_LIMIT_JITTER_FACTOR,
        )

        expected_lower = min(_RATE_LIMIT_BASE * (2**attempt), _RATE_LIMIT_MAXIMUM)

        assert delay >= expected_lower, f"Attempt {attempt}: delay {delay} is below lower bound {expected_lower}"

    @given(attempt=attempt_strategy)
    def test_rate_limit_backoff_delay_upper_bound(self, attempt: int) -> None:
        """**Validates: Requirements 5.1, 5.2, 5.3**.

        The computed delay SHALL be at most min(min(5*2^N, 60) * 1.5, 60).
        This verifies jitter adds at most 50% of the computed base delay,
        and the total is capped at the 60-second maximum.
        """
        delay = _compute_backoff_delay(
            attempt,
            base=_RATE_LIMIT_BASE,
            maximum=_RATE_LIMIT_MAXIMUM,
            jitter_factor=_RATE_LIMIT_JITTER_FACTOR,
        )

        base_delay = min(_RATE_LIMIT_BASE * (2**attempt), _RATE_LIMIT_MAXIMUM)
        expected_upper = min(base_delay * (1 + _RATE_LIMIT_JITTER_FACTOR), _RATE_LIMIT_MAXIMUM)

        assert delay <= expected_upper, f"Attempt {attempt}: delay {delay} exceeds upper bound {expected_upper}"

    @given(attempt=attempt_strategy)
    def test_rate_limit_backoff_delay_within_full_range(self, attempt: int) -> None:
        """**Validates: Requirements 5.1, 5.2, 5.3**.

        The computed delay SHALL fall within
        [min(5*2^N, 60), min(min(5*2^N, 60)*1.5, 60)] for any attempt N.
        This combines both bounds in a single assertion for clarity.
        """
        delay = _compute_backoff_delay(
            attempt,
            base=_RATE_LIMIT_BASE,
            maximum=_RATE_LIMIT_MAXIMUM,
            jitter_factor=_RATE_LIMIT_JITTER_FACTOR,
        )

        base_delay = min(_RATE_LIMIT_BASE * (2**attempt), _RATE_LIMIT_MAXIMUM)
        expected_lower = base_delay
        expected_upper = min(base_delay * (1 + _RATE_LIMIT_JITTER_FACTOR), _RATE_LIMIT_MAXIMUM)

        assert expected_lower <= delay <= expected_upper, (
            f"Attempt {attempt}: delay {delay} not in [{expected_lower}, {expected_upper}]"
        )

    @given(attempt=attempt_strategy)
    def test_rate_limit_backoff_never_exceeds_maximum(self, attempt: int) -> None:
        """**Validates: Requirements 5.2**.

        The maximum backoff delay SHALL be 60 seconds regardless of
        the attempt number.
        """
        delay = _compute_backoff_delay(
            attempt,
            base=_RATE_LIMIT_BASE,
            maximum=_RATE_LIMIT_MAXIMUM,
            jitter_factor=_RATE_LIMIT_JITTER_FACTOR,
        )

        assert delay <= _RATE_LIMIT_MAXIMUM, f"Attempt {attempt}: delay {delay} exceeds maximum {_RATE_LIMIT_MAXIMUM}"
