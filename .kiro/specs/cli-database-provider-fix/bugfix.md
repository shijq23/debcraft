# Bugfix Requirements Document

## Introduction

Running `uv run debcraft mirror sync` crashes with `'NoneType' object has no attribute 'close'`. The `_CliDatabaseProvider` class in the CLI mirror module returns `None` from `get_session()`, violating the `DatabaseProvider` contract which guarantees a valid `AsyncSession`. When `MirrorEngine` calls `await session.close()` in its `finally` blocks, it crashes because the session is `None`.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the CLI invokes `debcraft mirror sync` THEN the system crashes with `AttributeError: 'NoneType' object has no attribute 'close'` because `_CliDatabaseProvider.get_session()` returns `None`

1.2 WHEN `MirrorEngine._resume_interrupted_downloads()` calls `await self._db_provider.get_session("mirror")` using the CLI database provider THEN the system receives `None` instead of a valid `AsyncSession`

1.3 WHEN any `MirrorEngine` method executes its `finally` block with `await session.close()` after receiving `None` from the CLI database provider THEN the system raises an `AttributeError`

### Expected Behavior (Correct)

2.1 WHEN the CLI invokes `debcraft mirror sync` THEN the system SHALL execute the sync operation without raising an `AttributeError` on session handling

2.2 WHEN `MirrorEngine` calls `await self._db_provider.get_session("mirror")` using the CLI database provider THEN the system SHALL return a valid `AsyncSession` instance that honors the `DatabaseProvider` contract

2.3 WHEN any `MirrorEngine` method executes its `finally` block with `await session.close()` THEN the system SHALL successfully close the session without error

### Unchanged Behavior (Regression Prevention)

3.1 WHEN the platform-level `DatabaseProvider` is used (non-CLI context) THEN the system SHALL CONTINUE TO provide fully functional database sessions connected to the configured SQLite databases

3.2 WHEN `MirrorEngine` performs database operations (execute, commit, rollback) through a valid session THEN the system SHALL CONTINUE TO persist and retrieve mirror state correctly

3.3 WHEN `_CliDatabaseProvider.dispose()` is called THEN the system SHALL CONTINUE TO clean up resources without error

3.4 WHEN the CLI mirror sync completes file downloads THEN the system SHALL CONTINUE TO download and store repository files correctly regardless of the database provider implementation
