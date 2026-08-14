# Implementation Plan: SBOM Enrichment Pipeline

## Overview

This implementation plan connects the `debcraft sbom` CLI command to the real enrichment infrastructure. The approach is incremental: first wire the database engines and snapshot resolution, then extend the enricher with metadata.db fallback, add the ISO .deb extraction path, update the ModelAssembler for new field mappings, and finally wire everything together in the CLI's DI scope.

## Tasks

- [x] 1. Database engine factory and snapshot resolution
  - [x] 1.1 Create `DatabaseEngines` dataclass and `resolve_snapshot_id` function
    - Create a new module `src/debcraft/cli/_sbom_db.py`
    - Implement `DatabaseEngines` dataclass holding `metadata_engine`, `cache_engine`, `metadata_session_factory`, `cache_session_factory` with an `async dispose()` method
    - Implement `async resolve_snapshot_id(session_factory, explicit_id)` that queries `RepositorySnapshot` table for the highest published ID
    - Use `create_async_engine_for` and `create_session_factory` from `debcraft.infrastructure.database.session`
    - Use `resolve_xdg_path("database")` for metadata.db path and `resolve_xdg_path("cache")` for cache.db path
    - Handle missing metadata.db (return 0, log warning) and missing cache.db (create file + schema via `Base.metadata.create_all`)
    - If cache.db connection fails, return None for cache fields and log warning
    - _Requirements: 1.1, 1.3, 6.1, 6.2, 6.3, 6.4, 6.5_

  - [x] 1.2 Add `--snapshot-id` CLI flag to the `sbom` command
    - In `src/debcraft/cli/sbom.py`, add a `--snapshot-id` option of type `int | None` with validation for positive integers in range [1, 2_147_483_647]
    - Exit with non-zero code and error message if value is not a positive integer (zero, negative, overflow)
    - Pass the resolved snapshot_id into `_run_sbom` and through to `SBOMWorkflowConfig`
    - _Requirements: 1.2, 1.4, 1.5, 5.1_

  - [x] 1.3 Write property test for snapshot resolution (Property 1)
    - **Property 1: Snapshot Resolution Returns Highest Published ID**
    - Generate random lists of `RepositorySnapshot`-like records with varying `id` and `published` states using Hypothesis
    - Assert that `resolve_snapshot_id` returns the highest `id` among published records, or 0 if none are published
    - Place in `tests/properties/infrastructure/scanners/test_sbom_enrichment_properties.py`
    - **Validates: Requirements 1.1, 1.3**

  - [x] 1.4 Write property test for snapshot ID input validation (Property 2)
    - **Property 2: Snapshot ID Input Validation**
    - Generate random strings (negative integers, zero, floats, non-numeric, empty) and positive integers using Hypothesis
    - Assert that only positive integers in [1, 2_147_483_647] are accepted; all others rejected with non-zero exit
    - Test via Typer CLI runner against the `sbom` command's `--snapshot-id` parsing
    - **Validates: Requirements 1.2, 1.5**

- [x] 2. Extend MetadataEnricher with metadata.db fallback
  - [x] 2.1 Add `metadata_session_factory` and `deb_extractor` dependencies to `MetadataEnricher`
    - In `src/debcraft/infrastructure/scanners/enricher.py`, extend `__init__` to accept optional `metadata_session_factory` and `deb_extractor` parameters
    - Implement `_query_metadata_db(pkg, snapshot_id)` method: query `PackageInstance` table with (name, version, arch, snapshot_id), use highest `id` on multiple matches, join `LicenseExpression` records
    - Construct `PackageEnrichment` from the query result, generating PURL via `generate_purl()` (catching `PURLGenerationError`)
    - On successful metadata.db lookup, store result in cache (swallow store failures with warning)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 4.1, 4.2, 4.3, 4.4_

  - [x] 2.2 Update `enrich()` method fallback chain
    - When cache misses and `snapshot_id > 0`, call `_query_metadata_db` before returning None
    - When `snapshot_id == 0` and `deb_extractor` is available, delegate to `deb_extractor.extract_enrichment(pkg)`
    - When `snapshot_id > 0`, metadata.db misses, and `deb_extractor` is available, fall back to .deb extraction
    - Log diagnostics for each fallback path taken
    - _Requirements: 3.5, 5.3, 8.1, 8.9_

  - [x] 2.3 Write property test for PackageInstance-to-PackageEnrichment mapping (Property 3)
    - **Property 3: PackageInstance to PackageEnrichment Field Preservation**
    - Generate random PackageInstance-like dicts with associated LicenseExpression records using Hypothesis
    - Assert that all non-None fields are preserved in the mapped PackageEnrichment, license_expressions contains all (expression, source) pairs, and purl equals `generate_purl(name, version, arch)` or None
    - **Validates: Requirements 3.1, 3.3, 4.1, 4.4**

