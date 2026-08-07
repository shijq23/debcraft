# Fix Windows Test Failures Round 2 — Bugfix Design

## Overview

This design addresses 4 remaining Windows test failures in the debcraft test suite. A prior spec (`fix-windows-cross-platform-tests`) resolved 6 failures; these 4 persist due to additional platform-specific assumptions in test code. The production code (`src/debcraft/`) is correct and must NOT be modified. All fixes target test files only.

The failures fall into 4 categories:
1. **Path separator in assertions** — `PurePosixPath(str(result_relative))` receives backslash-separated strings on Windows
2. **Windows reserved device names** — Hypothesis generates filenames like `NUL` or `CON` that cannot be created on NTFS
3. **Path absoluteness validation** — `PurePosixPath("C:/Users/...").is_absolute()` returns `False` for Windows drive-letter paths
4. **Unix permission assertions** — NTFS does not support Unix-style permission bits (0o644, etc.)

## Architecture

All changes are confined to test files under `tests/`. The fixes use standard Python mechanisms:

```
┌─────────────────────────────────────────────────────────────────┐
│ Test Layer (modified)                                            │
├─────────────────────────────────────────────────────────────────┤
│ tests/properties/infrastructure/test_path_properties.py         │
│   └─ Fix: .as_posix() before PurePosixPath construction        │
│                                                                 │
│ tests/properties/infrastructure/test_temp_cleanup.py            │
│   └─ Fix: filter Windows reserved names from generated files   │
│                                                                 │
│ tests/properties/infrastructure/test_xdg_paths.py              │
│   └─ Fix: native Windows environ + Path.is_absolute() branch  │
│                                                                 │
│ tests/unit/infrastructure/test_mirror_paths.py                  │
│   └─ Fix: pytest.mark.skipif for permission tests on Windows   │
├─────────────────────────────────────────────────────────────────┤
│ Production Layer (NOT modified)                                  │
│ src/debcraft/  — no changes                                     │
└─────────────────────────────────────────────────────────────────┘
```

## Components and Interfaces

### Fix 1: Path Separator Normalization (`test_path_properties.py`)

**Affected test**: `TestProperty11RelativePathPreservation.test_path_suffix_matches_relative_path`

**Root cause**: The assertion `PurePosixPath(str(result_relative))` receives a `WindowsPath` on Windows. `str(WindowsPath("dists/elxr3"))` produces `"dists\\elxr3"`, which `PurePosixPath` interprets as a single component (since `\\` is not a POSIX separator), causing the equality check to fail.

**Fix**: Replace `PurePosixPath(str(result_relative))` with `PurePosixPath(result_relative.as_posix())`.

```python
# Before (fails on Windows):
assert PurePosixPath(str(result_relative)) == expected

# After (works everywhere):
assert PurePosixPath(result_relative.as_posix()) == expected
```

**Why this is a no-op on Linux/macOS**: On POSIX, `Path.as_posix()` returns the same string as `str(path)` since both use forward slashes.

### Fix 2: Windows Reserved Name Filtering (`test_temp_cleanup.py`)

**Affected test**: `TestTemporaryFileCleanupProperty.test_tmp_files_removed_and_others_untouched`

**Root cause**: Hypothesis generates arbitrary text for filenames. On Windows, names like `NUL`, `CON`, `PRN`, `AUX`, `COM1`–`COM9`, `LPT1`–`LPT9` (with or without extensions) are reserved device names that cannot be created as regular files. The `file_path.write_text(...)` call raises `OSError` or creates device handles instead of files.

**Fix**: Add a filter that skips filenames matching the Windows reserved name pattern when `sys.platform == "win32"`.

```python
import re
import sys

_WINDOWS_RESERVED_RE = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\..+)?$", re.IGNORECASE
)

def _is_windows_reserved(name: str) -> bool:
    """Return True if name is a Windows reserved device name."""
    return _WINDOWS_RESERVED_RE.match(name) is not None
```

In the test body, after sanitizing the filename:

