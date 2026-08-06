# Requirements Document

## Introduction

This document defines the requirements for Milestone M2 (Storage Layer) of the DebCraft platform. M2 implements the persistence infrastructure that sits between the domain model and the database. It provides a Storage Engine for managing filesystem paths and directory layout, three logical SQLite databases (mirror.db, metadata.db, cache.db), a Repository pattern implementation following Domain-Driven Design, a Unit of Work for transaction coordination, a Database Provider abstraction, schema evolution through forward-only migrations, and recovery mechanisms. All persistent operations flow through repository interfaces — business logic never accesses SQLAlchemy sessions directly. The storage layer integrates with M1's DI container, event bus, workflow context, and resource manager.

## Glossary

- **Storage_Engine**: The component responsible for managing filesystem paths, directory layout initialization, lifecycle, and path resolution for all persistent data directories.
- **Storage_Provider**: An abstraction over the physical storage backend (local filesystem) that the Storage_Engine delegates to for directory creation and path resolution.
- **Database_Provider**: The component managing SQLAlchemy engine and session creation for a logical database, abstracting the specific SQL dialect behind an interface.
- **Session**: A SQLAlchemy async session representing a conversation with the database, scoped to a single unit of work.
- **Unit_of_Work**: A transaction coordinator that groups repository operations into a single atomic commit, owned by a workflow.
- **Repository**: A DDD-pattern interface providing collection-like access to aggregate root entities, hiding persistence mechanics.
- **Aggregate_Root**: A domain entity that serves as the consistency boundary for a cluster of related objects.
- **Migration**: A versioned, forward-only schema change script that records its execution in the database.
- **StorageError**: The base exception for all storage layer errors, extending PlatformError from M1.
- **RepositoryFile**: An entity in mirror.db representing a file discovered in a remote Debian repository, with lifecycle states (Discovered, Queued, Downloading, Downloaded, Verified, Indexed).
- **PackageInstance**: An entity in metadata.db representing a specific binary package identified by name, version, architecture, and filename.
- **SourcePackage**: An entity in metadata.db representing a Debian source package.
- **RepositorySnapshot**: An immutable entity in metadata.db representing a point-in-time capture of repository state, recording the schema version used during indexing.
- **LicenseExpression**: An entity in metadata.db representing a parsed SPDX license expression associated with a package.
- **ScanSession**: An entity in metadata.db representing a complete analysis run.
- **SBOMDocument**: An entity in metadata.db representing a generated Software Bill of Materials document.
- **Surrogate_Key**: An auto-incrementing integer primary key used internally for entity identification.
- **Natural_Key**: A combination of domain-meaningful columns enforced via unique constraints (e.g., package_name + version + architecture + filename).

## Requirements

### Requirement 1: Storage Engine Filesystem Management

**User Story:** As a platform developer, I want a Storage Engine that manages all filesystem paths and directory layout, so that storage concerns are centralized, user-owned, and require no root privileges.

#### Acceptance Criteria

1. WHEN the Storage_Engine initializes, THE Storage_Engine SHALL create the directory layout under the XDG_CACHE_HOME-resolved base (defaulting to `~/.cache/debcraft/`) with subdirectories: `mirror/`, `workspace/`, `outputs/`, `logs/`, and `cache/`, creating parent directories as needed and succeeding without error if directories already exist.
2. WHEN the Storage_Engine initializes, THE Storage_Engine SHALL create the directory layout under the XDG_DATA_HOME-resolved base (defaulting to `~/.local/share/debcraft/`) for database files, creating parent directories as needed and succeeding without error if directories already exist.
3. WHEN the Storage_Engine initializes, THE Storage_Engine SHALL create the directory under the XDG_CONFIG_HOME-resolved base (defaulting to `~/.config/debcraft/`) for configuration files, creating parent directories as needed and succeeding without error if directories already exist.
4. THE Storage_Engine SHALL resolve all paths using `pathlib.Path` and the XDG Base Directory environment variables on Linux (XDG_CACHE_HOME, XDG_DATA_HOME, XDG_CONFIG_HOME), falling back to platform equivalents on macOS and Windows when XDG variables are not set.
5. THE Storage_Engine SHALL not require root or administrator privileges for any filesystem operation.
6. WHEN a path is requested for a specific storage purpose (mirror cache, workspace, outputs, logs, database, or configuration), THE Storage_Engine SHALL return an absolute `pathlib.Path` to the corresponding directory or file.
7. WHEN the Storage_Engine is shut down, THE Storage_Engine SHALL flush all pending writes and release filesystem resources within 30 seconds, raising a StorageError if the shutdown timeout is exceeded.
8. WHEN the Storage_Engine initializes and the workspace directory contains files or subdirectories with a `.tmp` suffix or matching a defined temporary naming convention, THE Storage_Engine SHALL remove those files and subdirectories.
9. THE Storage_Engine SHALL verify that required directories are writable during initialization and raise a StorageError if permissions are insufficient, identifying the unwritable path in the error.

