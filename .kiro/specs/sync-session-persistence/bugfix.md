# Bugfix Requirements Document

## Introduction

The `mirror status` command always displays "Last sync: never" even after a successful `mirror sync` run. The root cause is that `MirrorEngine.sync_repository()` computes sync outcome metrics (status, file counts, bytes transferred) and logs them, but never persists a `SyncSession` row to the `sync_sessions` table. Since the `mirror status` command queries `sync_sessions` for the most recent `completed_at` timestamp, the result is always empty.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN `MirrorEngine.sync_repository()` completes a sync (downloads files, skips cached files, or encounters failures) THEN the system logs the session outcome but does not insert a `SyncSession` record into the database

1.2 WHEN `MirrorEngine.sync_repository()` completes successfully and all suites are already up-to-date (conditional request returns None) THEN the system does not insert a `SyncSession` record into the database

1.3 WHEN `mirror status` is run after a successful sync THEN the system displays "Last sync: never" because the `sync_sessions` table contains no rows

### Expected Behavior (Correct)

2.1 WHEN `MirrorEngine.sync_repository()` completes a sync (regardless of outcome: completed, partial, failed, or cancelled) THEN the system SHALL insert a `SyncSession` record with the correct `session_id`, `repository_name`, `status`, file counts (`files_downloaded`, `files_skipped`, `files_failed`), `bytes_transferred`, `started_at`, and `completed_at` timestamps

2.2 WHEN `MirrorEngine.sync_repository()` completes successfully and all suites are already up-to-date THEN the system SHALL insert a `SyncSession` record with status "completed", zero file counts, and valid `started_at`/`completed_at` timestamps

2.3 WHEN `mirror status` is run after a successful sync THEN the system SHALL display the `completed_at` timestamp from the most recent `SyncSession` record

### Unchanged Behavior (Regression Prevention)

3.1 WHEN `MirrorEngine.sync_repository()` is called THEN the system SHALL CONTINUE TO return a `SyncResult` dataclass with accurate `files_downloaded`, `files_skipped`, `files_failed`, and `bytes_transferred` counts

3.2 WHEN `MirrorEngine.sync_repository()` encounters a cancellation token THEN the system SHALL CONTINUE TO stop processing and report the cancellation in the log

3.3 WHEN `mirror status` queries the database and no `sync_sessions` table exists or no rows are present THEN the system SHALL CONTINUE TO display "Last sync: never"

3.4 WHEN files are downloaded during sync THEN the system SHALL CONTINUE TO track `RepositoryFile` state transitions (DISCOVERED → QUEUED → DOWNLOADING → DOWNLOADED → VERIFIED) correctly

---

## Bug Condition

```pascal
FUNCTION isBugCondition(X)
  INPUT: X of type SyncRepositoryCall
  OUTPUT: boolean

  // The bug always triggers — no SyncSession is ever persisted
  RETURN TRUE
END FUNCTION
```

## Property Specification

```pascal
// Property: Fix Checking — SyncSession persistence
FOR ALL X WHERE isBugCondition(X) DO
  result ← MirrorEngine'.sync_repository(X.config, X.session_id)
  session ← query_sync_sessions(X.session_id)
  ASSERT session IS NOT NULL
  ASSERT session.repository_name = X.config.name
  ASSERT session.status IN {"completed", "partial", "failed", "cancelled"}
  ASSERT session.files_downloaded = result.files_downloaded
  ASSERT session.files_skipped = result.files_skipped
  ASSERT session.files_failed = result.files_failed
  ASSERT session.bytes_transferred = result.bytes_transferred
  ASSERT session.started_at IS NOT NULL
  ASSERT session.completed_at IS NOT NULL
END FOR
```

## Preservation Goal

```pascal
// Property: Preservation Checking — SyncResult unchanged
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT MirrorEngine(X) = MirrorEngine'(X)
END FOR

// Since the bug condition is always TRUE, preservation is expressed as:
// The SyncResult return value and all RepositoryFile state transitions
// remain identical between MirrorEngine and MirrorEngine'.
```
