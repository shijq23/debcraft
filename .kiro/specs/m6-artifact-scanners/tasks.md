# Implementation Plan: M6 Artifact Scanners

## Overview

Implement the artifact scanning subsystem that identifies installed Debian packages inside various artifact types (directories, Docker images, OCI layouts, ISO images, QCOW2 disks, raw disk images, and AMI images). The implementation follows clean architecture: domain layer value objects and ports first, then pure-function parsers, then infrastructure scanners, registry, enrichment, and bootstrap wiring.

## Tasks

- [x] 1. Domain layer value objects and errors
  - [x] 1.1 Create scanner domain value objects
    - Create `src/debcraft/domain/scanner/__init__.py` (empty package init)
    - Create `src/debcraft/domain/scanner/values.py` with frozen dataclasses: `ArtifactType` enum, `ScanningStrategy` enum, `VALID_PACKAGE_STATUSES` frozenset, `Artifact`, `IdentifiedPackage`, `PackageEnrichment`, `EnrichedPackage`, `ScanResult`
    - Follow existing pattern from `src/debcraft/domain/package_intelligence/values.py` (frozen dataclasses, `field(default_factory=...)` for mutable defaults)
    - _Requirements: 1.3, 1.4, 1.5, 1.9_

  - [x] 1.2 Create scanner domain errors
    - Create `src/debcraft/domain/scanner/errors.py` with: `ScannerError` (base), `ArtifactAccessError`, `UnsupportedArtifactTypeError`, `ScannerDependencyError`, `ArtifactFormatError`
    - `ArtifactAccessError` stores `path` and `reason` attributes
    - `UnsupportedArtifactTypeError` stores `artifact_type` and `registered` list
    - _Requirements: 1.8, 8.4, 8.10, 9.9_

  - [x] 1.3 Create scanner domain port interfaces
    - Create `src/debcraft/domain/scanner/ports.py` with Protocol classes: `ArtifactScanner` (async `scan` method), `ContentsIndexPort` (async `find_owners`), `PackageLookupPort` (async `find_by_name`), `GuestfsInspector` (`open_image`, `inspect_os`, `mount_readonly`, `read_file`, `ls`, `close`)
    - Follow existing port pattern from `src/debcraft/domain/package_intelligence/ports.py`
    - _Requirements: 1.1, 1.2, 1.6, 11.1, 16.3_

  - [x] 1.4 Write unit tests for value objects
    - Create `tests/unit/domain/scanner/__init__.py` and `tests/unit/domain/scanner/test_values.py`
    - Test frozen behavior (immutability), required fields, default values, enum membership
    - _Requirements: 1.3, 1.4, 1.5_

- [x] 2. dpkg status parser and printer (domain pure functions)
  - [x] 2.1 Implement dpkg status parser
    - Create `src/debcraft/domain/scanner/dpkg_parser.py` with: `DpkgStanza` frozen dataclass (ordered field list with `get` helper), `DpkgParseResult` frozen dataclass, `parse_dpkg_status(content: str) -> DpkgParseResult` pure function
    - Implement `_split_stanzas`, `_parse_stanza_fields` (continuation line handling), `_classify_package` (status field parsing: include "install ok installed", "hold ok installed", "install ok config-files"; exclude "deinstall"/"purge"; skip missing Package/Version fields with diagnostic)
    - Handle empty/whitespace input returning empty list; missing Architecture field uses empty string
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10_

  - [x] 2.2 Implement dpkg status printer
    - Create `src/debcraft/domain/scanner/dpkg_printer.py` with: `format_dpkg_status(stanzas: list[DpkgStanza]) -> str` and helpers `_format_stanza`, `_format_field_value`
    - Continuation lines prefixed with single space; empty lines in multiline values become ` .\n`; stanzas separated by one blank line; output ends with one trailing newline; empty list returns empty string; field order preserved
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x] 2.3 Write property test for dpkg round-trip (Property 1)
    - **Property 1: dpkg Status Round-Trip**
    - Create `tests/properties/domain/scanner/__init__.py` and `tests/properties/domain/scanner/test_dpkg_roundtrip.py`
    - Generate random valid dpkg status file text, parse → format → parse again, assert IdentifiedPackage lists are equal
    - Use Hypothesis strategies: `st_dpkg_stanza()`, `st_dpkg_status_file()`
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

  - [x] 2.4 Write property test for dpkg classification (Property 2)
    - **Property 2: dpkg Parser Classification Correctness**
    - Create `tests/properties/domain/scanner/test_dpkg_classification.py`
    - Generate stanzas with various Status field combinations; assert "install ok installed" → included with status "installed"; assert "deinstall"/"purge" → excluded
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.7, 2.9**

  - [x] 2.5 Write unit tests for dpkg parser edge cases
    - Create `tests/unit/domain/scanner/test_dpkg_parser.py`
    - Test: empty input, whitespace-only input, missing Package field, missing Version field, missing Architecture field, continuation lines, multiline fields, "config-files" status
    - _Requirements: 2.4, 2.5, 2.6, 2.10_

