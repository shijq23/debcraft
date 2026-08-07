# Tasks

## Task 1: Write bug condition exploration property test
- [x] Create a property-based test file at `tests/test_download_batch_bug.py` that uses Hypothesis
- [x] Write a test that generates a batch of download tasks where at least one triggers HttpClientError
- [x] The test asserts that `download_batch()` returns a complete list of DownloadResult objects (same length as input) without raising ExceptionGroup
- [x] This test is expected to FAIL on the current unfixed code, confirming the bug exists
Requirements addressed: 1.1, 1.2, 2.1, 2.2

## Task 2: Implement the fix
- [x] Modify `_download_with_semaphore()` in `src/debcraft/infrastructure/mirror/download.py` to catch `HttpClientError`
- [x] Return a failed `DownloadResult(url=task.url, success=False, sha256_verified=False, bytes_transferred=0, error=str(exc))` on HttpClientError
- [x] Add a `logger.warning()` call for the caught client error with URL and status code
Requirements addressed: 2.1, 2.2, 2.3

## Task 3: Verify fix and add regression tests
- [x] Run the exploration test from Task 1 — it should now PASS
- [x] Add a property test verifying that multiple 4xx failures in a batch each produce independent failed results
- [x] Add a test verifying that direct `download_file()` calls still raise `HttpClientError`
- [x] Add a test verifying that 5xx/network errors in a batch still trigger retries before failing
- [x] Ensure all tests pass
Requirements addressed: 2.3, 3.1, 3.2, 3.3, 3.4
