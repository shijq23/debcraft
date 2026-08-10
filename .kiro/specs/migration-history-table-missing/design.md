# Migration History Table Missing Bugfix Design

## Overview

The `SnapshotPublisher._get_schema_version()` method crashes with `sqlite3.OperationalError: no such table: _migration_history` when the migration history table does not exist in the metadata database. This occurs because the method queries `_migration_history` unconditionally without handling the case where `MigrationRunner.ensure_history_table()` has never been called. The fix adds targeted exception handling to catch the specific "no such table" `OperationalError` and return 0 as the default schema version, consistent with the existing fallback when no rows are found.

## Glossary

- **Bug_Condition (C)**: The `_migration_history` table does not exist in the metadata database when `_get_schema_version` is called
- **Property (P)**: When the table is missing, `_get_schema_version` returns 0 without raising an exception
- **Preservation**: Existing behavior when the table exists (with or without rows) and when other database errors occur must remain unchanged
- **`_get_schema_version`**: Static async method on `SnapshotPublisher` in `src/debcraft/infrastructure/mirror/publisher.py` that queries the highest migration version from `_migration_history`
- **`_migration_history`**: SQLite table created by `MigrationRunner.ensure_history_table()` that tracks applied migration versions
- **`SnapshotPublisher`**: Class responsible for creating and publishing `RepositorySnapshot` entities in `metadata.db`

## Bug Details

### Bug Condition

The bug manifests when `_get_schema_version` is called on a database session where the `_migration_history` table has never been created. This happens when `MigrationRunner.ensure_history_table()` has not been invoked prior to snapshot publication — for example, on a fresh database, in CLI contexts, or when the migration runner is bypassed.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type AsyncSession (connected to metadata.db)
  OUTPUT: boolean

  RETURN NOT tableExists("_migration_history", input.database)
         AND _get_schema_version(input) is called
END FUNCTION
```

### Examples

- **Fresh database**: A newly created `metadata.db` with no prior migration runs. Calling `publish_snapshot` raises `sqlite3.OperationalError: no such table: _migration_history`. Expected: returns schema_version=0 and continues.
- **CLI-only context**: The application is invoked through a CLI path that doesn't initialize the migration runner. The metadata database exists but lacks the history table. Expected: returns schema_version=0.
- **After database reset**: The database file is recreated (e.g., during testing or recovery) but migrations haven't been re-run. Expected: returns schema_version=0.
- **Table exists, no rows**: `_migration_history` exists but is empty. Current behavior already returns 0 correctly. This must remain unchanged.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- When `_migration_history` exists and contains rows, `_get_schema_version` must continue to return the highest version number (`MAX(version)`)
- When `_migration_history` exists but is empty, `_get_schema_version` must continue to return 0
- When `verified_file_count` is 0, `publish_snapshot` must continue to return `None` and publish a failure event without querying schema version
- When a database error other than "no such table: _migration_history" occurs, the exception must continue to propagate and cause transaction rollback

**Scope:**
All inputs where the `_migration_history` table exists should be completely unaffected by this fix. This includes:
- Databases with applied migrations (table has rows)
- Databases where `ensure_history_table()` was called but no migrations applied (table exists, empty)
- Any `OperationalError` that is NOT specifically "no such table: _migration_history"

## Hypothesized Root Cause

Based on the bug description, the root cause is:

1. **Missing Table Existence Check**: `_get_schema_version` executes `SELECT MAX(version) FROM _migration_history` directly without any guard for the table's existence. SQLite raises `OperationalError` when querying a non-existent table.

2. **Decoupled Lifecycle**: The `_migration_history` table is created by `MigrationRunner.ensure_history_table()`, but `SnapshotPublisher` has no dependency on or coordination with `MigrationRunner`. There is no guarantee that the migration runner has been invoked before the publisher is used.

3. **Incomplete Error Handling**: The method already handles the case of `None` result (empty table), but does not handle the case of the table itself being absent. The existing `except Exception` block in `publish_snapshot` catches it but re-raises after rollback, causing the entire operation to fail.

## Correctness Properties

Property 1: Bug Condition - Missing Table Returns Default Version

_For any_ database session where the `_migration_history` table does not exist, the fixed `_get_schema_version` function SHALL return 0 without raising an exception.

**Validates: Requirements 2.1, 2.2**

Property 2: Preservation - Existing Table Behavior Unchanged

_For any_ database session where the `_migration_history` table exists (with or without rows), the fixed `_get_schema_version` function SHALL produce the same result as the original function, preserving correct version retrieval and the empty-table fallback to 0.

**Validates: Requirements 3.1, 3.2**

Property 3: Preservation - Non-Table Errors Propagate

_For any_ database error that is NOT specifically "no such table: _migration_history", the fixed function SHALL propagate the exception exactly as the original function does, preserving error handling and transaction rollback behavior.

**Validates: Requirements 3.4**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `src/debcraft/infrastructure/mirror/publisher.py`

**Function**: `_get_schema_version`

**Specific Changes**:
1. **Add try/except around the query**: Wrap the `session.execute(...)` call in a try/except block that catches `sqlite3.OperationalError` (or SQLAlchemy's wrapped version).

2. **Check error message specificity**: In the except handler, inspect the error message to ensure it contains "no such table" before returning the default. This avoids masking other `OperationalError` types (e.g., database locked, disk I/O error).

3. **Return 0 for missing table**: When the specific "no such table" error is caught, return 0 — consistent with the existing fallback for an empty table.

4. **Re-raise non-matching OperationalErrors**: If an `OperationalError` occurs but is NOT the "no such table" variant, re-raise it so it propagates to the caller's error handling.

5. **Import the exception type**: Add the necessary import for `OperationalError` from `sqlalchemy.exc` (SQLAlchemy wraps sqlite3 errors in its own exception hierarchy).

**Proposed Implementation:**
```python
from sqlalchemy.exc import OperationalError