- [x] 3. Filesystem analyzer (domain layer)
  - [x] 3.1 Implement filesystem analyzer
    - Create `src/debcraft/domain/scanner/filesystem_analyzer.py` with: `FilesystemAnalysisResult` frozen dataclass, `analyze_filesystem` async function
    - Implement: truncate to max_paths (record diagnostic if truncated), batch-query ContentsIndexPort, deduplicate by package name, query PackageLookupPort for version/arch, skip unresolved packages with diagnostic, set status to "inferred"
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8_

  - [x] 3.2 Write property test for filesystem analyzer invariants (Property 9)
    - **Property 9: Filesystem Analyzer Output Invariants**
    - Create `tests/properties/domain/scanner/test_fs_analyzer.py`
    - Generate random file path lists, mock ContentsIndexPort returning duplicates; assert no duplicate package names in output; assert all statuses equal "inferred"
    - **Validates: Requirements 11.3, 11.4**

  - [x] 3.3 Write property test for filesystem analyzer path limit (Property 10)
    - **Property 10: Filesystem Analyzer Path Limit**
    - Create `tests/properties/domain/scanner/test_fs_limit.py`
    - Generate lists exceeding max_paths; assert exactly max_paths processed; assert diagnostic mentions skipped count
    - **Validates: Requirements 11.6, 11.8**

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Infrastructure scanner implementations (part 1: directory and container scanners)
  - [x] 5.1 Implement DirectoryScanner
    - Create `src/debcraft/infrastructure/scanners/__init__.py` (empty package init)
    - Create `src/debcraft/infrastructure/scanners/directory.py` with `DirectoryScanner` class
    - Constructor receives `ContentsIndexPort` and `PackageLookupPort`
    - Implement `scan`: validate directory exists, check `<path>/var/lib/dpkg/status`, parse with `parse_dpkg_status` if found and readable, fall back to `analyze_filesystem` otherwise, check cancellation between package entries, report progress, apply symlink containment via `_is_safe_path`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8_

  - [x] 5.2 Write property test for symlink containment (Property 6)
    - **Property 6: Symlink Containment**
    - Create `tests/properties/domain/scanner/test_symlink_safety.py`
    - Generate symlink targets resolving outside artifact root; assert scanner skips them and does not access the target
    - **Validates: Requirements 4.7**

  - [x] 5.3 Implement DockerScanner
    - Create `src/debcraft/infrastructure/scanners/docker.py` with `DockerScanner` class
    - Constructor receives `ContentsIndexPort` and `PackageLookupPort`
    - Implement `scan`: open tarball, read `manifest.json`, select first image entry, extract layers bottom-to-top into virtual filesystem dict, apply whiteout semantics (`.wh.*` and `.wh..wh..opq`), locate `var/lib/dpkg/status` in merged fs, parse or fall back, check cancellation between layers
    - Implement `_apply_whiteouts` and `_merge_layer` helpers
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9_

  - [x] 5.4 Implement OCIScanner
    - Create `src/debcraft/infrastructure/scanners/oci.py` with `OCIScanner` class
    - Implement `scan`: validate `oci-layout` file (imageLayoutVersion "1.0.0"), read `index.json`, read manifest, extract layer blobs (gzip and zstd), merge with whiteout handling, locate dpkg status, parse or return empty with diagnostic
    - Support `SUPPORTED_MEDIA_TYPES` (tar+gzip, tar+zstd), skip unsupported with diagnostic
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 6.11_

  - [x] 5.5 Write property test for layer merge with whiteouts (Property 7)
    - **Property 7: Layer Merge with Whiteouts**
    - Create `tests/properties/domain/scanner/test_layer_merge.py`
    - Generate layer sequences with regular files and whiteout markers; assert whited-out files do not appear in merged filesystem; assert opaque whiteouts clear entire directory from lower layers
    - **Validates: Requirements 5.2, 5.3, 6.6**

