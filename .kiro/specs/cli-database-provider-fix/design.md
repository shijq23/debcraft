# CLI Database Provider Fix - Bugfix Design

## Overview

The `_CliDatabaseProvider.get_session()` method in `src/debcraft/cli/mirror.py` returns `None` instead of a valid `AsyncSession`, violating the `DatabaseProvider` contract. This causes `MirrorEngine` to crash with `AttributeError: 'NoneType' object has no attribute 'close'` whenever any method calls `await session.close()` in its `finally` block. The fix replaces the `None`-returning stub with a proper in-memory SQLite async session using `aiosqlite`, satisfying the contract while remaining ephemeral (no persistence between runs).

## Glossary

- **Bug_Condition (C)**: The condition that triggers the bug — when `_CliDatabaseProvider.get_session()` is called and returns `None` instead of a valid `AsyncSession`
- **Property (P)**: The desired behavior — `get_session()` returns a valid `AsyncSession` that supports `execute`, `commit`, `rollback`, and `close` without error
- **Preservation**: Existing platform-level `SqliteDatabaseProvider` behavior and `MirrorEngine` file-download functionality must remain unchanged
- **_CliDatabaseProvider**: The CLI-context database provider class in `src/debcraft/cli/mirror.py` that implements the `DatabaseProvider` contract
- **DatabaseProvider**: Abstract base class in `src/debcraft/platform/contracts/persistence.py` defining the `get_session`, `dispose`, and `health_check` interface
- **MirrorEngine**: The infrastructure component in `src/debcraft/infrastructure/mirror/engine.py` that calls `get_session("mirror")` and uses the returned session for database operations

## Bug Details

### Bug Condition

The bug manifests when the CLI invokes `debcraft mirror sync` which instantiates `_CliDatabaseProvider` and passes it to `MirrorEngine`. Every `MirrorEngine` method that calls `await self._db_provider.get_session("mirror")` receives `None`, and the subsequent `await session.close()` (or `session.execute()`, `session.commit()`, `session.rollback()`) in `try/finally` blocks crashes with `AttributeError`.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type { provider: DatabaseProvider, db_name: DatabaseName }
  OUTPUT: boolean

  RETURN input.provider IS _CliDatabaseProvider
         AND input.db_name IN ['mirror', 'metadata', 'cache']
         AND input.provider.get_session(input.db_name) RETURNS None
END FUNCTION
```

### Examples

- **Example 1**: `MirrorEngine._resume_interrupted_downloads()` calls `await self._db_provider.get_session("mirror")` → receives `None` → `await session.execute(stmt)` raises `AttributeError: 'NoneType' object has no attribute 'execute'`
- **Example 2**: Same method reaches `finally: await session.close()` → raises `AttributeError: 'NoneType' object has no attribute 'close'`
- **Example 3**: `MirrorEngine._upsert_repository_file()` calls `get_session("mirror")` → receives `None` → crashes on first session method call
- **Edge case**: Calling `get_session("metadata")` or `get_session("cache")` also returns `None`, though `MirrorEngine` currently only requests `"mirror"`

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- The platform-level `SqliteDatabaseProvider` must continue to provide fully functional sessions connected to on-disk SQLite databases
- `MirrorEngine` database operations (execute, commit, rollback) through a valid session must continue to persist and retrieve mirror state correctly when using the platform provider
- `_CliDatabaseProvider.dispose()` must continue to clean up resources without error
- CLI mirror sync file-download logic must continue to download and store repository files correctly
- The `health_check()` method behavior is not impacted by this fix

**Scope:**
All inputs that do NOT involve the `_CliDatabaseProvider` class should be completely unaffected by this fix. This includes:
- Platform-level database operations via `SqliteDatabaseProvider`
- File download and storage operations
- Event bus, cancellation token, and progress reporter behavior
- Any other CLI stub implementations (`_CliEventBus`, `_CliLogger`, etc.)

## Hypothesized Root Cause

Based on the bug description, the root cause is clear:

1. **Intentional but incorrect stub**: The `_CliDatabaseProvider.get_session()` was written as a deliberate no-op that returns `None` with a `# type: ignore[return-value]` comment. The original developer assumed `MirrorEngine` would handle `None` gracefully, but it does not — every code path unconditionally calls methods on the returned session object.

2. **Contract violation**: The `DatabaseProvider` abstract class defines `get_session()` with return type `AsyncSession` (not `Optional[AsyncSession]`). Returning `None` violates this contract. The type-ignore comment suppresses the static analysis warning that would have caught this.

3. **Missing fallback in MirrorEngine**: `MirrorEngine` has no `None`-checks on the session because the contract guarantees a valid session. This is correct design — the provider should honor its contract rather than pushing null-checks onto every consumer.

## Correctness Properties

Property 1: Bug Condition - get_session Returns Valid AsyncSession

