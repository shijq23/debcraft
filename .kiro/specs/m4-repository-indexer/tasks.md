# Implementation Plan: Repository Indexer

## Overview

Implement the M4 Repository Indexer feature, which parses Debian repository metadata files (Packages, Sources, Contents, Release) from the local mirror cache into structured domain objects persisted in `metadata.db`. The implementation follows clean architecture with domain parsers producing value objects, an infrastructure mapper converting them to ORM models, and CLI commands for operator interaction.

## Tasks

- [x] 1. Create domain value objects and error types
  - [x] 1.1 Create `src/debcraft/domain/indexer/__init__.py` and `src/debcraft/domain/indexer/values.py`
    - Define frozen dataclasses: `PackageMetadata`, `SourcePackageMetadata`, `FileOwnership`, `RepositoryIdentity`, `IndexResult`
    - Include all fields from the design document with proper type annotations
    - Use `field(default_factory=list)` for list fields
    - _Requirements: 1.1, 2.1, 3.1, 4.1, 8.1_

  - [x] 1.2 Create `src/debcraft/domain/indexer/errors.py`
    - Define `ReleaseParseError`, `IndexingError` exception classes
    - Follow existing error pattern from `domain/mirror/errors.py`
    - _Requirements: 4.3, 12.1_

- [x] 2. Implement PackagesParser
  - [x] 2.1 Create `src/debcraft/domain/indexer/packages_parser.py`
    - Implement `PackagesParser` class with `PARSER_VERSION = 1`
    - Implement `parse(content: str) -> list[PackageMetadata]` method
    - Parse stanza-based format, extracting all fields from the design
    - Implement Source field inference: `name (version)` → split; name-only → use binary version; absent → use binary name/version
    - Skip stanzas missing required fields (Package, Version, Architecture, Filename, SHA256, Size) with debug logging
    - Handle multi-line continuation fields (Description)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 2.2 Implement `format(metadata: PackageMetadata) -> str` method on PackagesParser
    - Format a PackageMetadata back into a Packages stanza string
    - Used for round-trip verification in property-based tests
    - _Requirements: 1.6_

  - [x] 2.3 Write property test for PackageMetadata round-trip
    - **Property 1: PackageMetadata round-trip**
    - Create `tests/properties/domain/indexer/__init__.py` and `tests/properties/domain/indexer/test_packages_parser_properties.py`
    - Use Hypothesis to generate valid PackageMetadata objects, format then parse, assert equivalence
    - `@settings(max_examples=200)`
    - **Validates: Requirements 1.1, 1.6**

  - [x] 2.4 Write property test for Source field inference rules
    - **Property 2: Source field inference rules**
    - Generate stanzas with various Source field formats and verify correct source_package/source_version extraction
    - `@settings(max_examples=200)`
    - **Validates: Requirements 1.3, 1.4, 1.5**

  - [x] 2.5 Write property test for invalid stanza skipping
    - **Property 3: Invalid Packages stanzas are skipped**
    - Generate stanzas missing at least one required field, verify no output and no exception
    - `@settings(max_examples=100)`
    - **Validates: Requirements 1.2**

  - [x] 2.6 Write unit tests for PackagesParser edge cases
    - Create `tests/unit/domain/indexer/__init__.py` and `tests/unit/domain/indexer/test_packages_parser.py`
    - Test empty content, single stanza, multiple stanzas, Unicode package names, non-integer Size, negative Size
    - _Requirements: 1.1, 1.2_

- [x] 3. Implement SourcesParser
  - [x] 3.1 Create `src/debcraft/domain/indexer/sources_parser.py`
    - Implement `SourcesParser` class with `PARSER_VERSION = 1`
    - Implement `parse(content: str) -> list[SourcePackageMetadata]` method
    - Parse stanza-based format extracting name, version, maintainer, uploaders, section, homepage, build_depends, binary_packages
    - Split Binary field on commas and trim whitespace
    - Skip stanzas missing Package or Version with debug logging
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.2 Implement `format(metadata: SourcePackageMetadata) -> str` method on SourcesParser
    - Format a SourcePackageMetadata back into a Sources stanza string
    - _Requirements: 2.4_

  - [x] 3.3 Write property test for SourcePackageMetadata round-trip
    - **Property 4: SourcePackageMetadata round-trip**
    - Create `tests/properties/domain/indexer/test_sources_parser_properties.py`
    - Use Hypothesis to generate valid SourcePackageMetadata, format then parse, assert equivalence
    - `@settings(max_examples=200)`
    - **Validates: Requirements 2.1, 2.4**

  - [x] 3.4 Write property test for invalid Sources stanza skipping
    - **Property 5: Invalid Sources stanzas are skipped**
    - Generate stanzas missing Package or Version field, verify no output and no exception
    - `@settings(max_examples=100)`
    - **Validates: Requirements 2.2**

  - [x] 3.5 Write property test for Binary field comma splitting
    - **Property 6: Binary field comma splitting**
    - Generate lists of package names, join with commas/whitespace, verify parsed binary_packages matches original
    - `@settings(max_examples=100)`
    - **Validates: Requirements 2.3**

