"""Property-based tests for format_bytes IEC binary unit correctness.

# Feature: pylint-cleanup, Property 4: format_bytes IEC binary unit correctness

**Validates: Requirements 5.1**

For any non-negative integer n, format_bytes(n) SHALL return a string that:
- Uses "B" when n < 1024
- Uses "KiB" when 1024 <= n < 1024^2
- Uses "MiB" when 1024^2 <= n < 1024^3
- Uses "GiB" when n >= 1024^3
- Contains a numeric value that, when parsed and multiplied by the unit's byte count,
  is within 0.1 of the unit of the original value n
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.cli._formatting import format_bytes

# Unit multipliers in bytes
UNIT_MULTIPLIERS: dict[str, int] = {
    "B": 1,
    "KiB": 1024,
    "MiB": 1024**2,
    "GiB": 1024**3,
}


@pytest.mark.unit
class TestFormatBytesIECCorrectness:
    """Property 4: format_bytes IEC binary unit correctness.

    For any non-negative integer n (up to 2^50), format_bytes(n) returns a string
    using the correct IEC binary unit and a numeric value that accurately represents
    the original byte count within 0.1 of the unit.
    """

    @given(n=st.integers(min_value=0, max_value=2**50))
    def test_correct_unit_selection_and_numeric_accuracy(self, n: int) -> None:
        """**Validates: Requirements 5.1**.

        Verifies that format_bytes selects the correct IEC unit based on the
        magnitude of n and that the numeric value, when converted back to bytes,
        is within 0.1 of the unit of the original value.
        """
        result = format_bytes(n)

        # Determine expected unit based on magnitude
        if n < 1024:
            expected_unit = "B"
        elif n < 1024**2:
            expected_unit = "KiB"
        elif n < 1024**3:
            expected_unit = "MiB"
        else:
            expected_unit = "GiB"

        # Verify the result ends with the expected unit
        assert result.endswith(expected_unit), f"format_bytes({n}) = '{result}', expected unit '{expected_unit}'"

        # Parse the numeric value and unit from the result
        match = re.match(r"^([\d.]+)\s+(B|KiB|MiB|GiB)$", result)
        assert match is not None, f"format_bytes({n}) = '{result}' does not match expected format '<number> <unit>'"

        numeric_str = match.group(1)
        unit = match.group(2)
        numeric_value = float(numeric_str)
        multiplier = UNIT_MULTIPLIERS[unit]

        # Verify numeric accuracy: the parsed value times the multiplier
        # should be within 0.1 of the unit of the original value n
        reconstructed = numeric_value * multiplier
        tolerance = 0.1 * multiplier
        assert abs(reconstructed - n) <= tolerance, (
            f"format_bytes({n}) = '{result}': reconstructed value {reconstructed} "
            f"differs from {n} by more than 0.1 * {multiplier} = {tolerance}"
        )
