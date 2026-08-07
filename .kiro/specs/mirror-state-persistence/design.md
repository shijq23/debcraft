# Mirror State Persistence Bugfix Design

## Overview

The `mirror sync` command uses an ephemeral in-memory SQLite database (`_CliDatabaseProvider`) that loses all state between process invocations. This causes every sync to re-download all files. A secondary issue compounds this: `check_conditional()` is called without etag/last_modified headers from previously downloaded InRelease files, making HTTP 304 responses impossible.

The fix replaces `_CliDatabaseProvider` with a persistent database provider that writes to `resolve_xdg_path("database") / "mirror.db"`, and passes stored etag/last_modified values from the database to enable conditional requests.

## Glossary

- **Bug_Condition (C)**: The database provider uses an in-memory SQLite engine (`sqlite+aiosqlite:///` with empty path) causing state loss between invocations
- **Property (P)**: Data written to mirror.db in one process invocation SHALL be readable in a subsequent invocation from the same path
- **Preservation**: Existing behaviors of `mirror verify`, `mirror status`, `mirror clean`, and first-run sync SHALL remain unchanged
- **`_CliDatabaseProvider`**: The current in-memory database provider in `src/debcraft/cli/mirror.py` that creates a fresh SQLite on every invocation
- **`resolve_xdg_path("database")`**: Returns the platform-specific path for persistent database storage (e.g., `~/.local/share/debcraft/` on Linux)
- **`check_conditional`**: Method on `DownloadCoordinator` that sends HTTP HEAD with If-None-Match/If-Modified-Since headers

## Bug Details

### Bug Condition

The bug manifests when `mirror sync` is run more than once. The `_CliDatabaseProvider` creates a new in-memory SQLite engine on every instantiation, so all `RepositoryFile` records from previous syncs are lost. This causes `_get_local_checksums()` to always return an empty dict, which causes `FileComparator` to mark all files as "download" (reason: "file not cached").

A secondary condition compounds this: `_stage_release()` calls `check_conditional(inrelease_url)` without passing `etag` or `last_modified` parameters, so the HTTP request has no conditional headers and the server cannot respond with 304.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type MirrorSyncInvocation
  OUTPUT: boolean

  RETURN input.invocationCount > 1
         AND input.previousSyncStoredRecords > 0
         AND databaseProvider.engineUrl = "sqlite+aiosqlite:///"
END FUNCTION
```

### Examples

- First run downloads 100 files and stores records in-memory → records discarded at process exit → second run re-downloads all 100 files
- InRelease file already on disk, `check_conditional` called without headers → server returns 200 instead of 304 → file re-downloaded and re-parsed
- `_get_local_checksums()` returns `{}` on second run → `FileComparator` produces 100 "download" decisions with reason "file not cached"
- Third run: same behavior, all files re-downloaded again regardless of cache state

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- `mirror verify` reads from `resolve_xdg_path("database") / "mirror.db"` and verifies checksums — this must continue unchanged
- `mirror status` reads from the same persistent database — must continue unchanged
- `mirror clean` queries referenced paths from the same database — must continue unchanged
- First-run behavior (no prior database) must download all files and create the schema
- Files with changed remote checksums must still be re-downloaded

**Scope:**
All inputs where `invocationCount = 1` (first run) or where remote checksums have changed should be completely unaffected by this fix. This includes:
- First-ever sync with no existing mirror.db
- Syncs where remote files have been updated (checksum differs)
- All `mirror verify`, `mirror status`, `mirror clean` commands
- Mouse/keyboard interaction with CLI output

## Hypothesized Root Cause

Based on the bug description, the most likely issues are:

1. **In-memory database URL**: `_CliDatabaseProvider` uses `create_async_engine("sqlite+aiosqlite:///")` — the empty path after `///` means SQLite creates an anonymous in-memory database that exists only for the process lifetime.

2. **No directory creation**: Even if the URL were changed to a file path, the parent directory might not exist on first use. The provider needs to `mkdir -p` the database directory.

3. **Missing conditional request headers**: `_stage_release()` at line ~270 calls `check_conditional(inrelease_url)` without extracting stored etag/last_modified from the database, passing them as `None`.

4. **No etag/last_modified storage**: The `RepositoryFile` model doesn't currently store HTTP response headers (etag, last_modified) that would be needed for conditional requests.

## Correctness Properties

Property 1: Bug Condition - Mirror State Persists Between Invocations

