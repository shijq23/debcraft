# Implementation Plan: M2 Storage Layer

## Overview

This plan implements DebCraft's persistence infrastructure (Milestone 2). It builds on M1's platform kernel, delivering a Storage Engine, Database Provider, Repository pattern, Unit of Work, entity models, migrations, recovery mechanisms, and bootstrap wiring. All code is Python 3.13+, async-native, and must pass `ruff check` and `basedpyright` with zero errors.

## Tasks

- [x] 1. Add dependency and set up module structure
  - [x] 1.1 Add aiosqlite dependency and create infrastructure package skeleton
    - Add `aiosqlite>=0.20` to `[project] dependencies` in `pyproject.toml`
    - Create all `__init__.py` files for infrastructure subpackages: `storage/`, `database/`, `database/migrations/`, `database/migrations/mirror/`, `database/migrations/metadata/`, `database/migrations/cache/`, `repositories/`, `models/`
    - Create placeholder `__init__.py` for `tests/unit/infrastructure/` and `tests/properties/infrastructure/`
    - _Requirements: 2.1, 9.7, 11.1, 11.2_

- [x] 2. Define storage contracts
  - [x] 2.1 Create `platform/contracts/storage.py` with StorageEngine and StorageProvider ABCs
    - Define `StoragePurpose` literal type with all 7 values
    - Define `StorageProvider` ABC with methods: `create_directory`, `remove_matching`, `resolve_path`, `check_writable`
    - Define `StorageEngine` ABC with methods: `initialize`, `shutdown`, `get_path`, `__aenter__`, `__aexit__`
    - All methods `async def` with full type annotations
    - _Requirements: 1.1, 1.4, 1.6, 1.7, 2.1, 11.5, 11.6_

  - [x] 2.2 Create `platform/contracts/persistence.py` with Repository, UnitOfWork, and DatabaseProvider ABCs
    - Define `Repository[T]` generic ABC with: `add`, `get_by_id`, `find`, `update`, `delete`
    - Define `UnitOfWork` ABC with: `commit`, `rollback`, `__aenter__`, `__aexit__`
    - Define `DatabaseProvider` ABC with: `get_session`, `dispose`, `health_check`
    - Use `Generic[T]`, `Mapped` annotations, `Literal["mirror", "metadata", "cache"]` for db names
    - _Requirements: 2.1, 3.1, 3.2, 3.5, 3.8, 4.1, 4.10, 4.11, 11.5, 11.6_

  - [x] 2.3 Write unit tests for storage and persistence contracts
    - Verify `StoragePurpose` literal contains all 7 values
    - Verify `Repository[T]`, `UnitOfWork`, `DatabaseProvider` are abstract (cannot instantiate directly)
    - Verify all ABC methods raise `NotImplementedError` without implementation
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`
    - _Requirements: 11.7_

- [x] 3. Implement error hierarchy
  - [x] 3.1 Create `infrastructure/errors.py` with StorageError hierarchy
    - Define `StorageError(PlatformError)` base with `cause: Exception | None` field; set `__cause__`
    - Define `DatabaseConnectionError(StorageError)` with `db_name: str` and `failure_type: Literal["corruption", "permission_denied", "not_found"]`
    - Define `EntityNotFoundError(StorageError)` with `entity_type: str`, `key_name: str`, `key_value: object`
    - Define `ImmutableEntityError(StorageError)` with `entity_type: str`, `entity_id: int`
    - Define `MigrationError(StorageError)` with `migration_version: int`, `db_name: str`
    - Define `StorageTimeoutError(StorageError)` with `timeout_seconds: float`
    - All classes fully type-annotated; add `__init__` methods with descriptive messages
    - _Requirements: 9.5, 3.7, 3.12, 6.5, 1.7, 11.11_

  - [x] 3.2 Write unit tests for error hierarchy
    - Verify `StorageError` is a subclass of `PlatformError` (M1 integration)
    - Verify each error subclass sets the correct fields and produces descriptive messages
    - Verify `__cause__` is preserved for wrapped exceptions
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`
    - _Requirements: 9.5, 11.7_