- [x] 6. Infrastructure scanner implementations (part 2: disk image and ISO scanners)
  - [x] 6.1 Implement ISOScanner
    - Create `src/debcraft/infrastructure/scanners/iso.py` with `ISOScanner` class and `ISOReader`/`SquashfsReader` Protocol interfaces
    - Constructor receives `ISOReader`, `SquashfsReader`, `ContentsIndexPort`, `PackageLookupPort`
    - Implement `scan`: open ISO, search squashfs at known paths (`live/filesystem.squashfs`, `casper/filesystem.squashfs`, `install/filesystem.squashfs`), decompress squashfs, read dpkg status from rootfs, fall back to FilesystemAnalyzer, check cancellation at each step
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9_

  - [x] 6.2 Implement QCOW2Scanner
    - Create `src/debcraft/infrastructure/scanners/qcow2.py` with `QCOW2Scanner` class and `QCOW2_MAGIC` constant
    - Constructor receives optional `GuestfsInspector`, `ContentsIndexPort`, `PackageLookupPort`
    - Implement `scan`: check guestfs availability, validate QCOW2 magic bytes, open image via guestfs, inspect OS roots, mount first root read-only, read `/var/lib/dpkg/status`, parse or fall back, check cancellation at major boundaries, report progress
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 8.10_

  - [x] 6.3 Implement IMGScanner
    - Create `src/debcraft/infrastructure/scanners/img.py` with `IMGScanner` class
    - Constructor receives optional `GuestfsInspector`, `ContentsIndexPort`, `PackageLookupPort`
    - Implement `scan`: check guestfs availability, open image, enumerate partitions, for each partition mount read-only and check for `/var/lib/dpkg/status`, use first found, fall back to FilesystemAnalyzer if none found, check cancellation between partitions
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9_

  - [x] 6.4 Implement AMIScanner
    - Create `src/debcraft/infrastructure/scanners/ami.py` with `AMIScanner` class
    - Constructor receives `QCOW2Scanner` and `IMGScanner`
    - Implement `scan`: check cancellation, read first 4 bytes, if QCOW2 magic delegate to QCOW2Scanner, otherwise delegate to IMGScanner, propagate ScanResult unchanged
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7_

  - [x] 6.5 Write property test for AMI format detection (Property 8)
    - **Property 8: AMI Format Detection Correctness**
    - Create `tests/properties/domain/scanner/test_ami_detection.py`
    - Generate file headers with/without QCOW2 magic; assert correct delegation; assert ScanResult propagated unmodified
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.4**

