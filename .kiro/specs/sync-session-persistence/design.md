# Sync Session Persistence Bugfix Design

## Overview

The `mirror status` command always reports "Last sync: never" because `MirrorEngine.sync_repository()` never persists a `SyncSession` row after the sync pipeline completes. The fix adds a single database insert at the end of `sync_repository()`, after status determination and before returning the `SyncResult`. The insert uses the existing `_CliDatabaseProvider.get_session("mirror")` path and the already-defined `SyncSession` SQLAlchemy model. A try/except around the DB write ensures that a persistence failure does not crash the sync or alter its return value.

## Glossary

- **Bug_Condition (C)**: Every call to `MirrorEngine.sync_repository()` — the session is never persisted regardless of outcome
- **Property (P)**: After `sync_repository()` returns, a `SyncSession` row with matching `session_id`, metrics, and timestamps exists in `sync_sessions`
- **Preservation**: The `SyncResult` return value, `RepositoryFile` state transitions, cancellation behavior, and progress reporting remain identical
- **`MirrorEngine.sync_repository()`**: The method in `infrastructure/mirror/engine.py` that orchestrates the five-stage sync pipeline for one repository
- **`SyncSession`**: SQLAlchemy model in `infrastructure/models/mirror.py` representing one sync execution's metadata
- **`_CliDatabaseProvider`**: The `DatabaseProvider` implementation in `cli/mirror.py` that provides async sessions backed by `mirror.db`
- **`SyncResult`**: Dataclass returned by `sync_repository()` with `files_downloaded`, `files_skipped`, `files_failed`, `bytes_transferred`

## Bug Details

### Bug Condition

The bug manifests on every invocation of `MirrorEngine.sync_repository()`. The method computes the sync outcome (status, file counts, bytes transferred), logs the summary, but never writes a `SyncSession` record to the database. Since `mirror status` queries `sync_sessions` for the most recent `completed_at`, the display always shows "Last sync: never".

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type SyncRepositoryCall (config, session_id)
  OUTPUT: boolean

  // The bug triggers unconditionally — no SyncSession is ever persisted
  RETURN TRUE
END FUNCTION
```

### Examples

- **Completed sync**: User runs `mirror sync`, all files download successfully → `sync_sessions` table remains empty → `mirror status` shows "Last sync: never"
- **Partial sync**: 8 of 10 files download, 2 fail → `sync_sessions` table remains empty → same "never" display
- **Failed sync**: All files fail to download → no session record → no visibility into failure time
- **Cancelled sync**: User sends SIGINT mid-sync → no session record → cannot tell when cancellation occurred
- **Up-to-date sync**: All conditional requests return 304 → no session record → user cannot confirm sync ran

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- `sync_repository()` must continue to return a `SyncResult` with accurate file counts and bytes transferred
- `RepositoryFile` state transitions (DISCOVERED → QUEUED → DOWNLOADING → DOWNLOADED → VERIFIED) must remain unchanged
- Cancellation token checks between stages must continue to stop processing immediately
- Progress reporting at each stage milestone must remain unchanged
- The summary log entry at the end of sync must continue to emit all current fields
- `mirror status` displaying "Last sync: never" when the table is empty/absent must remain unchanged

**Scope:**
The fix adds behavior (a DB insert) after the existing pipeline logic. It does not modify any existing pipeline stage, state transition, progress report, or return value. The only new failure mode is a persistence error, which is caught and logged without altering the return path.

## Hypothesized Root Cause

Based on the bug description, the issue is straightforward:

1. **Missing persistence code**: The `sync_repository()` method was implemented with the full pipeline and metrics computation but the final step — inserting a `SyncSession` row — was never written. The `SyncSession` model exists, the database provider is injected, and `mirror status` queries the table, but no code path ever writes to it.

2. **No integration test covering the contract**: There is no test that asserts a `SyncSession` row exists after `sync_repository()` returns, so the omission went undetected.

## Correctness Properties

Property 1: Bug Condition - SyncSession Persisted After Sync

_For any_ call to `sync_repository(config, session_id)` that returns a `SyncResult`, the `sync_sessions` table SHALL contain exactly one row with `session_id` matching the input, `repository_name` matching `config.name`, `status` in `{"completed", "partial", "failed", "cancelled"}`, file counts matching the returned `SyncResult`, `started_at` <= `completed_at`, and both timestamps non-null.

**Validates: Requirements 2.1, 2.2**

Property 2: Preservation - SyncResult Return Value Unchanged

_For any_ call to `sync_repository(config, session_id)`, the returned `SyncResult` SHALL have identical `files_downloaded`, `files_skipped`, `files_failed`, and `bytes_transferred` values whether or not the session persistence succeeds, preserving the original function's return contract.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `src/debcraft/infrastructure/mirror/engine.py`

**Function**: `sync_repository()`

**Specific Changes**:

1. **Record `started_at` timestamp**: Capture `datetime.now(UTC)` at the top of `sync_repository()`, immediately after initializing `self._result`. This uses wall-clock time (not monotonic) since it will be stored in the database and displayed to users.

2. **Import `SyncSession` model**: Add `SyncSession` to the existing import from `debcraft.infrastructure.models.mirror` (currently only imports `RepositoryFile` and `RepositoryFileState`).

3. **Insert `SyncSession` after status determination**: After the status variable is computed and before the summary log entry, open a database session via `self._db_provider.get_session("mirror")`, create a `SyncSession` instance with all computed fields, add it, commit, and close.

4. **Wrap persistence in try/except**: The DB insert must be inside a `try/except Exception` block. On failure, log the error at `self._logger.error(...)` level and continue to the normal return path. This ensures a transient DB issue (disk full, locked file, schema mismatch) does not crash the sync or lose the `SyncResult`.

5. **Set `completed_at` to `datetime.now(UTC)` at persistence time**: This captures the moment the sync finished (immediately after status computation), distinct from `started_at`.

**Pseudocode of the change:**

```python
# At method start (after self._result = SyncResult()):
started_at = datetime.now(UTC)