_For any_ call to `_CliDatabaseProvider.get_session(db_name)` where `db_name` is one of `"mirror"`, `"metadata"`, or `"cache"`, the fixed implementation SHALL return a valid `AsyncSession` instance (not `None`) that supports `execute()`, `commit()`, `rollback()`, and `close()` without raising `AttributeError`.

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation - Platform DatabaseProvider Unchanged

_For any_ usage of `SqliteDatabaseProvider` (the platform-level implementation), the fixed code SHALL produce exactly the same behavior as the original code, preserving full database persistence, session management, and error handling for non-CLI contexts.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `src/debcraft/cli/mirror.py`

**Class**: `_CliDatabaseProvider`

**Specific Changes**:

1. **Add imports**: Import `create_async_engine` and `async_sessionmaker` from `sqlalchemy.ext.asyncio` (or reuse existing session factory utilities from the project)

2. **Create in-memory engine**: Initialize a `sqlite+aiosqlite:///` (in-memory) async engine in the class constructor or lazily on first `get_session()` call

3. **Create session factory**: Build an `async_sessionmaker` bound to the in-memory engine

4. **Replace get_session body**: Return `self._session_factory()` instead of `None` — this produces a real `AsyncSession` that supports all standard operations (execute, commit, rollback, close) as no-ops from a persistence standpoint since no tables are created and nothing is saved

5. **Update dispose()**: Dispose of the in-memory engine to clean up resources properly, matching the contract's expectation of closing connection pools within 10 seconds

6. **Optionally update health_check()**: Return `{"mirror": True, "metadata": True, "cache": True}` or execute a simple `SELECT 1` against the in-memory engine to reflect that sessions are functional

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that instantiate `_CliDatabaseProvider`, call `get_session("mirror")`, and attempt to use the returned value as a session. Run these tests on the UNFIXED code to observe failures and confirm the root cause.

**Test Cases**:
1. **get_session returns None**: Call `get_session("mirror")` and assert the return value is not `None` (will fail on unfixed code)
2. **session.close() crashes**: Call `await session.close()` on the returned value (will fail on unfixed code with `AttributeError`)
3. **session.execute() crashes**: Call `await session.execute(...)` on the returned value (will fail on unfixed code with `AttributeError`)
4. **session.rollback() crashes**: Call `await session.rollback()` on the returned value (will fail on unfixed code with `AttributeError`)

**Expected Counterexamples**:
- `get_session("mirror")` returns `None`
- Any method call on the result raises `AttributeError: 'NoneType' object has no attribute '<method>'`
- Root cause confirmed: the method body is `return None`

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL db_name IN ['mirror', 'metadata', 'cache'] DO
  session := await _CliDatabaseProvider_fixed.get_session(db_name)
  ASSERT session IS NOT None
  ASSERT session IS instance of AsyncSession
  ASSERT await session.close() does not raise
  ASSERT await session.execute(text("SELECT 1")) does not raise
  ASSERT await session.commit() does not raise
  ASSERT await session.rollback() does not raise
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT SqliteDatabaseProvider_original(input) = SqliteDatabaseProvider_fixed(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain
- It catches edge cases that manual unit tests might miss
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

**Test Plan**: Observe behavior on UNFIXED code first for `SqliteDatabaseProvider` operations and `MirrorEngine` file downloads, then write property-based tests capturing that behavior.

**Test Cases**:
1. **SqliteDatabaseProvider session validity**: Verify that `SqliteDatabaseProvider.get_session()` continues to return functional sessions for all three database names
2. **dispose() cleanup**: Verify that `_CliDatabaseProvider.dispose()` completes without error both before and after sessions are created
3. **MirrorEngine integration**: Verify that `MirrorEngine` methods using the CLI provider no longer crash and can execute their full try/finally flows

### Unit Tests

- Test `_CliDatabaseProvider.get_session()` returns a valid `AsyncSession` for each `DatabaseName`
- Test that returned sessions support `execute`, `commit`, `rollback`, `close` without raising
- Test `dispose()` cleans up the in-memory engine without error
- Test `health_check()` returns appropriate status
- Test that multiple `get_session()` calls return independent sessions

### Property-Based Tests

- Generate random sequences of session operations (`execute`, `commit`, `rollback`, `close`) and verify none raise `AttributeError` on the CLI provider's sessions
- Generate random `DatabaseName` values and verify `get_session` always returns a non-None `AsyncSession`
- Generate random interleaved `get_session` / `dispose` call sequences and verify no crashes

### Integration Tests

- Test full `debcraft mirror sync` CLI flow with the fixed provider (end-to-end smoke test)
- Test that `MirrorEngine._resume_interrupted_downloads()` completes without error using the CLI provider
- Test that `MirrorEngine._upsert_repository_file()` completes without error using the CLI provider