- [x] 7. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Scanner registry and cache infrastructure
  - [x] 8.1 Implement ScannerRegistry
    - Create `src/debcraft/infrastructure/scanners/registry.py` with `ScannerRegistry` class
    - Implement `load_from_entry_points`: query `importlib.metadata` for `debcraft.scanners` group, load each entry point, validate protocol conformance (async `scan` method), map to ArtifactType enum, handle priority (higher wins, lexicographic tiebreak), record diagnostics for failures
    - Implement `get_scanner`: return registered scanner or raise `UnsupportedArtifactTypeError`
    - Implement `_validate_protocol`: check for async `scan` method presence
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 12.8_

  - [x] 8.2 Write property test for registry priority selection (Property 11)
    - **Property 11: Scanner Registry Priority Selection**
    - Create `tests/properties/infrastructure/test_registry_priority.py`
    - Generate multiple scanner registrations for same ArtifactType with varying priorities; assert highest priority wins; assert lexicographic tiebreak on equal priority
    - **Validates: Requirements 12.8**

  - [x] 8.3 Create CachedEnrichment SQLAlchemy model
    - Add `CachedEnrichment` class to `src/debcraft/infrastructure/models/cache.py`
    - Fields: id, package_name, version, architecture, snapshot_id, source_package, maintainer, homepage, depends, section, priority, description, sha256, download_url, purl, license_expressions_json, local_deb_path
    - UniqueConstraint on (package_name, version, architecture, snapshot_id), Index on (package_name, version)
    - Follow existing cache model pattern (e.g. `ParsedDep5`, `ChecksumCache`)
    - _Requirements: 17.1, 17.4, 17.5_

  - [x] 8.4 Implement EnrichmentCacheAdapter
    - Create `src/debcraft/infrastructure/scanners/cache_adapter.py` with `EnrichmentCacheAdapter` class
    - Constructor receives `async_sessionmaker[AsyncSession]`
    - Implement `get(package_name, version, architecture, snapshot_id) -> PackageEnrichment | None`: query CachedEnrichment, deserialize license_expressions_json
    - Implement `store(package_name, version, architecture, snapshot_id, enrichment) -> None`: upsert CachedEnrichment, serialize license_expressions to JSON
    - Handle database errors gracefully (return None / log warning)
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5, 17.7_

  - [x] 8.5 Implement MetadataEnricher
    - Create `src/debcraft/infrastructure/scanners/enricher.py` with `MetadataEnricher` class
    - Constructor receives `EnrichmentCacheAdapter`
    - Implement `enrich(packages, context)`: resolve latest published RepositorySnapshot, for each package check cache then query PackageRepository/LicenseRepository from WorkflowContext scope, generate PURL/download URL if services available, store in cache, return enriched packages and diagnostics
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8, 14.9, 14.10, 15.1, 15.2, 15.3, 15.4, 15.5_

  - [x] 8.6 Write property test for cache equivalence (Property 12)
    - **Property 12: Cache Equivalence**
    - Create `tests/properties/infrastructure/test_cache_equiv.py`
    - Generate identified packages, mock repos with consistent data; assert cached enrichment equals fresh query result
    - **Validates: Requirements 17.1, 17.2, 17.3, 17.5**

- [x] 9. Bootstrap and entry point wiring
  - [x] 9.1 Implement scanner bootstrap function
    - Create `src/debcraft/infrastructure/scanners/bootstrap.py` with `scanner_bootstrap(container: Container) -> None`
    - Create and load ScannerRegistry, register as singleton instance
    - Register scoped: MetadataEnricher, EnrichmentCacheAdapter
    - Follow existing pattern from `src/debcraft/infrastructure/bootstrap.py`
    - _Requirements: 16.5_

  - [x] 9.2 Register entry points in pyproject.toml
    - Add `[project.entry-points."debcraft.scanners"]` section to `pyproject.toml`
    - Register all 7 scanner implementations: directory, docker, oci, iso, qcow2, img, ami
    - _Requirements: 12.1_

  - [x] 9.3 Write unit tests for scanner registry with entry points
    - Create `tests/unit/infrastructure/scanner/__init__.py` and `tests/unit/infrastructure/scanner/test_registry.py`
    - Test: successful loading, ImportError handling, protocol validation failure, unsupported type error, priority selection
    - _Requirements: 12.3, 12.5, 12.6, 12.7_

