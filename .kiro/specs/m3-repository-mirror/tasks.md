# Implementation Plan: Repository Mirror (M3)

## Overview

Implement the repository mirroring engine as a `MirrorWorkflow` using the existing M1/M2 platform infrastructure. The implementation follows a bottom-up approach: domain layer (pure logic, value objects, parsers), infrastructure layer (I/O, orchestration, DI), and CLI integration. Each task builds incrementally on prior tasks, with property tests validating correctness properties from the design document.

## Tasks

- [x] 1. Domain layer — value objects and configuration
  - [x] 1.1 Create `src/debcraft/domain/mirror/__init__.py`, `values.py`, and `config.py`
    - Create the `domain/mirror/` package with `__init__.py`
    - Implement `FileEntry`, `SyncDecision`, `DownloadResult` frozen dataclasses in `values.py`
    - Implement `RepositoryConfig` and `MirrorConfig` frozen dataclasses in `config.py`
    - Include field validation logic (name length, URL format, list bounds) as class methods or standalone validators
    - _Requirements: 8.1, 8.6_

  - [x] 1.2 Write property tests for configuration validation (Properties 16, 17)
    - **Property 16: Configuration validation rejects all invalid inputs**
    - **Property 17: Valid configuration is always accepted**
    - Use Hypothesis strategies to generate valid/invalid MirrorConfig instances
    - **Validates: Requirements 8.1, 8.6**

- [x] 2. Domain layer — Release file parser
  - [x] 2.1 Implement `src/debcraft/domain/mirror/release_parser.py`
    - Implement `ReleaseParser.parse(content: str) -> ReleaseMetadata`
    - Implement `ReleaseMetadata` frozen dataclass holding `list[FileEntry]` and optional metadata fields
    - Parse `SHA256:` / `SHA256Sums:` section, extracting hash, size, and relative path per line
    - Raise `ReleaseParseError` for malformed content or missing SHA256 section
    - _Requirements: 1.2, 1.7_

  - [x] 2.2 Write property tests for Release parser (Properties 1, 2)
    - **Property 1: Release file parsing round-trip**
    - **Property 2: Malformed Release content is always rejected**
    - Use Hypothesis to generate valid SHA256Sums sections and verify round-trip fidelity
    - Use Hypothesis to generate strings without valid SHA256 sections and verify rejection
    - **Validates: Requirements 1.2, 1.7**

- [x] 3. Domain layer — Packages index parser
  - [x] 3.1 Implement `src/debcraft/domain/mirror/packages_parser.py`
    - Implement `PackagesParser.parse(content: str) -> list[FileEntry]`
    - Parse stanza-separated package entries extracting `Filename`, `SHA256`, `Size` fields
    - Handle edge cases: empty content, missing fields, multi-line continuations
    - _Requirements: 2.1, 3.1_

  - [x] 3.2 Write unit tests for Packages parser
    - Test with real Debian Packages file snippets
    - Test edge cases: empty input, missing SHA256 field, large file counts
    - _Requirements: 2.1, 3.1_

- [x] 4. Domain layer — file comparator (incremental sync logic)
  - [x] 4.1 Implement `src/debcraft/domain/mirror/comparator.py`
    - Implement `FileComparator.compute_sync_decisions(remote_entries, local_checksums) -> list[SyncDecision]`
    - Return `action="skip"` when local SHA256 matches remote SHA256
    - Return `action="download"` when local file is absent or SHA256 differs
    - _Requirements: 1.3, 2.1, 2.2, 3.1, 3.2_

  - [x] 4.2 Write property tests for file comparator (Properties 3, 4, 5)
    - **Property 3: Matching checksums produce skip decisions**
    - **Property 4: Mismatched or absent checksums produce download decisions**
    - **Property 5: Component × architecture Cartesian product path generation**
    - **Validates: Requirements 1.3, 2.1, 2.2, 2.3, 3.1, 3.2**

