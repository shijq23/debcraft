# Implementation Plan: Client-Side Rate Limiting

## Overview

Implement a token bucket rate limiter for the debcraft mirror download client that proactively throttles outgoing HTTP requests, reclassifies CDN 403 responses as retriable rate-limit signals with aggressive backoff, and exposes configuration via `mirrors.toml`. The implementation adds new classes (`TokenBucketRateLimiter`, `HttpRateLimitError`, `RateLimitTimeoutError`), modifies the existing `DownloadCoordinator` retry logic, extends `MirrorConfig` and `ConfigReader`, and adds property-based tests using Hypothesis.

## Tasks

- [x] 1. Add rate-limit configuration fields and error classes
  - [x] 1.1 Add `rate_limit_rps` and `rate_limit_burst` fields to `MirrorConfig`
    - Add `rate_limit_rps: float = 50.0` field to the frozen dataclass
    - Add `rate_limit_burst: int | None = None` field (None defaults to `max_connections_per_repo` at runtime)
    - Update `validate_mirror_config` to check `1 <= rate_limit_rps <= 1000` and `1 <= rate_limit_burst <= 200`
    - Update docstring to document new fields
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [x] 1.2 Add `HttpRateLimitError` and `RateLimitTimeoutError` to errors module
    - Create `HttpRateLimitError(DownloadError)` with `status_code` attribute, defaulting to 403
    - Create `RateLimitTimeoutError(MirrorError)` with `timeout` attribute
    - Follow existing error class patterns (preserve cause, structured init)
    - _Requirements: 3.1, 1.3_

- [ ] 2. Implement TokenBucketRateLimiter
  - [x] 2.1 Create `src/debcraft/infrastructure/mirror/rate_limiter.py`
    - Implement `TokenBucketRateLimiter` class with `__init__(rate: float, burst_size: int)`
    - Initialize `_tokens` to `burst_size`, `_max_tokens` to `burst_size`, `_rate` to `rate`
    - Use `time.monotonic()` for `_last_replenish` timestamp
    - Use `asyncio.Lock` for serialization, `list[asyncio.Future]` for `_waiters`
    - _Requirements: 1.5, 1.6_

  - [x] 2.2 Implement `acquire` method with timeout
    - Replenish tokens based on elapsed time: `min(current + rate * elapsed, burst_size)`
    - If token available, decrement and return immediately
    - If no token available, sleep in a loop until token replenishes or 60s timeout expires
    - Raise `RateLimitTimeoutError` if timeout elapses without acquiring token
    - Track waiters in `_waiters` list for cancellation support
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x] 2.3 Implement `cancel_waiters` method
    - Iterate `_waiters` list and cancel each pending Future
    - Clear the waiters list after cancellation
    - _Requirements: 4.5_

  - [ ] 2.4 Write property test for token bucket replenishment (Property 1)
    - **Property 1: Token bucket replenishment computes correctly**
    - Test that for any rate, burst_size, initial tokens, and elapsed time, replenished count equals `min(current + rate * elapsed, burst_size)`
    - Test that initialization sets tokens to `burst_size`
    - **Validates: Requirements 1.5, 1.6**

  - [ ] 2.5 Write property test for acquire blocking and timeout (Property 2)
    - **Property 2: Token bucket acquire blocks when empty and times out**
    - Test that acquire blocks when tokens=0, returns after replenishment
    - Test that acquire raises `RateLimitTimeoutError` when wait exceeds timeout
    - Test that acquire returns within 1ms when tokens are available
    - **Validates: Requirements 1.2, 1.3, 1.4**

  - [ ] 2.6 Write property test for maximum request rate enforcement (Property 3)
    - **Property 3: Token bucket enforces maximum request rate**
    - Test that N > B requests take at least `(N - B) / R` seconds
    - Use controlled time simulation to verify throughput bound
    - **Validates: Requirements 1.1**

- [ ] 3. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 4. Extend ConfigReader to parse rate-limit settings
  - [x] 4.1 Update `ConfigReader._build_config` to parse rate-limit fields
    - Parse `rate_limit_rps` from `[settings]` as float, default 50.0
    - Parse `rate_limit_burst` from `[settings]` as int, default to `max_connections_per_repo`
    - Resolve `None` burst to `max_connections_per_repo` in `_build_config`
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 4.2 Add validation for rate-limit config values
    - Validate `rate_limit_rps` is numeric and in [1, 1000]; report error with accepted range
    - Validate `rate_limit_burst` is numeric and in [1, 200]; report error with accepted range
    - Reject non-numeric values with clear error message indicating expected type
    - _Requirements: 2.5, 2.6, 2.7_

  - [x] 4.3 Update `DEFAULT_CONFIG` to include rate-limit defaults
    - Set `rate_limit_rps=50.0` and `rate_limit_burst=None` (resolved to `max_connections_per_repo=20`)
    - _Requirements: 2.3, 2.4_

  - [ ] 4.4 Write property test for config parsing round-trip (Property 4)
    - **Property 4: Rate limit config parsing round-trip**
    - Test that any numeric rps in [1, 1000] and burst in [1, 200] written to TOML produce correct `MirrorConfig` values
    - Test that omitted `rate_limit_burst` defaults to `max_connections_per_repo`
    - **Validates: Requirements 2.1, 2.2, 2.4**

  - [ ] 4.5 Write property test for config validation rejection (Property 5)
    - **Property 5: Rate limit config validation rejects invalid values**
    - Test that rps < 1 or > 1000, burst < 1 or > 200, or non-numeric values all produce validation errors
    - **Validates: Requirements 2.5, 2.6, 2.7**

