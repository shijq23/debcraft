# Implementation Plan: ISO & SquashFS Reader Adapters

## Overview

Replace the no-op `_NoOpISOReader` and `_NoOpSquashfsReader` stubs with production implementations backed by `pycdlib` and `PySquashfsImage`. The implementation covers dependency additions, adapter modules, CLI wiring, test fixtures, unit tests, and property-based tests.

## Tasks

- [x] 1. Add dependencies and create test fixture
  - [x] 1.1 Add pycdlib and PySquashfsImage to pyproject.toml dependencies
    - Add `pycdlib>=1.14` and `PySquashfsImage>=0.9` to the `[project] dependencies` list
    - Preserve all existing dependency entries unchanged
    - Run `uv sync` to verify resolution without conflicts
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

  - [x] 1.2 Create `fixtures/build-squashfs.sh` script for squashfs test fixture
    - Create a shell script following the same pattern as `fixtures/build-iso.sh`
    - The script must use `mksquashfs` to create `fixtures/images/test.squashfs`
    - Include `var/lib/dpkg/status` with a synthetic single-package entry
    - Include nested directories (`usr/bin/`, `etc/`) and a small text file for listing/read tests
    - Make the script executable
    - _Requirements: 4.1, 5.1, 6.1_

  - [x] 1.3 Generate the squashfs fixture image
    - Run `fixtures/build-squashfs.sh` to produce `fixtures/images/test.squashfs`
    - Verify the file exists and is non-empty
    - _Requirements: 4.1_

- [x] 2. Implement PyCdlibISOReader adapter
  - [x] 2.1 Create `src/debcraft/infrastructure/scanners/iso_reader_pycdlib.py`
    - Implement `PyCdlibISOReader` class with `open`, `list_dir`, `read_file`, `close` methods
    - `open(path)`: Create `PyCdlib()` instance, call `.open(path)` with Rock Ridge support
    - `list_dir(path)`: Normalize path to absolute Rock Ridge path, list children, filter `.`/`..`, return basenames
    - `read_file(path)`: Normalize path, open via `open_file_from_iso(rr_path=...)`, read all bytes
    - `close()`: Call `_iso.close()` if open, set to None; safe to call multiple times
    - Map `pycdlib.PyCdlibInvalidInput` → `FileNotFoundError`, `pycdlib.PyCdlibInvalidISO` → `OSError`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.1, 3.2, 3.3, 3.4, 9.1, 9.3, 9.5_

  - [x] 2.2 Write unit tests for PyCdlibISOReader
    - Create `tests/unit/infrastructure/test_iso_reader_pycdlib.py`
    - Use the existing `fixtures/images/test.iso` fixture
    - Test: open valid ISO, open nonexistent path raises OSError, open invalid file raises OSError
    - Test: close without open, close then operations raise
    - Test: list_dir root, list_dir subdirectory, list_dir nonexistent raises, list_dir on file raises
    - Test: read_file valid, read_file nonexistent raises, read_file on directory raises
    - Test: path with and without leading slash equivalence
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.1, 3.2, 3.3, 10.1, 10.3_

  - [x] 2.3 Write property test for ISO directory listing bare names
    - **Property 1: ISO directory listing entries are bare names**
    - Create test in `tests/properties/infrastructure/scanners/test_iso_squashfs_readers_properties.py`
    - Walk all directories in fixture ISO, assert each entry has no "/" and is not "." or ".."
    - **Validates: Requirements 2.5, 10.3**

  - [x] 2.4 Write property test for ISO path round-trip composability
    - **Property 2: ISO path round-trip composability**
    - For randomly selected directories from fixture ISO, compose paths with entries and verify they are valid args to `read_file()` or `list_dir()`
    - Use `st.sampled_from(all_directories_in_iso)` strategy
    - `@settings(max_examples=100)`
    - **Validates: Requirements 10.1**

