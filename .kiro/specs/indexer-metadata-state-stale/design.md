# Indexer Metadata State Stale Bugfix Design

## Overview

After a successful index run, `mark_indexed()` transitions metadata files (Packages.gz, Sources, Contents, Release) from VERIFIED → INDEXED state. On subsequent mirror syncs, when `_stage_indexes()` determines an index file is unchanged (already cached), it skips re-downloading and does NOT reset the file's state. Since `get_verified_files()` only queries files in VERIFIED state, the indexer never sees the metadata files again, resulting in "0 packages indexed" on re-runs.

The fix modifies `get_verified_files()` to query files in both VERIFIED and INDEXED states. The indexer's existing `_should_skip()` logic already handles INDEXED files correctly — it checks the indexing record's SHA256 and parser version to determine if re-parsing is needed. This approach is minimal, localized, and avoids adding write operations to the mirror sync hot path.

## Glossary

- **Bug_Condition (C)**: The condition where `get_verified_files()` is called for a repository whose metadata files are all in INDEXED state, causing it to return an empty set of metadata files
- **Property (P)**: `get_verified_files()` returns metadata files regardless of whether they are in VERIFIED or INDEXED state, enabling the indexer to evaluate them for incremental skip or re-parse
- **Preservation**: The incremental skip logic (`_should_skip`), mouse-click behavior of `.deb` artifact handling, state transitions for newly downloaded files, and re-parse on parser version bump must all remain unchanged
- **get_verified_files()**: Method in `SqlAlchemyMirrorFileRepository` (`src/debcraft/infrastructure/indexer/mirror_file_repository.py`) that queries the mirror database for files eligible for indexing
- **_should_skip()**: Method in `IndexerService` (`src/debcraft/domain/indexer/service.py`) that determines whether to skip re-parsing a file based on its indexing record's SHA256 and parser version
- **RepositoryFileState**: Enum in `src/debcraft/infrastructure/models/mirror.py` with states: DISCOVERED, QUEUED, DOWNLOADING, DOWNLOADED, VERIFIED, INDEXED, FAILED
- **IndexingRecord**: Model in metadata.db tracking the parser version and SHA256 used when a file was last indexed

## Bug Details

### Bug Condition

The bug manifests when the indexer is invoked on a repository where all metadata files have already been indexed once (state = INDEXED) and the upstream content has not changed. The `get_verified_files()` query only matches `state == VERIFIED`, so previously-indexed metadata files are invisible to the indexer.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type { repository_name: string, file_states: Map<FileId, RepositoryFileState> }
  OUTPUT: boolean

  metadataFiles := filter(input.file_states, f => isMetadataFile(f.url))

  RETURN metadataFiles.size > 0
         AND ALL f IN metadataFiles: f.state == INDEXED
         AND get_verified_files(input.repository_name) returns NO metadata files
