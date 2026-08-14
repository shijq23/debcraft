# Implementation Plan: Pylint Cleanup

## Overview

This plan addresses raising the debcraft pylint score from 9.58/10 to ≥9.90/10 through configuration changes, shared module extraction, function decomposition, exception narrowing, and style fixes. Tasks are ordered so quick configuration wins come first, then new shared modules, then refactoring existing code to use them, and finally verification.

## Tasks

- [x] 1. Configuration and inline suppression quick wins
  - [x] 1.1 Add W2301, C0415, W0622 to pylint disable list and set fail-under=9.90
    - Edit `pyproject.toml` `[tool.pylint.messages_control]` `disable` list to add `"unnecessary-ellipsis"`, `"import-outside-toplevel"`, `"redefined-builtin"`
    - Add `fail-under = 9.90` to `[tool.pylint.main]`
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 11.4_

  - [x] 1.2 Add inline suppressions for E1102 (not-callable) false positives
    - Find all `func.now()` and `func.count()` call sites and add `# pylint: disable=not-callable` end-of-line comments
    - _Requirements: 9.1, 9.3, 9.4_

  - [x] 1.3 Add inline suppressions for E1128 (assignment-from-none) false positives
    - Find all affected return sites in `license_mapper.py` and add `# pylint: disable=assignment-from-none` end-of-line comments
    - _Requirements: 9.2, 9.3, 9.4_

- [x] 2. Create shared CLI modules
  - [x] 2.1 Create `src/debcraft/cli/_storage.py` with MinimalStorageEngine
    - Implement `MinimalStorageEngine` class that resolves XDG-compliant `config` and `mirror` paths
    - Raises `ValueError` for unsupported storage purposes
    - Implements the full `StorageEngine` interface (`initialize`, `shutdown`, `get_path`, `__aenter__`, `__aexit__`)
    - _Requirements: 2.1, 2.4_

  - [x] 2.2 Write property test for MinimalStorageEngine path resolution
    - **Property 3: MinimalStorageEngine path resolution correctness**
    - Create `tests/properties/infrastructure/test_cli_storage_properties.py`
    - Use `st.sampled_from(["config", "mirror"])` for purpose and `st.from_regex(r'[a-zA-Z0-9_/.-]{0,50}')` for relative path
    - Verify returned path is under XDG base dir and contains the relative suffix
    - **Validates: Requirements 2.4**

  - [x] 2.3 Create `src/debcraft/cli/_formatting.py` with format_bytes utility
    - Implement `format_bytes(n: int) -> str` returning IEC binary unit strings (B, KiB, MiB, GiB) with one decimal place for values ≥1024
    - _Requirements: 5.1_

  - [x] 2.4 Write property test for format_bytes IEC correctness
    - **Property 4: format_bytes IEC binary unit correctness**
    - Create `tests/properties/infrastructure/test_format_bytes_properties.py`
    - Use `st.integers(min_value=0, max_value=2**50)` for byte count
    - Verify correct unit selection and numeric accuracy within 0.1 of the unit
    - **Validates: Requirements 5.1**

  - [x] 2.5 Create `src/debcraft/cli/_progress.py` with progress bar factory
    - Implement `create_progress_bar(*, disabled: bool = False) -> Progress` returning a Rich Progress with standard columns (SpinnerColumn, TextColumn description, BarColumn, TextColumn percentage, TimeElapsedColumn)
    - Bind to the module-level console
    - _Requirements: 4.1, 4.2_

