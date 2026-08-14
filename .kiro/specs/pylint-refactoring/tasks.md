# Implementation Plan: Pylint Refactoring

## Overview

Refactor the debcraft Python codebase to eliminate all remaining pylint warnings and achieve a 10.00/10 score. The work is organized into three tracks: deduplication (shared utilities), complexity reduction (module splits, argument reduction, helper extraction), and style fixes. All changes are behavior-preserving.

## Tasks

- [x] 1. Create shared stanza parser utility
  - [x] 1.1 Create `src/debcraft/domain/_stanza_parser.py` with `split_stanzas`, `parse_stanza_fields`, and `parse_stanza_fields_ordered` functions
    - Implement `split_stanzas(content: str) -> list[str]` that splits content into stanza blocks separated by blank lines
    - Implement `parse_stanza_fields(stanza: str, *, preserve_continuations: bool = False) -> dict[str, str]` for key-value field extraction
    - Implement `parse_stanza_fields_ordered(stanza: str) -> list[tuple[str, str]]` preserving field order for dpkg parser compatibility
    - _Requirements: 6.1_

  - [x] 1.2 Migrate `packages_parser` and `sources_parser` to use shared stanza utility
    - Update `domain/indexer/packages_parser.py` to delegate stanza boundary detection and field extraction to `_stanza_parser`
    - Update `domain/indexer/sources_parser.py` to delegate stanza boundary detection and field extraction to `_stanza_parser`
    - Remove duplicated inline parsing logic from both modules
    - _Requirements: 6.2, 6.3_

  - [x] 1.3 Migrate `mirror/packages_parser` and `scanner/dpkg_parser` to use shared stanza utility
    - Update `domain/mirror/packages_parser.py` to use `split_stanzas` and `parse_stanza_fields(preserve_continuations=False)`
    - Update `domain/scanner/dpkg_parser.py` to use `split_stanzas` and `parse_stanza_fields_ordered`
    - Remove duplicated inline parsing logic from both modules
    - _Requirements: 6.2, 6.3_

  - [x] 1.4 Write property test for stanza parsing equivalence
    - **Property 1: Stanza Parsing Equivalence**
    - **Validates: Requirements 6.3**
    - Create `tests/properties/domain/test_stanza_parser_properties.py`
    - Generate random stanza-formatted content with Hypothesis, run both old and new implementations, assert equivalence
    - Use `@settings(max_examples=200)`

- [x] 2. Extend scanner mixin with shared scan-result/cancellation utilities
  - [x] 2.1 Add `_build_cancellation_result`, `_iterate_packages_with_cancellation`, and `_build_success_result` methods to `ScannerMixin`
    - Extend `infrastructure/scanners/_mixin.py` with the three new keyword-only-arg methods
    - `_build_cancellation_result` constructs a ScanResult for early-exit on cancellation with empty packages, cancellation diagnostic, and elapsed duration
    - `_iterate_packages_with_cancellation` checks cancellation token between entries, returns partial result if cancelled
    - `_build_success_result` constructs a ScanResult for successful completion
    - _Requirements: 5.1_

  - [x] 2.2 Migrate scanner modules to use shared mixin methods
    - Update `infrastructure/scanners/directory.py` to invoke mixin methods instead of inline ScanResult construction
    - Update `infrastructure/scanners/qcow2.py` to invoke mixin methods
    - Update `infrastructure/scanners/iso.py` to invoke mixin methods
    - Update `infrastructure/scanners/img.py` to invoke mixin methods
    - Update `infrastructure/scanners/ami.py` to invoke mixin methods
    - _Requirements: 5.2, 5.3_

  - [x] 2.3 Write property test for ScanResult construction equivalence
    - **Property 2: ScanResult Construction Equivalence**
    - **Validates: Requirements 5.3**
    - Create `tests/properties/infrastructure/scanners/test_mixin_properties.py`
    - Generate random packages, strategy, diagnostics, start_time, artifact_path with Hypothesis
    - Assert field equality on returned ScanResult
    - Use `@settings(max_examples=200)`

  - [x] 2.4 Write property test for package-iteration cancellation correctness
    - **Property 3: Package-Iteration Cancellation Correctness**
    - **Validates: Requirements 5.4**
    - Same test file as Property 2
    - Generate random package lists of length M and cancellation position N (0 ≤ N < M)
    - Assert result contains exactly first N packages and diagnostic stating "N of M" processed
    - Use `@settings(max_examples=200)`