- [x] 4. Implement XDG path resolution
  - [x] 4.1 Create `infrastructure/storage/paths.py` with cross-platform XDG path resolver
    - Implement `resolve_xdg_path(purpose: StoragePurpose, environ: Mapping[str, str] | None = None, platform: str | None = None) -> Path`
    - Handle `sys.platform` values: `"linux"` (XDG vars with defaults), `"darwin"` (macOS Library paths), `"win32"` (LOCALAPPDATA/APPDATA)
    - For each `StoragePurpose` value map to the correct XDG variable and fallback per the design table
    - All paths returned as absolute `pathlib.Path`; no `os.path` usage
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 11.3, 11.9_

  - [x] 4.2 Write property test for XDG path resolution (Property 1)
    - **Property 1: XDG Path Resolution Correctness**
    - **Validates: Requirements 1.4, 1.6**
    - Use `@settings(max_examples=200)`
    - Strategy: `st.sampled_from(["linux", "darwin", "win32"])` × `st.dictionaries(st.text(), st.text())` for env vars × `st.sampled_from(StoragePurpose values)`
    - Assert: result is absolute, rooted in expected platform base, has correct subdirectory suffix
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`

  - [x] 4.3 Write unit tests for XDG path resolution
    - Test Linux default paths (no XDG env vars set)
    - Test Linux with custom `XDG_CACHE_HOME` and `XDG_DATA_HOME`
    - Test macOS fallback paths
    - Test Windows fallback paths
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`, `@pytest.mark.cross_platform`
    - _Requirements: 1.4, 11.9_

- [x] 5. Implement LocalStorageProvider and DefaultStorageEngine
  - [x] 5.1 Create `infrastructure/storage/providers.py` with LocalStorageProvider
    - Implement `LocalStorageProvider(StorageProvider)` backed by `pathlib.Path`
    - `create_directory`: use `path.mkdir(parents=True, exist_ok=True)` — wrap `PermissionError` as `StorageError`
    - `remove_matching`: use `path.glob(pattern)` and `shutil.rmtree`/`Path.unlink`
    - `resolve_path`: delegate to `resolve_xdg_path()` from `paths.py`
    - `check_writable`: use `os.access(path, os.W_OK)`
    - All methods `async def` with `await asyncio.to_thread(...)` for blocking calls
    - _Requirements: 1.1, 1.2, 1.3, 1.5, 1.8, 1.9, 11.3, 11.5_

  - [x] 5.2 Create `infrastructure/storage/engine.py` with DefaultStorageEngine
    - Implement `DefaultStorageEngine(StorageEngine)` taking `StorageProvider` and `EventBus` in constructor
    - `initialize()`: create all 7 purpose directories, remove `.tmp`/`tmp_` files from workspace, verify all dirs writable, publish `StorageInitializedEvent`
    - `shutdown()`: publish `StorageShutdownEvent`; apply 30-second timeout via `asyncio.wait_for`; raise `StorageTimeoutError` on timeout
    - `get_path(purpose, relative)`: delegate to provider's `resolve_path` and apply relative suffix
    - Implement `__aenter__`/`__aexit__` calling `initialize`/`shutdown`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.6, 1.7, 1.8, 1.9, 9.4, 9.6_

  - [x] 5.3 Write property test for temporary file cleanup (Property 2)
    - **Property 2: Temporary File Cleanup**
    - **Validates: Requirements 1.8, 7.4**
    - Use `@settings(max_examples=200)`
    - Strategy: `st.lists(st.tuples(st.text(min_size=1).filter(lambda s: '/' not in s), st.booleans()))` for (filename, is_tmp)
    - Create actual files in `tmp_path`; some with `.tmp` suffix or `tmp_` prefix
    - Assert: after `initialize()`, all `.tmp`/`tmp_` files removed; other files untouched
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`

  - [x] 5.4 Write unit tests for DefaultStorageEngine
    - Test `initialize` creates all expected directories (mock StorageProvider)
    - Test `initialize` raises `StorageError` if a directory is not writable
    - Test `shutdown` raises `StorageTimeoutError` when timeout exceeded
    - Test `__aenter__`/`__aexit__` calls `initialize`/`shutdown` in order
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`
    - _Requirements: 1.7, 1.9_