- [x] 3. Create shared infrastructure modules
  - [x] 3.1 Create `src/debcraft/infrastructure/scanners/_mixin.py` with ScannerMixin
    - Implement `_check_cancellation(context, artifact_path, step)` — raises ScannerError if cancelled
    - Implement `_run_filesystem_analysis(file_paths, contents_port, package_port, snapshot_id, context)` — invokes analyze_filesystem with cancellation check and progress report
    - Implement `_report_progress(context, percentage, message)` — delegates to context.progress.report
    - _Requirements: 1.1, 1.2, 1.3_

  - [x] 3.2 Write property test for ScannerMixin cancellation check
    - **Property 1: Cancellation check raises for cancelled tokens**
    - Create `tests/properties/infrastructure/test_scanner_mixin_properties.py`
    - Use `st.text()` for artifact_path and step; `st.booleans()` for is_cancelled
    - Verify raises ScannerError with path+step in message when cancelled; returns normally when not cancelled
    - **Validates: Requirements 1.1**

  - [x] 3.3 Write property test for ScannerMixin progress delegation
    - **Property 2: Progress delegation preserves arguments**
    - Add to `tests/properties/infrastructure/test_scanner_mixin_properties.py`
    - Use `st.floats(0.0, 100.0)` for percentage; `st.text(max_size=256)` for message
    - Verify context.progress.report called with identical arguments
    - **Validates: Requirements 1.3**

  - [x] 3.4 Create `src/debcraft/infrastructure/sbom_writers/_output.py` with write_sbom_output
    - Implement `write_sbom_output(output_bytes: bytes, output_path: Path) -> tuple[str, int]`
    - Encapsulates mkdir, write, SHA-256 computation, partial-file cleanup on error
    - Raises `OutputPathError` on OS errors
    - _Requirements: 3.1, 3.3_

- [x] 4. Checkpoint - Verify new modules
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Refactor CLI modules to use shared code
  - [x] 5.1 Refactor `cli/mirror.py` to import MinimalStorageEngine from `_storage.py`
    - Remove the local `_MinimalStorageEngine` class definition
    - Import `MinimalStorageEngine` from `debcraft.cli._storage`
    - Update all references (constructor calls, type hints)
    - _Requirements: 2.2, 2.3, 2.4_

  - [x] 5.2 Refactor `cli/index.py` to import MinimalStorageEngine from `_storage.py`
    - Remove the local `_MinimalStorageEngine` class definition
    - Import `MinimalStorageEngine` from `debcraft.cli._storage`
    - Update all references
    - _Requirements: 2.2, 2.3_

  - [x] 5.3 Refactor `cli/mirror.py` to use shared `format_bytes` and `create_progress_bar`
    - Remove the local `_format_bytes` function
    - Import `format_bytes` from `debcraft.cli._formatting`
    - Replace inline `Progress(...)` constructions with `create_progress_bar()` from `debcraft.cli._progress`
    - _Requirements: 4.3, 5.2_

  - [x] 5.4 Refactor `cli/index.py` to use shared `create_progress_bar`
    - Replace inline `Progress(...)` constructions with `create_progress_bar()` from `debcraft.cli._progress`
    - _Requirements: 4.3_

  - [x] 5.5 Refactor `cli/sbom.py` to use shared `format_bytes` and `create_progress_bar`
    - Import and use `format_bytes` from `debcraft.cli._formatting` if size formatting is present
    - Replace inline `Progress(...)` constructions with `create_progress_bar()` from `debcraft.cli._progress`
    - _Requirements: 4.3, 5.2_

- [x] 6. Refactor SBOM writers to use shared output helper
  - [x] 6.1 Refactor `cyclonedx.py` to use `write_sbom_output`
    - Replace inline directory creation, byte writing, cleanup, and hash logic with call to `write_sbom_output`
    - _Requirements: 3.2, 3.4_

  - [x] 6.2 Refactor `spdx23.py` to use `write_sbom_output`
    - Replace inline directory creation, byte writing, cleanup, and hash logic with call to `write_sbom_output`
    - _Requirements: 3.2, 3.4_

  - [x] 6.3 Refactor `spdx3.py` to use `write_sbom_output`
    - Replace inline directory creation, byte writing, cleanup, and hash logic with call to `write_sbom_output`
    - _Requirements: 3.2, 3.4_

