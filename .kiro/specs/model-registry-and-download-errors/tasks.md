# Implementation Plan

## Overview
Fix two bugs: (1) SQLAlchemy mapper fails to resolve `ScanSession` relationship because `models/__init__.py` is empty, and (2) artifact download failures lack HTTP status code and detailed error context in logs.

## Tasks

- [x] 1. Write bug condition exploration test for SQLAlchemy mapper resolution
  - **Property 1: Bug Condition** - SQLAlchemy Mapper Resolution Failure
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the mapper resolution bug exists
  - **Scoped PBT Approach**: Import `RepositorySnapshot` via the models package (without the `import scan` workaround) and attempt to configure the mapper
  - Test that importing `debcraft.infrastructure.models` and then accessing `RepositorySnapshot.scan_sessions` relationship resolves without `InvalidRequestError`
  - Use a fresh SQLAlchemy registry or `Base.registry.configure()` to trigger mapper configuration
  - The bug condition: `models/__init__.py` is empty so `scan.py` never loads → `"ScanSession"` string reference unresolved
  - Run test on UNFIXED code — expect FAILURE (`InvalidRequestError`)
  - Document counterexample: `InvalidRequestError: expression 'ScanSession' failed to locate a name`
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2_

- [x] 2. Write bug condition exploration test for download error context
  - **Property 2: Bug Condition** - Download Error Context Propagation
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the missing status_code on DownloadResult
  - **Scoped PBT Approach**: Mock `aiohttp` to return HTTP 503 for all attempts, then verify `DownloadResult` has a `status_code` field equal to 503
  - Test that after retries exhaust with `HttpServerError(status_code=503)`, the returned `DownloadResult` has `status_code=503`
  - Use Hypothesis to generate status codes in the 500-599 range and verify propagation
  - The bug condition: `DownloadResult` has no `status_code` field, so assertion will fail with `AttributeError` or value mismatch
  - Run test on UNFIXED code — expect FAILURE (field doesn't exist or is missing)
  - Document counterexample: `AttributeError: 'DownloadResult' object has no attribute 'status_code'` or `AssertionError: None != 503`
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.3, 1.4_

- [x] 3. Write preservation property tests (BEFORE implementing fix)
  - **Property 3: Preservation** - Model Import Compatibility and Download Behavior
  - **IMPORTANT**: Follow observation-first methodology
  - Observe: `from debcraft.infrastructure.models.metadata import RepositorySnapshot` works on unfixed code
  - Observe: `from debcraft.infrastructure.models.scan import ScanSession` works on unfixed code
  - Observe: Successful downloads return `DownloadResult(success=True, sha256_verified=True, bytes_transferred=N, error=None, retry_count=0)`
  - Observe: 4xx errors immediately return `DownloadResult(success=False)` without retrying
  - Write property-based tests with Hypothesis:
    - Generate random model import sequences and verify they don't raise `ImportError`
    - Generate random successful download scenarios (mocked) and verify `DownloadResult` fields unchanged
    - Generate random 4xx status codes (400-499) and verify immediate fail without retry
  - Verify tests PASS on UNFIXED code (confirms baseline behavior to preserve)
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 4. Fix Bug 1: Update models `__init__.py` to register all model modules

  - [x] 4.1 Implement the models package fix
    - Replace the empty `__init__.py` at `src/debcraft/infrastructure/models/__init__.py` with:
      ```python
      """SQLAlchemy ORM entity models."""

      from debcraft.infrastructure.models import cache, metadata, mirror, scan  # noqa: F401
      ```
    - This ensures that when any code imports from the models package, all modules are loaded and SQLAlchemy's mapper can resolve all string-based relationship references
    - _Bug_Condition: isBugCondition_Bug1(input) where models/__init__.py is empty_
    - _Expected_Behavior: all model modules loaded on package import, mapper resolves "ScanSession"_
    - _Preservation: existing direct imports continue to work_
    - _Requirements: 2.1, 2.2, 3.1, 3.2_

  - [x] 4.2 Verify mapper exploration test now passes
    - **Property 1: Expected Behavior** - SQLAlchemy Mapper Resolution
    - **IMPORTANT**: Re-run the SAME test from task 1 — do NOT write a new test
    - The test from task 1 encodes the expected behavior (mapper resolves without error)
    - When this test passes, it confirms the mapper resolution bug is fixed
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2_

  - [x] 4.3 Verify preservation tests still pass for Bug 1
    - **Property 3: Preservation** - Model Import Compatibility
    - **IMPORTANT**: Re-run the SAME tests from task 3 (model import portion) — do NOT write new tests
    - Run preservation property tests from step 3
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions in model imports)