- [x] 5. Checkpoint — Domain layer validation
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Infrastructure layer — error types and domain events
  - [x] 6.1 Implement `src/debcraft/infrastructure/mirror/__init__.py` and `errors.py`
    - Create the `infrastructure/mirror/` package with `__init__.py`
    - Implement error hierarchy: `MirrorError`, `ConfigurationError`, `ReleaseParseError`, `DownloadError`, `HttpClientError`, `HttpServerError`, `NetworkError`, `ChecksumMismatchError`, `SizeMismatchError`, `DiskSpaceError`
    - All errors extend from the platform's `PlatformError`
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5_

  - [x] 6.2 Implement `src/debcraft/infrastructure/mirror/events.py`
    - Implement `MirrorSyncStartedEvent`, `MirrorSyncCompletedEvent`, `MirrorSyncFailedEvent`, `SnapshotPublishedEvent`
    - All extend `DomainEvent` from `platform/contracts/events.py`
    - _Requirements: 7.3, 13.6, 14.1_

- [x] 7. Infrastructure layer — SyncSession database model
  - [x] 7.1 Add `SyncSession` model to `src/debcraft/infrastructure/models/mirror.py`
    - Add `SyncSession` SQLAlchemy model with fields: `session_id`, `repository_name`, `status`, `files_downloaded`, `files_skipped`, `files_failed`, `bytes_transferred`, `started_at`, `completed_at`
    - Add appropriate indexes on `session_id` (unique) and `status`
    - _Requirements: 6.1, 6.3, 14.5_

- [x] 8. Infrastructure layer — configuration reader
  - [x] 8.1 Implement `src/debcraft/infrastructure/mirror/config_reader.py`
    - Implement `ConfigReader` class with `read() -> MirrorConfig` and `validate(config) -> list[str]`
    - Read TOML from `{XDG_CONFIG_HOME}/debcraft/mirrors.toml`
    - Fall back to default eLxr configuration when file doesn't exist
    - Validate all fields using domain config validation rules
    - Raise `ConfigurationError` with line number on TOML parse failure
    - _Requirements: 8.1, 8.2, 8.4, 8.6, 8.7_

  - [x] 8.2 Write unit tests for ConfigReader
    - Test TOML parsing with valid config
    - Test default config fallback
    - Test validation error reporting
    - Test invalid TOML syntax error with line number
    - _Requirements: 8.2, 8.6, 8.7_

- [x] 9. Infrastructure layer — download coordinator
  - [x] 9.1 Implement `src/debcraft/infrastructure/mirror/download.py`
    - Implement `DownloadCoordinator` with aiohttp session management (`start()`, `close()`)
    - Implement `download_file()` with: `.part` file writes, 64KB chunked streaming, SHA256 verification, atomic `os.replace()`, size verification
    - Implement exponential backoff retry (base 1s, max 30s, 25% jitter, 3 attempts)
    - Implement `download_batch()` using `asyncio.TaskGroup` with semaphore-based concurrency
    - Implement `check_conditional()` for If-Modified-Since / If-None-Match
    - Configure `TCPConnector` with `limit_per_host` and `limit` from `MirrorConfig`
    - _Requirements: 3.3, 4.1, 4.2, 4.3, 4.7, 11.1, 11.2, 11.3, 11.6, 12.1, 12.2, 12.3, 12.5_

  - [x] 9.2 Write property tests for download safety (Properties 6, 7, 20, 21, 22)
    - **Property 6: SHA256 verification accepts correct hashes and rejects incorrect ones**
    - **Property 7: Atomic download lifecycle (.part file safety)**
    - **Property 20: Exponential backoff delay bounds**
    - **Property 21: HTTP error classification**
    - **Property 22: Size mismatch detection**
    - **Validates: Requirements 2.5, 4.1, 4.2, 4.7, 11.3, 12.2, 12.3, 12.5, 12.7**

- [x] 10. Infrastructure layer — snapshot publisher
  - [x] 10.1 Implement `src/debcraft/infrastructure/mirror/publisher.py`
    - Implement `SnapshotPublisher` with `publish_snapshot()` method
    - Create `RepositorySnapshot` entity atomically (create + associate files + set published=True in one transaction)
    - Return `None` and publish failure event when zero verified files exist
    - Publish `SnapshotPublishedEvent` through EventBus on success
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_

  - [x] 10.2 Write property test for snapshot atomicity (Property 15)
    - **Property 15: Snapshot publication atomicity**
    - **Validates: Requirements 7.6**