- [x] 7. Refactor scanners to use ScannerMixin
  - [x] 7.1 Refactor `directory.py` scanner to use ScannerMixin
    - Add `ScannerMixin` to the class inheritance
    - Replace inline cancellation checks with `self._check_cancellation(...)`
    - Replace direct `analyze_filesystem` calls with `self._run_filesystem_analysis(...)`
    - Replace `context.progress.report(...)` calls with `self._report_progress(...)`
    - _Requirements: 1.4, 1.5_

  - [x] 7.2 Refactor `docker.py` scanner to use ScannerMixin
    - Same pattern as 7.1
    - _Requirements: 1.4, 1.5_

  - [x] 7.3 Refactor `oci.py` scanner to use ScannerMixin
    - Same pattern as 7.1
    - _Requirements: 1.4, 1.5_

  - [x] 7.4 Refactor `iso.py` scanner to use ScannerMixin
    - Same pattern as 7.1
    - _Requirements: 1.4, 1.5_

  - [x] 7.5 Refactor `qcow2.py` scanner to use ScannerMixin
    - Same pattern as 7.1
    - _Requirements: 1.4, 1.5_

  - [x] 7.6 Refactor `img.py` scanner to use ScannerMixin
    - Same pattern as 7.1
    - _Requirements: 1.4, 1.5_

  - [x] 7.7 Refactor `ami.py` scanner to use ScannerMixin
    - Same pattern as 7.1
    - _Requirements: 1.4, 1.5_

- [x] 8. Checkpoint - Verify consolidation refactoring
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Function decomposition
  - [x] 9.1 Decompose `_run_sync` in `cli/mirror.py`
    - Extract inner classes (`_CliProgressReporter`, `_CliCancellationToken`, `_CliEventBus`, `_CliLogger`) to module-level private classes
    - Extract sync loop body into a `_sync_single_repository` helper
    - Target: each helper ≤15 locals, ≤50 statements
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [x] 9.2 Decompose `verify` command in `cli/mirror.py`
    - Extract DB query logic into `_query_verified_files` helper
    - Extract checksum verification loop into `_verify_checksums` helper
    - Extract result display into `_display_verification_results` helper
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [x] 9.3 Decompose `status` command in `cli/mirror.py`
    - Extract database stat gathering into `_gather_mirror_stats` helper
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [x] 9.4 Decompose `_run_index` in `cli/index.py`
    - Extract repository discovery logic into `_discover_repos_to_index` helper
    - Extract indexing loop into `_index_repositories` helper
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [x] 9.5 Decompose `scan` in `scanners/oci.py`
    - Extract validation step into `_validate_oci_artifact` helper
    - Extract layer extraction into `_extract_oci_layers` helper
    - Extract dpkg status parsing into `_parse_dpkg_status` helper
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [x] 9.6 Decompose `scan` in `scanners/docker.py`
    - Extract validation step into `_validate_docker_artifact` helper
    - Extract manifest parsing into `_parse_docker_manifest` helper
    - Extract layer processing loop into `_process_docker_layers` helper
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [x] 9.7 Decompose complex methods in `scanners/qcow2.py`
    - Extract validation and mount logic into helper methods
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [x] 9.8 Decompose complex methods in `engine.py` and `service.py`
    - Extract workflow steps into underscore-prefixed helpers
    - Target: each helper ≤15 locals, ≤50 statements, ≤12 branches
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

- [x] 10. Exception narrowing
  - [x] 10.1 Replace broad `except Exception` with specific types in infrastructure layer
    - Replace with `OSError`, `ValueError`, `KeyError`, `TypeError`, `sqlalchemy.exc.SQLAlchemyError`, `aiohttp.ClientError`, `json.JSONDecodeError` as appropriate per call site
    - _Requirements: 7.1_

  - [x] 10.2 Add inline suppressions for justified broad exception catches
    - Add `# pylint: disable=broad-exception-caught` with justification comment at CLI top-level handlers, event bus dispatch, and plugin loaders
    - Ensure total count ≤10 across codebase
    - _Requirements: 7.2, 7.3_