- [x] 3. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. ISODebFileReader and DebExtractor
  - [x] 4.1 Implement `ISODebFileReader` adapter
    - Create `src/debcraft/infrastructure/package_intelligence/iso_file_reader.py`
    - Implement `ISODebFileReader` class conforming to `DebFileReader` protocol
    - Constructor accepts an `ISOReader` instance
    - `read_ar_member(deb_path, member_prefix)`: call `self._iso_reader.read_file(deb_path)` to get full .deb bytes, then perform ar parsing in-memory (reuse the ar parsing logic from `LocalDebFileReader`)
    - `compute_sha256(file_path)`: read file bytes via `self._iso_reader.read_file(file_path)` and compute SHA256
    - _Requirements: 8.8_

  - [x] 4.2 Implement `DebExtractor` service
    - Create `src/debcraft/infrastructure/scanners/deb_extractor.py`
    - Implement `DebExtractor` class with constructor accepting `iso_reader: ISOReader`, `deb_parser: DebParser`, `dep5_parser: DEP5Parser`, `license_mapper: LicenseMapper`
    - Implement `extract_enrichment(pkg: IdentifiedPackage) -> PackageEnrichment | None`
    - Pool directory discovery: walk `pool/` → component → letter/lib prefix → package directory → match `{name}_{version}_{arch}.deb`
    - Parse .deb via `DebParser`, extract control fields and copyright
    - If copyright is DEP5 format, use `DEP5Parser` + `LicenseMapper` to generate license_expressions
    - If free-form text, attempt `LicenseMapper` with raw text
    - Generate PURL via `generate_purl()`
    - Handle errors gracefully: log warning on parse failure, return None
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8_

  - [x] 4.3 Write property test for ISODebFileReader (Property 5)
    - **Property 5: ISODebFileReader Ar Member Extraction**
    - Generate random valid ar archives using Hypothesis, store in a mock ISOReader
    - Assert that `ISODebFileReader.read_ar_member` returns identical decompressed bytes as `LocalDebFileReader.read_ar_member` for the same archive content
    - **Validates: Requirements 8.8**

  - [x] 4.4 Write property test for DebExtractor (Property 6)
    - **Property 6: Direct .deb Extraction Produces Valid Enrichment**
    - Generate random valid .deb archives (with control file containing Package, Version, Architecture) and optional DEP5 copyright using Hypothesis
    - Assert that `extract_enrichment` produces a PackageEnrichment with all control fields mapped, and license_expressions is non-empty when valid DEP5 copyright is present
    - **Validates: Requirements 8.2, 8.3, 8.5**

- [x] 5. Update ModelAssembler enrichment field mappings
  - [x] 5.1 Extend `_build_single_package` with download_location and supplier mappings
    - In `src/debcraft/domain/sbom/assembler.py`, update `_build_single_package` method
    - Map `enrichment.download_url` → `SBOMPackage.download_location` (when non-None)
    - Map `enrichment.maintainer` → `SBOMPackage.supplier` (when non-None)
    - Ensure None enrichment fields leave corresponding SBOMPackage fields as None
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [x] 5.2 Write property test for ModelAssembler enrichment mapping (Property 4)
    - **Property 4: ModelAssembler Enrichment-to-SBOMPackage Mapping**
    - Generate random `EnrichedPackage` values with varying enrichment field combinations using Hypothesis
    - Assert that download_location, supplier, package_url, concluded_license, declared_license are correctly mapped from enrichment fields, and None enrichment fields result in None SBOMPackage fields
    - **Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 7.6**

- [x] 6. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Wire real adapters in CLI DI scope
  - [x] 7.1 Update `_create_di_scope` to use real database engines and adapters
    - In `src/debcraft/cli/sbom.py`, refactor `_create_di_scope` to accept snapshot_id and optional DatabaseEngines
    - Replace `_NoOpCacheAdapter` with real `EnrichmentCacheAdapter` when cache session factory is available
    - Fall back to `_NoOpCacheAdapter` if cache.db connection fails (log warning)
    - Pass metadata session factory and (optionally) a `DebExtractor` to `MetadataEnricher`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 7.2 Update `_run_sbom` to manage database lifecycle
    - Create `DatabaseEngines` at the start of `_run_sbom`
    - Call `resolve_snapshot_id` with the metadata session factory and explicit CLI flag value
    - Set `SBOMWorkflowConfig.snapshot_id` to the resolved value
    - Dispose all engines in a `finally` block after workflow completion
    - Pass snapshot_id=0 and log warning when metadata.db doesn't exist
    - _Requirements: 5.1, 5.2, 6.1, 6.2, 6.4_

  - [x] 7.3 Wire `DebExtractor` for ISO artifacts
    - When artifact type is ISO, create `ISODebFileReader` and `DebExtractor` instances using the ISOReader from the scanner
    - Pass `DebExtractor` to `MetadataEnricher` so .deb fallback is available for ISO scans
    - _Requirements: 8.1, 8.8, 8.9_

  - [x] 7.4 Write unit tests for DI wiring and fallback scenarios
    - Test that `_create_di_scope` uses real `EnrichmentCacheAdapter` when cache.db is available
    - Test fallback to `_NoOpCacheAdapter` when cache.db connection fails
    - Test that `resolve_snapshot_id` is called and result flows to `SBOMWorkflowConfig.snapshot_id`
    - Test engine disposal on both success and error paths
    - _Requirements: 2.1, 2.2, 2.4, 6.2_

- [x] 8. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The design uses Python throughout; all implementation is in Python with pytest + Hypothesis for testing
- The `_NoOpCacheAdapter` remains as fallback for when cache.db is unavailable
- Database engines are always disposed in a `finally` block to prevent leaked file handles

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "4.1"] },
    { "id": 1, "tasks": ["1.2", "4.2", "5.1"] },
    { "id": 2, "tasks": ["1.3", "1.4", "2.1", "4.3", "4.4", "5.2"] },
    { "id": 3, "tasks": ["2.2", "2.3"] },
    { "id": 4, "tasks": ["7.1", "7.2"] },
    { "id": 5, "tasks": ["7.3", "7.4"] }
  ]
}
```
