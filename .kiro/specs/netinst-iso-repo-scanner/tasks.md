# Implementation Plan: NETINST ISO Repository Scanner

## Overview

Extend the existing `ISOScanner` in `src/debcraft/infrastructure/scanners/iso.py` with private methods to detect and scan Debian NETINST ISO images structured as package repositories. The implementation adds repository structure detection between squashfs search and direct rootfs dpkg status check in the existing fallback chain, using `split_stanzas()` and `parse_stanza_fields_ordered()` for Packages file parsing, and `gzip` for decompression.

## Tasks

- [x] 1. Implement repository structure detection and scanning orchestration
  - [x] 1.1 Add `_has_repository_structure()` method to ISOScanner
    - Add a private method that calls `self._iso_reader.list_dir("")` and checks for `"dists"` in the returned entries
    - Return `False` on any `OSError` or `FileNotFoundError` (graceful degradation)
    - Record a diagnostic message when repository structure is detected
    - _Requirements: 1.1, 1.2, 1.4_

  - [x] 1.2 Add `_scan_repository()` orchestration method to ISOScanner
    - Add a private method that orchestrates: check repository structure → discover Packages files → parse each → deduplicate → return ScanResult or None
    - Accept `artifact`, `context`, `start_time`, `diagnostics` parameters
    - Check cancellation token after discovering codenames, after each Packages file discovery, and after each parse
    - Return `None` when no packages are found (signals fallback to caller)
    - Return a `ScanResult` with `strategy=DPKG_METADATA` when packages are found
    - _Requirements: 5.1, 5.2, 5.3, 6.1, 6.2, 7.2, 7.3_

  - [x] 1.3 Modify `ISOScanner.scan()` to insert repository scanning in strategy chain
    - After squashfs search fails and before `_scan_direct_rootfs()`, call `_has_repository_structure()` then `_scan_repository()`
    - If `_scan_repository()` returns a `ScanResult` (not None), return it directly
    - If it returns `None`, fall through to `_scan_direct_rootfs()` as before
    - Handle `ScannerError` from cancellation in the repository scanning path
    - _Requirements: 7.1, 7.2, 7.3, 1.3_

- [x] 2. Implement Packages file discovery
  - [x] 2.1 Add `_discover_packages_files()` method to ISOScanner
    - Walk `dists/<codename>/<component>/binary-<arch>/` by listing directories at each level
    - Enumerate codenames as subdirectories of `dists/`
    - For each codename, enumerate components by excluding known metadata entries (Release, InRelease)
    - For each component, identify `binary-<arch>/` directories by matching the naming pattern
    - In each architecture directory: if `Packages.gz` exists use it, else if `Packages` exists use it
    - Record diagnostics for I/O errors on directory listings and continue
    - Record a diagnostic if no Packages files are found at all
    - Return a list of discovered Packages file paths
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