### Requirement 2: Database Provider Abstraction

**User Story:** As a platform developer, I want database access abstracted behind a provider interface, so that the default SQLite implementation can be replaced with PostgreSQL or DuckDB without changing business logic.

#### Acceptance Criteria

1. THE Database_Provider SHALL provide an abstract interface defining methods for: creating an async engine for a logical database by name, acquiring an async session for a logical database by name, disposing all engines and sessions, and reporting health status of managed databases.
2. THE Database_Provider SHALL manage separate SQLAlchemy async engines for each logical database (mirror.db, metadata.db, cache.db).
3. WHEN a session is requested by logical database name (one of "mirror", "metadata", or "cache"), THE Database_Provider SHALL return an async SQLAlchemy session bound to the corresponding database engine.
4. THE Database_Provider SHALL configure connection pooling with a maximum pool size of 5 connections per logical database and a single-writer constraint enforced via connection serialization for write operations.
5. WHEN the Database_Provider is disposed, THE Database_Provider SHALL close all connection pools and release database file handles within 10 seconds, forcibly terminating connections that do not close within that period.
6. THE Database_Provider SHALL enable WAL (Write-Ahead Logging) mode for all SQLite databases to support concurrent reads during writes.
7. THE Database_Provider SHALL configure SQLite PRAGMA settings for integrity (foreign_keys=ON, journal_mode=WAL, synchronous=NORMAL).
8. IF the database file is corrupted or inaccessible when an engine is created or a session is requested, THEN THE Database_Provider SHALL raise a StorageError with a message identifying the logical database name and the nature of the failure (corruption, permission denied, or file not found).
9. IF a session is requested for a logical database name that is not one of "mirror", "metadata", or "cache", THEN THE Database_Provider SHALL raise a StorageError indicating the unrecognized database name.

### Requirement 3: Repository Pattern Implementation

**User Story:** As a platform developer, I want repository interfaces for all aggregate roots, so that services interact with persistence through a collection-like API without coupling to SQLAlchemy or SQL.

#### Acceptance Criteria

1. THE Platform SHALL define abstract repository interfaces for: RepositoryRepository, RepositorySnapshotRepository, RepositoryFileRepository, PackageRepository, SourcePackageRepository, LicenseRepository, ScanSessionRepository, and SBOMRepository.
2. THE Repository interfaces SHALL define a base interface providing: add (insert one entity), get_by_id (lookup by integer surrogate key returning the entity), find (query returning a list of zero or more entities matching filter parameters), update (persist modifications to an existing entity), and delete (remove an entity by identity), where each concrete repository interface explicitly declares which of these operations it supports.
3. WHEN a repository method is called, THE Repository implementation SHALL execute the operation through the Unit_of_Work's session without committing independently.
4. THE Repository implementations SHALL use SQLAlchemy ORM mapped classes for all database interactions.
5. THE Repository interfaces SHALL be defined in `src/debcraft/platform/contracts/persistence.py` as abstract base classes using `abc.ABC` and `abc.abstractmethod`.
6. THE Repository implementations SHALL be placed in `src/debcraft/infrastructure/repositories/`.
7. WHEN a repository get_by_id or get_by_natural_key call finds no matching entity, THE Repository SHALL raise a StorageError identifying the entity type, the lookup key name, and the requested key value.
8. THE Repository interfaces SHALL use Python generic types (Generic[T]) parameterized by the entity type so that all method signatures return and accept the specific entity type rather than a base class.
9. THE PackageRepository SHALL support lookup by natural key (package_name, version, architecture, filename) via a dedicated get_by_natural_key method.
10. THE RepositoryFileRepository SHALL support querying by lifecycle state (Discovered, Queued, Downloading, Downloaded, Verified, Indexed), returning a list of matching RepositoryFile entities.
11. WHEN a find query matches zero entities, THE Repository SHALL return an empty list rather than raising an error.
12. IF an update or delete operation is called on the RepositorySnapshotRepository for a snapshot that has been published, THEN THE Repository SHALL raise a StorageError indicating that published snapshots are immutable.

