# Bugfix Requirements Document

## Introduction

This spec addresses two bugs in debcraft's infrastructure layer:

1. **SQLAlchemy mapper failure**: The models package `__init__.py` is empty, so importing a single model (e.g., `RepositorySnapshot`) does not trigger loading of related model modules (e.g., `scan.py`). SQLAlchemy's mapper cannot resolve string-based relationship references like `"ScanSession"`, causing `InvalidRequestError` at runtime.

2. **Artifact download error context**: When all download retries are exhausted, the error log at the engine level lacks HTTP status code, response details, and total elapsed time, making production debugging difficult.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN `publisher.py` imports `RepositorySnapshot` from `debcraft.infrastructure.models.metadata` without importing `scan.py` THEN the system raises `InvalidRequestError: expression 'ScanSession' failed to locate a name` when SQLAlchemy attempts to configure the mapper for `RepositorySnapshot.scan_sessions`

1.2 WHEN importing any model via the `debcraft.infrastructure.models` package THEN the system does not register all interrelated model classes because `__init__.py` contains only a docstring and no module imports

1.3 WHEN all download retries are exhausted for an artifact THEN the system logs "Artifact download failed" in the engine with only `url` and `error` (a plain stringified exception) without HTTP status code, total attempts, or elapsed time context

1.4 WHEN a download fails due to an HTTP 5xx error after exhausting retries THEN the system's `DownloadResult.error` field contains only `str(last_error)` without the HTTP status code that was available in the exception object

### Expected Behavior (Correct)

2.1 WHEN `publisher.py` imports `RepositorySnapshot` from the models package THEN the system SHALL have all model modules (metadata, scan, mirror, cache) already registered with SQLAlchemy's mapper registry so that string-based relationship references resolve correctly

2.2 WHEN importing any model via `debcraft.infrastructure.models` THEN the system SHALL ensure all model modules are loaded by the package `__init__.py`, guaranteeing mapper completeness

2.3 WHEN all download retries are exhausted for an artifact THEN the system SHALL log an ERROR-level message in `download.py` that includes the URL, total attempts made, final error type, final error message, and HTTP status code (when available from the exception)

2.4 WHEN the engine logs "Artifact download failed" THEN the system SHALL include the HTTP status code (if available), retry count, and error type in addition to the URL and error message already present

2.5 WHEN an artifact download succeeds THEN the engine SHALL log a DEBUG-level message that includes the deb package name (derived from the file's relative path) so that successful downloads are traceable

### Unchanged Behavior (Regression Prevention)

3.1 WHEN all model modules are imported individually (e.g., `from debcraft.infrastructure.models.metadata import RepositorySnapshot`) THEN the system SHALL CONTINUE TO make those models available without import errors

3.2 WHEN `ScanSession` and `RepositorySnapshot` are both loaded THEN the system SHALL CONTINUE TO correctly resolve the bidirectional relationship between them

3.3 WHEN a download succeeds on the first attempt THEN the system SHALL CONTINUE TO return a successful `DownloadResult` with `sha256_verified=True` and correct `bytes_transferred`

3.4 WHEN a download fails with a 4xx client error THEN the system SHALL CONTINUE TO immediately fail without retrying and return a `DownloadResult` with `success=False`

3.5 WHEN a download fails but succeeds on a subsequent retry THEN the system SHALL CONTINUE TO return a successful `DownloadResult` without logging any final ERROR-level message
