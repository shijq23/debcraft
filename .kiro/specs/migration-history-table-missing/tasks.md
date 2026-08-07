# Implementation Plan

## Overview

Fix the `SnapshotPublisher._get_schema_version()` crash when the `_migration_history` table does not exist. The fix adds targeted exception handling to catch the specific "no such table" `OperationalError` and return 0 as the default schema version. This follows the bug condition methodology: exploration tests confirm the bug, preservation tests lock baseline behavior, then the fix is applied and validated.

## Tasks

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Missing Table Raises OperationalError
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists
  - **Scoped PBT Approach**: Scope the property to databases where `_migration_history` table does not exist
  - Write a property-based test (using Hypothesis) that:
    - Creates an in-memory SQLite database session without the `_migration_history` table
    - Calls `_get_schema_version(session)` on the database
    - Asserts that the result is 0 and no exception is raised
  - Use `hypothesis.given` with strategies for optional other tables existing in the database
  - Test file: `tests/unit/infrastructure/mirror/test_publisher_bug_condition.py`
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS with `sqlalchemy.exc.OperationalError: no such table: _migration_history`
  - Document counterexamples found (e.g., "any database session without _migration_history table raises OperationalError")
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Existing Table Behavior Unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - Observe: `_get_schema_version` on a database with `_migration_history` containing rows returns `MAX(version)` on unfixed code
  - Observe: `_get_schema_version` on a database with empty `_migration_history` table returns 0 on unfixed code
  - Observe: Non-"no such table" `OperationalError` exceptions propagate on unfixed code
  - Write property-based tests (using Hypothesis) that:
    - Generate random lists of positive integer versions, insert them into `_migration_history`, and assert `_get_schema_version` returns `max(versions)`
    - Assert that an empty `_migration_history` table returns 0
    - Assert that `OperationalError` with messages NOT containing "no such table" are re-raised
  - Test file: `tests/unit/infrastructure/mirror/test_publisher_preservation.py`
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.4_

- [x] 3. Fix for missing `_migration_history` table crash in `_get_schema_version`

  - [x] 3.1 Implement the fix
    - Add `from sqlalchemy.exc import OperationalError` import to `src/debcraft/infrastructure/mirror/publisher.py`
    - Wrap the `session.execute(...)` call in `_get_schema_version` with a try/except block
    - Catch `OperationalError` and check if `"no such table"` is in the string representation of the exception
    - Return 0 when the missing table error is caught (consistent with existing empty-table fallback)
    - Re-raise the exception for any other `OperationalError` (e.g., database locked, disk I/O)
    - Update the docstring to document the missing table handling
    - _Bug_Condition: isBugCondition(input) where NOT tableExists("_migration_history", input.database) AND _get_schema_version(input) is called_
    - _Expected_Behavior: _get_schema_version returns 0 without raising when table is missing_
    - _Preservation: Existing table queries return MAX(version); empty table returns 0; non-table OperationalErrors propagate_
    - _Requirements: 1.1, 1.2, 2.1, 2.2, 3.1, 3.2, 3.4_

  - [x] 3.2 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Missing Table Returns Default Version
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior (returns 0 when table missing)
    - When this test passes, it confirms the expected behavior is satisfied
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2_

  - [x] 3.3 Verify preservation tests still pass
    - **Property 2: Preservation** - Existing Table Behavior Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all tests still pass after fix (no regressions)
    - _Requirements: 3.1, 3.2, 3.4_

- [x] 4. Checkpoint - Ensure all tests pass
  - Run the full test suite to verify no regressions
  - Verify bug condition test passes (Property 1)
  - Verify preservation tests pass (Property 2)
  - Verify existing project tests still pass
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

- Tests use Hypothesis for property-based testing, which is already configured in this project (`.hypothesis/` directory exists)
- The fix is minimal and targeted: only `_get_schema_version` in `src/debcraft/infrastructure/mirror/publisher.py` is modified
- The exploration test (task 1) is expected to FAIL before the fix and PASS after — this is by design
- The preservation tests (task 2) should PASS both before and after the fix
- Import `OperationalError` from `sqlalchemy.exc` (SQLAlchemy wraps sqlite3 errors in its own hierarchy)
