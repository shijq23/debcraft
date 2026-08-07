# Implementation Plan

## Overview

Fix `_CliDatabaseProvider.get_session()` which returns `None` instead of a valid `AsyncSession`, causing `MirrorEngine` to crash with `AttributeError: 'NoneType' object has no attribute 'close'`. The fix replaces the `None`-returning stub with an in-memory SQLite async session factory using `aiosqlite`.

## Tasks

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - get_session Returns None and Session Methods Crash
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists
  - **Scoped PBT Approach**: For all DatabaseName values ("mirror", "metadata", "cache"), scope the property to confirm get_session returns None and session operations crash
  - Using Hypothesis, generate DatabaseName values from `st.sampled_from(["mirror", "metadata", "cache"])`
  - Assert `await _CliDatabaseProvider().get_session(db_name)` returns a non-None value (will fail on unfixed code)
  - Assert the returned session supports `close()`, `execute()`, `commit()`, `rollback()` without `AttributeError`
  - Test file: `tests/test_cli_database_provider_bug_condition.py`
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS with counterexamples showing `get_session("mirror")` returns `None` and method calls raise `AttributeError: 'NoneType' object has no attribute 'close'`
  - Document counterexamples found to confirm root cause: method body is `return None`
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Platform Provider and MirrorEngine Behavior Unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - Observe: `_CliDatabaseProvider.dispose()` completes without error on unfixed code
  - Observe: `_CliEventBus`, `_CliLogger`, and other CLI stubs continue to function on unfixed code
  - Observe: Platform-level `SqliteDatabaseProvider` (if testable in isolation) returns valid sessions
  - Write property-based tests using Hypothesis:
    - Property: `_CliDatabaseProvider.dispose()` never raises regardless of prior state
    - Property: `_CliDatabaseProvider.health_check()` returns without crash
    - Property: For random sequences of dispose/health_check calls, no unexpected errors occur
  - Test file: `tests/test_cli_database_provider_preservation.py`
  - Verify tests pass on UNFIXED code (these capture baseline behavior that must be preserved)
  - **EXPECTED OUTCOME**: Tests PASS (confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [x] 3. Fix _CliDatabaseProvider to return valid AsyncSession

  - [x] 3.1 Implement the fix in `src/debcraft/cli/mirror.py`
    - Add imports: `create_async_engine`, `async_sessionmaker` from `sqlalchemy.ext.asyncio`
    - Add `__init__` method to `_CliDatabaseProvider` that creates an in-memory SQLite async engine (`sqlite+aiosqlite:///`)
    - Create an `async_sessionmaker` bound to the in-memory engine
    - Replace `get_session()` body: return `self._session_factory()` instead of `return None`
    - Update `dispose()` to call `await self._engine.dispose()` to clean up the in-memory engine
    - Optionally update `health_check()` to return `{"mirror": True, "metadata": True, "cache": True}`
    - Remove the `# type: ignore[return-value]` comment since the return is now correct
    - _Bug_Condition: isBugCondition(input) where input.provider IS _CliDatabaseProvider AND get_session returns None_
    - _Expected_Behavior: get_session(db_name) returns valid AsyncSession supporting execute, commit, rollback, close_
    - _Preservation: dispose() and health_check() continue to work; platform SqliteDatabaseProvider unchanged_
    - _Requirements: 2.1, 2.2, 2.3, 3.3_

  - [x] 3.2 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - get_session Returns Valid AsyncSession
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior (non-None AsyncSession with working methods)
    - When this test passes, it confirms the bug is fixed for all DatabaseName values
    - Run bug condition exploration test from step 1: `tests/test_cli_database_provider_bug_condition.py`
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.3 Verify preservation tests still pass
    - **Property 2: Preservation** - Platform Provider and MirrorEngine Behavior Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run preservation property tests from step 2: `tests/test_cli_database_provider_preservation.py`
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all tests still pass after fix (no regressions)
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [x] 4. Checkpoint - Ensure all tests pass
  - Run full test suite to verify no regressions
  - Ensure bug condition test passes (Property 1 satisfied)
  - Ensure preservation tests pass (Property 2 satisfied)
  - Verify `debcraft mirror sync` no longer crashes with `AttributeError`
  - Ensure all tests pass, ask the user if questions arise.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2"] },
    { "id": 1, "tasks": ["3.1"] },
    { "id": 2, "tasks": ["3.2", "3.3"] },
    { "id": 3, "tasks": ["4"] }
  ]
}
```

## Notes

- The in-memory SQLite engine (`sqlite+aiosqlite:///`) is ephemeral — no persistence between runs, which matches the CLI's intended lightweight behavior.
- Property-based tests use Hypothesis to generate `DatabaseName` values and random operation sequences.
- The exploration test (task 1) is expected to FAIL before the fix and PASS after — this is the core of the bug condition methodology.
- Preservation tests (task 2) must PASS both before and after the fix to ensure no regressions.
