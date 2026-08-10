# Indexer .deb File Errors Bugfix Design

## Overview

Two related bugs cause the indexer to produce noisy, undiagnosable output when encountering `.deb` binary files in the mirror database. First, `IndexerService` calls `file_reader.read_file()` for files with type "unknown" (including `.deb` files), triggering UTF-8 decode failures on binary content. Second, `_StructuredFormatter` never appends exception tracebacks to log output, so the resulting errors provide no actionable stack trace. The fix moves the "unknown" type check before the file-read call and adds `self.formatException()` output to the formatter.

## Glossary

- **Bug_Condition (C)**: The union of two conditions: (1) a file with inferred type "unknown" reaches `file_reader.read_file()`, or (2) a log record with `exc_info` is formatted without its traceback
- **Property (P)**: (1) Files with type "unknown" are skipped before any I/O, (2) log records with `exc_info` include the full traceback in formatted output
- **Preservation**: Existing indexing of Packages/Sources/Contents/Release files and normal (non-exception) log formatting must remain unchanged
- **`_infer_file_type(url_or_path)`**: Module-level function in `service.py` that classifies a URL as "packages", "sources", "contents", "release", or "unknown"
- **`_StructuredFormatter`**: Custom `logging.Formatter` subclass in `cli/__init__.py` that renders structured key=value pairs but currently drops tracebacks
- **`IndexerService.index_repository()`**: The main processing loop that iterates over verified files, reads, parses, and persists them

## Bug Details

### Bug Condition

The bug manifests in two distinct locations:

**Bug A — Indexer processes unknown files:** In `IndexerService.index_repository()`, the processing loop calls `self._file_reader.read_file(file_info.local_path)` at line ~165 *before* the `file_type` dispatch handles the "unknown" case. The `else` branch with `self._logger.warning("Unknown file type for: %s", ...)` and `continue` is only reached after the file has already been read from disk. For `.deb` files, `read_file()` attempts to decode binary content as UTF-8, raising `OSError`.

**Bug B — Formatter swallows tracebacks:** In `_StructuredFormatter.format()`, the method constructs the log line from `record.getMessage()` and extra fields but never checks `record.exc_info` or calls `self.formatException()`. When `logger.exception()` is called (which sets `exc_info=True`), the traceback is silently discarded.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type (FileProcessingInput | LogFormatInput)
  OUTPUT: boolean

  IF input IS FileProcessingInput:
    RETURN _infer_file_type(input.file_info.url) == "unknown"
           AND file_reader.read_file(input.file_info.local_path) IS CALLED

  IF input IS LogFormatInput:
    RETURN input.record.exc_info IS NOT None
           AND input.record.exc_info[0] IS NOT None
           AND formatted_output DOES NOT CONTAIN traceback text