- [ ] 5. Integrate rate limiter and 403 retry into DownloadCoordinator
  - [ ] 5.1 Modify `DownloadCoordinator.__init__` to accept rate limiter
    - Add optional `rate_limiter: TokenBucketRateLimiter | None = None` parameter
    - Store as `self._rate_limiter`
    - _Requirements: 4.1_

  - [ ] 5.2 Add rate limiter acquire call in `_attempt_download`
    - Before issuing the HTTP request, call `await self._rate_limiter.acquire()` if rate limiter is set
    - This ensures both initial attempts and retries are throttled
    - _Requirements: 4.1, 4.3_

  - [ ] 5.3 Reclassify HTTP 403 as `HttpRateLimitError`
    - In `_attempt_download`, add `if status == 403: raise HttpRateLimitError(...)` before the generic 4xx check
    - Ensure .part file cleanup occurs for 403 responses (same pattern as other errors)
    - _Requirements: 3.1, 3.6_

  - [ ] 5.4 Update retry loop to handle `HttpRateLimitError` with extended backoff
    - Catch `HttpRateLimitError` in the retry loop (alongside `HttpServerError`, `NetworkError`)
    - Use parameterized backoff: base=5.0, max=60.0, jitter_factor=0.5 for 403
    - Keep existing backoff for 5xx/network: base=1.0, max=30.0, jitter_factor=0.25
    - Refactor `_compute_backoff_delay` to accept base, maximum, and jitter_factor parameters
    - _Requirements: 3.1, 3.2, 3.3, 5.1, 5.2, 5.3, 5.4_

  - [ ] 5.5 Update `close()` and `download_batch` for rate limiter lifecycle
    - In `close()`, call `self._rate_limiter.cancel_waiters()` if rate limiter exists
    - In `download_batch`, log effective rate-limit settings at DEBUG level at batch start
    - _Requirements: 4.4, 4.5_

  - [ ] 5.6 Write property test for 403 retry behavior (Property 6)
    - **Property 6: HTTP 403 is retriable with 3 total attempts**
    - Mock HTTP responses; verify 403 triggers exactly 3 requests, non-403 4xx triggers exactly 1
    - **Validates: Requirements 3.1, 3.3**

  - [ ] 5.7 Write property test for exhausted 403 retries (Property 7)
    - **Property 7: Exhausted 403 retries produce correct failure result**
    - Verify `DownloadResult` has `success=False`, `status_code=403`, non-empty error, `retry_count=2`
    - **Validates: Requirements 3.4**

  - [ ] 5.8 Write property test for successful 403 retry (Property 8)
    - **Property 8: Successful 403 retry reports correct attempt number**
    - Verify that after N failed 403s then success, result has `success=True`, `retry_count=N`
    - **Validates: Requirements 3.5**

  - [ ] 5.9 Write property test for .part file cleanup on 403 (Property 9)
    - **Property 9: 403 retry cleans up .part file before retrying**
    - Verify .part file is deleted before backoff wait on 403, no .part remains after all retries exhausted
    - **Validates: Requirements 3.6**

  - [ ] 5.10 Write property test for 403 backoff delay bounds (Property 10)
    - **Property 10: 403 backoff delay bounds**
    - For any attempt N, verify computed delay falls within `[min(5*2^N, 60), min(min(5*2^N, 60)*1.5, 60)]`
    - **Validates: Requirements 5.1, 5.2, 5.3**

  - [ ] 5.11 Write property test for rate limiter acquire call count (Property 11)
    - **Property 11: Rate limiter acquire is called before every HTTP request**
    - Verify acquire is called exactly M times for M total HTTP requests (including retries)
    - **Validates: Requirements 4.1, 4.3**

- [ ] 6. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The implementation language is Python, matching the existing codebase and design examples
- Test files go in `tests/properties/infrastructure/` following existing project structure
- Property tests use Hypothesis with `@settings(max_examples=200)` for pure functions, `@settings(max_examples=50, deadline=None)` for async tests

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["2.1", "4.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "4.2", "4.3"] },
    { "id": 3, "tasks": ["2.4", "2.5", "2.6", "4.4", "4.5"] },
    { "id": 4, "tasks": ["5.1"] },
    { "id": 5, "tasks": ["5.2", "5.3"] },
    { "id": 6, "tasks": ["5.4", "5.5"] },
    { "id": 7, "tasks": ["5.6", "5.7", "5.8", "5.9", "5.10", "5.11"] }
  ]
}
```
