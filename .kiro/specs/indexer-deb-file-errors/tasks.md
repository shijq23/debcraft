# Implementation Plan

## Overview

Fix two related bugs in the indexer: (1) `IndexerService` processes `.deb` files it shouldn't by calling `file_reader.read_file()` before checking if the file type is "unknown", causing UTF-8 decode errors on binary content; (2) `_StructuredFormatter` swallows exception tracebacks by never calling `self.formatException()`, making errors undiagnosable. The fix moves the "unknown" type guard before the read call and adds traceback formatting to the custom formatter.

## Tasks

- [x] 1. Write bug condition exploration tests
  - **Property 1: Bug Condition** - Unknown File Types Processed and Tracebacks Swallowed
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bugs exist
  - **DO NOT attempt to fix the tests or the code when they fail**
  - **NOTE**: These tests encode the expected behavior - they will validate the fix when they pass after implementation
  - **GOAL**: Surface counterexamples that demonstrate both bugs exist
  - **Bug A - Indexer processes unknown files**:
    - Create a `RepositoryFileInfo` with a `.deb` URL (e.g., `http://repo/pool/main/h/hello/hello_2.10-3_amd64.deb`)
    - `_infer_file_type()` returns "unknown" for this URL
    - Inject a mock `file_reader` that records whether `read_file()` is called
    - Call `index_repository()` with this file in the verified files list
    - Assert that `file_reader.read_file()` was NOT called for the unknown file
    - On UNFIXED code: test FAILS because `read_file()` IS called before the type dispatch
  - **Bug B - Formatter swallows tracebacks**:
    - Create a log record with `exc_info` set to a valid exception tuple `(ValueError, ValueError("test error"), traceback)`
    - Format the record using `_StructuredFormatter().format(record)`
    - Assert that the formatted output CONTAINS the traceback text (e.g., "Traceback" and "ValueError: test error")
    - On UNFIXED code: test FAILS because `format()` never calls `self.formatException()`
  - Test file: `tests/test_indexer_deb_file_errors_bug_condition.py`
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests FAIL (this is correct - it proves the bugs exist)
  - Document counterexamples found to understand root cause
  - Mark task complete when tests are written, run, and failures are documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Known File Types Indexed and Non-Exception Formatting Unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - **Preservation A - Known file types still processed correctly**:
    - Observe: files with URLs containing "packages", "sources", "contents", "release" are read and processed on unfixed code
    - Write property-based test using Hypothesis: for all URLs where `_infer_file_type(url)` returns a known type ("packages", "sources", "contents", "release"), `file_reader.read_file()` IS called and the appropriate parser is invoked
    - Use `st.sampled_from()` and string strategies to generate varied URL patterns that match known file types
    - Verify tests pass on UNFIXED code
  - **Preservation B - Non-exception log formatting unchanged**:
    - Observe: `_StructuredFormatter.format()` produces `LEVELNAME logger.name: message key=value` output for records without `exc_info`
    - Write property-based test using Hypothesis: for all log records where `exc_info` is None, the formatter produces the same `LEVELNAME name: message` base format with optional `key=value` pairs
    - Generate random log records with various levels, names, messages, and extra fields using Hypothesis strategies
    - Verify extra_data dicts and direct `extra={}` fields are still appended as `key=value` pairs
    - Verify tests pass on UNFIXED code
  - Test file: `tests/test_indexer_deb_file_errors_preservation.py`
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [x] 3. Fix for indexer .deb file errors

  - [x] 3.1 Add early guard clause for "unknown" file types in `IndexerService.index_repository()`
    - In `src/debcraft/domain/indexer/service.py`, in the per-file processing loop
    - After `file_type = _infer_file_type(file_info.url)` and the incremental skip check
    - Add a guard clause: `if file_type == "unknown": self._logger.debug("Skipping unknown file type: %s", file_info.url); continue`
    - This MUST be placed BEFORE `content = await self._file_reader.read_file(file_info.local_path)`
    - Remove or convert the now-unreachable `else` branch at the bottom of the `if/elif` chain
    - _Bug_Condition: isBugCondition(input) where _infer_file_type(input.file_info.url) == "unknown" AND read_file IS called_
    - _Expected_Behavior: Files with type "unknown" are skipped before any I/O, debug message logged_
    - _Preservation: Known file types (packages, sources, contents, release) continue to be read and processed_
    - _Requirements: 2.1, 2.2_

  - [x] 3.2 Fix `_StructuredFormatter.format()` to include exception tracebacks
    - In `src/debcraft/cli/__init__.py`, in `_StructuredFormatter.format()` method
    - Before the final `return base` (and after constructing the output string)
    - Check if `record.exc_info` is truthy and `record.exc_info[0]` is not None
    - If so, call `self.formatException(record.exc_info)` and append the result (separated by `\n`) to the output string
    - Also handle `record.exc_text` if set (append separated by `\n`)
    - Also handle `record.stack_info` if set (append separated by `\n`)
    - _Bug_Condition: isBugCondition(input) where record.exc_info IS NOT None AND formatted output DOES NOT CONTAIN traceback_
    - _Expected_Behavior: Log records with exc_info include full exception traceback in formatted output_
    - _Preservation: Log records without exc_info produce the same output as before_
    - _Requirements: 2.3, 2.4_

  - [x] 3.3 Verify bug condition exploration tests now pass
    - **Property 1: Expected Behavior** - Unknown File Types Skipped and Tracebacks Included
    - **IMPORTANT**: Re-run the SAME tests from task 1 - do NOT write new tests
    - The tests from task 1 encode the expected behavior
    - When these tests pass, it confirms the expected behavior is satisfied
    - Run bug condition exploration tests from step 1: `tests/test_indexer_deb_file_errors_bug_condition.py`
    - **EXPECTED OUTCOME**: Tests PASS (confirms bugs are fixed)
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 3.4 Verify preservation tests still pass
    - **Property 2: Preservation** - Known File Types Indexed and Non-Exception Formatting Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run preservation property tests from step 2: `tests/test_indexer_deb_file_errors_preservation.py`
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all tests still pass after fix (no regressions)

- [x] 4. Checkpoint - Ensure all tests pass
  - Run full test suite to verify no regressions
  - Ensure bug condition tests pass (Property 1 satisfied)
  - Ensure preservation tests pass (Property 2 satisfied)
  - Verify `uv run debcraft --verbose index` no longer crashes on `.deb` files and shows tracebacks on errors
  - Ensure all tests pass, ask the user if questions arise.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2"] },
    { "id": 1, "tasks": ["3.1", "3.2"] },
    { "id": 2, "tasks": ["3.3", "3.4"] },
    { "id": 3, "tasks": ["4"] }
  ]
}
```

## Notes

- The two bugs are independent fixes in separate files but are linked by their combined effect: `.deb` files cause errors that are then logged without tracebacks.
- Bug A fix is in `src/debcraft/domain/indexer/service.py` — move the "unknown" type check before `file_reader.read_file()`.
- Bug B fix is in `src/debcraft/cli/__init__.py` — add `self.formatException()` call in `_StructuredFormatter.format()`.
- Property-based tests use Hypothesis to generate URL patterns and log record configurations.
- The exploration tests (task 1) are expected to FAIL before the fix and PASS after — this is the core of the bug condition methodology.
- Preservation tests (task 2) must PASS both before and after the fix to ensure no regressions.
- Implementation tasks 3.1 and 3.2 can run in parallel since they modify different files.