- [x] 11. Checkpoint - Verify decomposition and exception narrowing
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Minor style fixes
  - [x] 12.1 Fix R1705 (unnecessary-else-after-return) across codebase
    - Remove unnecessary `else` clauses after `return` statements
    - _Requirements: 10.1, 10.13_

  - [x] 12.2 Fix R1714 (consider-using-in) across codebase
    - Replace chained equality comparisons with `in` membership tests
    - _Requirements: 10.2, 10.13_

  - [x] 12.3 Fix R1721 (unnecessary-comprehension) across codebase
    - Simplify unnecessary comprehensions to direct constructor calls
    - _Requirements: 10.3, 10.13_

  - [x] 12.4 Fix W0706 (try-except-raise) across codebase
    - Remove redundant `try/except` that only re-raises
    - _Requirements: 10.4, 10.13_

  - [x] 12.5 Fix C0121, C2801, R1732, W0246 across codebase
    - C0121: Replace `== None` / `== True` with `is None` / `is True`
    - C2801: Replace dunder calls with operators/builtins
    - R1732: Use `with` statement for resource acquisition
    - W0246: Remove useless parent/super() delegations
    - _Requirements: 10.5, 10.6, 10.7, 10.8, 10.13_

  - [x] 12.6 Fix W0611, W0613 across codebase
    - W0611: Remove unused imports
    - W0613: Prefix unused arguments with underscore (if required by interface) or remove
    - _Requirements: 10.9, 10.10, 10.13_

  - [x] 12.7 Fix C0301, C0413 across codebase
    - C0301: Reformat lines exceeding 120 characters
    - C0413: Move imports to correct position after docstrings and `__future__` imports
    - _Requirements: 10.11, 10.12, 10.13_

- [x] 13. Final verification
  - [x] 13.1 Run full pylint check and verify score ≥9.90
    - Execute `uv run pylint src/` and confirm score meets threshold
    - Verify zero findings for suppressed codes (W2301, C0415, W0622, E1102, E1128)
    - Verify zero findings for fixed style codes (R1705, R1714, R1721, W0706, C0121, C2801, R1732, W0246, W0611, W0613, C0301, C0413)
    - _Requirements: 8.4, 9.4, 10.14, 11.1_

  - [x] 13.2 Run full test suite, mypy, and ruff checks
    - Execute `uv run pytest` — all tests must pass without modification
    - Execute `uv run mypy` — zero errors
    - Execute `uv run ruff check .` and `uv run ruff format --check .` — zero errors
    - _Requirements: 11.2, 11.3_

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The project uses Python 3.13 with hatchling build system and uv for dependency management
- Hypothesis is already in dev dependencies for property-based testing
- All new modules use underscore-prefixed names (`_storage.py`, `_progress.py`, `_mixin.py`, `_output.py`) to signal internal/private status

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3"] },
    { "id": 1, "tasks": ["2.1", "2.3", "2.5", "3.1", "3.4"] },
    { "id": 2, "tasks": ["2.2", "2.4", "3.2", "3.3"] },
    { "id": 3, "tasks": ["5.1", "5.2", "5.3", "5.4", "5.5", "6.1", "6.2", "6.3"] },
    { "id": 4, "tasks": ["7.1", "7.2", "7.3", "7.4", "7.5", "7.6", "7.7"] },
    { "id": 5, "tasks": ["9.1", "9.2", "9.3", "9.4", "9.5", "9.6", "9.7", "9.8"] },
    { "id": 6, "tasks": ["10.1", "10.2"] },
    { "id": 7, "tasks": ["12.1", "12.2", "12.3", "12.4", "12.5", "12.6", "12.7"] },
    { "id": 8, "tasks": ["13.1", "13.2"] }
  ]
}
```