### Requirement 4: Unit of Work

**User Story:** As a platform developer, I want a Unit of Work that coordinates transactions across repositories, so that each workflow commits or rolls back as a single atomic operation.

#### Acceptance Criteria

1. THE Unit_of_Work SHALL own exactly one database session bound to a single logical database and coordinate all repository operations within that session.
2. WHEN commit is called, THE Unit_of_Work SHALL persist all changes tracked by participating repositories as a single atomic transaction.
3. WHEN rollback is called, THE Unit_of_Work SHALL discard all pending changes and return the session to a state with no uncommitted operations, allowing subsequent operations on the same Unit_of_Work instance.
4. IF an unhandled exception occurs during commit, THEN THE Unit_of_Work SHALL automatically roll back the transaction, release the session, and raise a StorageError wrapping the original exception.
5. WHEN the async context manager exits without an exception, THE Unit_of_Work SHALL commit the transaction.
6. IF the async context manager exits with an exception, THEN THE Unit_of_Work SHALL roll back the transaction and re-raise the exception.
7. THE Unit_of_Work SHALL be accessible from the WorkflowContext as a scoped service, so that each workflow resolves exactly one Unit_of_Work instance per logical database through the context.
8. THE Unit_of_Work SHALL expose repository instances as typed properties (e.g., `packages: PackageRepository`) that share its managed session.
9. WHEN the WorkflowContext's CancellationToken is triggered, THE Unit_of_Work SHALL roll back any uncommitted changes and prevent further commit operations on that instance.
10. THE Unit_of_Work SHALL define its abstract interface in `src/debcraft/platform/contracts/persistence.py`.
11. THE Unit_of_Work SHALL implement the async context manager protocol (`__aenter__` / `__aexit__`).

### Requirement 5: Entity Models and Identity Strategy

**User Story:** As a platform developer, I want entities with surrogate integer keys and natural uniqueness constraints, so that internal lookups are efficient while domain uniqueness is enforced.

#### Acceptance Criteria

1. THE entity models SHALL use auto-incrementing integer primary keys (surrogate keys) for internal identification.
2. THE PackageInstance entity SHALL enforce a unique constraint on the combination of package_name, version, architecture, and filename.
3. THE PackageInstance, RepositoryFile, and SBOMDocument entity models SHALL include a SHA256 column stored as a 64-character hex string with a database index for deduplication and integrity verification.
4. THE RepositoryFile entity SHALL track lifecycle state as an enumeration with values: Discovered, Queued, Downloading, Downloaded, Verified, and Indexed.
5. IF an update operation is attempted on a RepositorySnapshot whose published flag is True, THEN THE RepositorySnapshot entity SHALL raise a StorageError indicating that published snapshots are immutable.
6. THE RepositorySnapshot entity SHALL record the schema version used during indexing as an integer corresponding to the migration version identifier.
7. THE entity models SHALL be defined as SQLAlchemy ORM mapped classes in `src/debcraft/infrastructure/models/`.
8. THE entity models SHALL include a created_at column set to the current UTC timestamp on insert, and an updated_at column set to the current UTC timestamp on insert and automatically updated to the current UTC timestamp on every modification.
9. THE entity models SHALL use SQLAlchemy's Mapped type annotations for complete type safety such that basedpyright reports zero type errors.

### Requirement 6: Schema Evolution (Migrations)

**User Story:** As a platform developer, I want forward-only versioned migrations, so that database schemas evolve predictably and each migration is recorded for audit.

#### Acceptance Criteria

1. THE Migration system SHALL apply schema changes as forward-only migrations (no downgrade support).
2. THE Migration system SHALL version each migration with a monotonically increasing integer identifier starting from 1.
3. WHEN a migration executes successfully, THE Migration system SHALL record the migration identifier, execution timestamp, and duration in milliseconds in a migration history table.
4. WHEN the application starts, THE Migration system SHALL detect pending migrations by comparing available migration identifiers against those recorded in the migration history table, and apply pending migrations in ascending version order before any repository operations.
5. IF a migration fails during execution, THEN THE Migration system SHALL roll back that single migration, raise a StorageError identifying the failed migration identifier and the cause, and halt further migration execution for that database.
6. THE Migration system SHALL maintain a separate migration history table and independent version sequence for each of the three logical databases (mirror.db, metadata.db, cache.db).
7. THE RepositorySnapshot entity SHALL reference the schema version active when indexing occurred.
8. WHEN the Migration system initializes for a database that has no migration history table, THE Migration system SHALL create the migration history table before applying any migrations.
9. WHEN the Migration system encounters a migration identifier already recorded in the migration history table, THE Migration system SHALL skip that migration without re-executing it.

