# Implementation Plan

## Overview

Fix `get_verified_files()` in `SqlAlchemyMirrorFileRepository` which only queries files in `RepositoryFileState.VERIFIED` state, causing previously-indexed metadata files (Packages.gz, Sources, Contents, Release) to become invisible to the indexer after their first successful index run. The fix expands the state filter to include both VERIFIED and INDEXED states, allowing the indexer's existing `_should_skip()` logic to correctly evaluate re-parse needs.

## Tasks

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - INDEXED metadata files invisible to get_verified_files()
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate INDEXED files are excluded from query results
  - **Scoped PBT Approach**: Scope the property to the concrete failing case: files with state=INDEXED should be returned by `get_verified_files()` but currently are not
  - Test file: `tests/unit/infrastructure/test_mirror_file_repository.py`
  - Use Hypothesis to generate random file attributes (url containing metadata keywords, sha256 as hex strings, size_bytes as positive integers) with state fixed to `RepositoryFileState.INDEXED`
  - Property: for all generated INDEXED files seeded into the mirror DB, `get_verified_files()` returns them in the result set
  - Use the existing in-memory SQLite test fixtures (`_create_mirror_factory`, `_create_metadata_factory`, `_seed_mirror_file`)
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (Hypothesis finds counterexample: any INDEXED file is missing from results, confirming the bug exists because the SQLAlchemy filter only matches `RepositoryFileState.VERIFIED`)
  - Document counterexamples found to understand root cause
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 2.2_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Non-actionable file states excluded from query results
  - **IMPORTANT**: Follow observation-first methodology
  - Test file: `tests/unit/infrastructure/test_mirror_file_repository.py`
  - Observe on UNFIXED code: files with state in {DISCOVERED, QUEUED, DOWNLOADING, DOWNLOADED, FAILED} are never returned by `get_verified_files()`
  - Observe on UNFIXED code: files with state=VERIFIED are always returned by `get_verified_files()`
  - Use Hypothesis to generate random file attributes (url via `st.text`, sha256 via `st.text(alphabet=string.hexdigits, min_size=64, max_size=64)`, size_bytes via `st.integers(min_value=1, max_value=10**9)`) with state drawn from `st.sampled_from([DISCOVERED, QUEUED, DOWNLOADING, DOWNLOADED, FAILED])`
  - Property 1: for all generated files with non-actionable states, `get_verified_files()` never includes them in results
  - Property 2: for all generated VERIFIED files, `get_verified_files()` always includes them in results (existing behavior preserved)
  - Verify tests PASS on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (confirms baseline behavior to preserve — only VERIFIED files are returned, non-actionable states excluded)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.5_

- [x] 3. Fix for INDEXED metadata files invisible to indexer

  - [x] 3.1 Implement the fix in `get_verified_files()`
    - In `src/debcraft/infrastructure/indexer/mirror_file_repository.py`, method `get_verified_files()`
    - Change `RepositoryFile.state == RepositoryFileState.VERIFIED` to `RepositoryFile.state.in_([RepositoryFileState.VERIFIED, RepositoryFileState.INDEXED])`
    - Update the method docstring to state it returns files in both VERIFIED and INDEXED states (files eligible for indexing evaluation)
    - _Bug_Condition: isBugCondition(input) where all metadata files have state == INDEXED and get_verified_files() returns no metadata files_
    - _Expected_Behavior: get_verified_files() returns files in both VERIFIED and INDEXED states so the indexer can evaluate them via _should_skip()_
    - _Preservation: Files in DISCOVERED, QUEUED, DOWNLOADING, DOWNLOADED, FAILED states must NOT be returned_
    - _Requirements: 1.1, 1.2, 2.1, 2.2, 3.1, 3.2, 3.5_

  - [x] 3.2 Update protocol docstring in `src/debcraft/domain/indexer/ports.py`
    - Update the `MirrorFileRepository.get_verified_files()` docstring to reflect the new contract: returns files in VERIFIED or INDEXED state for indexing evaluation
    - _Requirements: 2.2_

  - [x] 3.3 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - INDEXED metadata files returned by get_verified_files()
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior: INDEXED files must appear in query results
    - When this test passes, it confirms the expected behavior is satisfied
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed — INDEXED files now returned)
    - _Requirements: 2.1, 2.2_

  - [x] 3.4 Verify preservation tests still pass
    - **Property 2: Preservation** - Non-actionable file states still excluded
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions — non-actionable states still excluded, VERIFIED files still returned)
    - Confirm all tests still pass after fix (no regressions)

  - [x] 3.5 Update existing unit test expectations
    - In `tests/unit/infrastructure/test_mirror_file_repository.py`, update `test_excludes_non_verified_files` which currently asserts that INDEXED files are excluded — this assertion must change to expect INDEXED files ARE returned (since they are now included in the query)
    - Verify all existing tests in `TestGetVerifiedFiles`, `TestGetIndexingRecord`, and `TestMarkIndexed` pass with the fix
    - _Requirements: 2.2, 3.2_

- [x] 4. Checkpoint - Ensure all tests pass
  - Run full test suite: `pytest tests/unit/infrastructure/test_mirror_file_repository.py -v`
  - Ensure bug condition property test passes (Property 1 satisfied)
  - Ensure preservation property tests pass (Property 2 satisfied)
  - Ensure all existing unit tests pass (with updated expectations for INDEXED state)
  - Ensure no regressions in other test files that may reference `get_verified_files()`
  - Ensure all tests pass, ask the user if questions arise.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2"] },
    { "id": 1, "tasks": ["3.1", "3.2"] },
    { "id": 2, "tasks": ["3.3", "3.4", "3.5"] },
    { "id": 3, "tasks": ["4"] }
  ]
}
```

## Notes

- The fix is a single-line query change from `state == VERIFIED` to `state.in_([VERIFIED, INDEXED])` in `get_verified_files()`.
- The indexer's existing `_should_skip()` logic already handles INDEXED files correctly — it checks SHA256 + parser version match to determine if re-parsing is needed.
- Property-based tests use Hypothesis to generate file attributes and states for stronger guarantees.
- The exploration test (task 1) is expected to FAIL before the fix and PASS after — this is the core of the bug condition methodology.
- Preservation tests (task 2) must PASS both before and after the fix to ensure no regressions.
- The existing unit test `test_excludes_non_verified_files` must be updated since it currently asserts INDEXED files are excluded, which will no longer be correct after the fix.