END FUNCTION
```

### Examples

- **`.deb` file processed**: `file_info.url = "http://repo/pool/main/h/hello/hello_2.10-3_amd64.deb"` → `_infer_file_type()` returns "unknown" → `read_file()` called → `OSError("Failed to decode file as UTF-8: ...")` raised
- **`.udeb` file processed**: `file_info.url = "http://repo/pool/main/l/linux/linux-image_6.1_amd64.udeb"` → same "unknown" type → same decode failure
- **Exception logged without traceback**: `logger.exception("Error processing file: %s", url)` → formatter outputs only `ERROR debcraft.domain.indexer.service: Error processing file: <url>` with no stack trace
- **Normal file processed correctly** (not a bug): `file_info.url = "http://repo/dists/bookworm/main/binary-amd64/Packages.gz"` → type "packages" → read, decompress, parse succeeds

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Packages files (`.gz`, `.xz`, uncompressed) in VERIFIED state continue to be read, decompressed, parsed, and indexed
- Sources files in VERIFIED state continue to be read, parsed, and indexed
- Contents files in VERIFIED state continue to be read, parsed, and indexed
- Release/InRelease files in VERIFIED state continue to be parsed with a debug log message
- Log records without `exc_info` continue to produce the same `LEVELNAME logger.name: message key=value` format
- Structured extra fields continue to be appended as `key=value` pairs

**Scope:**
All inputs that do NOT involve (a) files with inferred type "unknown" or (b) log records with `exc_info` should be completely unaffected by this fix. This includes:
- Processing of Packages, Sources, Contents, Release files
- Incremental skip logic for already-indexed files
- Normal log formatting (DEBUG, INFO, WARNING without exceptions)
- The `_infer_file_type()` classification logic itself

## Hypothesized Root Cause

Based on the code analysis, the root causes are:

1. **Incorrect control flow in processing loop (Bug A)**: In `index_repository()`, the `content = await self._file_reader.read_file(file_info.local_path)` call at line ~165 precedes the `if/elif/else` dispatch on `file_type`. The `else` branch that handles "unknown" types is only reached *after* the file has been read. The fix requires moving the "unknown" check before the `read_file()` call.

2. **Missing `formatException` call (Bug B)**: `_StructuredFormatter.format()` constructs the output string from `record.getMessage()` and extra fields, then returns it. It never checks `record.exc_info` or calls `self.formatException(record.exc_info)` to append the traceback. The standard `logging.Formatter.format()` method does this automatically, but since `_StructuredFormatter` overrides `format()` completely without calling `super().format()`, the traceback handling is lost.

## Correctness Properties

Property 1: Bug Condition A — Unknown File Types Skipped Before I/O

_For any_ file in VERIFIED state where `_infer_file_type(file.url)` returns "unknown", the fixed `index_repository()` function SHALL skip that file without calling `file_reader.read_file()`, and SHALL log a debug message indicating the skip.

**Validates: Requirements 2.1, 2.2**

Property 2: Bug Condition B — Exception Tracebacks Included in Formatted Output

_For any_ log record where `record.exc_info` is set and contains a valid exception tuple, the fixed `_StructuredFormatter.format()` SHALL include the full exception traceback text in the returned string.

**Validates: Requirements 2.3, 2.4**

Property 3: Preservation — Known File Types Still Indexed

_For any_ file in VERIFIED state where `_infer_file_type(file.url)` returns one of "packages", "sources", "contents", or "release", the fixed `index_repository()` function SHALL read and process the file exactly as before, producing the same parsing and persistence outcomes.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

Property 4: Preservation — Non-Exception Log Formatting Unchanged

_For any_ log record where `record.exc_info` is None or `record.exc_info[0]` is None, the fixed `_StructuredFormatter.format()` SHALL produce the same output as the original formatter.

**Validates: Requirements 3.5, 3.6**

## Fix Implementation

### Changes Required

**File**: `src/debcraft/domain/indexer/service.py`

**Function**: `IndexerService.index_repository()` — the per-file processing loop

**Specific Changes**:
1. **Move "unknown" type check before `read_file()`**: After `file_type = _infer_file_type(file_info.url)` and the incremental skip check, add a guard clause that skips files with type "unknown" before calling `self._file_reader.read_file()`. Log a debug message indicating the skip with the file URL.

2. **Remove the redundant `else` branch**: The existing `else: self._logger.warning(...)` branch after the `if/elif` chain becomes unreachable once the early guard is in place. It can either be removed or converted to a defensive assertion.

---

**File**: `src/debcraft/cli/__init__.py`

**Class**: `_StructuredFormatter`

**Specific Changes**:
3. **Add traceback formatting**: At the end of `_StructuredFormatter.format()`, before returning, check if `record.exc_info` is truthy and `record.exc_info[0]` is not None. If so, call `self.formatException(record.exc_info)` and append the result (separated by a newline) to the output string.

4. **Handle `record.exc_text`**: Also check `record.exc_text` (which may be set if `formatException` was called earlier) and append it if present, following the pattern used by the standard library's `logging.Formatter`.

5. **Handle stack info**: Optionally check `record.stack_info` and append it if present, to fully replicate standard formatter behavior for completeness.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bugs on unfixed code, then verify the fixes work correctly and preserve existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate both bugs BEFORE implementing the fix. Confirm the root cause analysis.

**Test Plan**: Write tests that exercise the indexer with files that have "unknown" type (e.g., `.deb` URLs) and tests that format log records containing `exc_info`. Run on the UNFIXED code to observe failures.

**Test Cases**:
1. **Unknown file type reaches read_file**: Create a `RepositoryFileInfo` with a `.deb` URL, inject a mock `file_reader` that raises if called, verify the exception occurs (will fail on unfixed code because `read_file` IS called)
2. **Exception traceback missing from formatted output**: Create a log record with `exc_info=(ValueError, ValueError("test"), tb)`, format it with `_StructuredFormatter`, assert traceback text is present (will fail on unfixed code)
3. **Multiple unknown files**: Process a batch containing both known and unknown files, verify unknown files trigger read attempts (will fail on unfixed code)

**Expected Counterexamples**:
- `file_reader.read_file()` is called for `.deb` files, raising `OSError`
- `_StructuredFormatter.format()` returns a string with no traceback text despite `exc_info` being set

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed functions produce the expected behavior.

**Pseudocode:**
```
FOR ALL file WHERE _infer_file_type(file.url) == "unknown" DO
  result := index_repository_fixed(file)
  ASSERT file_reader.read_file WAS NOT CALLED for this file
  ASSERT debug log message was emitted indicating skip