_For any_ mirror sync invocation where a previous sync stored `RepositoryFile` records to the database, the database provider SHALL use a persistent file-backed SQLite database at `resolve_xdg_path("database") / "mirror.db"`, and `_get_local_checksums()` SHALL return a non-empty dictionary containing the previously stored records.

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation - First-Run and Changed-Checksum Behavior

_For any_ input where the bug condition does NOT hold (first invocation with no prior database, or remote checksums differ from stored values), the fixed code SHALL produce the same result as the original code: downloading all necessary files and correctly handling checksum mismatches.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `src/debcraft/cli/mirror.py`

**Class**: `_CliDatabaseProvider`

**Specific Changes**:
1. **Replace in-memory engine with file-backed engine**: Change from `create_async_engine("sqlite+aiosqlite:///")` to `create_async_engine(f"sqlite+aiosqlite:///{db_path}")` where `db_path = resolve_xdg_path("database") / "mirror.db"`

2. **Create directory on initialization**: Add `db_path.parent.mkdir(parents=True, exist_ok=True)` before creating the engine to ensure the XDG data directory exists

3. **Use `create_async_engine_for` helper**: Leverage the existing `infrastructure/database/session.py` helper which configures WAL mode, foreign keys, and proper pooling — or replicate its PRAGMA settings

4. **Add etag/last_modified columns to RepositoryFile model**: Add optional `etag: Mapped[str | None]` and `last_modified: Mapped[str | None]` columns to support conditional requests

5. **Pass stored headers in `_stage_release()`**: Query the database for the InRelease file's stored etag/last_modified and pass them to `check_conditional(url, etag=..., last_modified=...)`

6. **Store response headers after download**: After downloading an InRelease file, persist the response's ETag and Last-Modified headers in the `RepositoryFile` record

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm that `_CliDatabaseProvider` loses state between instantiations.

**Test Plan**: Write a property-based test that creates a `_CliDatabaseProvider`, writes a `RepositoryFile` record, disposes the provider, creates a new instance, and asserts the record is retrievable. Run on UNFIXED code to observe failure.

**Test Cases**:
1. **Persistence test**: Create provider → write record → dispose → create new provider → query record (will fail on unfixed code because in-memory DB is destroyed)
2. **Checksums test**: Store a file with known sha256 → new provider instance → query via `_get_local_checksums` pattern → expect empty result on unfixed code

**Expected Counterexamples**:
- After creating a second `_CliDatabaseProvider` instance, any query returns zero rows
- `_get_local_checksums()` returns `{}` for paths that were stored moments earlier

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  provider1 := createPersistentProvider(tmp_path)
  session1 := provider1.get_session("mirror")
  write RepositoryFile(url, sha256, state=VERIFIED) to session1
  commit and dispose provider1

  provider2 := createPersistentProvider(tmp_path)
  session2 := provider2.get_session("mirror")
  result := query RepositoryFile WHERE url = input.url
  ASSERT result is not None
  ASSERT result.sha256 = input.sha256
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT createPersistentProvider(empty_dir).get_session("mirror") succeeds
  ASSERT schema tables (repository_files, sync_sessions) are created
  ASSERT FileComparator.compute_sync_decisions(entries, {}) produces all "download" decisions
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many random RepositoryFile records to verify first-run behavior
- It catches edge cases with unusual paths or checksums
- It provides strong guarantees that the schema creation and empty-state behavior is unchanged

**Test Plan**: Observe behavior on UNFIXED code for first-run scenarios (empty database), then write property-based tests verifying that behavior is preserved after the fix.

**Test Cases**:
1. **First-run schema creation**: New provider with non-existent DB path → tables are created → session works
2. **Empty checksums on first run**: New provider → `_get_local_checksums` equivalent query returns `{}`
3. **FileComparator still works**: Given entries and empty local_checksums → all decisions are "download"

### Unit Tests

- Test that `_CliDatabaseProvider` (fixed) creates the directory if it doesn't exist
- Test that the database file is created at the expected XDG path
- Test that schema (repository_files, sync_sessions) is created on first use
- Test that conditional request headers are passed correctly

### Property-Based Tests

- Generate random RepositoryFile records, store them, verify persistence across provider instances
- Generate random FileEntry lists with empty local_checksums, verify all produce "download" decisions (preservation)
- Generate random etag/last_modified strings, verify they're stored and retrievable

### Integration Tests

- Full `mirror sync` with mocked HTTP that returns 304 when correct headers are sent
- Two sequential `mirror sync` calls verifying second run skips cached files
- `mirror verify` after a persistent sync verifying it reads from the same DB
