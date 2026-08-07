# Implementation Plan

## Overview

Fix the missing `SyncSession` persistence in `MirrorEngine.sync_repository()`. The method computes sync metrics but never writes a `SyncSession` row, causing `mirror status` to always show "Last sync: never". The fix adds a database insert after status determination, wrapped in try/except to preserve the existing return contract.

## Tasks

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - No SyncSession Row After sync_repository()
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate no `SyncSession` is persisted
  - **Scoped PBT Approach**: Since the bug condition is always TRUE (every call triggers it), scope the property to a concrete sync call with a mock pipeline that completes successfully
  - Create test file `tests/properties/infrastructure/test_sync_session_bug_condition.py`
  - Set up an in-memory SQLite database via `_CliDatabaseProvider` (with temp path override)
  - Mock the download coordinator and HTTP layer so `sync_repository()` completes without network I/O
  - Call `engine.sync_repository(config, session_id)` and assert a `SyncSession` row exists with matching `session_id`
  - Assert `session.repository_name == config.name`
  - Assert `session.status in {"completed", "partial", "failed", "cancelled"}`
  - Assert `session.files_downloaded == result.files_downloaded`
  - Assert `session.files_skipped == result.files_skipped`
  - Assert `session.files_failed == result.files_failed`
  - Assert `session.bytes_transferred == result.bytes_transferred`
  - Assert `session.started_at is not None and session.completed_at is not None`
  - Assert `session.started_at <= session.completed_at`
  - Use Hypothesis to generate varied `RepositoryConfig` inputs (different names, suites)
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (query returns no rows — proves the bug exists)
  - Document counterexamples found: `sync_sessions` table contains zero rows after any `sync_repository()` call
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - SyncResult Return Value Unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - Create test file `tests/properties/infrastructure/test_sync_session_preservation.py`
  - Observe: on UNFIXED code, `sync_repository()` returns a `SyncResult` with correct file counts regardless of DB state
  - Observe: `SyncResult.files_downloaded`, `files_skipped`, `files_failed`, `bytes_transferred` match the pipeline's actual processing
  - Write property-based test using Hypothesis: generate random combinations of download outcomes (files that succeed, skip, or fail) and verify `SyncResult` fields match expected counts
  - Write property-based test: inject a DB failure (mock `db_provider.get_session()` to raise) and verify `SyncResult` is still returned unchanged (this simulates the try/except path in the fixed code)
  - Write property-based test: verify cancellation token still stops processing and returns partial results
  - Verify tests PASS on UNFIXED code (the SyncResult contract is already satisfied today)
  - **EXPECTED OUTCOME**: Tests PASS (confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [x] 3. Fix for missing SyncSession persistence in MirrorEngine.sync_repository()

  - [x] 3.1 Implement the fix
    - Add `SyncSession` to the import from `debcraft.infrastructure.models.mirror` (line importing `RepositoryFile, RepositoryFileState`)
    - Capture `started_at = datetime.now(UTC)` at the top of `sync_repository()`, immediately after `self._result = SyncResult()`
    - After status determination (after the `status = ...` block) and before the summary log entry, insert `SyncSession` persistence block
    - Open a database session via `await self._db_provider.get_session("mirror")`
    - Create `SyncSession(session_id=session_id, repository_name=config.name, status=status, files_downloaded=self._result.files_downloaded, files_skipped=self._result.files_skipped, files_failed=self._result.files_failed, bytes_transferred=self._result.bytes_transferred, started_at=started_at, completed_at=datetime.now(UTC))`
    - Add to session, commit, and close inside a `try/finally` for the session close
    - Wrap the entire persistence block in `try/except Exception as exc` — on failure, log error with `self._logger.error("Failed to persist sync session", session_id=session_id, error=str(exc))` and continue
    - _Bug_Condition: isBugCondition(input) → TRUE for all calls (no SyncSession ever persisted)_
    - _Expected_Behavior: After sync_repository() returns, sync_sessions contains a row with matching session_id, metrics, and timestamps_
    - _Preservation: SyncResult return value, RepositoryFile state transitions, cancellation, and progress reporting remain identical_
    - _Requirements: 2.1, 2.2, 3.1, 3.2, 3.3, 3.4_

  - [x] 3.2 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - SyncSession Persisted After Sync
    - **IMPORTANT**: Re-run the SAME test from task 1 — do NOT write a new test
    - The test from task 1 encodes the expected behavior (SyncSession row exists with correct fields)
    - When this test passes, it confirms the expected behavior is satisfied
    - Run `pytest tests/properties/infrastructure/test_sync_session_bug_condition.py -v`
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2_

  - [x] 3.3 Verify preservation tests still pass
    - **Property 2: Preservation** - SyncResult Return Value Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Run `pytest tests/properties/infrastructure/test_sync_session_preservation.py -v`
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all preservation tests still pass after fix (no regressions to SyncResult, cancellation, or progress)

- [x] 4. Checkpoint - Ensure all tests pass
  - Run full property test suite: `pytest tests/properties/infrastructure/test_sync_session_bug_condition.py tests/properties/infrastructure/test_sync_session_preservation.py -v`
  - Ensure all tests pass, ask the user if questions arise.


## Task Dependency Graph

```json
{
  "waves": [
    {"tasks": ["1", "2"]},
    {"tasks": ["3.1"]},
    {"tasks": ["3.2", "3.3"]},
    {"tasks": ["4"]}
  ]
}
```

## Notes

- Tests use Hypothesis for property-based testing (already a project dependency)
- Test files go in `tests/properties/infrastructure/` following existing conventions
- The fix modifies only `src/debcraft/infrastructure/mirror/engine.py`
- The `SyncSession` model already exists in `src/debcraft/infrastructure/models/mirror.py`
- `_CliDatabaseProvider` in `src/debcraft/cli/mirror.py` already provides async sessions for "mirror" DB
