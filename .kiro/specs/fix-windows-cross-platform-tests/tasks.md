# Implementation Plan

## Overview

Fix remaining Windows test failures caused by using `str(result)` instead of `result.as_posix()` when validating path absoluteness with `PurePosixPath`. On Windows, `str(WindowsPath("/home/..."))` produces backslashes (`\home\...`), which `PurePosixPath` does not recognize as absolute. The fix: replace `str(result)` with `result.as_posix()` in all `PurePosixPath(...)` assertions, and guard the `re.escape` preservation test with a platform check.

## Tasks

- [x] 1. Write bug condition exploration test
  - _(completed in prior iteration — test exists at tests/properties/infrastructure/test_bug_condition_exploration.py)_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - _(completed in prior iteration — test exists at tests/properties/infrastructure/test_preservation_properties.py)_

- [x] 3. Fix str(result) to result.as_posix() in all PurePosixPath assertions

  - [x] 3.1. Fix unit test xdg_paths assertions
    - In tests/unit/infrastructure/test_xdg_paths.py:
    - In TestLinuxDefaultPaths.test_all_paths_are_absolute: replace PurePosixPath(str(result)).is_absolute() with PurePosixPath(result.as_posix()).is_absolute()
    - In TestMacOSFallbackPaths.test_all_paths_are_absolute: replace PurePosixPath(str(result)).is_absolute() with PurePosixPath(result.as_posix()).is_absolute()
    - In TestWindowsFallbackPaths.test_all_paths_are_absolute: replace PurePosixPath(str(result)).is_absolute() with PurePosixPath(result.as_posix()).is_absolute()
    - _Requirements: 2.1_

  - [x] 3.2. Fix property test xdg_paths assertions
    - In tests/properties/infrastructure/test_xdg_paths.py:
    - In TestXdgPathResolutionProperty.test_result_is_absolute_path: replace PurePosixPath(str(result)).is_absolute() with PurePosixPath(result.as_posix()).is_absolute()
    - _Requirements: 2.2_

  - [x] 3.3. Fix bug condition exploration test assertions
    - In tests/properties/infrastructure/test_bug_condition_exploration.py:
    - In TestBugConditionFixValidation.test_posix_paths_absolute_with_pure_posix_validation: replace PurePosixPath(str(result)).is_absolute() with PurePosixPath(result.as_posix()).is_absolute()
    - _Requirements: 1.1, 1.2_

  - [x] 3.4. Fix preservation properties test assertions
    - In tests/properties/infrastructure/test_preservation_properties.py:
    - In TestPreservationProperties.test_posix_path_absoluteness_on_linux_host: replace PurePosixPath(str(result)).is_absolute() with PurePosixPath(result.as_posix()).is_absolute()
    - In TestPreservationProperties.test_re_escape_path_on_linux_produces_matching_pattern: change the assertion to compare re.search(escaped, path_str) instead of re.search(escaped, "/fake/config") so it works on any platform where str(Path(...)) may differ from the literal
    - _Requirements: 3.1, 3.2_

- [x] 4. Run full test suite to verify all tests pass
  - Run pytest on the current Linux host to confirm no regressions
  - Ensure the resolve_xdg_path() implementation has NOT been modified
  - Verify that the assertions now use .as_posix() and would work correctly on Windows

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["3.1", "3.2", "3.3", "3.4"] },
    { "id": 1, "tasks": ["4"] }
  ]
}
```

## Notes

- The implementation (resolve_xdg_path in src/debcraft/infrastructure/storage/paths.py) MUST NOT be modified
- All fixes are test-only changes
- The root cause: str(WindowsPath("/home/...")) produces backslashes, and PurePosixPath("\home\...") does not recognize backslash as a separator, so .is_absolute() returns False
- The fix: result.as_posix() always returns forward slashes regardless of host OS, so PurePosixPath(result.as_posix()) correctly recognizes /home/... as absolute
- On Linux/macOS, str(result) and result.as_posix() produce identical output, so this is a no-op change on those platforms
