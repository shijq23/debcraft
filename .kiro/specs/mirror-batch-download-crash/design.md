# Design: Mirror Batch Download Crash Fix

## Overview

A single HTTP 4xx response during `download_batch()` crashes the entire sync operation because `HttpClientError` propagates out of an `asyncio.TaskGroup` task, triggering Python's `ExceptionGroup` behavior that cancels all sibling tasks. The fix catches `HttpClientError` inside the TaskGroup task wrapper and converts it to a failed `DownloadResult`, allowing the batch to complete with partial failures instead of aborting.

## Change Details

### Modified File

**File:** `src/debcraft/infrastructure/mirror/download.py`
**Function:** `download_batch()` → inner `_download_with_semaphore()`

The `_download_with_semaphore` inner function currently delegates directly to `download_file()` without any exception handling:

```python
async def _download_with_semaphore(task: DownloadTask) -> DownloadResult:
    async with semaphore:
        return await self.download_file(
            url=task.url,
            dest_path=task.dest_path,
            expected_sha256=task.expected_sha256,
            expected_size=task.expected_size,
        )
```

The fix wraps the `download_file()` call with a try/except for `HttpClientError`, converting the exception into a failed `DownloadResult`:

```python
async def _download_with_semaphore(task: DownloadTask) -> DownloadResult:
    async with semaphore:
        try:
            return await self.download_file(
                url=task.url,
                dest_path=task.dest_path,
                expected_sha256=task.expected_sha256,
                expected_size=task.expected_size,
            )
        except HttpClientError as exc:
            logger.warning(
                "Download failed with client error",
                extra={
                    "url": task.url,
                    "status_code": exc.status_code,
                    "error": str(exc),
                },
            )
            return DownloadResult(
                url=task.url,
                success=False,
                sha256_verified=False,
                bytes_transferred=0,
                error=str(exc),
            )
```

The returned `DownloadResult` mirrors the same pattern used when retries are exhausted for server errors — `success=False`, `sha256_verified=False`, `bytes_transferred=0`, and an `error` string.

## Design Rationale

1. **Batch-level catch, not download_file-level** — `download_file()` continues to raise `HttpClientError` for direct callers (requirement 3.3). The exception is only caught at the TaskGroup boundary where it would otherwise propagate destructively.

2. **TaskGroup contract** — Python's `asyncio.TaskGroup` cancels all sibling tasks when any task raises an unhandled exception. Every task must either return a value or handle its own exceptions internally. Catching `HttpClientError` satisfies this contract.

3. **Consistent result type** — The fix uses the existing `DownloadResult(success=False, ...)` pattern already established for retry-exhausted failures, so downstream consumers (logging, progress reporting, result aggregation) require no changes.

4. **Minimal change surface** — Only the inner function body changes. No new classes, files, or dependencies are introduced.

## Testing Strategy

1. **Bug reproduction test** — Create a batch with one task that triggers a 404 and others that succeed. Verify the batch returns all results (not an ExceptionGroup), the 404 task has `success=False` with an error message, and remaining tasks have `success=True`.

2. **Multiple 4xx failures** — Verify that several failing tasks in a batch are each recorded independently and non-failing tasks still complete.

3. **Regression: direct download_file call** — Confirm that calling `download_file()` directly with a 4xx URL still raises `HttpClientError`.

4. **Regression: retry behavior** — Confirm that 5xx/network errors still trigger retries with backoff inside the batch.

5. **Regression: all-success batch** — Confirm a fully successful batch still returns correct results with `sha256_verified=True`.