# After status determination, before summary log:
try:
    session = await self._db_provider.get_session("mirror")
    try:
        sync_session = SyncSession(
            session_id=session_id,
            repository_name=config.name,
            status=status,
            files_downloaded=self._result.files_downloaded,
            files_skipped=self._result.files_skipped,
            files_failed=self._result.files_failed,
            bytes_transferred=self._result.bytes_transferred,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )
        session.add(sync_session)
        await session.commit()
    finally:
        await session.close()
except Exception as exc:
    self._logger.error(
        "Failed to persist sync session",
        session_id=session_id,
        error=str(exc),
    )
```

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm that the root cause is simply missing persistence code.

**Test Plan**: Write a test that calls `sync_repository()` with a mock `DatabaseProvider`, then queries `sync_sessions` for the given `session_id`. Run on unfixed code to confirm the table is empty.

**Test Cases**:
1. **Completed sync — no session row**: Sync completes successfully, query `sync_sessions` → empty result (will fail on unfixed code)
2. **Partial sync — no session row**: Some files fail, query `sync_sessions` → empty result (will fail on unfixed code)
3. **Failed sync — no session row**: All files fail, query `sync_sessions` → empty result (will fail on unfixed code)
4. **Cancelled sync — no session row**: Cancellation token set mid-sync, query → empty (will fail on unfixed code)

**Expected Counterexamples**:
- `sync_sessions` table contains zero rows after any `sync_repository()` call
- Confirms the root cause: no code path inserts the record

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := sync_repository_fixed(input.config, input.session_id)
  session := query_sync_sessions(input.session_id)
  ASSERT session IS NOT NULL
  ASSERT session.repository_name = input.config.name
  ASSERT session.status IN {"completed", "partial", "failed", "cancelled"}
  ASSERT session.files_downloaded = result.files_downloaded
  ASSERT session.files_skipped = result.files_skipped
  ASSERT session.files_failed = result.files_failed
  ASSERT session.bytes_transferred = result.bytes_transferred
  ASSERT session.started_at IS NOT NULL
  ASSERT session.completed_at IS NOT NULL
  ASSERT session.started_at <= session.completed_at
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT sync_repository_original(input) = sync_repository_fixed(input)
END FOR
```

Since the bug condition is always TRUE, preservation is expressed as: the `SyncResult` return value must be identical regardless of whether the DB write succeeds or fails.

```
FOR ALL input DO
  result_normal := sync_repository_fixed(input)  // DB write succeeds
  result_db_fail := sync_repository_fixed_with_db_failure(input)  // DB write raises
  ASSERT result_normal = result_db_fail
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many combinations of file counts and sync outcomes automatically
- It catches edge cases (zero files, very large byte counts, all-skipped syncs)
- It provides strong guarantees that the return value is never altered by the persistence step

**Test Plan**: Observe `SyncResult` values on unfixed code for various sync scenarios, then write property-based tests verifying the fixed code returns identical results.

**Test Cases**:
1. **SyncResult preservation with successful DB write**: Verify `SyncResult` fields match pipeline metrics exactly
2. **SyncResult preservation with failed DB write**: Inject DB failure, verify `SyncResult` is still returned unchanged
3. **Cancellation behavior preservation**: Verify cancellation still stops processing and returns partial results
4. **Progress reporting preservation**: Verify progress callbacks still fire at expected percentages

### Unit Tests

- Test that `SyncSession` is inserted with correct fields after a completed sync
- Test that `SyncSession` is inserted with status "partial" when some files fail
- Test that `SyncSession` is inserted with status "failed" when all files fail
- Test that `SyncSession` is inserted with status "cancelled" when cancellation occurs
- Test that `started_at` < `completed_at` for all outcomes
- Test that a DB write failure logs an error but does not raise
- Test that `SyncResult` is returned unchanged when DB write fails

### Property-Based Tests

- Generate random combinations of `(files_downloaded, files_skipped, files_failed, bytes_transferred)` and verify the persisted `SyncSession` row matches exactly
- Generate random sync outcomes and verify `status` is correctly derived (completed/partial/failed/cancelled)
- Generate random scenarios with injected DB failures and verify `SyncResult` is always returned unchanged

### Integration Tests

- End-to-end test: run `mirror sync` against a mock HTTP server, then run `mirror status` and verify it shows a real timestamp instead of "never"
- Test that multiple sequential syncs produce multiple `SyncSession` rows with distinct `session_id` values
- Test that the same `mirror.db` file is used by both the sync write path and the status read path
