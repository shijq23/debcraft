# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Mirror State Persists Between Invocations
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the in-memory database doesn't persist state
  - **Scoped PBT Approach**: Create a `_CliDatabaseProvider` instance, write a `RepositoryFile` record via a session, dispose the provider, create a new `_CliDatabaseProvider` instance, and assert the record is retrievable
  - Test file: `tests/properties/infrastructure/test_mirror_persistence_properties.py`
  - Use Hypothesis to generate random `RepositoryFile` data (url, sha256, size_bytes)
  - Create first provider → get session → add RepositoryFile with state=VERIFIED → commit → close session → dispose provider
  - Create second provider → get session → query `SELECT * FROM repository_files WHERE url = generated_url`
  - Assert the query returns the stored record with matching sha256
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS because in-memory DB is discarded between provider instances
  - Document counterexamples found (e.g., "RepositoryFile(url='...', sha256='...') stored in first instance not found in second instance")
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - First-Run and Changed-Checksum Behavior
  - **IMPORTANT**: Follow observation-first methodology
  - Observe: `_CliDatabaseProvider()` creates schema successfully on first use (tables exist)
  - Observe: querying an empty database returns no records (empty checksums map)
  - Observe: `FileComparator.compute_sync_decisions(entries, {})` returns all "download" decisions
  - Test file: `tests/properties/infrastructure/test_mirror_persistence_preservation.py`
  - Write property-based test 1: For any new provider instance with a fresh DB path, `get_session("mirror")` succeeds and tables `repository_files` and `sync_sessions` exist
  - Write property-based test 2: For any list of generated `FileEntry` objects and an empty `local_checksums` dict, `FileComparator.compute_sync_decisions()` produces all decisions with `action="download"`
  - Write property-based test 3: For any `RepositoryFile` with a sha256 that differs from a remote `FileEntry`'s sha256 at the same path, `FileComparator` produces `action="download"` with reason `"checksum differs"`
  - Verify all tests pass on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 3. Implement persistent database provider

  - [x] 3.1 Replace `_CliDatabaseProvider` with persistent storage
    - In `src/debcraft/cli/mirror.py`, modify `_CliDatabaseProvider.__init__`:
      - Compute `db_path = resolve_xdg_path("database") / "mirror.db"`
      - Create directory: `db_path.parent.mkdir(parents=True, exist_ok=True)`
      - Replace `create_async_engine("sqlite+aiosqlite:///")` with `create_async_engine(f"sqlite+aiosqlite:///{db_path}")` or use `create_async_engine_for(db_path)` from `infrastructure/database/session.py`
      - Keep `_ensure_schema()` for first-use table creation
    - _Bug_Condition: isBugCondition(input) where databaseProvider.engineUrl = "sqlite+aiosqlite:///" (empty path)_
    - _Expected_Behavior: Database records persist between process invocations via file-backed SQLite_
    - _Preservation: First-run schema creation, verify/status/clean commands continue reading from same path_
    - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.3, 3.4, 3.5_

  - [x] 3.2 Add etag/last_modified columns to RepositoryFile model
    - In `src/debcraft/infrastructure/models/mirror.py`, add to `RepositoryFile`:
      - `etag: Mapped[str | None] = mapped_column(String(256), nullable=True)`
      - `last_modified: Mapped[str | None] = mapped_column(String(128), nullable=True)`
    - _Requirements: 2.4_

  - [x] 3.3 Pass stored headers in `_stage_release()` conditional request
    - In `src/debcraft/infrastructure/mirror/engine.py`, modify `_stage_release()`:
      - Before calling `check_conditional(inrelease_url)`, query the database for an existing `RepositoryFile` with that URL
      - Extract `etag` and `last_modified` from the stored record
      - Pass them: `check_conditional(inrelease_url, etag=stored_etag, last_modified=stored_last_modified)`
    - _Requirements: 2.4_

  - [x] 3.4 Store response headers after InRelease download
    - After successfully downloading an InRelease file, store the HTTP response's ETag and Last-Modified headers in the `RepositoryFile` record via `_upsert_repository_file()`
    - Update `_upsert_repository_file` signature to accept optional `etag` and `last_modified` parameters
    - _Requirements: 2.4_

  - [x] 3.5 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Mirror State Persists Between Invocations
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior
    - When this test passes, it confirms the expected behavior is satisfied
    - Run: `uv run pytest tests/properties/infrastructure/test_mirror_persistence_properties.py -v`
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed — records persist across provider instances)
    - _Requirements: 2.1, 2.2_

  - [x] 3.6 Verify preservation tests still pass
    - **Property 2: Preservation** - First-Run and Changed-Checksum Behavior
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run: `uv run pytest tests/properties/infrastructure/test_mirror_persistence_preservation.py -v`
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions — first-run behavior and comparator logic unchanged)
    - Confirm all tests still pass after fix (no regressions)

- [x] 4. Checkpoint - Ensure all tests pass
  - Run full test suite: `uv run pytest tests/ -v --tb=short`
  - Ensure all property tests pass (both exploration and preservation)
  - Ensure existing mirror tests still pass (`tests/properties/domain/test_mirror_comparator_properties.py`, etc.)
  - Ensure no regressions in other test modules
  - Ask the user if questions arise