- [x] 3. Create write-with-cancellation utility for SBOM writers
  - [x] 3.1 Create `src/debcraft/infrastructure/sbom_writers/_write_utils.py` with `write_with_cancellation` async function
    - Implement the full sequence: pre-write cancellation check, disk write via `write_sbom_output`, post-write cancellation check with file unlink, WriterResult construction
    - Use keyword-only arguments for all parameters
    - _Requirements: 7.1_

  - [x] 3.2 Migrate `cyclonedx.py` and `spdx23.py` writers to use shared write utility
    - Update `infrastructure/sbom_writers/cyclonedx.py` to invoke `write_with_cancellation` instead of inline cancellation/write/cleanup logic
    - Update `infrastructure/sbom_writers/spdx23.py` to invoke `write_with_cancellation` instead of inline logic
    - Remove duplicated inline write-with-cancellation sequences from both modules
    - _Requirements: 7.2, 7.3, 7.5_

  - [x] 3.3 Write property test for write utility hash and size correctness
    - **Property 4: Write Utility Hash and Size Correctness**
    - **Validates: Requirements 7.1, 7.3**
    - Create `tests/properties/infrastructure/sbom_writers/test_write_utils_properties.py`
    - Generate random non-empty byte sequences with Hypothesis, write to temp directory, assert sha256 and file_size match expected values
    - Use `@settings(max_examples=200)`

  - [x] 3.4 Write property test for write utility pre-cancellation safety
    - **Property 5: Write Utility Pre-Cancellation Safety**
    - **Validates: Requirements 7.4**
    - Same test file as Property 4
    - Generate random byte sequences, pre-cancel token before call
    - Assert `WriterCancellationError` is raised and no file exists at the output path
    - Use `@settings(max_examples=200)`

- [x] 4. Checkpoint - Deduplication track complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Split mirror engine module
  - [x] 5.1 Extract `_persistence.py` module from `engine.py`
    - Create `src/debcraft/infrastructure/mirror/_persistence.py`
    - Move `_upsert_repository_file`, `_batch_create_repository_files`, `_batch_update_state`, `_batch_mark_failed` into the new module
    - Update `engine.py` to import and delegate to the extracted functions
    - _Requirements: 1.1, 1.3_

  - [x] 5.2 Extract `_checksums.py` module from `engine.py`
    - Create `src/debcraft/infrastructure/mirror/_checksums.py`
    - Move `_get_local_checksums`, `_get_artifact_checksums`, `_deduplicate_entries` into the new module
    - Update `engine.py` to import and delegate to the extracted functions
    - _Requirements: 1.1, 1.3_

  - [x] 5.3 Extract `_staging.py` module from `engine.py`
    - Create `src/debcraft/infrastructure/mirror/_staging.py`
    - Move `_stage_release`, `_check_release_unchanged`, `_parse_and_store_release`, `_download_release_file` into the new module
    - Update `engine.py` to import and delegate to the extracted functions
    - Verify `engine.py` is now ≤1000 lines and public API (`sync_repository`, `SyncResult`) remains importable from the original path
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 6. Reduce positional argument counts
  - [x] 6.1 Refactor `MirrorEngine.__init__` to use keyword-only arguments
    - Add `*` separator after `download_coordinator` parameter
    - Convert remaining parameters to keyword-only
    - Update all call sites
    - _Requirements: 2.1, 2.7, 2.8, 2.9_

  - [x] 6.2 Create `DownloadSpec` dataclass and refactor `_attempt_download`
    - Create frozen dataclass bundling `expected_sha256`, `expected_size`, `timeout`
    - Refactor `_attempt_download` to accept `DownloadSpec` instead of separate positional arguments
    - Update all call sites in `download.py`
    - _Requirements: 2.2, 2.7, 2.8, 2.9_

  - [x] 6.3 Create `SyncContext` dataclass and refactor `_sync_single_repository`
    - Create frozen dataclass bundling infrastructure dependencies
    - Refactor `_sync_single_repository` to accept `SyncContext` instead of separate positional arguments
    - Update all call sites in `mirror.py`
    - _Requirements: 2.3, 2.7, 2.8, 2.9_

  - [x] 6.4 Refactor `_run_sbom` to use keyword-only arguments
    - Add `*` separator after the 5th positional parameter
    - Convert `quiet`, `progress`, `task_id` to keyword-only
    - Update all call sites in `cli/sbom.py`
    - _Requirements: 2.4, 2.7, 2.8, 2.9_

  - [x] 6.5 Refactor `WorkflowContext.__init__` to use keyword-only arguments
    - Add `*` separator after the core positional parameters
    - Convert `resource_manager`, `logger`, `event_bus` to keyword-only
    - Update all call sites
    - _Requirements: 2.5, 2.7, 2.8, 2.9_

  - [x] 6.6 Refactor `_upsert_repository_file` to use keyword-only arguments
    - Convert `state` and other trailing parameters to keyword-only
    - Update all call sites in the mirror engine
    - _Requirements: 2.6, 2.7, 2.8, 2.9_

