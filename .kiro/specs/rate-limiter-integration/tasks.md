# Implementation Plan

## Overview

Wire the existing `TokenBucketRateLimiter` into the `DownloadCoordinator` download pipeline (instantiation in `start()`, `acquire()` before HTTP requests, cleanup in `close()`) and replace the hardcoded `_MAX_CONCURRENT_DOWNLOADS = 20` in `engine.py` with `config.max_connections_per_repo`. This fixes CDN 403 rate-limiting errors caused by unthrottled HTTP requests.

## Tasks

- [ ] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Rate Limiter Not Integrated Into Download Pipeline
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the rate limiter is never instantiated and acquire() is never called before HTTP requests
  - **Scoped PBT Approach**: Generate random MirrorConfig values (rate_limit_rps in [1, 1000], rate_limit_burst in [1, 200] or None, max_connections_per_repo in [1, 100]) and verify:
    1. After `DownloadCoordinator.start()`, `self._rate_limiter` is not None
    2. `self._rate_limiter._rate` equals `config.rate_limit_rps`
    3. `self._rate_limiter._max_tokens` equals resolved burst size (`config.rate_limit_burst or config.max_connections_per_repo`)
    4. When `_attempt_download()` is called, `rate_limiter.acquire()` is called exactly once before the HTTP GET request
    5. When `check_conditional()` is called, `rate_limiter.acquire()` is called exactly once before the HTTP HEAD request
  - Create test file at `tests/properties/infrastructure/test_rate_limiter_integration_bug_condition.py`
  - Use `hypothesis` with strategies for config values; use `unittest.mock.AsyncMock` to mock aiohttp session
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - proves the bug exists: no `_rate_limiter` attribute, acquire never called)
  - Document counterexamples found (e.g., "DownloadCoordinator has no _rate_limiter after start()", "HTTP GET issued without acquire()")
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 2.5_

- [ ] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Download Behavior Unchanged For Non-Rate-Limiting Paths
  - **IMPORTANT**: Follow observation-first methodology
  - Observe behavior on UNFIXED code for non-buggy inputs (download success/failure paths that don't involve rate limiting):
    - Observe: successful download with valid SHA256 and size produces `DownloadResult(success=True, sha256_verified=True)`
    - Observe: 4xx HTTP response fails immediately without retry, produces `DownloadResult(success=False)`
    - Observe: 5xx HTTP response retries up to `_MAX_ATTEMPTS` (3) times with exponential backoff
    - Observe: checksum mismatch raises `ChecksumMismatchError` and retries
    - Observe: size mismatch raises `SizeMismatchError` and retries
    - Observe: network error retries with backoff
    - Observe: `download_batch()` API signature and behavior unchanged
  - Write property-based tests capturing observed behavior patterns:
    - For all HTTP 4xx status codes (400-499): `download_file` fails immediately without retry (retry_count=0 in result)
    - For all HTTP 5xx status codes (500-599): `download_file` retries exactly `_MAX_ATTEMPTS - 1` times
    - For all valid downloads: checksum and size verification still occur, atomic `.part` → final rename happens
    - For all backoff computations: `_compute_backoff_delay(attempt)` produces `min(1 * 2^attempt, 30) + jitter`
  - Create test file at `tests/properties/infrastructure/test_rate_limiter_integration_preservation.py`
  - Use `hypothesis` strategies to generate random HTTP status codes, file sizes, checksums
  - Verify tests PASS on UNFIXED code (confirms baseline behavior to preserve)
  - **EXPECTED OUTCOME**: Tests PASS (confirms existing download behavior is captured correctly)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [ ] 3. Fix for rate limiter not integrated into download pipeline

  - [ ] 3.1 Wire TokenBucketRateLimiter into DownloadCoordinator
    - Add import: `from debcraft.infrastructure.mirror.rate_limiter import TokenBucketRateLimiter`
    - Add instance attribute in `__init__`: `self._rate_limiter: TokenBucketRateLimiter | None = None`
    - In `start()`: after creating session, resolve burst size as `self._config.rate_limit_burst or self._config.max_connections_per_repo`, then instantiate `self._rate_limiter = TokenBucketRateLimiter(rate=self._config.rate_limit_rps, burst_size=burst_size)`
    - In `_attempt_download()`: add `await self._rate_limiter.acquire()` before `self._session.get(url, ...)` call
    - In `check_conditional()`: add `await self._rate_limiter.acquire()` before `self._session.head(url, ...)` call
    - In `close()`: before closing session, add `if self._rate_limiter is not None: self._rate_limiter.cancel_waiters()` then set `self._rate_limiter = None`
    - _Bug_Condition: isBugCondition(input) where input.coordinator.rate_limiter IS None OR acquire() NOT called before HTTP request_
    - _Expected_Behavior: rate_limiter instantiated in start(), acquire() called before every HTTP GET and HEAD, cancel_waiters() called in close()_
    - _Preservation: Retry logic, checksum verification, atomic writes, connection pooling, session lifecycle unchanged_
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [ ] 3.2 Replace hardcoded _MAX_CONCURRENT_DOWNLOADS in engine.py
    - Remove or comment out `_MAX_CONCURRENT_DOWNLOADS = 20` constant
    - Change `max_concurrent=_MAX_CONCURRENT_DOWNLOADS` to `max_concurrent=config.max_connections_per_repo` in the `_sync_artifacts` method
    - Access the config value through the appropriate path (the `RepositoryConfig` or `MirrorConfig` accessible in the engine context)
    - _Bug_Condition: engine.max_concurrent != config.max_connections_per_repo (hardcoded 20 instead of config value)_
    - _Expected_Behavior: batch_concurrency == config.max_connections_per_repo_
    - _Requirements: 2.4_

  - [ ] 3.3 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Rate Limiter Integrated Into Download Pipeline
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior (rate limiter instantiated, acquire called before HTTP)
    - When this test passes, it confirms the expected behavior is satisfied
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed — rate limiter is now instantiated and acquire is called)
    - _Requirements: 2.1, 2.2, 2.3, 2.5_

  - [ ] 3.4 Verify preservation tests still pass
    - **Property 2: Preservation** - Download Behavior Unchanged For Non-Rate-Limiting Paths
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions — retry logic, checksum verification, atomic writes all unchanged)
    - Confirm all tests still pass after fix (no regressions)

- [ ] 4. Checkpoint - Ensure all tests pass
  - Run full test suite: `pytest tests/properties/infrastructure/test_rate_limiter_integration_bug_condition.py tests/properties/infrastructure/test_rate_limiter_integration_preservation.py -v`
  - Ensure all property-based tests pass
  - Run existing rate limiter tests to ensure no regressions: `pytest tests/properties/infrastructure/test_rate_limiter_properties.py tests/properties/infrastructure/test_rate_limit_config_properties.py -v`
  - Ensure no other tests in the project are broken by the changes
  - Ask the user if questions arise

## Task Dependency Graph

```json
{
  "waves": [
    ["1", "2"],
    ["3.1", "3.2"],
    ["3.3"],
    ["3.4"],
    ["4"]
  ]
}
```

## Notes

- Tests use `hypothesis` property-based testing library (already a project dependency)
- Test files go in `tests/properties/infrastructure/` following existing conventions
- The `TokenBucketRateLimiter` class is already fully implemented in `rate_limiter.py` — this fix only wires it in
- The `MirrorConfig` dataclass already has `rate_limit_rps`, `rate_limit_burst`, and `max_connections_per_repo` fields
- Burst size resolution: `config.rate_limit_burst or config.max_connections_per_repo` (None means use max_connections_per_repo)
