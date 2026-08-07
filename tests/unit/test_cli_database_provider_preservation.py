"""Preservation property tests for _CliDatabaseProvider baseline behavior.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

Property 2: Preservation — Platform Provider and MirrorEngine Behavior Unchanged

These tests capture baseline behavior of _CliDatabaseProvider that already works
correctly on the UNFIXED code and must continue to work after the fix is applied:
- dispose() completes without error regardless of prior state
- health_check() returns without crash and returns a dict
- Random sequences of dispose/health_check calls never raise unexpected errors

These tests MUST PASS on the current unfixed code (they verify baseline behavior
to preserve) and MUST STILL PASS after the fix is applied (confirming no regressions).
"""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.cli.mirror import _CliDatabaseProvider

# Strategy for generating operation sequences
_OPERATIONS = st.sampled_from(["dispose", "health_check"])


@pytest.mark.unit
class TestCliDatabaseProviderPreservation:
    """Property 2: Preservation — dispose() and health_check() baseline behavior.

    These tests verify existing correct behavior that must be preserved
    after the fix is applied. They capture the invariants:
    - dispose() never raises regardless of prior state
    - health_check() returns a dict without crash
    - Arbitrary sequences of these operations are safe
    """

    @settings(max_examples=50)
    @given(call_count=st.integers(min_value=1, max_value=10))
    def test_dispose_never_raises_regardless_of_call_count(self, call_count: int) -> None:
        """dispose() completes without error for any number of repeated calls.

        On both unfixed and fixed code, dispose() should be a safe no-op
        that never raises, regardless of how many times it is called.

        **Validates: Requirements 3.3**
        """
        provider = _CliDatabaseProvider()

        async def _run() -> None:
            for _ in range(call_count):
                await provider.dispose()

        asyncio.run(_run())

    @settings(max_examples=50)
    @given(call_count=st.integers(min_value=1, max_value=10))
    def test_health_check_returns_dict_without_crash(self, call_count: int) -> None:
        """health_check() returns a dict and never crashes for any number of calls.

        On both unfixed and fixed code, health_check() should return a dict
        (possibly empty) without raising any exception.

        **Validates: Requirements 3.3**
        """
        provider = _CliDatabaseProvider()

        async def _run() -> dict[str, bool]:
            result = {}
            for _ in range(call_count):
                result = await provider.health_check()
            return result

        result = asyncio.run(_run())
        assert isinstance(result, dict), f"health_check() returned {type(result).__name__}, expected dict"

    @settings(max_examples=100)
    @given(
        operations=st.lists(
            _OPERATIONS,
            min_size=1,
            max_size=20,
        )
    )
    def test_random_operation_sequences_never_raise(self, operations: list[str]) -> None:
        """For random sequences of dispose/health_check calls, no unexpected errors occur.

        This property captures the invariant that arbitrary interleaving of
        dispose() and health_check() calls is always safe, regardless of order
        or repetition.

        **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
        """
        provider = _CliDatabaseProvider()

        async def _run() -> None:
            for op in operations:
                if op == "dispose":
                    await provider.dispose()
                elif op == "health_check":
                    result = await provider.health_check()
                    # health_check must always return a dict
                    assert isinstance(result, dict), (
                        f"health_check() returned {type(result).__name__} after operation sequence, expected dict"
                    )

        asyncio.run(_run())

    @settings(max_examples=50)
    @given(db_name=st.sampled_from(["mirror", "metadata", "cache"]))
    def test_dispose_after_get_session_does_not_raise(self, db_name: str) -> None:
        """dispose() completes without error even after get_session was called.

        Regardless of what get_session returns (even None on unfixed code),
        a subsequent dispose() call must never raise.

        **Validates: Requirements 3.3**
        """
        provider = _CliDatabaseProvider()

        async def _run() -> None:
            # Call get_session (returns valid session on fixed code)
            _session = await provider.get_session(db_name)
            # dispose() must still succeed
            await provider.dispose()

        asyncio.run(_run())

    @settings(max_examples=50)
    @given(db_name=st.sampled_from(["mirror", "metadata", "cache"]))
    def test_health_check_after_get_session_returns_dict(self, db_name: str) -> None:
        """health_check() returns a dict even after get_session was called.

        Regardless of what get_session returns, a subsequent health_check()
        call must return a dict without error.

        **Validates: Requirements 3.3, 3.4**
        """
        provider = _CliDatabaseProvider()

        async def _run() -> dict[str, bool]:
            _session = await provider.get_session(db_name)
            return await provider.health_check()

        result = asyncio.run(_run())
        assert isinstance(result, dict), (
            f"health_check() returned {type(result).__name__} after get_session({db_name!r}), expected dict"
        )
