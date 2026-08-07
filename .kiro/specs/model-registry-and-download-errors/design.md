# Model Registry and Download Errors Bugfix Design

## Overview

This design addresses two bugs in debcraft's infrastructure layer:

1. **SQLAlchemy mapper resolution failure**: The `debcraft.infrastructure.models.__init__.py` contains only a docstring. When code imports a single model (e.g., `RepositorySnapshot`), related model modules (e.g., `scan.py`) are not loaded, causing SQLAlchemy's mapper to fail resolving string-based relationship references like `"ScanSession"`.

2. **Missing download error context**: When all retries are exhausted, the `DownloadResult` lacks the HTTP status code, and the engine-level log only contains `url` and a stringified error—insufficient for production debugging.

The fix approach is minimal and targeted: populate `__init__.py` with explicit module imports, and extend `DownloadResult` with a `status_code` field while enhancing logging in the download and engine layers.

## Glossary

- **Bug_Condition (C)**: For Bug 1: importing a model module without all related modules being registered. For Bug 2: retries exhausted for an HTTP error where `status_code` is available on the exception but not propagated.
- **Property (P)**: For Bug 1: all mapper relationships resolve without error. For Bug 2: `DownloadResult` carries `status_code` and logs include full error context.
- **Preservation**: Existing import paths continue to work; successful downloads and 4xx immediate-fail behavior remain unchanged.
- **`models/__init__.py`**: The package initializer at `src/debcraft/infrastructure/models/__init__.py` — currently empty.
- **`DownloadResult`**: Frozen dataclass in `src/debcraft/domain/mirror/values.py` representing download outcomes.
- **`DownloadCoordinator.download_file`**: Method in `src/debcraft/infrastructure/mirror/download.py` that handles single file downloads with retry.
- **`MirrorEngine._stage_download_artifacts`**: Method in `src/debcraft/infrastructure/mirror/engine.py` that processes download results.

## Bug Details

### Bug Condition

**Bug 1** manifests when any code imports a model from the `debcraft.infrastructure.models` package and SQLAlchemy's mapper attempts to resolve string-based relationship references before all related model modules have been loaded.

**Bug 2** manifests when all download retries are exhausted for an HTTP error (5xx) and the status code available on the exception object is not propagated to `DownloadResult` or to the engine-level log.

**Formal Specification:**
```
FUNCTION isBugCondition_Bug1(input)
  INPUT: input of type PythonImport
  OUTPUT: boolean

  RETURN input.imports_model_from("debcraft.infrastructure.models.*")
         AND NOT all_related_model_modules_loaded()
         AND model_has_string_relationship_references()
END FUNCTION

FUNCTION isBugCondition_Bug2(input)
  INPUT: input of type DownloadAttempt
  OUTPUT: boolean

  RETURN input.retries_exhausted = True
         AND input.last_error IS (HttpServerError OR HttpClientError)
         AND input.last_error.status_code IS NOT NULL
END FUNCTION
```

### Examples

- **Bug 1**: `publisher.py` imports `RepositorySnapshot` → `RepositorySnapshot.scan_sessions` references `"ScanSession"` as a string → `scan.py` not loaded → `InvalidRequestError`
- **Bug 1**: Test file imports `from debcraft.infrastructure.models.metadata import RepositorySnapshot` without workaround `import debcraft.infrastructure.models.scan  # noqa: F401` → same failure
- **Bug 2**: Download to `http://repo/pool/main/l/lib_1.0.deb` fails with HTTP 503 after 3 attempts → `DownloadResult.error = "Download failed for '...': HTTP 503 server error"` but `status_code` is `None` → engine log says "Artifact download failed" with only `url` and `error` string
- **Bug 2**: Download succeeds on first try → no ERROR log emitted, but also no DEBUG log identifying which package was downloaded

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Individual model imports (e.g., `from debcraft.infrastructure.models.metadata import RepositorySnapshot`) must continue to work without import errors
- Bidirectional relationships between `ScanSession` and `RepositorySnapshot` must continue to resolve correctly when both are loaded
- Successful downloads on first attempt must return `DownloadResult(success=True, sha256_verified=True)` unchanged
- 4xx client errors must continue to immediately fail without retrying
- Downloads that succeed on a retry must return successful `DownloadResult` without any final ERROR log