```python
# Skip Windows reserved names on Windows
if sys.platform == "win32" and _is_windows_reserved(name):
    continue
```

**Why this is a no-op on Linux/macOS**: The `sys.platform == "win32"` guard ensures the filter is never applied on non-Windows hosts.

### Fix 3: Platform-Appropriate Absoluteness Validation (`test_xdg_paths.py`)

**Affected test**: `TestXdgPathResolutionProperty.test_result_is_absolute_path`

**Root cause**: When `platform="win32"` and `sys.platform == "win32"` (running on actual Windows), the current code falls through to the `else` branch which provides `{"HOME": "/home/testuser"}`. The `_resolve_windows` function then uses `Path(environ.get("USERPROFILE", "~")).expanduser()`, which (since USERPROFILE is absent) expands `~` to the real Windows user path (e.g., `C:\Users\runner`). The final `PurePosixPath("C:/Users/runner/AppData/Local/debcraft/cache/mirror").is_absolute()` returns `False` because PurePosixPath doesn't recognize drive-letter prefixes as absolute.

**Fix**: Add a new branch for the case where both the simulated platform and the host are Windows:

```python
if platform == "win32" and sys.platform == "win32":
    # Running Windows path logic on actual Windows — use native paths
    environ = {
        "USERPROFILE": "C:\\Users\\testuser",
        "LOCALAPPDATA": "C:\\Users\\testuser\\AppData\\Local",
        "APPDATA": "C:\\Users\\testuser\\AppData\\Roaming",
    }
elif platform == "win32" and sys.platform != "win32":
    # Simulating Windows on a POSIX host — use POSIX-style paths
    environ = {"USERPROFILE": "/users/testuser", "LOCALAPPDATA": "/local", "APPDATA": "/roaming"}
elif platform == "darwin":
    environ = {"HOME": "/Users/testuser"}
else:
    environ = {"HOME": "/home/testuser"}

result = resolve_xdg_path(purpose, environ=environ, platform=platform)

# Use native is_absolute() for native Windows paths, PurePosixPath for simulated POSIX paths
if platform == "win32" and sys.platform == "win32":
    assert result.is_absolute()
else:
    assert PurePosixPath(result.as_posix()).is_absolute()
```

**Why this is a no-op on Linux/macOS**: The new branch only activates when `sys.platform == "win32"`, which is never true on Linux/macOS. The existing branches are unchanged.

### Fix 4: Skip Unix Permission Tests on Windows (`test_mirror_paths.py`)

**Affected tests**: `TestSetFileMode.test_sets_0o644_mode`, `test_mode_allows_owner_read_write`, `test_mode_allows_group_read`, `test_mode_allows_other_read`

**Root cause**: NTFS does not implement Unix-style permission bits. `os.chmod(path, 0o644)` on Windows does not produce the expected `st_mode` bits. The permission mask `stat().st_mode & 0o777` returns platform-dependent values that don't match `0o644`.

**Fix**: Add `@pytest.mark.skipif(sys.platform == "win32", reason="...")` to each test that asserts Unix permission values.

```python
import sys

_skip_windows_permissions = pytest.mark.skipif(
    sys.platform == "win32",
    reason="NTFS does not support Unix file permissions",
)

class TestSetFileMode:
    @_skip_windows_permissions
    def test_sets_0o644_mode(self, tmp_path):
        ...

    @_skip_windows_permissions
    def test_mode_allows_owner_read_write(self, tmp_path):
        ...

    @_skip_windows_permissions
    def test_mode_allows_group_read(self, tmp_path):
        ...

    @_skip_windows_permissions
    def test_mode_allows_other_read(self, tmp_path):
        ...
```

The `test_file_mode_constant` test does NOT need skipping — it only checks the value of the `_FILE_MODE` constant (pure arithmetic), not actual filesystem behavior.

**Why this is a no-op on Linux/macOS**: `sys.platform == "win32"` is `False` on POSIX, so the skipif condition is never satisfied.

## Data Models

