# Fix Windows Cross-Platform Tests — Bugfix Design

## Overview

Six tests fail on Windows (Python 3.13, win32) because the test code makes POSIX-specific assumptions about path behavior, file encoding, and path separator representation. The implementation (`resolve_xdg_path` in `src/debcraft/infrastructure/storage/paths.py`) is correct and must not be modified. All fixes target only test files across three categories: path absoluteness validation (4 tests), Unicode encoding (1 test), and path separator in regex matching (1 test).

## Glossary

- **Bug_Condition (C)**: The condition under which tests fail — test code uses POSIX-specific assumptions that break when Python's `pathlib.Path` instantiates `WindowsPath` on a Windows host
- **Property (P)**: All 6 affected tests pass on Windows while continuing to pass on Linux and macOS
- **Preservation**: The 315 non-affected tests continue to pass unchanged on all platforms; the implementation code remains untouched
- **`resolve_xdg_path`**: The function in `src/debcraft/infrastructure/storage/paths.py` that resolves storage paths using XDG conventions with platform-specific fallbacks
- **`PurePosixPath`**: A `pathlib` class that interprets paths using POSIX rules regardless of host OS — useful for validating POSIX-style paths on Windows
- **cp1252**: The default Windows locale encoding that cannot represent all byte values Hypothesis may generate

## Bug Details

### Bug Condition

The bug manifests when the 6 affected tests run on a Windows host where `pathlib.Path` creates `WindowsPath` instances. The tests pass POSIX-style paths (e.g., `/home/testuser`) through `resolve_xdg_path`, which correctly wraps them in the host-native `Path`. On Windows, the resulting `WindowsPath` does not consider `/home/testuser/.cache/debcraft/mirror` absolute (no drive letter), `write_text()` uses cp1252 by default, and `str(Path("/fake/config"))` produces `\fake\config`.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type TestExecution
  OUTPUT: boolean

  RETURN input.hostPlatform == "win32"
         AND (
           (input.testName IN ["test_all_paths_are_absolute" (Linux class),
                               "test_all_paths_are_absolute" (macOS class),
                               "test_all_paths_are_absolute" (Windows class),
                               "test_result_is_absolute_path"]
            AND input.environ contains POSIX-style paths without drive letters)
           OR
           (input.testName == "test_tmp_files_removed_and_others_untouched"
            AND input.hypothesisGeneratedFilename contains bytes > 0x7F)
           OR
           (input.testName == "test_error_message_identifies_unwritable_path"
            AND input.regexPattern contains literal forward slashes for path)
         )