### Requirement 7: Recovery Mechanisms

**User Story:** As a platform developer, I want recovery capabilities for each storage component, so that the system resumes operation after interruption without data loss or corruption.

#### Acceptance Criteria

1. WHEN the Storage_Engine initializes and mirror.db contains RepositoryFile entries in Downloading state, THE Storage_Engine SHALL transition those entries back to Queued state for retry, up to a maximum of 3 retry attempts per entry, after which the entry SHALL be transitioned to a failed state.
2. WHEN the Storage_Engine initializes and metadata.db has uncommitted transaction journal files, THE Database_Provider SHALL let SQLite perform automatic journal recovery.
3. WHEN a cache.db entry is detected as corrupt through SHA256 checksum mismatch or is missing, THE Storage_Engine SHALL mark the entry's status as requiring recomputation rather than raising an error.
4. WHEN the Storage_Engine initializes and the workspace directory contains files matching the temporary file naming prefix (configurable, default `tmp_`), THE Storage_Engine SHALL remove those files.
5. WHEN the Storage_Engine initializes and the mirror cache directory contains files, THE Storage_Engine SHALL verify file integrity by comparing each file's SHA256 checksum against the stored checksum in mirror.db and discard files that fail verification.
6. IF recovery of a database file is not possible because SQLite journal recovery fails or the database file cannot be opened after 3 attempts, THEN THE Storage_Engine SHALL raise a StorageError identifying the affected database file path and indicating that manual deletion or restoration is required.

### Requirement 8: Performance Requirements

**User Story:** As a platform developer, I want efficient database operations, so that the storage layer handles large Debian repositories without excessive memory usage or slow queries.

#### Acceptance Criteria

1. THE Repository implementations SHALL support batched insert operations accepting a list of entities and persisting them in a single round-trip using SQLAlchemy's bulk insert capabilities.
2. THE Database_Provider SHALL use prepared statements for repeated queries to avoid re-parsing SQL.
3. THE Repository implementations SHALL support incremental update operations that modify only changed fields using SQLAlchemy's attribute tracking.
4. THE entity models SHALL define database indexes on columns used in frequent lookup patterns (SHA256 hash, natural key columns, lifecycle state, foreign keys).
5. THE Repository implementations SHALL support streaming queries using SQLAlchemy's `yield_per()` for result sets exceeding 1000 rows, returning an async iterator rather than materializing the full result set.
6. THE Storage_Engine SHALL enforce bounded memory usage by never loading an entire repository's package list into memory simultaneously.

### Requirement 9: Integration with M1 Platform Kernel

**User Story:** As a platform developer, I want the storage layer to integrate with M1's DI container, event bus, and resource manager, so that storage components compose naturally with the existing kernel infrastructure.

#### Acceptance Criteria

1. THE Storage_Engine SHALL be registered as a singleton service in the M1 DI container, resolvable by its abstract interface type.
2. THE Repository interfaces SHALL be registered with scoped lifetime in the M1 DI container, resolvable by their abstract interface types, so that each Scope receives repository instances bound to its Unit_of_Work session.
3. THE Unit_of_Work SHALL be accessible through the WorkflowContext so that workflows obtain persistence access without resolving services directly from the Container.
4. THE Storage_Engine SHALL implement the async context manager protocol and be registered with M1's ResourceManager so that ResourceManager invokes initialization on enter and cleanup on exit in deterministic order.
5. THE storage layer SHALL extend the M1 error hierarchy with StorageError as a subclass of PlatformError.
6. WHEN a storage lifecycle event occurs (initialization, shutdown, migration), THE Storage_Engine SHALL publish a DomainEvent through the M1 Event_Bus.
7. THE storage layer SHALL provide a bootstrap function that registers all storage services (Storage_Engine, Database_Provider, repositories, Unit_of_Work) in the M1 Container with their specified lifetimes.
8. IF the CancellationToken from the WorkflowContext is cancelled, THEN THE Unit_of_Work SHALL roll back any uncommitted changes and release the database session.
9. WHEN the bootstrap function executes, THE Container SHALL have registrations for Storage_Engine (singleton), Database_Provider (singleton), Repository interfaces (scoped), and Unit_of_Work (scoped).