No data model changes. All fixes operate on existing test assertions and test helper logic.

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Windows Reserved Name Exclusion

*For any* filename generated by Hypothesis on a Windows host, after passing through the reserved-name filter, the filename SHALL NOT match the Windows reserved device name pattern `^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\..+)?$` (case-insensitive).

**Validates: Requirements 2.1, 2.3**

### Property 2: Path Separator Normalization Round-Trip

*For any* relative path composed of valid path segments, converting a `Path` result to a string via `.as_posix()` and then constructing a `PurePosixPath` SHALL produce a path equal to the original forward-slash-separated relative path, regardless of host platform.

**Validates: Requirements 1.1, 1.2**

### Property 3: Absoluteness Validation Correctness

*For any* combination of platform identifier and storage purpose, when the test provides platform-appropriate environment variables (Windows-style drive-letter paths for native Windows, POSIX-style paths otherwise), the resolved path SHALL be recognized as absolute by the platform-appropriate validation method.

**Validates: Requirements 3.1, 3.2, 3.3**

## Error Handling

These fixes do not introduce new error handling paths. The changes are:

- **Reserved name filter**: Uses `continue` to skip invalid filenames silently (consistent with the existing `except OSError: continue` pattern already in the test)
- **Permission skip**: Uses pytest's `skipif` mechanism which reports skipped tests cleanly in test output
- **Path assertions**: No error paths — the assertions either pass or fail

The existing `try/except OSError` block in `test_temp_cleanup.py` already handles file creation failures gracefully. The reserved-name filter prevents the failure earlier in the pipeline, which is cleaner than relying on the catch-all OSError handler (since some reserved names may succeed in creating device handles rather than raising).

## Testing Strategy

### Validation Approach

Since this is a bugfix spec, testing focuses on:
1. **Bug reproduction**: Confirm the 4 tests fail on Windows without the fix
2. **Fix verification**: Confirm the 4 tests pass on Windows after the fix
3. **Preservation**: Confirm all tests pass unchanged on Linux/macOS

### Property-Based Tests (existing, modified)

The following existing Hypothesis-based tests are modified by this fix and serve as the primary validation:

- **`test_path_suffix_matches_relative_path`** (200 iterations) — Validates Property 2 by generating random relative paths and checking the PurePosixPath comparison works after `.as_posix()` conversion
- **`test_tmp_files_removed_and_others_untouched`** (200 iterations) — Validates Property 1 by generating random filenames; the reserved-name filter ensures no invalid names reach file creation on Windows
- **`test_result_is_absolute_path`** (200 iterations) — Validates Property 3 by generating random platform/purpose combinations and checking absoluteness

Each test already uses `@settings(max_examples=200)` which exceeds the 100-iteration minimum.

**Tag references:**
- Feature: fix-windows-test-failures-round2, Property 1: Windows Reserved Name Exclusion
- Feature: fix-windows-test-failures-round2, Property 2: Path Separator Normalization Round-Trip
- Feature: fix-windows-test-failures-round2, Property 3: Absoluteness Validation Correctness

### Unit Tests (existing, gated)

The 4 permission tests in `TestSetFileMode` continue to run as unit tests on Linux/macOS. On Windows they are skipped with a descriptive reason.

### Integration Tests

- Run full test suite on Linux via CI — all tests pass (preservation)
- Run full test suite on macOS via CI — all tests pass (preservation)
- Run full test suite on Windows via CI — the 4 previously-failing tests now pass; permission tests are cleanly skipped

### Exploratory Bug Condition Verification

Before applying fixes, run on Windows to confirm:
1. `test_path_suffix_matches_relative_path` fails with path comparison mismatch (backslash vs forward slash)
2. `test_tmp_files_removed_and_others_untouched` fails with OSError creating reserved-name files
3. `test_result_is_absolute_path` fails with `AssertionError` (PurePosixPath doesn't recognize drive-letter paths as absolute)
4. `test_sets_0o644_mode` (and related) fails with mode mismatch (NTFS doesn't set Unix bits)