END FUNCTION
```

### Examples

- **Path absoluteness**: On Windows, `Path("/home/testuser/.cache/debcraft/mirror").is_absolute()` returns `False` because there is no drive letter. The test `test_all_paths_are_absolute` asserts `result.is_absolute()` and fails.
- **Unicode encoding**: On Windows, `file_path.write_text("content of \x80file")` raises `UnicodeEncodeError` because cp1252 cannot encode `\x80` in a filename string. The test `test_tmp_files_removed_and_others_untouched` crashes.
- **Path separator regex**: On Windows, `str(Path("/fake/config"))` produces `\fake\config`. The test `test_error_message_identifies_unwritable_path` matches against the regex `/fake/config` which doesn't match the backslash form.
- **Property test**: `test_result_is_absolute_path` uses `{"HOME": "/home/testuser"}` for all platforms — on Windows host, this creates a `WindowsPath` that isn't absolute.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- All 6 affected tests must continue to pass on Linux with identical assertions and logic
- All 6 affected tests must continue to pass on macOS with identical assertions and logic
- The remaining 315 tests in the suite must pass unchanged on all platforms
- The `resolve_xdg_path()` implementation must not be modified in any way
- Test semantics (what they validate) must remain the same — only the platform-specific mechanics change

**Scope:**
All inputs that do NOT involve running on a Windows host are completely unaffected by this fix. This includes:
- Running the full test suite on Linux
- Running the full test suite on macOS
- Running any test that does not assert path absoluteness, use `write_text()` without encoding, or regex-match against path strings

## Hypothesized Root Cause

Based on the bug description, the root causes are confirmed (not merely hypothesized, since the failure modes are well-understood):

1. **POSIX paths not absolute on Windows (4 tests)**: `WindowsPath.is_absolute()` requires a drive letter prefix (e.g., `C:\`). Test environ values like `HOME=/home/testuser` produce paths that pass through `Path(...)` and become `WindowsPath("/home/testuser/...")` which is not absolute on Windows.
   - `TestLinuxDefaultPaths.test_all_paths_are_absolute`
   - `TestMacOSFallbackPaths.test_all_paths_are_absolute`
   - `TestWindowsFallbackPaths.test_all_paths_are_absolute`
   - `TestXdgPathResolutionProperty.test_result_is_absolute_path`

2. **Default encoding is cp1252 on Windows (1 test)**: `Path.write_text()` without an explicit `encoding` parameter uses `locale.getpreferredencoding()` which is `cp1252` on Windows. Hypothesis generates filenames with arbitrary characters that may not be representable in cp1252.
   - `TestTemporaryFileCleanupProperty.test_tmp_files_removed_and_others_untouched`

3. **Path separators differ in string representation (1 test)**: `str(Path("/fake/config"))` returns `/fake/config` on POSIX but `\fake\config` on Windows. A regex literal `/fake/config` won't match the Windows form.
   - `TestInitializeWritabilityCheck.test_error_message_identifies_unwritable_path`

## Correctness Properties

Property 1: Bug Condition — Affected Tests Pass on Windows

_For any_ test execution where the host platform is Windows and the test is one of the 6 affected tests, the fixed test code SHALL pass without errors by using platform-appropriate path validation (`PurePosixPath.is_absolute()` for POSIX-style paths, `encoding="utf-8"` for file writes, and `re.escape(str(Path(...)))` for regex patterns).

**Validates: Requirements 2.1, 2.2, 2.3, 2.4**

Property 2: Preservation — Non-Windows and Non-Affected Tests Unchanged

_For any_ test execution where either (a) the host platform is Linux or macOS, or (b) the test is not one of the 6 affected tests, the fixed test code SHALL produce exactly the same behavior and results as the original code, preserving all existing test semantics and pass/fail outcomes.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct (it is, given well-understood `pathlib` behavior):

**File**: `tests/unit/infrastructure/test_xdg_paths.py`

**Tests**: `test_all_paths_are_absolute` in `TestLinuxDefaultPaths`, `TestMacOSFallbackPaths`, `TestWindowsFallbackPaths`

**Specific Changes**:
1. **Import `PurePosixPath`** from `pathlib`
2. **Replace `result.is_absolute()` with `PurePosixPath(str(result)).is_absolute()`** in the three `test_all_paths_are_absolute` methods. Since the test environ uses POSIX-style paths and the `platform` parameter forces POSIX resolution logic, the result is a POSIX-style path string regardless of the host OS. Validating with `PurePosixPath` checks absoluteness using POSIX rules (leading `/`) rather than Windows rules (drive letter required).

---

**File**: `tests/properties/infrastructure/test_xdg_paths.py`

**Test**: `test_result_is_absolute_path` in `TestXdgPathResolutionProperty`

**Specific Changes**:
3. **Import `PurePosixPath`** from `pathlib`
4. **Replace `assert result.is_absolute()` with `assert PurePosixPath(str(result)).is_absolute()`**. Same reasoning as above — the test uses POSIX-style environ paths and needs POSIX absoluteness rules.

---

**File**: `tests/properties/infrastructure/test_temp_cleanup.py`

**Test**: `test_tmp_files_removed_and_others_untouched` in `TestTemporaryFileCleanupProperty`

**Specific Changes**:
5. **Add `encoding="utf-8"` to `file_path.write_text()`** call. This ensures all characters Hypothesis might embed in the content string can be encoded regardless of the system's default locale.

---

**File**: `tests/unit/infrastructure/test_storage_engine.py`

**Test**: `test_error_message_identifies_unwritable_path` in `TestInitializeWritabilityCheck`

**Specific Changes**:
6. **Import `re`** at top of file
7. **Replace `match="/fake/config"` with `match=re.escape(str(Path("/fake/config")))`** in the `pytest.raises` call. On Linux/macOS this produces the identical string `/fake/config`; on Windows it produces `\\fake\\config` (properly escaped for regex), matching the actual error message.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code (run tests on Windows without the fix), then verify the fix works correctly and preserves existing behavior (run full suite on all platforms).

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Run the 6 affected tests on a Windows host with the current (unfixed) test code. Observe the specific failures and confirm they match the predicted patterns.

**Test Cases**:
1. **Path Absoluteness (Linux class)**: Run `TestLinuxDefaultPaths.test_all_paths_are_absolute` on Windows — expect `AssertionError: mirror path is not absolute` (will fail on unfixed code)
2. **Path Absoluteness (macOS class)**: Run `TestMacOSFallbackPaths.test_all_paths_are_absolute` on Windows — expect same `AssertionError` pattern (will fail on unfixed code)
3. **Path Absoluteness (Windows class)**: Run `TestWindowsFallbackPaths.test_all_paths_are_absolute` on Windows — expect same `AssertionError` pattern (will fail on unfixed code)
4. **Path Absoluteness (property test)**: Run `TestXdgPathResolutionProperty.test_result_is_absolute_path` on Windows — expect Hypothesis counterexample (will fail on unfixed code)
5. **Unicode Encoding**: Run `TestTemporaryFileCleanupProperty.test_tmp_files_removed_and_others_untouched` on Windows — expect `UnicodeEncodeError` (will fail on unfixed code)
6. **Path Separator Regex**: Run `TestInitializeWritabilityCheck.test_error_message_identifies_unwritable_path` on Windows — expect `AssertionError: Pattern '/fake/config' does not match` (will fail on unfixed code)

**Expected Counterexamples**:
- `WindowsPath("/home/testuser/.cache/debcraft/mirror").is_absolute()` returns `False`
- `file_path.write_text("content of \x80name")` raises `UnicodeEncodeError: 'charmap' codec can't encode character '\x80'`
- `pytest.raises(StorageError, match="/fake/config")` does not match `\fake\config` in error message

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds (running on Windows), the fixed tests pass correctly.