- [x] 5. Fix Bug 2: Add status_code to DownloadResult and enhance logging

  - [x] 5.1 Add `status_code` field to `DownloadResult`
    - In `src/debcraft/domain/mirror/values.py`, add `status_code: int | None = None` field to the `DownloadResult` dataclass after `retry_count`
    - _Requirements: 2.3, 2.4_

  - [x] 5.2 Update `download_file` to propagate status_code
    - In `src/debcraft/infrastructure/mirror/download.py`, after the retry loop exhausts:
      - Extract `status_code` from `last_error` if it's an instance of `HttpServerError` or `HttpClientError` (both have `.status_code` attribute)
      - Include `status_code` in the ERROR log extra dict
      - Pass `status_code=status_code` to the returned `DownloadResult`
    - _Bug_Condition: retries exhausted AND last_error has status_code attribute_
    - _Expected_Behavior: DownloadResult.status_code = last_error.status_code_
    - _Requirements: 2.3_

  - [x] 5.3 Update engine logging for failed downloads
    - In `src/debcraft/infrastructure/mirror/engine.py`, in the failure branch of `_stage_download_artifacts`:
      - Add `status_code=result.status_code` to the "Artifact download failed" log
      - Add `retry_count=result.retry_count` to the log
    - _Requirements: 2.4_

  - [x] 5.4 Add DEBUG log for successful downloads in engine
    - In `src/debcraft/infrastructure/mirror/engine.py`, in the success branch:
      - Derive package name from `entry.relative_path` (e.g., extract filename or deb package name from path like `pool/main/l/libfoo/libfoo_1.0_amd64.deb`)
      - Log at DEBUG level: "Artifact downloaded successfully" with `url`, `package_name`, `bytes_transferred`
    - _Requirements: 2.5_

  - [x] 5.5 Verify download error context exploration test now passes
    - **Property 2: Expected Behavior** - Download Error Context Propagation
    - **IMPORTANT**: Re-run the SAME test from task 2 — do NOT write a new test
    - The test from task 2 encodes the expected behavior (status_code populated on DownloadResult)
    - When this test passes, it confirms the download error context bug is fixed
    - Run bug condition exploration test from step 2
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.3, 2.4_

  - [x] 5.6 Verify preservation tests still pass for Bug 2
    - **Property 4: Preservation** - Download Success and 4xx Behavior
    - **IMPORTANT**: Re-run the SAME tests from task 3 (download portion) — do NOT write new tests
    - Run preservation property tests from step 3
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions in download behavior)
    - _Requirements: 3.3, 3.4, 3.5_

- [x] 6. Checkpoint — Ensure all tests pass
  - Run the full test suite to confirm no regressions
  - Verify exploration tests (tasks 1 and 2) now PASS
  - Verify preservation tests (task 3) still PASS
  - Verify existing project tests still pass (especially `tests/properties/infrastructure/`)
  - Ask the user if questions arise

## Task Dependency Graph

```json
{
  "waves": [
    {"tasks": ["1", "2", "3"]},
    {"tasks": ["4.1", "5.1"]},
    {"tasks": ["4.2", "4.3", "5.2", "5.4"]},
    {"tasks": ["5.3"]},
    {"tasks": ["5.5", "5.6"]},
    {"tasks": ["6"]}
  ]
}
```

## Notes

- Exploration tests (tasks 1-2) are expected to FAIL on unfixed code — this confirms the bug exists
- Preservation tests (task 3) must PASS on unfixed code — confirms baseline behavior
- After fixes (tasks 4-5), all exploration and preservation tests should PASS
