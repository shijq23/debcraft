# Implementation Plan: Fix Windows Test Failures Round 2

## Overview

Apply 4 independent test-only fixes to resolve remaining Windows test failures. Each fix targets a different test file under `tests/` and addresses a distinct platform-specific assumption. No production code (`src/debcraft/`) is modified.

## Tasks

- [x] 1. Fix path separator normalization in test_path_properties.py
  - [x] 1.1 Replace `PurePosixPath(str(result_relative))` with `PurePosixPath(result_relative.as_posix())` in `test_path_suffix_matches_relative_path`
    - In `tests/properties/infrastructure/test_path_properties.py`, locate the `test_path_suffix_matches_relative_path` method in `TestProperty11RelativePathPreservation`
    - Change the assertion from `PurePosixPath(str(result_relative))` to `PurePosixPath(result_relative.as_posix())`
    - This ensures Windows backslash separators are converted to forward slashes before PurePosixPath construction
    - On Linux/macOS, `.as_posix()` produces the same string as `str()`, so behavior is unchanged
    - _Requirements: 1.1, 1.2, 1.3_

- [x] 2. Filter Windows reserved names in test_temp_cleanup.py
  - [x] 2.1 Add `_WINDOWS_RESERVED_RE` regex and `_is_windows_reserved()` helper at module level
    - In `tests/properties/infrastructure/test_temp_cleanup.py`, add `import re` and `import sys` to the imports section
    - Add `_WINDOWS_RESERVED_RE = re.compile(r"^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\..+)?$", re.IGNORECASE)` as a module-level constant
    - Add a `_is_windows_reserved(name: str) -> bool` helper function that returns `_WINDOWS_RESERVED_RE.match(name) is not None`
    - _Requirements: 2.1_

  - [x] 2.2 Add reserved-name skip logic in `test_tmp_files_removed_and_others_untouched`
    - After the filename sanitization block (after `if not sanitized: continue`), add: `if sys.platform == "win32" and _is_windows_reserved(sanitized): continue`
    - Also check after `name` is assigned (before the `if name in seen_names` check): `if sys.platform == "win32" and _is_windows_reserved(name): continue`
    - The `sys.platform == "win32"` guard ensures the filter is never applied on Linux/macOS
    - _Requirements: 2.1, 2.2, 2.3_

- [x] 3. Fix absoluteness validation in test_xdg_paths.py
  - [x] 3.1 Add native Windows environ branch and platform-appropriate assertion in `test_result_is_absolute_path`
    - In `tests/properties/infrastructure/test_xdg_paths.py`, modify the `test_result_is_absolute_path` method
    - Add a new first branch: `if platform == "win32" and sys.platform == "win32":` that sets `environ = {"USERPROFILE": "C:\\Users\\testuser", "LOCALAPPDATA": "C:\\Users\\testuser\\AppData\\Local", "APPDATA": "C:\\Users\\testuser\\AppData\\Roaming"}`
    - Modify the existing `if platform == "win32" and sys.platform != "win32":` to be `elif`
    - Replace the single assertion with a conditional: use `assert result.is_absolute()` when `platform == "win32" and sys.platform == "win32"`, and keep `assert PurePosixPath(result.as_posix()).is_absolute()` for all other cases
    - This new branch only activates on actual Windows hosts, so Linux/macOS behavior is unchanged
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [x] 4. Skip Unix permission tests on Windows in test_mirror_paths.py
  - [x] 4.1 Add `import sys` and `_skip_windows_permissions` marker
    - In `tests/unit/infrastructure/test_mirror_paths.py`, add `import sys` to the imports
    - Add a module-level marker: `_skip_windows_permissions = pytest.mark.skipif(sys.platform == "win32", reason="NTFS does not support Unix file permissions")`
    - _Requirements: 4.4_

  - [x] 4.2 Decorate permission-asserting tests with the skip marker
    - Apply `@_skip_windows_permissions` to `test_sets_0o644_mode`
    - Apply `@_skip_windows_permissions` to `test_mode_allows_owner_read_write`
    - Apply `@_skip_windows_permissions` to `test_mode_allows_group_read`
    - Apply `@_skip_windows_permissions` to `test_mode_allows_other_read`
    - Do NOT apply to `test_file_mode_constant` (it only checks a constant value, no filesystem)
    - On Linux/macOS, `sys.platform == "win32"` is False so these tests continue to run normally
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

- [x] 5. Checkpoint — Run full test suite
  - [x] 5.1 Run pytest to verify no regressions
    - Run `pytest tests/` to ensure all existing tests still pass on the current platform
    - Verify that the 4 modified test files produce no errors
    - Ensure all tests pass, ask the user if questions arise.
    - _Requirements: 5.1, 5.2, 5.3_

## Notes

- All changes are confined to test files under `tests/` — no production code is modified
- Each fix is guarded by `sys.platform == "win32"` so behavior on Linux/macOS is unchanged
- Tasks 1–4 are independent and can be executed in parallel
- Task 5 (verification) depends on all of tasks 1–4 completing first
- The design does not use pseudocode — it specifies Python directly, matching the existing test code

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "3.1", "4.1"] },
    { "id": 1, "tasks": ["2.2", "4.2"] },
    { "id": 2, "tasks": ["5.1"] }
  ]
}
```
