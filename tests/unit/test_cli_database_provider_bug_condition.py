"""Bug condition exploration test for _CliDatabaseProvider.get_session() returning None.

**Validates: Requirements 1.1, 1.2, 1.3**

Property 1: Bug Condition — get_session Returns None and Session Methods Crash

This test demonstrates that the _CliDatabaseProvider class in the CLI mirror module
returns None from get_session() instead of a valid AsyncSession, violating the
DatabaseProvider contract. When MirrorEngine calls session methods (close, execute,
commit, rollback) in its finally blocks, it crashes with AttributeError because the
session is None.

The test encodes the EXPECTED behavior: get_session() should return a non-None value
that supports standard AsyncSession operations. On unfixed code, this test FAILS,
confirming the bug exists. After the fix is applied, this test PASSES.
"""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.cli.mirror import _CliDatabaseProvider


@pytest.mark.unit
class TestCliDatabaseProviderBugCondition:
    """Exploration test confirming _CliDatabaseProvider.get_session() bug.

    These tests encode the EXPECTED behavior (non-None session with working methods).
    They FAIL on unfixed code, confirming the bug exists. After the fix, they PASS.
    """

    @settings(deadline=None)
    @given(db_name=st.sampled_from(["mirror", "metadata", "cache"]))
    def test_get_session_returns_non_none(self, db_name: str) -> None:
        """For all DatabaseName values, get_session returns a non-None value.

        On unfixed code, get_session() returns None, so this assertion fails.
        This confirms the bug: the method body is `return None`.

        **Validates: Requirements 1.1, 1.2**
        """
        provider = _CliDatabaseProvider()

        session = asyncio.run(provider.get_session(db_name))

        # Expected behavior: session should NOT be None
        assert session is not None, (
            f"get_session({db_name!r}) returned None — violates DatabaseProvider contract. "
            f"Expected a valid AsyncSession instance."
        )

    @settings(deadline=None)
    @given(db_name=st.sampled_from(["mirror", "metadata", "cache"]))
    def test_session_supports_close_without_attribute_error(self, db_name: str) -> None:
        """For all DatabaseName values, the returned session supports close().

        On unfixed code, calling close() on None raises:
        AttributeError: 'NoneType' object has no attribute 'close'

        **Validates: Requirements 1.3**
        """
        provider = _CliDatabaseProvider()

        session = asyncio.run(provider.get_session(db_name))

        # Expected behavior: session.close() should not raise AttributeError
        assert hasattr(session, "close"), (
            f"get_session({db_name!r}) returned object without 'close' attribute. "
            f"Got {type(session).__name__} (None). "
            f"This causes 'AttributeError: NoneType has no attribute close' in MirrorEngine."
        )

    @settings(deadline=None)
    @given(db_name=st.sampled_from(["mirror", "metadata", "cache"]))
    def test_session_supports_execute_without_attribute_error(self, db_name: str) -> None:
        """For all DatabaseName values, the returned session supports execute().

        On unfixed code, calling execute() on None raises:
        AttributeError: 'NoneType' object has no attribute 'execute'

        **Validates: Requirements 1.2, 1.3**
        """
        provider = _CliDatabaseProvider()

        session = asyncio.run(provider.get_session(db_name))

        # Expected behavior: session.execute() should not raise AttributeError
        assert hasattr(session, "execute"), (
            f"get_session({db_name!r}) returned object without 'execute' attribute. "
            f"Got {type(session).__name__} (None). "
            f"This causes 'AttributeError: NoneType has no attribute execute' in MirrorEngine."
        )

    @settings(deadline=None)
    @given(db_name=st.sampled_from(["mirror", "metadata", "cache"]))
    def test_session_supports_commit_and_rollback(self, db_name: str) -> None:
        """For all DatabaseName values, the returned session supports commit() and rollback().

        On unfixed code, calling commit()/rollback() on None raises:
        AttributeError: 'NoneType' object has no attribute 'commit'/'rollback'

        **Validates: Requirements 1.2, 1.3**
        """
        provider = _CliDatabaseProvider()

        session = asyncio.run(provider.get_session(db_name))

        # Expected behavior: session should support commit and rollback
        assert hasattr(session, "commit"), (
            f"get_session({db_name!r}) returned object without 'commit' attribute. Got {type(session).__name__} (None)."
        )
        assert hasattr(session, "rollback"), (
            f"get_session({db_name!r}) returned object without 'rollback' attribute. "
            f"Got {type(session).__name__} (None)."
        )