- [x] 11. Checkpoint — Infrastructure services validation
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Infrastructure layer — mirror engine (orchestrator)
  - [x] 12.1 Implement `src/debcraft/infrastructure/mirror/engine.py`
    - Implement `MirrorEngine` with constructor accepting all dependencies (DownloadCoordinator, DatabaseProvider, StorageEngine, EventBus, CancellationToken, ProgressReporter, Logger)
    - Implement `sync_repository(config, session_id) -> SyncResult` orchestrating the 5-stage pipeline
    - Implement `_stage_release()`: download InRelease (fallback to Release), parse, compare checksum, conditional requests
    - Implement `_stage_indexes()`: compute sync decisions via FileComparator, download changed indexes, parse Packages
    - Implement `_stage_artifacts()`: filter cached artifacts, download_batch with concurrency
    - Implement `_stage_publish()`: delegate to SnapshotPublisher
    - Check CancellationToken between each stage
    - Report progress at stage milestones (0-20%, 20-50%, 50-80%, 80-95%, 95-100%)
    - Batch database commits (≤500 entities per transaction)
    - Manage RepositoryFile state transitions (DISCOVERED→QUEUED→DOWNLOADING→DOWNLOADED→VERIFIED)
    - Handle resumption: re-queue DOWNLOADING entities to QUEUED on startup
    - _Requirements: 1.1, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 3.1, 3.2, 3.4, 3.5, 3.6, 3.7, 3.8, 4.4, 4.5, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 9.6, 11.4, 11.5, 13.4_

  - [x] 12.2 Write property tests for engine state management (Properties 8, 9, 12, 13, 14)
    - **Property 8: Verified files are never overwritten**
    - **Property 9: Startup cleanup removes all orphaned .part files**
    - **Property 12: RepositoryFile state machine transitions are forward-only**
    - **Property 13: URL uniqueness constraint (upsert idempotency)**
    - **Property 14: Batch commit size limit**
    - **Validates: Requirements 4.4, 4.5, 4.6, 6.2, 6.7, 6.8, 6.9**