- [x] 6. Implement entity models
  - [x] 6.1 Create `infrastructure/models/base.py` with DeclarativeBase and TimestampMixin
    - Define `Base(DeclarativeBase)` with no extra metadata
    - Define `TimestampMixin` with `created_at: Mapped[datetime]` and `updated_at: Mapped[datetime]` using `server_default=func.now()` and `onupdate=lambda: datetime.now(UTC)`
    - Use `DateTime(timezone=True)` for both columns
    - _Requirements: 5.1, 5.8, 5.9_

  - [x] 6.2 Create `infrastructure/models/mirror.py` with RepositoryFile model
    - Define `RepositoryFileState` enum: `DISCOVERED`, `QUEUED`, `DOWNLOADING`, `DOWNLOADED`, `VERIFIED`, `INDEXED`, `FAILED`
    - Define `RepositoryFile(Base, TimestampMixin)` with all columns per design table: `id`, `url`, `sha256`, `size_bytes`, `state`, `retry_count`, `local_path`
    - Add appropriate `Index` definitions for `url`, `sha256`, `state`
    - Use `Mapped[]` annotations throughout; zero `basedpyright` errors
    - `__tablename__ = "repository_files"`; bind to mirror metadata only (separate `Base` subclass or metadata group)
    - _Requirements: 5.1, 5.3, 5.4, 5.7, 5.9, 10.2_

  - [x] 6.3 Create `infrastructure/models/metadata.py` with metadata.db entity models
    - Define `Repository`, `RepositorySnapshot`, `PackageInstance`, `SourcePackage`, `LicenseExpression` with all columns and constraints from design tables
    - Enforce unique constraint on `PackageInstance(package_name, version, architecture, filename)`
    - Enforce unique constraint on `SourcePackage(name, version)`
    - Enforce unique constraint on `Repository(name)`
    - Add FK relationships: `RepositorySnapshot.repository_id → Repository.id`, `PackageInstance.snapshot_id → RepositorySnapshot.id`, `LicenseExpression.package_id → PackageInstance.id`
    - All columns use `Mapped[]`; add indexes per design table
    - _Requirements: 5.1, 5.2, 5.3, 5.6, 5.7, 5.9, 10.3_

  - [x] 6.4 Create `infrastructure/models/scan.py` with ScanSession and SBOMDocument models
    - Define `ScanState` enum: `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`
    - Define `ScanSession(Base, TimestampMixin)` and `SBOMDocument(Base, TimestampMixin)` with all columns per design tables
    - Add FK relationships and indexes per design table
    - _Requirements: 5.1, 5.3, 5.7, 5.9, 10.3_

  - [x] 6.5 Create `infrastructure/models/cache.py` with cache.db entity models
    - Define `ParsedDep5`, `NormalizedLicense`, `ChecksumCache` with `valid: Mapped[bool]` column (default `True`)
    - Key columns: `ParsedDep5.source_sha256`, `NormalizedLicense.raw_expression`, `ChecksumCache.content_sha256`
    - All models include `TimestampMixin` and use `Mapped[]` annotations
    - _Requirements: 5.1, 5.7, 5.9, 10.4_

  - [x] 6.6 Write property test for timestamp invariants (Property 15)
    - **Property 15: Timestamp Invariants**
    - **Validates: Requirements 5.8**
    - Use `@settings(max_examples=200)`; use in-memory SQLite via `sqlite+aiosqlite:///:memory:`
    - Strategy: entity construction with randomized field values using `st.builds()`
    - Assert: after `add()`, `created_at == updated_at`; after `update()`, `updated_at >= old_updated_at` and `created_at` unchanged
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`

  - [x] 6.7 Write unit tests for entity models
    - Verify correct `__tablename__` values and column definitions
    - Verify unique constraints are present in SQLAlchemy table args
    - Verify `RepositoryFileState` and `ScanState` enum values
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`
    - _Requirements: 5.2, 5.4, 11.7_