- [x] 4. Implement ContentsParser
  - [x] 4.1 Create `src/debcraft/domain/indexer/contents_parser.py`
    - Implement `ContentsParser` class with `PARSER_VERSION = 1`
    - Implement `parse(content: str) -> list[FileOwnership]` method
    - Parse `path  section/package_name` format with whitespace separation
    - Handle lines mapping one path to multiple comma-separated packages (produce one FileOwnership per package)
    - Skip malformed lines and handle optional header section
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [x] 4.2 Write property test for Contents parsing correctness
    - **Property 7: Contents parsing correctness**
    - Create `tests/properties/domain/indexer/test_contents_parser_properties.py`
    - Generate valid Contents lines, verify one FileOwnership per package with correct path and name
    - `@settings(max_examples=100)`
    - **Validates: Requirements 3.1, 3.2**

  - [x] 4.3 Write property test for Contents header invariance
    - **Property 8: Contents header invariance**
    - Generate valid Contents body, prepend arbitrary headers, verify same FileOwnership set
    - `@settings(max_examples=100)`
    - **Validates: Requirements 3.4**

- [x] 5. Implement ReleaseMetadataParser
  - [x] 5.1 Create `src/debcraft/domain/indexer/release_metadata_parser.py`
    - Implement `ReleaseMetadataParser` class
    - Implement `parse(content: str) -> RepositoryIdentity` method
    - Extract suite, codename, origin, label, architectures, components, date
    - Implement suite fallback to codename when Suite absent
    - Raise `ReleaseParseError` when neither Suite nor Codename present
    - _Requirements: 4.1, 4.2, 4.3_

  - [x] 5.2 Write property test for Release metadata extraction with suite fallback
    - **Property 9: Release metadata extraction with suite fallback**
    - Create `tests/properties/domain/indexer/test_release_metadata_parser_properties.py`
    - Generate Release content with Suite and/or Codename, verify suite field logic
    - `@settings(max_examples=100)`
    - **Validates: Requirements 4.1, 4.2**

- [x] 6. Checkpoint - Ensure all domain parser tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Create database migration and infrastructure models
  - [x] 7.1 Create `src/debcraft/infrastructure/database/migrations/metadata/v3_add_indexer_tables.py`
    - Add columns to `package_instances`: source_package, source_version, homepage, maintainer, depends, provides, section, priority, description, download_url
    - Add columns to `source_packages`: uploaders, section, homepage, build_depends, binary_packages, snapshot_id
    - Create `file_ownerships` table with snapshot_id, file_path, package_name, indexes
    - Create `indexing_records` table with repository_file_id, parser_version, indexed_sha256, indexed_at
    - _Requirements: 6.1, 6.3, 9.1, 5.1_

  - [x] 7.2 Update `src/debcraft/infrastructure/models/metadata.py` with new columns and models
    - Add new columns to `PackageInstance` model: source_package, source_version, homepage, maintainer, depends, provides, section, priority, description, download_url
    - Add new columns to `SourcePackage` model: uploaders, section, homepage, build_depends, binary_packages, snapshot_id
    - Add `FileOwnership` ORM model and `IndexingRecord` ORM model
    - Add relationship from `RepositorySnapshot` to `FileOwnership`
    - _Requirements: 6.3, 9.1, 5.1_

- [x] 8. Implement infrastructure layer (mapper, file reader, repository)
  - [x] 8.1 Create `src/debcraft/infrastructure/indexer/__init__.py` and `src/debcraft/infrastructure/indexer/mapper.py`
    - Implement `IndexerMapper` class that converts domain value objects to/from SQLAlchemy models
    - Map `PackageMetadata` → `PackageInstance` model fields
    - Map `SourcePackageMetadata` → `SourcePackage` model fields
    - Map `FileOwnership` → `FileOwnership` model
    - _Requirements: 12.5_

  - [x] 8.2 Create `src/debcraft/infrastructure/indexer/file_reader.py`
    - Implement `LocalFileReader` class satisfying the `FileReader` protocol
    - Support reading and decompressing `.gz`, `.xz`, `.bz2` files
    - Handle plain text files (no decompression)
    - _Requirements: 12.4_

  - [x] 8.3 Create `src/debcraft/infrastructure/indexer/repository.py`
    - Implement `SqlAlchemyMetadataRepository` satisfying the `MetadataRepository` protocol
    - Implement `find_or_create_repository`, `create_snapshot`, `publish_snapshot`
    - Implement `add_package_instances` with duplicate natural key skipping
    - Implement `add_source_packages` with upsert behavior
    - Implement `replace_file_ownerships` (delete existing for snapshot, insert new)
    - Implement `get_package_metadata` for CLI package lookup
    - _Requirements: 6.1, 6.2, 7.1, 7.2, 7.3, 8.1, 8.2, 8.3, 9.1, 9.2, 9.3_

  - [x] 8.4 Create `src/debcraft/infrastructure/indexer/mirror_file_repository.py`
    - Implement `SqlAlchemyMirrorFileRepository` satisfying the `MirrorFileRepository` protocol
    - Implement `get_verified_files` querying RepositoryFile in VERIFIED state
    - Implement `get_indexing_record` and `mark_indexed` for state transitions
    - _Requirements: 5.1, 5.2_