- [x] 10. Cooperative cancellation, progress, and statelessness verification
  - [x] 10.1 Write property test for scanner statelessness (Property 3)
    - **Property 3: Scanner Statelessness**
    - Create `tests/properties/domain/scanner/test_scanner_stateless.py`
    - Call scan twice on same scanner instance with same artifact (mock WorkflowContext without cancellation); assert identical packages, strategy, artifact_path
    - **Validates: Requirements 1.7**

  - [x] 10.2 Write property test for cancellation produces valid subset (Property 4)
    - **Property 4: Cancellation Produces Valid Subset**
    - Create `tests/properties/domain/scanner/test_cancellation.py`
    - Generate dpkg status files, cancel at random point; assert partial result is prefix of full result; assert diagnostic mentions cancellation
    - **Validates: Requirements 4.5, 13.1, 13.2, 13.3**

  - [x] 10.3 Write property test for progress monotonicity (Property 5)
    - **Property 5: Progress Monotonicity**
    - Create `tests/properties/domain/scanner/test_progress.py`
    - Capture progress reports during scan; assert monotonically non-decreasing; assert final == 100.0 on successful completion
    - **Validates: Requirements 13.4, 13.5**

- [x] 11. Architecture compliance and integration
  - [x] 11.1 Verify import-linter compliance
    - Run `lint-imports` to confirm `debcraft.domain.scanner` has no imports from `debcraft.infrastructure`
    - Verify domain layer purity: dpkg_parser imports only stdlib and domain packages
    - Fix any violations
    - _Requirements: 16.1, 16.2, 16.6, 16.7_

  - [x] 11.2 Write integration tests for DirectoryScanner
    - Create `tests/integration/scanner/__init__.py` and `tests/integration/scanner/test_directory_scanner.py`
    - Test with real temp directories: dpkg status present, dpkg status absent, unreadable file, symlinks outside root
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.7_

  - [x] 11.3 Write integration tests for DockerScanner
    - Create `tests/integration/scanner/test_docker_scanner.py`
    - Test with crafted minimal Docker image tarballs: valid image with dpkg status, missing manifest.json, whiteout handling
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.6_

  - [x] 11.4 Write integration tests for enrichment cache
    - Create `tests/integration/scanner/test_enrichment_cache.py`
    - Test with real SQLite: store and retrieve enrichment, snapshot invalidation, cache miss fallthrough
    - _Requirements: 17.1, 17.2, 17.3, 17.7_

- [x] 12. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The design uses Python directly, so all implementation uses Python 3.13+
- Tests follow existing project structure: `tests/properties/domain/scanner/`, `tests/unit/domain/scanner/`, `tests/integration/scanner/`
- All scanners receive dependencies via constructor injection for testability (Requirement 16.8)

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "1.4"] },
    { "id": 2, "tasks": ["2.1", "2.2"] },
    { "id": 3, "tasks": ["2.3", "2.4", "2.5", "3.1"] },
    { "id": 4, "tasks": ["3.2", "3.3"] },
    { "id": 5, "tasks": ["5.1", "5.3", "5.4"] },
    { "id": 6, "tasks": ["5.2", "5.5", "6.1", "6.2", "6.3"] },
    { "id": 7, "tasks": ["6.4", "6.5"] },
    { "id": 8, "tasks": ["8.1", "8.3"] },
    { "id": 9, "tasks": ["8.2", "8.4"] },
    { "id": 10, "tasks": ["8.5", "8.6"] },
    { "id": 11, "tasks": ["9.1", "9.2"] },
    { "id": 12, "tasks": ["9.3", "10.1", "10.2", "10.3"] },
    { "id": 13, "tasks": ["11.1", "11.2", "11.3", "11.4"] }
  ]
}
```