- [x] 7. Checkpoint — contracts, errors, models
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Implement Database Provider and session factory
  - [x] 8.1 Create `infrastructure/database/session.py` with async session factory helpers
    - Implement `create_async_engine_for(db_path: Path) -> AsyncEngine` applying WAL, foreign_keys, synchronous PRAGMA via `event.listen` on `connect`
    - Implement `create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]` with `expire_on_commit=False`
    - Use `QueuePool` with `pool_size=5`, `max_overflow=0`
    - _Requirements: 2.4, 2.6, 2.7_

  - [x] 8.2 Create `infrastructure/database/provider.py` with SqliteDatabaseProvider
    - Implement `SqliteDatabaseProvider(DatabaseProvider)` taking `StorageEngine` in constructor
    - Lazy engine creation on first `get_session()` call; cache engines by name
    - `get_session(db_name)`: validate name is one of `{"mirror","metadata","cache"}`; raise `StorageError` for unknown names; wrap `OperationalError` into `DatabaseConnectionError` with correct `failure_type`
    - `dispose()`: call `engine.dispose()` on all engines with 10-second `asyncio.wait_for` timeout
    - `health_check()`: execute `SELECT 1` on each engine; return `{"mirror": bool, "metadata": bool, "cache": bool}`
    - _Requirements: 2.2, 2.3, 2.4, 2.5, 2.8, 2.9, 11.5_

  - [x] 8.3 Write property test for invalid database name rejection (Property 3)
    - **Property 3: Invalid Database Name Rejection**
    - **Validates: Requirements 2.9**
    - Use `@settings(max_examples=200)`
    - Strategy: `st.text().filter(lambda s: s not in {"mirror", "metadata", "cache"})`
    - Assert: `get_session(invalid_name)` raises `StorageError`
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`

  - [x] 8.4 Write unit tests for SqliteDatabaseProvider
    - Test engine creation with correct PRAGMA settings (inspect connection events)
    - Test `dispose()` calls `engine.dispose()` on all active engines
    - Test `health_check()` returns correct boolean map
    - Test `DatabaseConnectionError` raised for corrupt/missing/permission-denied databases
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`, `@pytest.mark.database`
    - _Requirements: 2.5, 2.7, 2.8_

- [x] 9. Implement migration runner
  - [x] 9.1 Create `infrastructure/database/migrations.py` with MigrationRunner
    - Implement `MigrationRunner` accepting a session factory and a `Path` to the migration directory
    - `ensure_history_table(session)`: create `_migration_history` if absent using raw DDL
    - `get_applied_versions(session) -> set[int]`: query history table
    - `discover_migrations(directory: Path) -> list[tuple[int, Path]]`: scan for `v{N}_*.py` files, sort ascending
    - `run_pending(session)`: for each unapplied version, use savepoint, call `migrate_vN(session)`, record in history with `duration_ms`, release savepoint; on failure rollback to savepoint, raise `MigrationError`, halt
    - On each successful migration, publish `MigrationAppliedEvent` via injected `EventBus`
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.8, 6.9, 9.6_

  - [x] 9.2 Create initial migration files for each database
    - `migrations/mirror/v1_create_repository_files.py`: create `repository_files` table + indexes
    - `migrations/metadata/v1_create_metadata_tables.py`: create `repositories`, `repository_snapshots`, `package_instances`, `source_packages`, `license_expressions` tables + indexes
    - `migrations/metadata/v2_create_scan_tables.py`: create `scan_sessions`, `sbom_documents` tables + indexes
    - `migrations/cache/v1_create_cache_tables.py`: create `parsed_dep5`, `normalized_licenses`, `checksum_cache` tables
    - Each migration is an `async def migrate_vN(session: AsyncSession) -> None` function
    - _Requirements: 6.1, 6.2, 10.1, 10.2, 10.3, 10.4_

  - [x] 9.3 Write property tests for migration ordering and idempotence (Properties 16, 17)
    - **Property 16: Migration Ordering and Idempotence**
    - **Property 17: Migration History Recording**
    - **Validates: Requirements 6.2, 6.3, 6.9**
    - Use `@settings(max_examples=200)`; use in-memory SQLite
    - Property 16 strategy: `st.permutations(range(1, N))` — assert migrations applied ascending regardless of discovery order
    - Property 17: verify history row has valid ISO-8601 timestamp and `duration_ms >= 0` after each migration
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`, `@pytest.mark.database`

  - [x] 9.4 Write unit tests for MigrationRunner
    - Test `ensure_history_table` creates table on empty database
    - Test migration is skipped if already in history
    - Test failed migration rolls back and raises `MigrationError`
    - Test `MigrationAppliedEvent` published on success
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`, `@pytest.mark.database`
    - _Requirements: 6.4, 6.5, 6.8, 6.9_