@staticmethod
async def _get_schema_version(session: AsyncSession) -> int:
    """Query the highest applied migration version from metadata.db.

    Args:
        session: The active database session.

    Returns:
        The highest version number, or 0 if no migrations have been applied
        or if the _migration_history table does not exist.
    """
    try:
        result = await session.execute(text("SELECT MAX(version) FROM _migration_history"))
        row = result.scalar()
        return row if row is not None else 0
    except OperationalError as exc:
        if "no such table" in str(exc):
            return 0
        raise
```

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that call `_get_schema_version` on a database session where `_migration_history` does not exist. Run these tests on the UNFIXED code to observe the `OperationalError` and confirm the root cause.

**Test Cases**:
1. **Fresh Database Test**: Call `_get_schema_version` on a brand new in-memory SQLite database with no tables (will fail on unfixed code with OperationalError)
2. **Database With Other Tables Only**: Call `_get_schema_version` on a database that has other tables but not `_migration_history` (will fail on unfixed code)
3. **After Table Drop**: Create `_migration_history`, drop it, then call `_get_schema_version` (will fail on unfixed code)

**Expected Counterexamples**:
- `sqlite3.OperationalError: no such table: _migration_history` raised from the `SELECT MAX(version)` query
- Confirms the root cause: no existence check before querying

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL session WHERE isBugCondition(session) DO
  result := _get_schema_version_fixed(session)
  ASSERT result == 0
  ASSERT no exception raised
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL session WHERE NOT isBugCondition(session) DO
  ASSERT _get_schema_version_original(session) == _get_schema_version_fixed(session)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain (various schema versions, empty tables, multiple rows)
- It catches edge cases that manual unit tests might miss (NULL values, negative versions, large integers)
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

**Test Plan**: Observe behavior on UNFIXED code first for databases where `_migration_history` exists, then write property-based tests capturing that behavior.

**Test Cases**:
1. **Version Retrieval Preservation**: Verify that `_get_schema_version` returns the correct MAX(version) for various populated tables
2. **Empty Table Preservation**: Verify that an empty `_migration_history` table still returns 0
3. **Other Error Propagation**: Verify that non-"no such table" OperationalErrors continue to propagate
4. **Transaction Behavior Preservation**: Verify that the fix doesn't affect the transactional behavior of `publish_snapshot`

### Unit Tests

- Test `_get_schema_version` with missing `_migration_history` table returns 0
- Test `_get_schema_version` with existing table and rows returns MAX(version)
- Test `_get_schema_version` with existing empty table returns 0
- Test that non-"no such table" OperationalErrors still propagate
- Test full `publish_snapshot` flow with missing table completes successfully

### Property-Based Tests

- Generate random schema versions (positive integers) inserted into `_migration_history` and verify `_get_schema_version` returns the maximum
- Generate random database states (table exists/missing, various row counts) and verify correct behavior for each case
- Generate random OperationalError messages and verify only "no such table" is caught

### Integration Tests

- Test full `publish_snapshot` workflow on a fresh database with no migration history
- Test `publish_snapshot` after migrations have been applied
- Test that snapshot publication and event emission work correctly when table is missing