- [x] 3. Implement Packages file parsing and deduplication
  - [x] 3.1 Add `_parse_packages_file()` method to ISOScanner
    - Read file bytes via `self._iso_reader.read_file(path)`
    - If path ends with `.gz`, decompress with `gzip.decompress()`
    - Decode bytes as UTF-8 (with `errors="replace"`)
    - Split into stanzas using `split_stanzas()` from `debcraft.domain._stanza_parser`
    - Parse each stanza with `parse_stanza_fields_ordered()`
    - For stanzas with `Package` and `Version` fields, create `IdentifiedPackage(status="installed")`
    - Skip stanzas missing `Package` or `Version` and record diagnostics identifying the missing field
    - Record diagnostics for I/O errors and gzip decompression failures; return empty list on failure
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 8.1, 8.2, 8.3_

  - [x] 3.2 Add `_deduplicate_packages()` method to ISOScanner
    - Deduplicate by `(name, version, architecture)` tuple
    - Retain only first occurrence, preserving discovery order
    - Record a summary diagnostic with total deduplicated count and number of Packages files processed
    - _Requirements: 4.1, 4.2, 4.4_

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Unit tests for repository scanning integration scenarios
  - [x] 5.1 Write unit tests for strategy ordering and fallback logic
    - Test: ISO with no squashfs + no `dists/` falls through to direct rootfs (Req 1.3)
    - Test: I/O error on root listing triggers fallback (Req 1.4)
    - Test: Repository detected + packages found short-circuits fallback (Req 7.2)
    - Test: Repository detected + 0 packages triggers fallback to direct rootfs (Req 7.3)
    - Test: Strategy order is squashfs → repo → direct rootfs → filesystem (Req 7.1)
    - Use mock `ISOReader` with configurable directory and file responses
    - Place in `tests/unit/infrastructure/test_iso_scanner_repo.py`
    - _Requirements: 1.3, 1.4, 7.1, 7.2, 7.3_

  - [x] 5.2 Write unit tests for Packages file discovery edge cases
    - Test: Empty `dists/` directory returns no Packages files (Req 2.6)
    - Test: Metadata entries (Release, InRelease) are not treated as components (Req 2.2)
    - Test: `Packages.gz` preferred over `Packages` when both exist (Req 2.4)
    - Test: I/O error on one codename doesn't abort discovery of others (Req 2.5)
    - Test: ScanResult fields (artifact_path, strategy, duration) are correct (Req 5.1, 5.2)
    - Place in `tests/unit/infrastructure/test_iso_scanner_repo.py`
    - _Requirements: 2.2, 2.4, 2.5, 2.6, 5.1, 5.2_

  - [x] 5.3 Write unit tests for Packages file parsing edge cases
    - Test: Gzip decompression failure records diagnostic and continues (Req 3.5)
    - Test: Stanza missing `Package` field is skipped with diagnostic (Req 8.2)
    - Test: Stanza missing `Version` field is skipped with diagnostic (Req 8.2)
    - Test: `.udeb` packages (Section: debian-installer) included with status "installed" (Req 8.3)
    - Test: Multiple Packages files with overlapping packages are deduplicated (Req 4.2)
    - Test: Partial file failures don't prevent successful files from contributing (Req 4.3)
    - Test: Summary diagnostic includes count info (Req 4.4)
    - Place in `tests/unit/infrastructure/test_iso_scanner_repo.py`
    - _Requirements: 3.5, 4.2, 4.3, 4.4, 8.2, 8.3_

  - [x] 5.4 Write unit tests for cancellation during repository scanning
    - Test: Cancellation after codename discovery returns partial result with diagnostic (Req 6.1, 6.2)
    - Test: Cancellation after parsing a Packages file returns packages parsed so far (Req 6.2)
    - Test: ScanResult from cancellation has DPKG_METADATA strategy (Req 6.2)
    - Place in `tests/unit/infrastructure/test_iso_scanner_repo.py`
    - _Requirements: 6.1, 6.2_