- [x] 10. Implement Repository base and concrete repositories
  - [x] 10.1 Create `infrastructure/repositories/base.py` with SqlAlchemyRepository[T]
    - Generic class `SqlAlchemyRepository(Repository[T])` accepting `AsyncSession` and `model_class: type[T]`
    - `add(entity)`: `session.add(entity)` + `session.flush()` → returns entity with populated PK
    - `get_by_id(entity_id)`: `session.get(model_class, entity_id)` → raise `EntityNotFoundError` if None
    - `find(**filters)`: build `select(model_class).where(...)` from kwargs → return `list[T]` (empty list if no results)
    - `update(entity)`: `session.merge(entity)` + `session.flush()` → returns merged entity
    - `delete(entity_id)`: execute `delete(model_class).where(id == entity_id)`
    - Support `batch_add(entities: list[T])` using `session.add_all()` + `session.flush()`
    - Support `stream(*, yield_per: int = 1000, **filters)` returning `AsyncIterator[T]` using `yield_per()`
    - _Requirements: 3.2, 3.3, 3.4, 3.7, 3.8, 3.11, 8.1, 8.3, 8.5, 8.6_

  - [x] 10.2 Create concrete repository implementations
    - `infrastructure/repositories/repository_file.py`: `RepositoryFileRepository` with `find_by_state(state: RepositoryFileState) -> list[RepositoryFile]`
    - `infrastructure/repositories/package.py`: `PackageRepository` with `get_by_natural_key(package_name, version, architecture, filename) -> PackageInstance`
    - `infrastructure/repositories/source_package.py`: `SourcePackageRepository`
    - `infrastructure/repositories/snapshot.py`: `SnapshotRepository` — override `update`/`delete` to check `published` flag, raise `ImmutableEntityError` if true
    - `infrastructure/repositories/license.py`: `LicenseRepository`
    - `infrastructure/repositories/scan_session.py`: `ScanSessionRepository`
    - `infrastructure/repositories/sbom.py`: `SBOMRepository`
    - _Requirements: 3.1, 3.7, 3.9, 3.10, 3.12, 5.5_

  - [x] 10.3 Write property tests for repository round-trips (Properties 4, 5, 6, 7, 8)
    - **Property 4: Repository Round-Trip (Surrogate Key)**
    - **Property 5: Repository Round-Trip (Natural Key)**
    - **Property 6: Repository State Filtering**
    - **Property 7: Empty Find Returns Empty List**
    - **Property 8: Missing Entity Lookup Raises StorageError**
    - **Validates: Requirements 3.2, 3.7, 3.9, 3.10, 3.11, 11.10**
    - Use `@settings(max_examples=200)`; use in-memory SQLite with applied migrations
    - Strategies: `st.builds(PackageInstance, ...)`, `st.builds(RepositoryFile, state=...)`, `st.integers(min_value=1)`
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`

  - [x] 10.4 Write property tests for snapshot immutability and natural key uniqueness (Properties 9, 14)
    - **Property 9: Published Snapshot Immutability**
    - **Property 14: Natural Key Uniqueness Enforcement**
    - **Validates: Requirements 3.12, 5.2, 5.5**
    - Use `@settings(max_examples=200)`; use in-memory SQLite
    - Property 9: insert published snapshot → assert `update()`/`delete()` raise `ImmutableEntityError`
    - Property 14: insert `PackageInstance` → insert duplicate natural key → assert `StorageError` raised
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`

