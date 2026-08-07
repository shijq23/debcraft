# Rate Limiter Integration Bugfix Design

## Overview

The `TokenBucketRateLimiter` class exists in `rate_limiter.py` with a complete implementation (token bucket algorithm, async acquire, waiter cancellation) but is never instantiated or called by the download pipeline. The `DownloadCoordinator` fires HTTP GET and HEAD requests without throttling, and `engine.py` ignores the user's `max_connections_per_repo` config by hardcoding `_MAX_CONCURRENT_DOWNLOADS = 20`. The fix wires the existing rate limiter into the download lifecycle and replaces the hardcoded concurrency with the configured value.

## Glossary

- **Bug_Condition (C)**: Any HTTP request (GET or HEAD) issued by `DownloadCoordinator` while `TokenBucketRateLimiter` is not instantiated or not called before the request
- **Property (P)**: Every outgoing HTTP request must be preceded by a successful `rate_limiter.acquire()` call, limiting sustained throughput to `rate_limit_rps` with bursts up to `rate_limit_burst`
- **Preservation**: Retry logic, checksum verification, atomic file writes, connection pooling, and session lifecycle behavior must remain unchanged
- **TokenBucketRateLimiter**: The async rate limiter in `rate_limiter.py` using token bucket algorithm with configurable rate and burst size
- **DownloadCoordinator**: The class in `download.py` managing concurrent HTTP downloads with retry and backoff
- **MirrorConfig**: Frozen dataclass in `config.py` holding `rate_limit_rps`, `rate_limit_burst`, `max_connections_per_repo`, and other mirror settings
- **MirrorEngine**: The orchestrator in `engine.py` that calls `download_batch()` with the hardcoded `_MAX_CONCURRENT_DOWNLOADS` constant

## Bug Details

### Bug Condition

The bug manifests for every HTTP request issued by `DownloadCoordinator`. The `TokenBucketRateLimiter` class is fully implemented but never instantiated during `start()`, never called in `_attempt_download()` or `check_conditional()`, and never cleaned up in `close()`. Additionally, `engine.py` passes a hardcoded constant `20` to `download_batch(max_concurrent=...)` instead of the user's `config.max_connections_per_repo` value.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type HTTPRequestContext
  OUTPUT: boolean

  RETURN input.coordinator.rate_limiter IS None
         OR input.coordinator.rate_limiter.acquire() NOT called before HTTP request
         OR input.engine.max_concurrent != input.config.max_connections_per_repo