END FOR

FOR ALL record WHERE record.exc_info IS NOT None DO
  output := _StructuredFormatter_fixed.format(record)
  ASSERT output CONTAINS traceback text from record.exc_info
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed functions produce the same result as the original functions.

**Pseudocode:**
```
FOR ALL file WHERE _infer_file_type(file.url) IN ["packages", "sources", "contents", "release"] DO
  ASSERT index_repository_original(file) == index_repository_fixed(file)
END FOR

FOR ALL record WHERE record.exc_info IS None DO
  ASSERT _StructuredFormatter_original.format(record) == _StructuredFormatter_fixed.format(record)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many URL patterns to verify the type classifier and skip logic
- It generates many log record configurations to verify formatting consistency
- It catches edge cases in URL classification that manual tests might miss

**Test Plan**: Observe behavior on UNFIXED code first for known file types and non-exception log records, then write property-based tests capturing that behavior.

**Test Cases**:
1. **Packages file processing preserved**: Verify Packages files with various compressions continue to be read and parsed identically
2. **Sources file processing preserved**: Verify Sources files continue to be processed
3. **Log format preserved for non-exception records**: Verify formatting of DEBUG/INFO/WARNING records without exc_info produces identical output
4. **Extra fields preserved**: Verify key=value pairs from both `extra_data` and direct `extra={}` continue to appear in output

### Unit Tests

- Test `_infer_file_type()` returns "unknown" for `.deb` and other non-metadata URLs
- Test the early-skip guard in the processing loop prevents `read_file()` calls for unknown types
- Test `_StructuredFormatter.format()` includes traceback when `exc_info` is set
- Test `_StructuredFormatter.format()` handles `exc_info=None` identically to before
- Test edge case: `exc_info=(None, None, None)` does not produce spurious traceback output

### Property-Based Tests

- Generate random URL strings and verify: if type is "unknown" then `read_file` is never called; if type is known then processing proceeds normally
- Generate random log records with and without `exc_info` and verify: records with exceptions include traceback text; records without exceptions produce output identical to the original formatter
- Generate random `extra` dictionaries and verify they are still rendered as `key=value` pairs regardless of `exc_info` presence

### Integration Tests

- Run the full indexer against a mirror database containing both metadata files and `.deb` files, verify no `OSError` exceptions are raised and `.deb` files are skipped
- Run the full indexer with `--verbose` and verify that any processing errors include full stack traces in stderr output
- Verify the combination: a `.deb` file that would have caused an error now shows only a debug skip message, not an error