**Scope:**
All inputs that do NOT involve the bug conditions should be completely unaffected:
- Direct model class usage (queries, inserts) once mapper is configured
- Download successes and non-HTTP failures (network errors, checksum mismatches)
- Existing log format for retry warnings

## Hypothesized Root Cause

### Bug 1: Empty `__init__.py`

1. **Missing module imports in `__init__.py`**: The file at `src/debcraft/infrastructure/models/__init__.py` contains only `"""SQLAlchemy ORM entity models."""`. When Python imports `debcraft.infrastructure.models.metadata`, it first executes `__init__.py`, but since that file imports nothing, only `metadata.py` and `base.py` get loaded.
2. **String-based relationship references**: `metadata.py` uses `TYPE_CHECKING`-guarded imports and `Mapped[list["ScanSession"]]` which SQLAlchemy resolves at mapper configuration time. If `scan.py` hasn't been imported, the name `"ScanSession"` cannot be resolved.
3. **Test workarounds**: Existing tests use `import debcraft.infrastructure.models.scan  # noqa: F401` to force-load the module—confirming the root cause.

### Bug 2: Unpropagated status code

1. **`DownloadResult` lacks `status_code` field**: The dataclass has no field to carry HTTP status information from the exception.
2. **`download_file` discards exception details**: After retries exhaust, only `str(last_error)` is stored—the `status_code` attribute on `HttpServerError`/`HttpClientError` is lost.
3. **Engine log is shallow**: `engine.py` logs only `url=task.url, error=result.error`—no structured fields for status code, retry count, or error type.
4. **No success logging at engine level**: When a download succeeds, there's no DEBUG-level log identifying which package (by name) was downloaded.

## Correctness Properties

Property 1: Bug Condition - SQLAlchemy Mapper Resolution

_For any_ import of a model class from the `debcraft.infrastructure.models` package, importing via the package SHALL cause all model modules (cache, metadata, mirror, scan) to be registered with SQLAlchemy's mapper registry, so that string-based relationship references (e.g., `"ScanSession"`) resolve without `InvalidRequestError`.

**Validates: Requirements 2.1, 2.2**

Property 2: Bug Condition - Download Error Context Propagation

_For any_ download attempt where all retries are exhausted and the final exception is an `HttpServerError` or `HttpClientError`, the returned `DownloadResult` SHALL include the HTTP `status_code` from the exception, and the ERROR log SHALL include structured fields: url, attempts, error_type, error_msg, and status_code.

**Validates: Requirements 2.3, 2.4**

Property 3: Preservation - Model Import Compatibility

_For any_ existing import path that directly imports a model class (e.g., `from debcraft.infrastructure.models.metadata import RepositorySnapshot`), the import SHALL continue to work without errors, and bidirectional relationships SHALL continue to resolve correctly.

**Validates: Requirements 3.1, 3.2**

Property 4: Preservation - Download Success and 4xx Behavior

_For any_ download that succeeds (on first attempt or after retry) or fails with a 4xx client error, the fixed code SHALL produce exactly the same `DownloadResult` fields (success, sha256_verified, bytes_transferred, error, retry_count) as the original code.

**Validates: Requirements 3.3, 3.4, 3.5**

## Fix Implementation

### Changes Required

**File**: `src/debcraft/infrastructure/models/__init__.py`

**Change**: Add imports of all model modules so they register with SQLAlchemy's mapper on package import.

```python
"""SQLAlchemy ORM entity models."""

from debcraft.infrastructure.models import cache, metadata, mirror, scan  # noqa: F401
```

---

**File**: `src/debcraft/domain/mirror/values.py`

**Change**: Add `status_code: int | None = None` field to `DownloadResult`.

```python
@dataclass(frozen=True)
class DownloadResult:
    url: str
    success: bool
    sha256_verified: bool
    bytes_transferred: int
    error: str | None = None
    retry_count: int = 0
    status_code: int | None = None  # NEW
```

---

**File**: `src/debcraft/infrastructure/mirror/download.py`