END FUNCTION
```

### Examples

- User sets `rate_limit_rps = 10` in `mirrors.toml` → CDN receives 200+ requests/second during bulk sync → HTTP 403 errors
- User sets `rate_limit_burst = 5` in `mirrors.toml` → Setting is parsed into `MirrorConfig` but has no effect on request timing
- User sets `max_connections_per_repo = 5` in `mirrors.toml` → `engine.py` still passes `20` to `download_batch()`, so 20 downloads run concurrently (though `TCPConnector.limit_per_host` caps actual connections to 5, the semaphore is still wrong)
- `check_conditional()` sends HEAD requests without any rate limiting → contributes to CDN rate-limit triggers

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Retry logic: 5xx and network errors still retry with exponential backoff up to `_MAX_ATTEMPTS` times
- 4xx errors (non-rate-limit) still fail immediately without retry
- SHA256 and size verification still occur after download
- Atomic `.part` file writes and `os.replace` rename still occur
- `aiohttp.TCPConnector` with `limit_per_host` and `limit` still controls connection pooling
- `aiohttp.ClientSession` creation and cleanup still works the same way
- `download_batch()` API signature remains unchanged (caller passes `max_concurrent`)
- Default config values (`rate_limit_rps=50.0`, `rate_limit_burst=None` → resolves to `max_connections_per_repo`) still apply when not explicitly configured

**Scope:**
All code paths that do NOT involve outgoing HTTP request initiation or batch concurrency parameterization should be completely unaffected. This includes:
- File system operations (mkdir, unlink, replace, chmod)
- Hash computation and size checks
- Backoff delay computation
- Logging and error classification
- `DownloadResult` construction

## Hypothesized Root Cause

Based on the bug description, the root cause is incomplete integration:

1. **Missing Instantiation**: `DownloadCoordinator.start()` creates the `TCPConnector` and `ClientSession` but never instantiates `TokenBucketRateLimiter` from the config values. The rate limiter class exists but has no construction site.

2. **Missing Acquire Calls**: `_attempt_download()` jumps directly to `self._session.get(url, ...)` without calling `rate_limiter.acquire()`. Similarly, `check_conditional()` calls `self._session.head(url, ...)` without acquiring a token.

3. **Missing Cleanup**: `close()` tears down the session and connector but has no reference to a rate limiter to call `cancel_waiters()` on.

4. **Hardcoded Concurrency in Engine**: `engine.py` defines `_MAX_CONCURRENT_DOWNLOADS = 20` at module level and passes it to `download_batch()` instead of using `config.max_connections_per_repo` which is already available on the `MirrorConfig` instance.

## Correctness Properties

Property 1: Bug Condition - Rate Limiter Acquire Before HTTP Request

_For any_ HTTP request (GET via `_attempt_download` or HEAD via `check_conditional`) issued by `DownloadCoordinator`, the fixed code SHALL call `rate_limiter.acquire()` exactly once before the HTTP call is made, ensuring the sustained request rate does not exceed `config.rate_limit_rps`.

**Validates: Requirements 2.1, 2.2, 2.3, 2.5**

Property 2: Bug Condition - Concurrency Uses Config Value

_For any_ call to `download_batch()` from `MirrorEngine`, the fixed code SHALL pass `config.max_connections_per_repo` as the `max_concurrent` argument instead of the hardcoded constant `20`.

**Validates: Requirements 2.4**

Property 3: Preservation - Download Behavior Unchanged

_For any_ input where the rate limiter has tokens available (no blocking occurs), the fixed `_attempt_download` and `download_file` functions SHALL produce the same `DownloadResult` as the original functions, preserving retry logic, checksum verification, atomic writes, and error classification.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `src/debcraft/infrastructure/mirror/download.py`

**Class**: `DownloadCoordinator`

**Specific Changes**:

1. **Add import**: Import `TokenBucketRateLimiter` from `debcraft.infrastructure.mirror.rate_limiter`

2. **Add instance attribute**: Add `self._rate_limiter: TokenBucketRateLimiter | None = None` in `__init__`

3. **Instantiate in `start()`**: After creating the session, resolve burst size (`config.rate_limit_burst or config.max_connections_per_repo`) and instantiate `TokenBucketRateLimiter(rate=config.rate_limit_rps, burst_size=burst_size)`

4. **Acquire in `_attempt_download()`**: Before the `self._session.get(url, ...)` call, add `await self._rate_limiter.acquire()`

5. **Acquire in `check_conditional()`**: Before the `self._session.head(url, ...)` call, add `await self._rate_limiter.acquire()`

6. **Cleanup in `close()`**: Before closing the session, call `self._rate_limiter.cancel_waiters()` if the rate limiter is not None, then set it to None

---

**File**: `src/debcraft/infrastructure/mirror/engine.py`

**Constant**: `_MAX_CONCURRENT_DOWNLOADS`

**Specific Changes**:

1. **Remove or deprecate constant**: Remove `_MAX_CONCURRENT_DOWNLOADS = 20` (or keep as a fallback comment)

2. **Pass config value**: Change `max_concurrent=_MAX_CONCURRENT_DOWNLOADS` to `max_concurrent=config.max_connections_per_repo` where `config` is the `MirrorConfig` (accessible via `self._config` or passed from the repository config context)

3. **Access config**: The `MirrorEngine` needs access to `max_connections_per_repo`. Check how `config` is threaded — it may require passing the `MirrorConfig` to the engine or accessing it through the download coordinator's config reference.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that verify `rate_limiter.acquire()` is called before HTTP requests. Run these tests on the UNFIXED code to observe failures (acquire never called because rate limiter is never instantiated).

**Test Cases**:
1. **Rate Limiter Not Instantiated**: Assert that `DownloadCoordinator._rate_limiter` is not None after `start()` (will fail on unfixed code)
2. **Acquire Not Called Before GET**: Mock the rate limiter, call `_attempt_download()`, assert `acquire()` was called (will fail on unfixed code — no rate limiter exists)
3. **Acquire Not Called Before HEAD**: Mock the rate limiter, call `check_conditional()`, assert `acquire()` was called (will fail on unfixed code)
4. **Hardcoded Concurrency**: Inspect the `max_concurrent` argument passed to `download_batch()` from engine, assert it equals `config.max_connections_per_repo` (will fail — gets 20 regardless of config)

**Expected Counterexamples**:
- `DownloadCoordinator` has no `_rate_limiter` attribute after `start()`
- HTTP requests proceed without any acquire call
- `download_batch` receives `20` even when `max_connections_per_repo=5`

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  coordinator = DownloadCoordinator(config=input.config)
  await coordinator.start()
  ASSERT coordinator._rate_limiter IS NOT None
  ASSERT coordinator._rate_limiter._rate == input.config.rate_limit_rps
  ASSERT coordinator._rate_limiter._max_tokens == resolved_burst_size

  result := await coordinator._attempt_download(input)
  ASSERT rate_limiter.acquire() called exactly once before HTTP GET

  result := await coordinator.check_conditional(input.url)
  ASSERT rate_limiter.acquire() called exactly once before HTTP HEAD
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT download_file_original(input) == download_file_fixed(input)
  // Specifically: retry counts, error types, checksums, file writes
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain (various HTTP status codes, file sizes, checksums)
- It catches edge cases that manual unit tests might miss (e.g., boundary conditions in backoff calculation)
- It provides strong guarantees that behavior is unchanged for all non-rate-limiting code paths

**Test Plan**: Observe behavior on UNFIXED code first for download success/failure paths, then write property-based tests capturing that the addition of rate limiting does not alter those outcomes.

**Test Cases**:
1. **Retry Preservation**: For any 5xx response, verify the fixed code retries the same number of times with the same backoff pattern as the original
2. **4xx Immediate Failure Preservation**: For any 4xx response, verify the fixed code fails immediately without retry, same as the original
3. **Checksum Verification Preservation**: For any download with mismatched SHA256, verify the fixed code raises `ChecksumMismatchError` same as original
4. **Atomic Write Preservation**: For any successful download, verify `.part` → final rename still occurs

### Unit Tests

- Test `TokenBucketRateLimiter` instantiation with config values during `start()`
- Test `acquire()` is called before `session.get()` in `_attempt_download()`
- Test `acquire()` is called before `session.head()` in `check_conditional()`
- Test `cancel_waiters()` is called during `close()`
- Test burst size resolution: explicit `rate_limit_burst` vs. fallback to `max_connections_per_repo`
- Test engine passes `config.max_connections_per_repo` to `download_batch()`

### Property-Based Tests

- Generate random `MirrorConfig` values (rate_limit_rps in [1, 1000], burst in [1, 200] or None, max_connections_per_repo in [1, 100]) and verify rate limiter is instantiated with correct parameters
- Generate random sequences of download requests and verify acquire is called exactly once per HTTP request
- Generate random HTTP response scenarios (2xx, 4xx, 5xx, network errors) and verify retry/error behavior is identical between original and fixed code paths (preservation)

### Integration Tests

- Test full download flow with rate limiter: configure low RPS, issue multiple downloads, verify timing indicates throttling occurred
- Test `close()` cancels pending waiters: start downloads that will block on acquire, call close, verify no hanging tasks
- Test engine-to-coordinator integration: set `max_connections_per_repo=3`, verify semaphore in `download_batch` uses value `3`