- [x] 3. Implement PySquashfsImageReader adapter
  - [x] 3.1 Create `src/debcraft/infrastructure/scanners/squashfs_reader_pysquashfsimage.py`
    - Implement `PySquashfsImageReader` class with `open`, `read_file`, `list_dir`, `close` methods
    - `open(data)`: Validate non-empty, check not already open, create `io.BytesIO(data)`, parse with `SquashFsImage`
    - `read_file(path)`: Normalize path (strip leading `/`), navigate inode tree, raise `FileNotFoundError` if missing/directory
    - `list_dir(path)`: Normalize path (empty = root), navigate inode tree, return basenames, raise `FileNotFoundError` if missing/file
    - `close()`: Set `_image = None`, `_open = False`; safe to call multiple times
    - Map invalid squashfs magic/parse errors → `OSError`, path not found → `FileNotFoundError`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.2, 6.3, 6.4, 6.5, 9.2, 9.4, 9.6_

  - [x] 3.2 Write unit tests for PySquashfsImageReader
    - Create `tests/unit/infrastructure/test_squashfs_reader_pysquashfsimage.py`
    - Load `fixtures/images/test.squashfs` for valid-image tests
    - Test: open valid squashfs, open empty bytes raises OSError, open invalid bytes raises OSError
    - Test: open when already open raises OSError, close without open, close releases resources
    - Test: list_dir root, list_dir subdirectory, list_dir nonexistent raises, list_dir on file raises
    - Test: read_file valid, read_file nonexistent raises, read_file on directory raises
    - Test: leading slash equivalence (`read_file("/var/lib/dpkg/status") == read_file("var/lib/dpkg/status")`)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.2, 6.3, 6.4, 6.5, 10.2, 10.4_

  - [x] 3.3 Write property test for squashfs directory listing bare names
    - **Property 3: Squashfs directory listing entries are bare names**
    - Walk all directories in fixture squashfs, assert each entry has no "/" character
    - **Validates: Requirements 10.4**

  - [x] 3.4 Write property test for squashfs path round-trip composability
    - **Property 4: Squashfs path round-trip composability**
    - For randomly selected directories from fixture squashfs, compose paths and verify validity
    - Use `st.sampled_from(all_directories_in_squashfs)` strategy
    - `@settings(max_examples=100)`
    - **Validates: Requirements 10.2**

  - [x] 3.5 Write property test for squashfs leading-slash path normalization
    - **Property 5: Squashfs leading-slash path normalization**
    - For randomly selected valid file/dir paths, verify `read_file("/" + P)` equals `read_file(P)` and `list_dir("/" + P)` equals `list_dir(P)`
    - Use `st.sampled_from(all_paths_in_squashfs)` strategy
    - `@settings(max_examples=100)`
    - **Validates: Requirements 5.5, 6.5**

  - [x] 3.6 Write property test for squashfs invalid data rejection
    - **Property 6: Squashfs invalid data rejection**
    - Generate random byte sequences that don't start with valid squashfs magic, verify `open(data)` raises `OSError`
    - Use `st.binary(min_size=0, max_size=1024)` filtered to exclude valid squashfs magic bytes
    - `@settings(max_examples=100)`
    - **Validates: Requirements 4.2**

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Wire production readers into CLI and integration test
  - [x] 5.1 Update `src/debcraft/cli/sbom.py` to use production readers
    - Replace `_NoOpISOReader()` with `PyCdlibISOReader()` in `_create_scanner_registry()`
    - Replace `_NoOpSquashfsReader()` with `PySquashfsImageReader()` in `_create_scanner_registry()`
    - Add guarded imports with try/except raising `ImportError` with helpful message if dependencies missing
    - Remove or deprecate the `_NoOpISOReader` and `_NoOpSquashfsReader` classes
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

  - [x] 5.2 Write unit tests for CLI wiring
    - Add tests in `tests/unit/test_cli_sbom.py` or a new test file
    - Test: scanner registry uses production reader types (not no-op)
    - Test: missing dependency raises ImportError with descriptive message (mock import failure)
    - _Requirements: 7.1, 7.2, 7.4_

  - [x] 5.3 Write integration test for ISO scanning end-to-end
    - Create `tests/integration/scanner/test_iso_scanner_integration.py`
    - Scan `fixtures/images/test.iso` through `ISOScanner` with production readers
    - Verify the full pipeline returns at least 1 package (base-files)
    - _Requirements: 7.3_

- [x] 6. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The design uses Python — all implementation is in Python
- Existing fixture `fixtures/images/test.iso` has `var/lib/dpkg/status` with a `base-files` package entry
- The squashfs fixture must be generated by `mksquashfs` (requires `squashfs-tools` package on the build system)

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3"] },
    { "id": 2, "tasks": ["2.1", "3.1"] },
    { "id": 3, "tasks": ["2.2", "2.3", "2.4", "3.2", "3.3", "3.4", "3.5", "3.6"] },
    { "id": 4, "tasks": ["5.1"] },
    { "id": 5, "tasks": ["5.2", "5.3"] }
  ]
}
```