### Requirement 10: Three Logical Database Separation

**User Story:** As a platform developer, I want mirror, metadata, and cache concerns separated into distinct databases, so that each database has independent lifecycle, backup, and recovery characteristics.

#### Acceptance Criteria

1. THE Storage_Engine SHALL manage three separate SQLite database files: mirror.db for synchronization state, metadata.db for authoritative package metadata, and cache.db for derived/computed data.
2. THE mirror.db database SHALL store RepositoryFile entities, download queue state, retry counters, download statistics, and synchronization checkpoints.
3. THE metadata.db database SHALL store Repository, RepositorySnapshot, PackageInstance, SourcePackage, PackageMetadata, LicenseExpression, ScanSession, and SBOMDocument entities.
4. THE cache.db database SHALL store only data that can be recomputed from mirror.db and metadata.db sources, including parsed DEP-5 ASTs, normalized license mappings, checksum caches, parser caches, and derived metadata.
5. THE cache.db data SHALL be treated as recomputable — loss of cache.db SHALL not constitute data loss, and WHEN cache.db is missing or deleted, THE Storage_Engine SHALL recreate an empty cache.db on initialization without requiring user intervention.
6. THE metadata.db data SHALL be treated as authoritative — IF a cache.db entry conflicts with a metadata.db entry for the same entity, THEN THE Storage_Engine SHALL discard the cache.db entry and mark it for recomputation from metadata.db.
7. WHEN operations span multiple logical databases, THE Storage_Engine SHALL use separate Unit_of_Work instances for each database (no cross-database transactions).
8. IF a Unit_of_Work commit succeeds on one database but a subsequent Unit_of_Work commit fails on another database within the same operation, THEN THE Storage_Engine SHALL log the partial commit state and raise a StorageError identifying which databases committed and which failed, leaving committed data intact.
9. IF one or two database files are inaccessible during initialization but at least one other remains accessible, THEN THE Storage_Engine SHALL raise a StorageError identifying the unavailable database files rather than starting in a degraded mode.

### Requirement 11: Cross-Platform and Quality Compliance

**User Story:** As a platform developer, I want all storage layer code to pass static analysis, type checking, and cross-platform tests, so that the storage layer is reliable across environments and maintains consistent code quality.

#### Acceptance Criteria

1. WHEN `uv run ruff check src/debcraft/infrastructure/ src/debcraft/platform/contracts/storage.py src/debcraft/platform/contracts/persistence.py` is executed, THE storage layer code SHALL report zero linting violations.
2. WHEN `uv run basedpyright src/debcraft/infrastructure/ src/debcraft/platform/contracts/storage.py src/debcraft/platform/contracts/persistence.py` is executed, THE storage layer code SHALL report zero type errors.
3. THE storage layer SHALL use `pathlib.Path` for all filesystem path construction, resolution, and file I/O operations, with no use of `os.path` string-based path manipulation.
4. THE storage layer SHALL not require root or administrator privileges for any operation, using only user-level file permissions and user-writable directories.
5. THE storage layer SHALL declare all database query functions, filesystem read/write functions, and repository persistence methods as `async def` and use `await` for their I/O calls via `asyncio`.
6. THE storage layer SHALL annotate all public functions and methods with complete type annotations following Google Python Style Guide conventions, including parameter types, return types, and generic type parameters.
7. THE storage layer SHALL provide unit tests marked with `@pytest.mark.unit` and `@pytest.mark.storage` for every public class and public function in the storage components.
8. THE storage layer SHALL provide database-specific tests marked with `@pytest.mark.database`.
9. WHEN the test suite is executed on Linux, macOS, or Windows, THE storage layer SHALL pass all tests marked with `@pytest.mark.cross_platform`, using `pathlib.Path` for platform-appropriate path separators and avoiding hardcoded forward-slash or backslash literals in path construction.
10. THE storage layer SHALL include property-based tests using Hypothesis for repository round-trip operations (verifying that storing an entity and retrieving it by identifier yields an equivalent entity) and entity invariant verification (verifying that domain entity construction constraints hold across randomized inputs), with a minimum Hypothesis setting of 200 examples per test case.
11. IF a storage operation encounters a filesystem permission error or database connection failure, THEN THE storage layer SHALL raise a domain-specific StorageError rather than propagating the underlying platform exception directly.