- [x] 11. Checkpoint — database, migrations, repositories
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Implement Unit of Work
  - [x] 12.1 Create `infrastructure/database/unit_of_work.py` with SqliteUnitOfWork
    - Implement `SqliteUnitOfWork(UnitOfWork)` parameterised by `db_name: str`
    - Constructor takes `DatabaseProvider`; store `db_name`
    - `__aenter__`: acquire `AsyncSession` from provider via `get_session(db_name)` and begin transaction
    - `__aexit__`: if no exception → `commit()`; if commit fails → `rollback()` + raise `StorageError`; if exception → `rollback()` + re-raise
    - `commit()`: check `CancellationToken` if available; if cancelled → `rollback()` + raise `StorageError`; else `session.commit()`
    - `rollback()`: `session.rollback()` — leave session usable for subsequent operations
    - Expose typed repository properties (lazy-created, sharing `self._session`): `packages`, `source_packages`, `repository_files`, `snapshots`, `licenses`, `scan_sessions`, `sbom_documents`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 4.11_

  - [x] 12.2 Write property tests for UoW commit atomicity and rollback (Properties 10, 11, 12)
    - **Property 10: Commit Atomicity**
    - **Property 11: Rollback Discards All Changes**
    - **Property 12: Cancellation Prevents Commit**
    - **Validates: Requirements 3.3, 4.2, 4.3, 4.9, 9.8**
    - Use `@settings(max_examples=200)`; use in-memory SQLite
    - Property 10: add N entities inside UoW, verify none visible before commit, all visible after commit
    - Property 11: add entities, call `rollback()`, verify none retrievable
    - Property 12: set `CancellationToken` → `commit()` raises `StorageError` and no entities persisted
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`, `@pytest.mark.database`

  - [x] 12.3 Write property test for auto-incrementing surrogate keys (Property 13)
    - **Property 13: Auto-Incrementing Surrogate Keys**
    - **Validates: Requirements 5.1**
    - Use `@settings(max_examples=200)`; use in-memory SQLite
    - Strategy: `st.lists(entity_strategy, min_size=2, max_size=50)`
    - Assert: all entity IDs unique and strictly ascending within session
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`

  - [x] 12.4 Write unit tests for SqliteUnitOfWork
    - Test context manager commits on clean exit
    - Test context manager rolls back on exception
    - Test repository properties return same instances on repeated access
    - Test `CancellationToken` prevents commit
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`, `@pytest.mark.database`
    - _Requirements: 4.4, 4.5, 4.6, 4.9_

- [x] 13. Implement storage events
  - [x] 13.1 Create `infrastructure/events.py` with storage lifecycle events
    - Define `StorageInitializedEvent(DomainEvent)` frozen dataclass with `base_path: str`
    - Define `StorageShutdownEvent(DomainEvent)` frozen dataclass
    - Define `MigrationAppliedEvent(DomainEvent)` frozen dataclass with `db_name: str`, `version: int`, `duration_ms: int`
    - All events set `event_type` to appropriate string in field default
    - _Requirements: 9.6_

  - [x] 13.2 Write property test for lifecycle event publication (Property 22)
    - **Property 22: Lifecycle Event Publication**
    - **Validates: Requirements 9.6**
    - Use `@settings(max_examples=200)`
    - Strategy: mock `EventBus`, call lifecycle methods on `StorageEngine`
    - Assert: `StorageInitializedEvent` published on initialize; `StorageShutdownEvent` on shutdown; `MigrationAppliedEvent` on each migration
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`

- [x] 14. Implement recovery mechanisms
  - [x] 14.1 Add download recovery logic to DefaultStorageEngine initialization
    - After directories are created and temps removed, query mirror.db for `RepositoryFile` entries in `DOWNLOADING` state
    - For each entry: if `retry_count < 3` → set state to `QUEUED`, increment `retry_count`; if `retry_count >= 3` → set state to `FAILED`
    - Use a UoW scoped to `mirror` for the bulk state update
    - _Requirements: 7.1_

  - [x] 14.2 Add cache integrity verification to DefaultStorageEngine initialization
    - After download recovery, scan mirror cache directory
    - For each file: compute SHA256 and compare to stored checksum in mirror.db
    - Remove files whose SHA256 doesn't match
    - Mark cache.db entries as `valid=False` if SHA256 mismatch detected between cached and metadata values
    - _Requirements: 7.3, 7.5, 10.6_

  - [x] 14.3 Write property tests for recovery (Properties 18, 19, 20, 23, 24)
    - **Property 18: Download Recovery State Machine**
    - **Property 19: Cache Integrity Verification**
    - **Property 20: Cache Corruption Marking**
    - **Property 23: cache.db Deletion Recovery**
    - **Property 24: Cache/Metadata Conflict Resolution**
    - **Validates: Requirements 7.1, 7.3, 7.5, 10.5, 10.6**
    - Use `@settings(max_examples=200)`; use in-memory SQLite + tmp_path for filesystem
    - Property 18: `st.builds(RepositoryFile, state=DOWNLOADING, retry_count=st.integers(0, 5))` — verify transitions
    - Property 19: create files with known/mismatched SHA → verify mismatched removed
    - Property 20: create cache entries with mismatched SHA → verify marked `valid=False`
    - Property 23: delete cache.db → reinitialize → verify recreated empty, mirror/metadata unaffected
    - Property 24: conflicting entries → cache marked invalid, metadata unchanged
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`

  - [x] 14.4 Write unit tests for recovery mechanisms
    - Test download recovery transitions `DOWNLOADING` → `QUEUED` with retry_count < 3
    - Test download recovery transitions `DOWNLOADING` → `FAILED` with retry_count >= 3
    - Test cache integrity removes mismatched files
    - Test `cache.db` recreation on missing file
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`, `@pytest.mark.database`
    - _Requirements: 7.1, 7.3, 7.5, 10.5_