- [x] 13. Infrastructure layer — mirror workflow
  - [x] 13.1 Implement `src/debcraft/infrastructure/mirror/workflow.py`
    - Implement `MirrorWorkflow(Workflow)` with `name = "mirror-sync"`
    - Implement `execute(context: WorkflowContext)` that:
      - Resolves dependencies from `context.scope`
      - Reads config via `ConfigReader`
      - Iterates over repositories, calling `MirrorEngine.sync_repository()` for each
      - Publishes `MirrorSyncStartedEvent` at begin, `MirrorSyncCompletedEvent` or `MirrorSyncFailedEvent` at end
      - Handles per-repository isolation (one failure doesn't stop others)
      - Checks CancellationToken between repositories
    - Implement cancellation state rollback logic (QUEUED→DISCOVERED, DOWNLOADING→QUEUED)
    - _Requirements: 8.3, 8.5, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 13.1, 13.2, 13.3, 13.4, 13.5, 13.6_

  - [x] 13.2 Write property tests for cancellation and repository isolation (Properties 18, 19)
    - **Property 18: Repository isolation on failure**
    - **Property 19: Cancellation state rollback rules**
    - **Validates: Requirements 8.3, 9.3**

- [x] 14. Infrastructure layer — DI bootstrap
  - [x] 14.1 Implement `src/debcraft/infrastructure/mirror/bootstrap.py`
    - Implement `mirror_bootstrap(container: Container)` following `storage_bootstrap` pattern
    - Register `MirrorWorkflow` and `ConfigReader` as singletons
    - Register `DownloadCoordinator`, `MirrorEngine`, `SnapshotPublisher` as scoped
    - _Requirements: 13.7, 13.8_

- [x] 15. Infrastructure layer — path derivation and cache layout
  - [x] 15.1 Implement mirror cache path logic in the engine/storage integration
    - Derive local paths from base URL: `{mirror_root}/{hostname}/{url_path}/`
    - Preserve exact relative paths from repository metadata
    - Create files with mode `0o644`
    - Ensure separate top-level directories per repository
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [x] 15.2 Write property tests for path derivation (Properties 10, 11)
    - **Property 10: Local path derivation from base URL**
    - **Property 11: Relative path preservation**
    - **Validates: Requirements 5.1, 5.2, 5.4**

- [x] 16. Checkpoint — Full infrastructure validation
  - Ensure all tests pass, ask the user if questions arise.

- [x] 17. CLI layer — mirror command group
  - [x] 17.1 Implement `src/debcraft/cli/mirror.py` and register with main app
    - Create Typer sub-app `mirror_app` with commands: `sync`, `verify`, `status`, `list`, `clean`
    - Register `mirror_app` in `cli/__init__.py` via `app.add_typer(mirror_app, name="mirror")`
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 10.9_

  - [x] 17.2 Implement `mirror sync` command
    - Invoke `MirrorWorkflow` through `WorkflowEngine.run()`
    - Display Rich progress bar during sync
    - Display summary on completion (downloaded, skipped, failed, bytes)
    - Handle errors with structured messages and non-zero exit codes
    - _Requirements: 10.1, 10.6, 10.7, 10.8, 10.9_

  - [x] 17.3 Implement `mirror verify` command
    - Compute SHA256 of all cached files
    - Compare against stored checksums in mirror.db
    - Display pass/fail status with mismatch details
    - Show Rich progress bar during verification
    - _Requirements: 10.2, 10.6, 10.9_

  - [x] 17.4 Implement `mirror status` command
    - Query mirror.db for repository count, last sync timestamp, file counts, cache size
    - Display human-readable output with Rich formatting
    - _Requirements: 10.3, 10.9_

  - [x] 17.5 Implement `mirror list` command
    - Read configuration and display table of repositories
    - Show name, base URL, suites, components, architectures
    - _Requirements: 10.4, 10.9_

  - [x] 17.6 Implement `mirror clean` command
    - Identify unreferenced artifacts not in latest Release
    - Display removal summary and prompt for confirmation (or skip with `--yes`)
    - Remove unreferenced files and display reclaimed space
    - Show Rich progress bar during cleanup
    - _Requirements: 10.5, 10.6, 10.9_

  - [x] 17.7 Write unit tests for CLI commands
    - Test each command's exit code behavior
    - Test error handling and structured error output
    - Test `--yes` flag for clean command
    - Test "no repositories configured" error message
    - _Requirements: 10.7, 10.8, 10.9_

- [x] 18. Logging integration
  - [x] 18.1 Add structured logging throughout mirror infrastructure
    - Add INFO logging for sync start/end with repository name and status
    - Add DEBUG logging for individual file operations with URL and entity ID
    - Add WARNING logging for retries, mismatches, and skipped files
    - Add ERROR logging for exhausted retries, unrecoverable errors, disk space issues
    - Include session correlation ID in all log entries
    - Emit summary log at session end (files processed, downloaded, skipped, failed, bytes, elapsed time)
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6_

- [x] 19. Final checkpoint — Full integration validation
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation at logical boundaries
- Property tests validate the 22 universal correctness properties defined in the design
- Unit tests validate specific examples and edge cases
- The domain layer (tasks 1-4) is pure Python with no I/O dependencies, enabling fast isolated testing
- The infrastructure layer (tasks 6-15) integrates with existing M1/M2 services
- The CLI layer (task 17) wires everything together for user interaction
- All async code uses `asyncio.TaskGroup` for structured concurrency
- Database operations use existing `SqliteUnitOfWork` and repository patterns
- The `DownloadCoordinator` session lifecycle is scoped to the workflow execution

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "6.1"] },
    { "id": 1, "tasks": ["1.2", "6.2", "7.1"] },
    { "id": 2, "tasks": ["2.1", "3.1", "4.1"] },
    { "id": 3, "tasks": ["2.2", "3.2", "4.2"] },
    { "id": 4, "tasks": ["8.1", "9.1"] },
    { "id": 5, "tasks": ["8.2", "9.2", "10.1"] },
    { "id": 6, "tasks": ["10.2", "12.1"] },
    { "id": 7, "tasks": ["12.2", "13.1", "14.1", "15.1"] },
    { "id": 8, "tasks": ["13.2", "15.2"] },
    { "id": 9, "tasks": ["17.1", "18.1"] },
    { "id": 10, "tasks": ["17.2", "17.3", "17.4", "17.5", "17.6"] },
    { "id": 11, "tasks": ["17.7"] }
  ]
}
```