**Changes**:
1. After retry loop exhausts, extract `status_code` from `last_error` if it's an `HttpServerError` or `HttpClientError`
2. Include `status_code` in the ERROR log
3. Pass `status_code` to the returned `DownloadResult`

---

**File**: `src/debcraft/infrastructure/mirror/engine.py`

**Changes**:
1. In the failure branch, include `status_code=result.status_code`, `retry_count=result.retry_count`, and `error_type` in the "Artifact download failed" log
2. In the success branch, add a DEBUG log with the package name derived from `entry.relative_path`

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bugs on unfixed code, then verify the fixes work correctly and preserve existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate both bugs BEFORE implementing the fix. Confirm or refute the root cause analysis.

**Test Plan — Bug 1**: Import `RepositorySnapshot` via the package without the `scan.py` workaround and attempt to configure the mapper. Run on UNFIXED code to observe `InvalidRequestError`.

**Test Plan — Bug 2**: Mock a download that exhausts retries with `HttpServerError(status_code=503)` and verify the returned `DownloadResult` lacks `status_code` field. Run on UNFIXED code to observe the missing field.

**Test Cases**:
1. **Mapper Resolution Test**: Import `RepositorySnapshot` without explicit `scan.py` import → call `Base.registry.configure()` → expect `InvalidRequestError` (will fail on unfixed code)
2. **Download Status Code Test**: Run `download_file` with mocked 503 responses → verify `DownloadResult` has no `status_code` attribute or it's absent (will fail on unfixed code since field doesn't exist)

**Expected Counterexamples**:
- `InvalidRequestError: When initializing mapper Mapper[RepositorySnapshot(repository_snapshots)], expression 'ScanSession' failed to locate a name`
- `DownloadResult` has no `status_code` field → `AttributeError` or assertion failure

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed functions produce the expected behavior.

**Pseudocode:**
```
FOR ALL import WHERE isBugCondition_Bug1(import) DO
  result := import_and_configure_mapper()
  ASSERT no_InvalidRequestError(result)
  ASSERT all_relationships_resolved(result)
END FOR

FOR ALL download WHERE isBugCondition_Bug2(download) DO
  result := download_file_fixed(download)
  ASSERT result.status_code = download.last_error.status_code
  ASSERT log_contains(url, attempts, error_type, status_code)
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed code produces the same result as the original.

**Pseudocode:**
```
FOR ALL import WHERE NOT isBugCondition_Bug1(import) DO
  ASSERT import_model_fixed(import) = import_model_original(import)
END FOR

FOR ALL download WHERE NOT isBugCondition_Bug2(download) DO
  ASSERT download_file_fixed(download) = download_file_original(download)
END FOR
```

**Testing Approach**: Property-based testing with Hypothesis for generating varied download scenarios (success, retry-then-success, 4xx immediate fail) and verifying `DownloadResult` fields are unchanged.

**Test Plan**: Observe behavior on UNFIXED code first for successful downloads and 4xx failures, then write property-based tests capturing that behavior.

**Test Cases**:
1. **Model Import Preservation**: Verify `from debcraft.infrastructure.models.metadata import RepositorySnapshot` still works
2. **Relationship Preservation**: Verify `RepositorySnapshot.scan_sessions` relationship resolves when both modules loaded
3. **Download Success Preservation**: Verify successful downloads return same `DownloadResult` structure
4. **4xx Immediate Fail Preservation**: Verify 4xx errors still immediately fail without retry

### Unit Tests

- Test that importing the models package loads all submodules
- Test `DownloadResult` with `status_code` field serialization
- Test `download_file` extracts status code from `HttpServerError`
- Test engine log includes structured error fields

### Property-Based Tests

- Generate random model import sequences and verify mapper always resolves
- Generate random HTTP status codes (5xx range) and verify `status_code` propagation
- Generate random successful download scenarios and verify preservation of existing fields
- Generate random 4xx status codes and verify immediate fail behavior unchanged

### Integration Tests

- Full mirror sync with a model that has cross-module relationships
- Download batch with mixed success/failure and verify log output
- Engine processing of results with status codes in structured logs