- [x] 15. Implement bootstrap function and wire everything together
  - [x] 15.1 Create `infrastructure/bootstrap.py` with `storage_bootstrap()`
    - Implement `async def storage_bootstrap(container: Container) -> None`
    - Register `DefaultStorageEngine` as singleton for `StorageEngine`
    - Register `SqliteDatabaseProvider` as singleton for `DatabaseProvider`
    - Register `SqliteUnitOfWork("mirror")`, `SqliteUnitOfWork("metadata")`, `SqliteUnitOfWork("cache")` as scoped for `UnitOfWork`
    - Register all 7 concrete repository classes as scoped for their abstract interface types
    - Acquire `StorageEngine` via `container.resolve(StorageEngine)` and pass to `ResourceManager.acquire_async()`
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.7, 9.9_

  - [x] 15.2 Write property test for error wrapping (Property 25)
    - **Property 25: Error Wrapping**
    - **Validates: Requirements 11.11**
    - Use `@settings(max_examples=200)`
    - Strategy: `st.sampled_from([PermissionError, FileNotFoundError, OSError])` → inject into storage operations
    - Assert: `StorageError` (or subclass) raised; original exception preserved as `__cause__`
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`

  - [x] 15.3 Write property test for batch insert correctness (Property 21)
    - **Property 21: Batched Insert Correctness**
    - **Validates: Requirements 8.1**
    - Use `@settings(max_examples=200)`; use in-memory SQLite
    - Strategy: `st.lists(entity_strategy, min_size=1, max_size=100)`
    - Call `batch_add(entities)`, commit, then retrieve each by surrogate key
    - Assert: all N entities individually retrievable with correct field values
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`

  - [x] 15.4 Write unit tests for bootstrap
    - Test all required service types are registered in the container after `storage_bootstrap()`
    - Test `StorageEngine` registered as singleton (same instance on two resolves)
    - Test repositories registered as scoped (different instances across scopes)
    - Test `ResourceManager.acquire_async()` called with `StorageEngine`
    - Mark `@pytest.mark.unit`, `@pytest.mark.storage`
    - _Requirements: 9.1, 9.2, 9.7, 9.9_

- [x] 16. Final checkpoint — full integration
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- `aiosqlite` must be added to runtime dependencies (not just dev), as it is required by SQLAlchemy async at runtime
- Import-linter constraints are strict: `platform/contracts/` may not import from `infrastructure/`; `domain/` may not import from `infrastructure/`. The new contracts files (storage.py, persistence.py) must not import from kernel or infrastructure.
- All property tests use `@settings(max_examples=200)` and Hypothesis strategies; mark `@pytest.mark.unit @pytest.mark.storage`
- Database tests that need actual SQLite use in-memory databases (`sqlite+aiosqlite:///:memory:`) for speed; filesystem tests use `tmp_path`
- Three separate SQLite databases means three independent migration tracks and three independent `Base` metadata instances if needed — or the same `Base` with separate engine bindings
- The `CancellationToken` passed to `SqliteUnitOfWork` comes from the `WorkflowContext` scope; if not present (e.g. in tests), skip the check
- Recovery logic in `DefaultStorageEngine.initialize()` depends on `SqliteDatabaseProvider` and repositories being available — inject them via the constructor or accept them as arguments to `initialize()`
- Use `uv run pytest -m 'unit and storage'` to run only storage unit tests during development

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "2.2", "3.1"] },
    { "id": 2, "tasks": ["2.3", "3.2", "4.1", "6.1"] },
    { "id": 3, "tasks": ["4.2", "4.3", "5.1", "6.2", "6.3", "6.4", "6.5", "8.1"] },
    { "id": 4, "tasks": ["5.2", "6.6", "6.7", "8.2", "9.1", "9.2", "10.1"] },
    { "id": 5, "tasks": ["5.3", "5.4", "8.3", "8.4", "9.3", "9.4", "10.2"] },
    { "id": 6, "tasks": ["10.3", "10.4", "12.1", "13.1"] },
    { "id": 7, "tasks": ["12.2", "12.3", "12.4", "13.2"] },
    { "id": 8, "tasks": ["14.1", "14.2"] },
    { "id": 9, "tasks": ["14.3", "14.4"] },
    { "id": 10, "tasks": ["15.1"] },
    { "id": 11, "tasks": ["15.2", "15.3", "15.4"] }
  ]
}
```