- [x] 6. Checkpoint - Ensure all unit tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Property-based tests for repository scanning correctness
  - [x] 7.1 Write property test for repository detection from root entries
    - **Property 1: Repository Detection from Root Entries**
    - For any set of root directory entries, scanner identifies repository structure iff "dists" is present
    - Use Hypothesis `st.lists(st.text())` strategy for root entries
    - Place in `tests/properties/infrastructure/scanners/test_iso_repo_scanner_properties.py`
    - **Validates: Requirements 1.1, 1.2**

  - [x] 7.2 Write property test for Packages file discovery naming patterns
    - **Property 2: Packages File Discovery Respects Naming Patterns**
    - For any directory structure under `dists/`, Packages files are discovered only in `binary-<arch>/` dirs; Release/InRelease excluded from components
    - Use mock directory trees generated by Hypothesis
    - Place in `tests/properties/infrastructure/scanners/test_iso_repo_scanner_properties.py`
    - **Validates: Requirements 2.2, 2.3**

  - [x] 7.3 Write property test for Packages.gz preference
    - **Property 3: Packages.gz Preference**
    - For any arch dir with both Packages and Packages.gz, only Packages.gz is used
    - Place in `tests/properties/infrastructure/scanners/test_iso_repo_scanner_properties.py`
    - **Validates: Requirements 2.4**

  - [x] 7.4 Write property test for gzip decompression round-trip
    - **Property 4: Gzip Decompression Round-Trip**
    - For any valid Packages content, gzip-compressing then scanner-decompressing produces the same packages
    - Use `st.text()` for Packages content generation
    - Place in `tests/properties/infrastructure/scanners/test_iso_repo_scanner_properties.py`
    - **Validates: Requirements 3.2**

  - [x] 7.5 Write property test for status-less stanzas producing installed packages
    - **Property 5: Status-less Stanzas Produce Installed Packages**
    - For any RFC822 stanza with Package and Version but no Status, produces IdentifiedPackage with status "installed"
    - Generate stanzas with Hypothesis strategies
    - Place in `tests/properties/infrastructure/scanners/test_iso_repo_scanner_properties.py`
    - **Validates: Requirements 3.4, 8.1, 8.3**

  - [x] 7.6 Write property test for missing required fields diagnostics
    - **Property 6: Missing Required Fields Produce Diagnostics**
    - For any stanza missing Package or Version, scanner skips it and produces a diagnostic
    - Place in `tests/properties/infrastructure/scanners/test_iso_repo_scanner_properties.py`
    - **Validates: Requirements 8.2**

  - [x] 7.7 Write property test for deduplication first-occurrence
    - **Property 7: Deduplication Retains First Occurrence**
    - For any sequence of packages with duplicates, only first occurrence of each (name, version, arch) is retained, order preserved
    - Use `st.lists(st.tuples(st.text(), st.text(), st.text()))` for package identity generation
    - Place in `tests/properties/infrastructure/scanners/test_iso_repo_scanner_properties.py`
    - **Validates: Requirements 4.1, 4.2**

  - [x] 7.8 Write property test for partial failure resilience
    - **Property 8: Partial Failure Resilience**
    - For any set of Packages paths where some fail, all successful files contribute packages and each failure produces a diagnostic
    - Place in `tests/properties/infrastructure/scanners/test_iso_repo_scanner_properties.py`
    - **Validates: Requirements 2.5, 3.5, 4.3**

  - [x] 7.9 Write property test for cancellation partial results
    - **Property 9: Cancellation Produces Partial Results**
    - For any cancellation point, scanner returns packages parsed before that point plus cancellation diagnostic
    - Place in `tests/properties/infrastructure/scanners/test_iso_repo_scanner_properties.py`
    - **Validates: Requirements 6.1, 6.2**

  - [x] 7.10 Write property test for scan result invariants
    - **Property 10: Scan Result Invariants**
    - For any execution, duration_seconds >= 0 and diagnostics order matches recording order
    - Place in `tests/properties/infrastructure/scanners/test_iso_repo_scanner_properties.py`
    - **Validates: Requirements 5.2, 5.3**

  - [x] 7.11 Write property test for successful repository scan short-circuiting fallback
    - **Property 11: Successful Repository Scan Short-Circuits Fallback**
    - For any ISO with dists/ producing ≥1 package, scanner returns without calling direct rootfs or filesystem fallback
    - Place in `tests/properties/infrastructure/scanners/test_iso_repo_scanner_properties.py`
    - **Validates: Requirements 7.2**

- [x] 8. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design
- Unit tests validate specific integration scenarios and edge cases
- All tests use mock `ISOReader` — no real ISO fixture needed for repository scanning tests
- The implementation modifies only `src/debcraft/infrastructure/scanners/iso.py` — no new modules
- Uses existing `split_stanzas()` and `parse_stanza_fields_ordered()` from `debcraft.domain._stanza_parser`
- Python `gzip.decompress()` handles Packages.gz decompression

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "3.2"] },
    { "id": 1, "tasks": ["2.1", "3.1"] },
    { "id": 2, "tasks": ["1.2"] },
    { "id": 3, "tasks": ["1.3"] },
    { "id": 4, "tasks": ["5.1", "5.2", "5.3", "5.4"] },
    { "id": 5, "tasks": ["7.1", "7.2", "7.3", "7.4", "7.5", "7.6", "7.7", "7.8", "7.9", "7.10", "7.11"] }
  ]
}
```