**Pseudocode:**
```
FOR ALL test IN affected_tests WHERE hostPlatform == "win32" DO
  result := run_test_fixed(test)
  ASSERT result == PASS
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold (running on Linux/macOS, or non-affected tests on any platform), the fixed tests produce the same result as the original tests.

**Pseudocode:**
```
FOR ALL test IN all_tests WHERE NOT isBugCondition(test) DO
  ASSERT run_test_fixed(test) == run_test_original(test)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- The Hypothesis-based tests (`test_result_is_absolute_path`, `test_tmp_files_removed_and_others_untouched`) already generate hundreds of random inputs
- Running the full 321-test suite on Linux/macOS after the fix confirms no regressions
- The fix is strictly narrower (adds encoding parameter, changes validation method) so preservation is structurally guaranteed for non-affected code paths

**Test Plan**: Run full test suite on Linux to confirm all 321 tests pass. Run on Windows to confirm the 6 previously-failing tests now pass.

**Test Cases**:
1. **Linux Full Suite Preservation**: Run all 321 tests on Linux — all must pass identically to before
2. **macOS Full Suite Preservation**: Run all 321 tests on macOS — all must pass identically to before
3. **Windows Full Suite**: Run all 321 tests on Windows — the 6 previously-failing tests now pass, others unchanged

### Unit Tests

- Verify `PurePosixPath("/home/testuser/.cache/debcraft/mirror").is_absolute()` returns `True` on Windows
- Verify `write_text("content", encoding="utf-8")` succeeds with arbitrary Unicode content on Windows
- Verify `re.escape(str(Path("/fake/config")))` matches both `/fake/config` and `\fake\config` depending on host

### Property-Based Tests

- `test_result_is_absolute_path` with `PurePosixPath` validation — generates random platforms and purposes, verifies absoluteness using POSIX rules for POSIX-style paths
- `test_tmp_files_removed_and_others_untouched` with `encoding="utf-8"` — generates random filenames including non-ASCII characters, verifies cleanup behavior regardless of encoding

### Integration Tests

- Run the full CI matrix (Linux + Windows + macOS) and confirm green across all platforms
- Verify the GitHub Actions `ci.yml` workflow passes with the `windows-latest` runner
- Confirm Hypothesis database is not invalidated by the fix (no new shrink loops)
