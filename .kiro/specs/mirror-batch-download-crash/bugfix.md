# Bugfix Requirements Document

## Introduction

When running `uv run debcraft mirror sync`, the command crashes at 25% progress with an unhelpful "Unexpected error during sync: unhandled errors in a TaskGroup (1 sub-exception)" message. The root cause is that `download_batch()` in `download.py` uses `asyncio.TaskGroup` for concurrent downloads, and when any single download encounters an HTTP 4xx response, the `HttpClientError` exception propagates out of the TaskGroup task. Python wraps this in an `ExceptionGroup`, cancels all other concurrent tasks, and the entire batch aborts. The CLI's generic `except Exception` handler then displays the cryptic error message. A single unavailable file (e.g., 404) should not crash the entire sync operation.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN `download_batch()` is called with a list of download tasks AND any single task encounters an HTTP 4xx response (e.g., 404 Not Found) THEN the system raises an `ExceptionGroup` containing the `HttpClientError`, aborting the entire batch and canceling all other in-progress downloads

1.2 WHEN a batch download is in progress AND one download receives a 4xx HTTP response THEN the system cancels all other concurrent downloads in the TaskGroup, even those that would otherwise succeed

1.3 WHEN the `ExceptionGroup` propagates to the CLI layer THEN the system displays "Unexpected error during sync: unhandled errors in a TaskGroup (1 sub-exception)" instead of a structured error report

### Expected Behavior (Correct)

2.1 WHEN `download_batch()` is called with a list of download tasks AND any single task encounters an HTTP 4xx response THEN the system SHALL catch the `HttpClientError` within the task, record a failed `DownloadResult` for that file (with `success=False` and the error message), and allow all other downloads in the batch to continue

2.2 WHEN a batch download completes with some 4xx failures THEN the system SHALL return a complete list of `DownloadResult` objects with the same length as the input task list, where failed downloads have `success=False`, `sha256_verified=False`, `bytes_transferred=0`, and an `error` string describing the HTTP status

2.3 WHEN multiple downloads in a batch encounter 4xx errors THEN the system SHALL record each failure independently and still complete all remaining downloads that do not error

### Unchanged Behavior (Regression Prevention)

3.1 WHEN all downloads in a batch succeed (HTTP 200 with valid SHA256 and size) THEN the system SHALL CONTINUE TO return a list of successful `DownloadResult` objects with correct `bytes_transferred` and `sha256_verified=True`

3.2 WHEN a download encounters a retriable error (5xx, network error, checksum mismatch, size mismatch) THEN the system SHALL CONTINUE TO retry with exponential backoff up to 3 attempts before recording a failed `DownloadResult`

3.3 WHEN `download_file()` is called directly (not via `download_batch()`) AND encounters a 4xx response THEN the system SHALL CONTINUE TO raise `HttpClientError` as before (the exception handling is only added at the batch level)

3.4 WHEN a batch download completes THEN the system SHALL CONTINUE TO log a summary with succeeded/failed counts and total bytes transferred