END FUNCTION
```

### Examples

- **Example 1**: Repository "debian-bookworm" has Packages.gz in INDEXED state after first successful run. Second `debcraft index` call returns 0 packages because `get_verified_files()` returns empty for metadata files. Expected: indexer should see Packages.gz, check `_should_skip()`, and report "1 file skipped".
- **Example 2**: Repository has Sources.gz (INDEXED) and a new .deb file (VERIFIED). Indexer sees only the .deb, skips it as "unknown" file type, reports 0 packages. Expected: indexer should also see Sources.gz, skip it via `_should_skip()`, and correctly report skipped count.
- **Example 3**: Repository has Release (INDEXED), Packages.gz (INDEXED), Contents (INDEXED). All invisible to indexer. Expected: all three should appear in query results and be evaluated by `_should_skip()`.
- **Edge case**: Parser version is bumped. Metadata file is in INDEXED state with old parser version. Expected: `get_verified_files()` returns the file, `_should_skip()` returns False (version mismatch), and the file is re-parsed.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Newly downloaded files via `_upsert_repository_file()` must continue to be persisted with state=VERIFIED
- The `_should_skip()` logic must continue to skip files whose SHA256 and parser version match the indexing record
- The `_should_skip()` logic must continue to re-parse files when parser version increases
- `.deb` artifact files in VERIFIED state must continue to be skipped as "unknown" file type
- `mark_indexed()` must continue to transition files from their current state to INDEXED and upsert the indexing record
- Files in FAILED or DOWNLOADING states must NOT be returned by the query
- The mirror engine's `_stage_indexes()` download-or-skip logic must remain unchanged

**Scope:**
All inputs that do NOT involve querying files for indexing should be completely unaffected by this fix. This includes:
- Mirror sync download decisions (computed by the comparator)
- Artifact staging and download coordination
- Database schema and model definitions
- CLI argument handling and progress display
- Event publishing lifecycle

## Hypothesized Root Cause

Based on the bug description, the root cause is:

1. **Overly restrictive query filter**: `get_verified_files()` uses `RepositoryFile.state == RepositoryFileState.VERIFIED` as its only state filter. After `mark_indexed()` transitions files to INDEXED, they no longer match this filter. The method name implies it only returns VERIFIED files, but the indexer's actual need is "files eligible for indexing evaluation" — which includes both VERIFIED (new/changed) and INDEXED (previously processed, may need re-evaluation).

2. **Missing state reset in mirror engine**: When `_stage_indexes()` skips a cached index file (checksum unchanged), it does not call `_upsert_repository_file()` to reset the state back to VERIFIED. This is the alternative fix location (Option A), but Option B is preferred because the indexer already has `_should_skip()` to handle INDEXED files efficiently.

3. **Semantic mismatch**: The state model treats INDEXED as a terminal state from the indexer's perspective, but the business requirement is that metadata files should be re-evaluated on every indexer run (with `_should_skip()` providing the actual short-circuit).

## Correctness Properties

Property 1: Bug Condition - Metadata files in INDEXED state are returned by query

_For any_ repository where metadata files exist in INDEXED state, the modified `get_verified_files()` SHALL return those files in its result set, making them available for the indexer to evaluate via `_should_skip()`.

**Validates: Requirements 2.1, 2.2**

Property 2: Preservation - Non-metadata file handling unchanged

_For any_ input where files are in states other than VERIFIED or INDEXED (FAILED, DOWNLOADING, QUEUED, DISCOVERED), the modified `get_verified_files()` SHALL NOT return those files, preserving the existing behavior that only actionable files are presented to the indexer.

**Validates: Requirements 3.1, 3.2, 3.5**

Property 3: Preservation - Incremental skip logic unchanged

_For any_ file returned by `get_verified_files()` where an indexing record exists with matching SHA256 and parser version, `_should_skip()` SHALL return True, preserving the incremental indexing optimization.

**Validates: Requirements 3.3, 3.4**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `src/debcraft/infrastructure/indexer/mirror_file_repository.py`

**Method**: `get_verified_files()`

**Specific Changes**:
1. **Modify state filter**: Change the SQLAlchemy query from filtering on `RepositoryFileState.VERIFIED` only to filtering on `RepositoryFileState.in_([VERIFIED, INDEXED])`. This single-line change makes previously-indexed metadata files visible to the indexer again.

2. **Update method docstring**: Clarify that the method returns files in both VERIFIED and INDEXED states, since the method name `get_verified_files` is now slightly misleading (or rename to `get_indexable_files` if preferred).

**File**: `src/debcraft/domain/indexer/ports.py`

**Protocol**: `MirrorFileRepository`

**Specific Changes**:
3. **Update protocol docstring**: Update the `get_verified_files()` docstring in the protocol to reflect that it now returns files in both VERIFIED and INDEXED states.

**No changes needed to**:
- `IndexerService._should_skip()` — already handles INDEXED files correctly
- `IndexerService.index_repository()` — already iterates all returned files
- Mirror engine `_stage_indexes()` — no coupling to indexer state query
- `mark_indexed()` — transition to INDEXED is still correct

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that set up a repository with metadata files in INDEXED state and call `get_verified_files()`. Run these tests on the UNFIXED code to observe failures demonstrating that INDEXED files are not returned.

**Test Cases**:
1. **Single INDEXED metadata file**: Create a RepositoryFile with state=INDEXED and call `get_verified_files()` — assert it is NOT returned (will fail assertion on unfixed code, confirming bug)
2. **Mixed states**: Create files with VERIFIED and INDEXED states, call `get_verified_files()` — assert only VERIFIED files returned (confirms partial visibility)
3. **All INDEXED repository**: All metadata files in INDEXED state — assert `get_verified_files()` returns empty list (confirms total invisibility)
4. **Post-mark_indexed transition**: Create VERIFIED file, call `mark_indexed()`, then `get_verified_files()` — assert file disappears (confirms state transition is the trigger)

**Expected Counterexamples**:
- `get_verified_files()` returns an empty list when all metadata files are in INDEXED state
- Possible cause: SQLAlchemy filter only matches `RepositoryFileState.VERIFIED`

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := get_verified_files_fixed(input.repository_name)
  ASSERT all_indexed_metadata_files IN result
  ASSERT len(result) >= count_of_indexed_metadata_files
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT get_verified_files_original(input) == get_verified_files_fixed(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many combinations of file states and repository configurations automatically
- It catches edge cases around state boundaries (FAILED, DOWNLOADING, etc. must NOT appear)
- It provides strong guarantees that non-VERIFIED/non-INDEXED files remain excluded

**Test Plan**: Observe behavior on UNFIXED code first for files in non-INDEXED states, then write property-based tests capturing that behavior.

**Test Cases**:
1. **FAILED files excluded**: Generate random files with state=FAILED, verify they are never returned by the query (before and after fix)
2. **DOWNLOADING files excluded**: Generate random files with state=DOWNLOADING, verify exclusion
3. **DISCOVERED/QUEUED files excluded**: Generate random files in early-lifecycle states, verify exclusion
4. **VERIFIED files still returned**: Generate random VERIFIED files, verify they continue to be returned after the fix
5. **_should_skip correctness**: Generate random indexing records and file SHA256s, verify `_should_skip()` returns True only when both SHA256 and parser version match

### Unit Tests

- Test `get_verified_files()` returns files in VERIFIED state (existing behavior preserved)
- Test `get_verified_files()` returns files in INDEXED state (new behavior)
- Test `get_verified_files()` excludes files in FAILED, DOWNLOADING, QUEUED, DISCOVERED states
- Test `get_verified_files()` with `repository_name` filter correctly filters by URL
- Test end-to-end: mark_indexed() then get_verified_files() still returns the file

### Property-Based Tests

- Generate random `RepositoryFileState` values and verify only VERIFIED and INDEXED files appear in results
- Generate random SHA256 / parser_version combinations and verify `_should_skip()` correctness is unchanged
- Generate random repository configurations with mixed file states and verify correct filtering

### Integration Tests

- Test full indexer workflow: first run indexes files, second run correctly skips them via `_should_skip()` and reports accurate skip count
- Test parser version bump scenario: file in INDEXED state with old parser version gets re-parsed after fix
- Test mixed scenario: new VERIFIED .deb file + INDEXED Packages.gz — both visible to indexer, .deb skipped as unknown, Packages.gz skipped via `_should_skip()`