- [x] 7. Checkpoint - Module split and argument reduction complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Reduce local variable and statement counts
  - [x] 8.1 Extract helper methods from `DockerScanner.scan` to reduce local variables
    - Extract VFS-to-dpkg-parse step into `_parse_packages_from_vfs` private method
    - Extract fallback-to-filesystem step into `_scan_filesystem_fallback` private method
    - Ensure local variable count ≤ 20 in the main `scan` method
    - _Requirements: 3.1, 3.4, 3.5_

  - [x] 8.2 Extract helper methods from `_run_sbom` to reduce local variables
    - Identify and extract logical sub-steps into private helper functions
    - Ensure local variable count ≤ 20 in `_run_sbom`
    - _Requirements: 3.2, 3.4, 3.5_

  - [x] 8.3 Extract `_write_all_formats` helper from `SBOMWorkflow.execute` to reduce statements
    - Extract the per-format write loop and result collection into `_write_all_formats`
    - Ensure statement count ≤ 50 in `execute`
    - _Requirements: 3.3, 3.4, 3.5_

- [x] 9. Reduce branch and return statement counts
  - [x] 9.1 Refactor `_validate_oci_artifact` to reduce return statements
    - Extract each validation step (oci-layout check, index.json check, manifest read) into separate private methods
    - Each helper returns either a success value or diagnostic string
    - Chain helpers in main method with early returns, keeping total ≤ 8 return statements
    - _Requirements: 4.1, 4.3, 4.4_

  - [x] 9.2 Refactor `SPDXTokenizer.tokenize` to reduce branches
    - Extract character-class dispatch into helper methods (`_consume_identifier`, `_try_document_ref`, `_try_license_ref`)
    - Use extracted methods to reduce if-elif chain depth
    - Ensure branch count ≤ 12
    - _Requirements: 4.2, 4.3, 4.4_

- [x] 10. Fix minor style issues
  - [x] 10.1 Fix broad-exception-caught warnings with pylint suppression comments
    - Add `# pylint: disable=broad-exception-caught` with inline justification at `sbom_writers/workflow.py:381`
    - Add `# pylint: disable=broad-exception-caught` with inline justification at `platform/kernel/workflow.py:302`
    - Justification must explain why catching `Exception` is intentional at these boundaries (max 120 chars)
    - _Requirements: 8.1, 8.2_

  - [x] 10.2 Fix protected-member-access warnings by adding public accessors
    - Add a public `@property` for `_config` on the owning class accessed at `engine.py:596`
    - Replace `obj._config` access with the new property
    - Add a public `registrations` property or `get_registrations()` method on the parent container class
    - Replace `container._registrations` accesses at `container.py:224,227` with the public accessor
    - _Requirements: 8.3, 8.4_

  - [x] 10.3 Fix useless-import-alias and unused-import at `mirror/errors.py:10`
    - Replace `from ... import ReleaseParseError as ReleaseParseError` with a direct import
    - Add explicit `__all__` list that includes `ReleaseParseError` to maintain re-export behavior
    - _Requirements: 8.5_

- [x] 11. Final verification checkpoint
  - Ensure all tests pass, ask the user if questions arise.
  - Run `pylint src/debcraft --score=yes` and verify 10.00/10 with zero violations of: C0302, R0917, R0914, R0915, R0911, R0912, R0801, W0718, W0212, C0414, W0611
  - _Requirements: 8.6_

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation after each major track
- Property tests validate universal correctness properties from the design document using Hypothesis
- Unit tests validate specific examples and edge cases
- The existing test suite serves as the correctness oracle — all refactoring is behavior-preserving
- Python implementation language with `src/debcraft/` layout, pytest + Hypothesis for testing

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "3.1", "10.1", "10.3"] },
    { "id": 1, "tasks": ["1.2", "1.3", "2.2", "3.2", "5.1", "5.2", "10.2"] },
    { "id": 2, "tasks": ["1.4", "2.3", "2.4", "3.3", "3.4", "5.3"] },
    { "id": 3, "tasks": ["6.1", "6.2", "6.3", "6.4", "6.5", "6.6"] },
    { "id": 4, "tasks": ["8.1", "8.2", "8.3", "9.1", "9.2"] }
  ]
}
```
