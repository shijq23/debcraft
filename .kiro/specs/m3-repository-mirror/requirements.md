# Requirements Document

## Introduction

This document defines the requirements for Milestone M3 (Repository Mirror) of the DebCraft platform. M3 implements a native Python repository mirroring engine that downloads and maintains a local cache of remote Debian repositories. The mirroring engine performs incremental synchronization based on Release file checksums, verifies all downloaded artifacts via SHA256, supports resumption of interrupted downloads, and publishes immutable RepositorySnapshot entities upon successful synchronization. The engine is implemented as a MirrorWorkflow using the existing M1 Workflow/WorkflowEngine infrastructure, persists download state in mirror.db through the existing M2 storage layer, and exposes CLI commands via Typer. The initial target repository is eLxr (https://mirror.elxr.dev), but the architecture supports any Debian-compatible repository. All operations run as a normal (non-root) user, are fully asynchronous, interruptible, and resumable.

## Glossary

- **Mirror_Engine**: The asynchronous component responsible for orchestrating repository synchronization, coordinating downloads, verification, and snapshot publication.
- **Mirror_Workflow**: A concrete Workflow implementation (extending M1's Workflow contract) that encapsulates the full mirror synchronization lifecycle.
- **Release_File**: A Debian repository metadata file (`Release` or `InRelease`) that contains checksums and sizes for all index files in a distribution.
- **Repository_Index**: A compressed file (e.g., `Packages.gz`, `Sources.gz`, `Contents-*.gz`) listed in the Release file, containing metadata about available packages.
- **Repository_Configuration**: A data structure defining a remote repository's base URL, suites, components, and architectures to mirror.
- **Synchronization_Session**: A single execution of the mirror workflow for one or more configured repositories, tracked in mirror.db.
- **Download_Coordinator**: The component managing concurrent HTTP downloads with connection pooling, rate limiting, and retry logic.
- **Part_File**: A temporary file (suffixed `.part`) used during download to ensure atomic writes — the destination file is only created after successful hash verification and atomic rename.
- **Repository_File**: An entity in mirror.db (from M2) representing a file discovered in a remote Debian repository, with lifecycle states (Discovered, Queued, Downloading, Downloaded, Verified, Indexed, Failed).
- **Repository_Snapshot**: An immutable entity in metadata.db (from M2) representing a point-in-time capture of repository state, published atomically after mirror completion and verification.
- **Cancellation_Token**: The cooperative cancellation mechanism (from M1) that long-running mirror operations check periodically to support graceful interruption.
- **Storage_Engine**: The M2 component managing filesystem paths, directory layout, and lifecycle for all persistent data directories.
- **Database_Provider**: The M2 component managing SQLAlchemy engine and session creation for logical databases.
- **Progress_Reporter**: The M1 mechanism for reporting workflow progress (percentage and message) to the user.

## Requirements

### Requirement 1: Release File Acquisition and Validation

**User Story:** As a repository operator, I want the mirror engine to download and validate Release files, so that synchronization decisions are based on authenticated and verified metadata.

#### Acceptance Criteria

1. WHEN a synchronization session begins for a configured repository and suite, THE Mirror_Engine SHALL download the `InRelease` file from `{base_url}/dists/{suite}/InRelease`, falling back to `{base_url}/dists/{suite}/Release` if the server returns HTTP 404 for `InRelease`.
2. WHEN the Release_File is downloaded, THE Mirror_Engine SHALL parse the file to extract the SHA256 checksums, file sizes, and relative paths for all listed index files.
3. WHEN the locally cached Release_File has the same SHA256 checksum as the remote Release_File, THE Mirror_Engine SHALL skip synchronization for that suite and report the repository as up-to-date.
4. IF the Release_File download fails after 3 retry attempts with exponential backoff (starting at 1 second), THEN THE Mirror_Engine SHALL mark the synchronization session as failed and report the HTTP status code or network error to the user.
5. WHEN the Release_File is successfully downloaded and parsed, THE Mirror_Engine SHALL store the Release_File as a RepositoryFile entity in mirror.db with state VERIFIED.
6. THE Mirror_Engine SHALL use HTTP conditional requests (If-Modified-Since or If-None-Match) when checking for Release_File changes to minimize bandwidth usage. WHEN the server responds with HTTP 304 Not Modified, THE Mirror_Engine SHALL treat the suite as up-to-date (same behavior as criterion 3).
7. IF the downloaded Release_File cannot be parsed (malformed content, missing SHA256Sums section, or encoding errors), THEN THE Mirror_Engine SHALL discard the file, mark the synchronization session as failed for that suite, and log the parse error with the affected URL.

### Requirement 2: Repository Index Synchronization

**User Story:** As a repository operator, I want the mirror engine to download only changed index files, so that synchronization is fast and bandwidth-efficient.

#### Acceptance Criteria

1. WHEN the Release_File indicates that an index file (Packages, Sources, or Contents) has a different SHA256 than the locally cached version, or no locally cached version exists for that index file, THE Mirror_Engine SHALL queue that index file for download.
2. WHEN the Release_File indicates that an index file has the same SHA256 as the locally cached version, THE Mirror_Engine SHALL skip downloading that index file and retain the cached copy.
3. THE Mirror_Engine SHALL download index files for each combination of component and architecture specified in the Repository_Configuration (e.g., `main/binary-amd64/Packages.gz`, `main/binary-arm64/Packages.gz`).
4. IF a component and architecture combination from the Repository_Configuration has no corresponding index file entry in the Release_File, THEN THE Mirror_Engine SHALL skip that combination and log a warning indicating the missing index.
5. WHEN an index file is downloaded, THE Mirror_Engine SHALL verify its SHA256 checksum against the value listed in the Release_File before accepting the file.
6. WHEN an index file passes SHA256 verification, THE Mirror_Engine SHALL atomically move the file to its destination path and transition the RepositoryFile entity to VERIFIED state.
7. IF an index file fails SHA256 verification, THEN THE Mirror_Engine SHALL discard the downloaded file, increment the retry counter for that RepositoryFile entity, and re-queue the download up to 3 attempts.
8. IF an index file fails verification after 3 retry attempts, THEN THE Mirror_Engine SHALL transition the RepositoryFile entity to FAILED state and continue synchronizing remaining files.

### Requirement 3: Package Artifact Download

**User Story:** As a repository operator, I want the mirror engine to download only new or changed package artifacts, so that the local cache stays current without re-downloading unchanged packages.

#### Acceptance Criteria

1. WHEN a Packages index is parsed and a package artifact has a SHA256 that does not exist in the local mirror cache, THE Mirror_Engine SHALL queue that artifact for download.
2. WHEN a package artifact's SHA256 already exists in the local mirror cache at the expected path, THE Mirror_Engine SHALL skip downloading that artifact.
3. THE Mirror_Engine SHALL download package artifacts concurrently using up to 20 parallel HTTP connections via aiohttp.
4. WHEN a package artifact is downloaded and its SHA256 checksum matches the value from the Packages index, THE Mirror_Engine SHALL atomically rename the Part_File to the final destination path and transition the RepositoryFile entity to VERIFIED state.
5. IF a package artifact download is interrupted by a network failure, THEN THE Mirror_Engine SHALL retain the Part_File on disk, increment the RepositoryFile entity's retry_count, and transition it to QUEUED state for resumption on the next synchronization session.
6. IF a package artifact's SHA256 verification fails after download, THEN THE Mirror_Engine SHALL discard the downloaded Part_File, increment the retry_count for that RepositoryFile entity, and re-queue the download.
7. IF a package artifact's RepositoryFile entity reaches a retry_count of 3, THEN THE Mirror_Engine SHALL transition the entity to FAILED state and not attempt further downloads in the current session.
8. THE Mirror_Engine SHALL track download progress and report through the Progress_Reporter at intervals no greater than every 50 files or every 10 seconds (whichever occurs first), including the count of files downloaded, files remaining, and total bytes transferred.

### Requirement 4: Atomic File Writes and Download Safety

**User Story:** As a repository operator, I want downloads to use atomic file operations, so that the mirror cache is never left in a corrupted state.

#### Acceptance Criteria

1. THE Mirror_Engine SHALL write all downloads to a temporary Part_File (destination path suffixed with `.part`) in the same directory as the final destination.
2. WHEN a download completes and SHA256 verification succeeds, THE Mirror_Engine SHALL atomically rename the Part_File to the final destination path using `os.replace()`.
3. IF a Part_File already exists when a download begins, THE Mirror_Engine SHALL delete the existing Part_File before starting the new download.
4. THE Mirror_Engine SHALL never write directly to a destination file whose corresponding RepositoryFile entity is in VERIFIED or INDEXED state and whose file exists on disk at the recorded local_path.
5. IF the process is interrupted during a download, THEN THE Mirror_Engine SHALL leave the Part_File on disk without modifying any verified destination file, so that recovery can detect and clean up or resume partial downloads.
6. WHEN the Storage_Engine initializes and discovers any Part_Files (files with `.part` suffix) in the mirror cache directory tree, THE Storage_Engine SHALL remove those Part_Files and transition corresponding RepositoryFile entities to QUEUED state.
7. IF SHA256 verification fails after a download completes, THEN THE Mirror_Engine SHALL delete the Part_File from disk and not modify the final destination path.
8. IF the atomic rename via `os.replace()` fails due to a filesystem error, THEN THE Mirror_Engine SHALL retain the Part_File on disk, transition the RepositoryFile entity to QUEUED state, and increment the retry_count.

### Requirement 5: Mirror Cache Layout and Compatibility

**User Story:** As a repository operator, I want the mirror cache to follow the standard Debian repository directory structure, so that external tools like `apt` can use it directly.

#### Acceptance Criteria

1. THE Mirror_Engine SHALL store mirrored files in the XDG-compliant cache directory at `{XDG_CACHE_HOME}/debcraft/mirror/{hostname}/{url_path}/` preserving the remote repository's directory hierarchy (`dists/`, `pool/`), where `{hostname}` is the host portion and `{url_path}` is the path portion of the repository base URL (e.g., base URL `https://mirror.elxr.dev/elxr` maps to `mirror.elxr.dev/elxr/`).
2. THE Mirror_Engine SHALL preserve the exact relative path structure of each file as it appears in the remote repository (e.g., `pool/main/l/libssl3/libssl3_3.0.2-0ubuntu1_amd64.deb`).
3. THE Mirror_Engine SHALL store files with no modifications to their content, so that SHA256 checksums of local files match the checksums published in the repository metadata.
4. WHEN multiple repositories are mirrored, THE Mirror_Engine SHALL use separate top-level directories derived from each repository's base URL (hostname and path combined, e.g., `mirror.elxr.dev/elxr/`, `deb.debian.org/debian/`) to prevent path collisions between repositories.
5. THE Mirror_Engine SHALL create stored files with read permission for the owning user (at minimum mode `0o644`) so that `apt` configured with a `file://` URI pointing to the repository root directory can read Release files, index files, and package artifacts without requiring file format conversion or path remapping.
6. WHEN the mirror cache directory for a repository contains a valid `dists/{suite}/Release` file and the corresponding index and pool files, THE Mirror_Engine SHALL ensure the directory is usable as an apt source by preserving the standard Debian repository layout expected by the `file://` transport (i.e., `dists/{suite}/` contains Release and component index directories, `pool/` contains package artifacts at their declared relative paths).

### Requirement 6: Mirror Database State Tracking

**User Story:** As a repository operator, I want synchronization state persisted in mirror.db, so that the mirror engine can resume operations after interruption and provide status information.

#### Acceptance Criteria

1. WHEN a file is discovered in remote repository metadata (Release files, index files, or package artifacts), THE Mirror_Engine SHALL create a RepositoryFile entity in mirror.db with state DISCOVERED and the file's URL, SHA256 checksum, and size as declared in the metadata.
2. THE Mirror_Engine SHALL transition RepositoryFile entities through lifecycle states in forward order only: DISCOVERED → QUEUED → DOWNLOADING → DOWNLOADED → VERIFIED → INDEXED, except for error transitions to FAILED state or cancellation rollbacks defined in Requirement 9.
3. WHEN a RepositoryFile entity transitions to DOWNLOADING state, THE Mirror_Engine SHALL record the transition timestamp in the entity's updated_at column in mirror.db.
4. WHEN a RepositoryFile entity transitions to VERIFIED state, THE Mirror_Engine SHALL record the local filesystem path in the entity's local_path column.
5. WHEN a download or verification operation fails for a RepositoryFile entity, THE Mirror_Engine SHALL increment the entity's retry_count field by 1 and transition the entity back to QUEUED state for retry.
6. IF a RepositoryFile entity reaches a retry_count of 3, THEN THE Mirror_Engine SHALL transition the entity to FAILED state and not attempt further downloads in the current session.
7. THE Mirror_Engine SHALL use the existing M2 Unit_of_Work for all mirror.db operations, committing in batches of no more than 500 entities per transaction to bound memory usage.
8. WHEN a synchronization session starts and mirror.db contains RepositoryFile entities in QUEUED or DOWNLOADING state from a previous interrupted session, THE Mirror_Engine SHALL re-queue those entities for processing by transitioning DOWNLOADING entities to QUEUED state before beginning new downloads.
9. THE Mirror_Engine SHALL identify an existing RepositoryFile entity by its url column (unique constraint), updating the existing entity rather than creating a duplicate when the same URL is discovered in a subsequent synchronization session.

### Requirement 7: Repository Snapshot Publication

**User Story:** As a platform developer, I want mirror completion to produce an immutable RepositorySnapshot, so that downstream indexing operates on a verified and consistent view of the repository.

#### Acceptance Criteria

1. WHEN all queued RepositoryFile entities for a synchronization session have reached VERIFIED or INDEXED state (with no entities in DOWNLOADING or QUEUED state), THE Mirror_Engine SHALL create a RepositorySnapshot entity in metadata.db with the `repository_id` set to the corresponding Repository entity, `captured_at` set to the current UTC timestamp, and `published` initially set to False.
2. WHEN the RepositorySnapshot entity has been persisted with `published=False` and all associated RepositoryFile entities are in VERIFIED or INDEXED state, THE Mirror_Engine SHALL set the RepositorySnapshot's `published` flag to True within the same database transaction described in criterion 6.
3. WHEN a RepositorySnapshot's `published` flag is set to True, THE Mirror_Engine SHALL publish a domain event through the M1 Event_Bus containing: the snapshot ID, the repository name, the `captured_at` timestamp, the count of verified files included, and the count of failed files (0 if none failed).
4. IF any RepositoryFile entities remain in FAILED state at the end of a synchronization session, THEN THE Mirror_Engine SHALL still publish a RepositorySnapshot for the successfully verified files (excluding FAILED entities from the snapshot's associated packages), and include the count of failed files in the published domain event.
5. THE Mirror_Engine SHALL record the highest applied migration version from metadata.db's `_migration_history` table in the RepositorySnapshot's `schema_version` field at the time of snapshot creation.
6. THE RepositorySnapshot publication SHALL be atomic — the snapshot entity creation, its association with verified files, and the `published=True` flag update SHALL be persisted in a single database transaction; IF the transaction fails, THEN THE Mirror_Engine SHALL roll back all changes and report the failure without leaving a partially-published snapshot.
7. WHEN a synchronization session produces zero RepositoryFile entities in VERIFIED or INDEXED state (all files failed), THE Mirror_Engine SHALL NOT create a RepositorySnapshot and SHALL publish a domain event through the M1 Event_Bus indicating synchronization failure with the count of failed files.

### Requirement 8: Multiple Repository Configuration

**User Story:** As a repository operator, I want to configure multiple repositories with different suites, components, and architectures, so that the mirror engine supports diverse Debian ecosystems.

#### Acceptance Criteria

1. THE Mirror_Engine SHALL support a list of Repository_Configuration entries, each specifying: a name (unique, 1–128 characters), a base URL, a list of suites (1–20 entries), a list of components (1–50 entries), and a list of architectures (1–20 entries).
2. IF the configuration file at `{XDG_CONFIG_HOME}/debcraft/mirrors.toml` does not exist, THEN THE Mirror_Engine SHALL use a default configuration with the eLxr repository (base URL: `https://mirror.elxr.dev`, suites: `["elxr3"]`, components: `["main"]`, architectures: `["amd64", "arm64"]`).
3. THE Mirror_Engine SHALL synchronize each configured repository independently, so that failure of one repository does not prevent synchronization of others.
4. THE Mirror_Engine SHALL store Repository_Configuration in the XDG-compliant config directory at `{XDG_CONFIG_HOME}/debcraft/mirrors.toml`.
5. WHEN a repository configuration specifies multiple suites (e.g., `["stable", "proposed"]`), THE Mirror_Engine SHALL synchronize each suite independently, downloading only the Release file and indexes specific to that suite.
6. THE Mirror_Engine SHALL validate Repository_Configuration entries at startup, raising a descriptive error if: a required field (name, base_url, suites, components, architectures) is missing, base_url is not a valid HTTP or HTTPS URL, suites/components/architectures contain empty strings, or name is not unique across all configured entries.
7. IF the configuration file exists but contains invalid TOML syntax, THEN THE Mirror_Engine SHALL report an error message indicating the parse failure location (line number) and refuse to start synchronization.

### Requirement 9: Cancellation and Graceful Interruption

**User Story:** As a repository operator, I want to interrupt a synchronization session gracefully, so that in-progress work is preserved and the mirror remains in a consistent state.

#### Acceptance Criteria

1. WHEN the Cancellation_Token is triggered during a synchronization session, THE Mirror_Workflow SHALL stop accepting new download tasks.
2. WHEN the Cancellation_Token is triggered, THE Mirror_Workflow SHALL allow in-progress downloads to complete at their current safe interruption point (end of current HTTP chunk write), waiting no longer than 30 seconds for all in-progress downloads to reach a safe point before forcefully terminating remaining connections.
3. WHEN cancellation occurs, THE Mirror_Workflow SHALL transition all QUEUED RepositoryFile entities back to DISCOVERED state, all DOWNLOADING entities to QUEUED state (preserving Part_Files on disk for future resumption), and leave all DOWNLOADED entities in DOWNLOADED state so that verification can proceed on the next session.
4. WHEN cancellation occurs, THE Mirror_Workflow SHALL commit all state transitions to mirror.db before exiting, so that the next session can resume from the interrupted point.
5. IF the database commit of cancellation state transitions fails, THEN THE Mirror_Workflow SHALL log the failure at ERROR level and exit without modifying Part_Files on disk, so that the next session can detect orphaned Part_Files and recover via the Storage_Engine's initialization cleanup.
6. THE Mirror_Workflow SHALL check the Cancellation_Token between each stage of the synchronization pipeline (after Release download, after index download, between download batches).
7. WHEN a synchronization session is cancelled, THE Mirror_Workflow SHALL NOT publish a RepositorySnapshot.

### Requirement 10: CLI Commands

**User Story:** As a user, I want CLI commands for mirror operations, so that I can synchronize, verify, and manage the local repository cache from the command line.

#### Acceptance Criteria

1. WHEN the user executes `debcraft mirror sync`, THE CLI SHALL invoke the Mirror_Workflow to synchronize all configured repositories and, upon completion, display a summary including the number of files downloaded, the number of files skipped (already up-to-date), the number of failures, and the total bytes transferred.
2. WHEN the user executes `debcraft mirror verify`, THE CLI SHALL compute SHA256 checksums of all files in the mirror cache and compare them against the stored checksums in mirror.db, reporting the number of files checked, the number of mismatches with their file paths, and a final pass/fail status line.
3. WHEN the user executes `debcraft mirror status`, THE CLI SHALL display: the number of configured repositories, the last synchronization timestamp (or "never" if no synchronization has completed), the number of cached files, the number of failed files, and the total cache size in human-readable format (bytes, KiB, MiB, or GiB as appropriate).
4. WHEN the user executes `debcraft mirror list`, THE CLI SHALL display a table of configured repositories with their name, base URL, suites, components, and architectures.
5. WHEN the user executes `debcraft mirror clean`, THE CLI SHALL identify package artifacts in the mirror cache that are no longer referenced by the latest Release file, display the number of files and total size to be removed, prompt the user for confirmation (unless `--yes` flag is provided), and upon confirmation remove those files and display the reclaimed disk space.
6. THE CLI mirror commands SHALL display a progress bar using Rich for long-running operations (sync, verify, clean) that updates at least once per second.
7. IF a mirror CLI command fails, THEN THE CLI SHALL display a structured error message including the failed operation, the affected repository, and a suggested remediation action, and exit with a non-zero exit code.
8. IF no repositories are configured WHEN the user executes `debcraft mirror sync`, `debcraft mirror verify`, or `debcraft mirror clean`, THEN THE CLI SHALL display a message indicating that no repositories are configured and exit with a non-zero exit code.
9. WHEN a mirror CLI command completes successfully, THE CLI SHALL exit with exit code 0.

### Requirement 11: Concurrency and Performance

**User Story:** As a repository operator, I want the mirror engine to download files concurrently and efficiently, so that synchronization completes in a reasonable time even for large repositories.

#### Acceptance Criteria

1. THE Download_Coordinator SHALL use aiohttp with a connection pool of up to 20 concurrent HTTP connections per repository and a total limit of 60 concurrent HTTP connections across all repositories being synchronized.
2. THE Download_Coordinator SHALL use a configurable download timeout (minimum 30 seconds, maximum 3600 seconds, default 300 seconds) per individual file. IF a download exceeds the configured timeout, THEN THE Download_Coordinator SHALL cancel that download and treat it as a retriable failure.
3. THE Download_Coordinator SHALL implement exponential backoff for retries starting at 1 second, doubling on each retry, with a maximum backoff of 30 seconds and random jitter of up to 25% of the computed delay added to each wait.
4. THE Mirror_Engine SHALL use asyncio structured concurrency (TaskGroup) so that all child download tasks belong to the parent Mirror_Workflow. WHEN the Mirror_Workflow is cancelled, THE Mirror_Engine SHALL cancel all child download tasks within the TaskGroup.
5. THE Mirror_Engine SHALL serialize SQLite write operations through the M2 repository pattern, avoiding concurrent writes to mirror.db.
6. THE Mirror_Engine SHALL limit in-memory buffer usage during downloads to a maximum of 64 KiB per connection by streaming received data to Part_Files in chunks of no more than 64 KiB each.

### Requirement 12: Error Handling and Resilience

**User Story:** As a repository operator, I want the mirror engine to handle network errors gracefully, so that transient failures do not corrupt the cache or require manual intervention.

#### Acceptance Criteria

1. IF a network connection is refused or times out during a file download, THEN THE Mirror_Engine SHALL retry the download up to 3 times with exponential backoff (starting at 1 second, doubling on each retry, with a maximum backoff of 30 seconds) before marking the RepositoryFile as FAILED.
2. IF an HTTP response returns a 4xx status code (client error), THEN THE Mirror_Engine SHALL delete any Part_File associated with the download, mark the RepositoryFile as FAILED without retrying, and log the URL and status code.
3. IF an HTTP response returns a 5xx status code (server error), THEN THE Mirror_Engine SHALL retry the download up to 3 times with exponential backoff (starting at 1 second, doubling on each retry, with a maximum backoff of 30 seconds), and mark the RepositoryFile as FAILED if all retries are exhausted.
4. IF the local filesystem runs out of disk space during a download, THEN THE Mirror_Engine SHALL stop all downloads, remove all Part_Files from the current session, and report the error with an estimate of the required free space calculated as the sum of the declared sizes of all remaining queued RepositoryFile entities.
5. IF a downloaded file has a size that does not match the size declared in the repository metadata, THEN THE Mirror_Engine SHALL delete the Part_File, treat the download as a verification failure, and retry up to 3 times before marking the RepositoryFile as FAILED.
6. THE Mirror_Engine SHALL log all errors using structured logging with fields: repository name, file URL, error type, retry count, and elapsed time.
7. IF a download fails for any reason and will not be retried, THEN THE Mirror_Engine SHALL delete the associated Part_File before transitioning the RepositoryFile to FAILED state, ensuring no orphaned temporary files remain.

### Requirement 13: MirrorWorkflow Integration with M1 Platform

**User Story:** As a platform developer, I want the mirror engine implemented as a Workflow using M1 infrastructure, so that it benefits from lifecycle management, cancellation, progress reporting, and DI integration.

#### Acceptance Criteria

1. THE Mirror_Workflow SHALL implement the M1 Workflow abstract base class with a `name` property returning `"mirror-sync"` and an async `execute(context: WorkflowContext)` method.
2. WHEN the Mirror_Workflow's `execute` method is invoked, THE Mirror_Workflow SHALL resolve the Storage_Engine, Database_Provider, and Download_Coordinator from the WorkflowContext's DI scope before beginning synchronization operations.
3. IF any dependency resolution fails during execute, THEN THE Mirror_Workflow SHALL raise the resolution error, allowing the WorkflowEngine to transition the workflow to FAILED state and publish a WorkflowFailedEvent.
4. THE Mirror_Workflow SHALL report progress through the WorkflowContext's Progress_Reporter at each synchronization stage with the following percentage milestones: Release download (0–20%), index download (20–50%), artifact download (50–80%), verification (80–95%), and snapshot publication (95–100%).
5. THE Mirror_Workflow SHALL check the WorkflowContext's Cancellation_Token between each synchronization stage and before each download batch begins, and SHALL return early from `execute` without raising an exception if cancellation is detected.
6. THE Mirror_Workflow SHALL publish domain events through the WorkflowContext's Event_Bus: a synchronization-started event when `execute` begins, a synchronization-completed event when all stages finish successfully, and a synchronization-failed event if any stage raises an unrecoverable error.
7. THE Mirror_Workflow SHALL be registered in the DI container via an async `mirror_bootstrap(container: Container)` function that registers the Mirror_Workflow as a singleton and the Download_Coordinator as a scoped service, following the same function signature and registration pattern as M2's `storage_bootstrap`.
8. WHEN the WorkflowFactory creates a Mirror_Workflow instance, THE Mirror_Workflow SHALL be resolvable from the Container without requiring additional constructor arguments beyond those available through DI registration.

### Requirement 14: Logging and Observability

**User Story:** As a repository operator, I want comprehensive structured logging during synchronization, so that I can diagnose failures and monitor mirror health.

#### Acceptance Criteria

1. THE Mirror_Engine SHALL log at INFO level: synchronization start (with repository name and configured suites) and synchronization end (with final status: completed, partial, or failed).
2. THE Mirror_Engine SHALL log at DEBUG level: individual file download start/completion, HTTP response status codes, SHA256 verification results, and state transitions, each including the file URL and RepositoryFile entity ID.
3. THE Mirror_Engine SHALL log at WARNING level: retries (with retry_count and backoff duration), SHA256 mismatches (with expected vs computed hash), and skipped files (with reason: already cached or missing from Release).
4. THE Mirror_Engine SHALL log at ERROR level: files that exhaust all retry attempts (with final error details), unrecoverable network errors (with exception type and message), and disk space exhaustion (with available vs required space).
5. THE Mirror_Engine SHALL use Python's standard `logging` module with structured fields (repository, url, state, sha256, retry_count, session_id) to support machine-parseable log analysis. All log entries within a synchronization session SHALL include a shared session correlation ID.
6. WHEN a synchronization session completes (whether successfully, partially, or cancelled), THE Mirror_Engine SHALL emit a summary log entry at INFO level containing: total files processed, files downloaded, files skipped, files failed, total bytes transferred, and total elapsed time.