- [x] 9. Implement IndexerService domain orchestrator
  - [x] 9.1 Create `src/debcraft/domain/indexer/service.py`
    - Implement `IndexerService` with constructor injection of all dependencies
    - Implement `index_repository(repository_name, base_url, suite, component) -> IndexResult`
    - Orchestrate: read files → determine skip/parse → parse → persist → publish events
    - Implement incremental indexing logic: skip if INDEXED + SHA256 match + parser version match
    - Implement deterministic processing order: sort by (repository_name, file_type, file_path)
    - Compute download_url by joining repository base_url with package filename
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 6.4, 8.1, 8.2, 8.3, 8.4, 10.1, 10.2, 10.3_

  - [x] 9.2 Create event dataclasses in `src/debcraft/domain/indexer/events.py`
    - Define `IndexingStarted`, `IndexingCompleted`, `IndexingFailed` events extending `DomainEvent`
    - _Requirements: 10.1, 10.2, 10.3_

  - [x] 9.3 Write property test for incremental indexing decision
    - **Property 10: Incremental indexing decision**
    - Create `tests/properties/domain/indexer/test_indexer_service_properties.py`
    - Generate file states (sha256, parser_version, file_state), verify skip logic correctness
    - `@settings(max_examples=100)`
    - **Validates: Requirements 5.1, 5.2, 5.3**

  - [x] 9.4 Write property test for deterministic processing order
    - **Property 11: Deterministic processing order**
    - Generate sets of pending files with various insertion orders, verify sorted output
    - `@settings(max_examples=100)`
    - **Validates: Requirements 5.4**

  - [x] 9.5 Write property test for duplicate natural key skipping
    - **Property 12: Duplicate natural key skipping**
    - Generate PackageMetadata lists with duplicate keys, verify only unique keys persisted
    - `@settings(max_examples=100)`
    - **Validates: Requirements 6.2**

  - [x] 9.6 Write property test for download URL computation
    - **Property 13: Download URL computation**
    - Generate base URLs and filenames, verify computed download_url is base_url + "/" + filename
    - `@settings(max_examples=100)`
    - **Validates: Requirements 6.4**

- [x] 10. Checkpoint - Ensure all service and infrastructure tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Implement CLI commands
  - [x] 11.1 Create `src/debcraft/cli/index.py` with `index_app` Typer sub-application
    - Implement `debcraft index` command: index all repositories with VERIFIED files
    - Implement `debcraft index --repository <name>` flag for single-repository indexing
    - Handle no-VERIFIED-files case with informational message, exit 0
    - Handle database failures with error message and suggested fix, exit 1
    - Display Rich progress and summary table
    - _Requirements: 11.1, 11.2, 11.3_

  - [x] 11.2 Add `debcraft package <name>` command to `src/debcraft/cli/index.py`
    - Query metadata database for latest indexed metadata by package name
    - Display version, architecture, source package, repository membership
    - Handle package-not-found with "Package not found" message, exit 1
    - _Requirements: 11.4, 11.5_

  - [x] 11.3 Register `index_app` in `src/debcraft/cli/__init__.py`
    - Import and add `index_app` to the main Typer app following the `mirror_app` pattern
    - _Requirements: 11.1_

- [x] 12. Integration tests
  - [x] 12.1 Write integration test for full indexing pipeline
    - Create `tests/integration/test_indexer_pipeline.py`
    - Test: read file → parse → persist → query with real in-memory SQLite
    - Verify schema migration v3 applies cleanly on top of v1+v2
    - _Requirements: 6.1, 8.2, 8.3_

  - [x] 12.2 Write integration test for incremental indexing
    - Verify second run skips already-indexed files
    - Verify parser version bump triggers re-indexing
    - _Requirements: 5.1, 5.2, 5.3_

  - [x] 12.3 Write integration test for CLI commands
    - Use Typer's `CliRunner` to test `debcraft index` and `debcraft package` commands end-to-end
    - _Requirements: 11.1, 11.4, 11.5_

- [x] 13. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The v3 migration builds on existing v1 (metadata tables) and v2 (scan tables)
- Domain parsers in `domain/indexer/` are distinct from the mirror parsers in `domain/mirror/` — the indexer parsers extract full metadata fields
- The import linter contract in `pyproject.toml` already enforces domain independence from infrastructure

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["2.1", "3.1", "4.1", "5.1"] },
    { "id": 2, "tasks": ["2.2", "2.6", "3.2", "7.1"] },
    { "id": 3, "tasks": ["2.3", "2.4", "2.5", "3.3", "3.4", "3.5", "4.2", "4.3", "5.2", "7.2"] },
    { "id": 4, "tasks": ["8.1", "8.2", "8.4", "9.2"] },
    { "id": 5, "tasks": ["8.3", "9.1"] },
    { "id": 6, "tasks": ["9.3", "9.4", "9.5", "9.6", "11.1"] },
    { "id": 7, "tasks": ["11.2", "11.3"] },
    { "id": 8, "tasks": ["12.1", "12.2", "12.3"] }
  ]
}
```
