"""Download coordinator for repository mirroring.

Manages concurrent HTTP downloads with retry, exponential backoff,
SHA256 verification, and atomic file writes using .part files.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp

from debcraft.domain.mirror.values import DownloadResult
from debcraft.infrastructure.mirror.errors import (
    ChecksumMismatchError,
    HttpClientError,
    HttpServerError,
    NetworkError,
    SizeMismatchError,
)
from debcraft.infrastructure.mirror.rate_limiter import TokenBucketRateLimiter

if TYPE_CHECKING:
    from debcraft.domain.mirror.config import MirrorConfig
    from debcraft.platform.contracts.storage import StorageEngine

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 64 * 1024  # 64 KiB
_MAX_ATTEMPTS = 3
_BASE_BACKOFF = 1.0  # seconds
_MAX_BACKOFF = 30.0  # seconds
_JITTER_FACTOR = 0.25


@dataclass
class DownloadTask:
    """A single file download task for batch operations."""

    url: str
    dest_path: Path
    expected_sha256: str
    expected_size: int


class DownloadCoordinator:
    """Manages concurrent HTTP downloads with retry and backoff."""

    def __init__(
        self,
        storage_engine: StorageEngine,
        config: MirrorConfig,
    ) -> None:
        """Initialize the download coordinator.

        Args:
            storage_engine: Storage engine for path resolution.
            config: Mirror configuration with connection limits and timeout.
        """
        self._storage_engine = storage_engine
        self._config = config
        self._session: aiohttp.ClientSession | None = None
        self._connector: aiohttp.TCPConnector | None = None
        self._rate_limiter: TokenBucketRateLimiter | None = None

    async def start(self) -> None:
        """Initialize aiohttp session with connection pooling."""
        self._connector = aiohttp.TCPConnector(
            limit_per_host=self._config.max_connections_per_repo,
            limit=self._config.max_total_connections,
            ttl_dns_cache=300,
        )
        self._session = aiohttp.ClientSession(connector=self._connector)
        burst_size = self._config.rate_limit_burst or self._config.max_connections_per_repo
        self._rate_limiter = TokenBucketRateLimiter(rate=self._config.rate_limit_rps, burst_size=burst_size)

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._rate_limiter is not None:
            self._rate_limiter.cancel_waiters()
            self._rate_limiter = None
        if self._session is not None:
            await self._session.close()
            self._session = None
        if self._connector is not None:
            await self._connector.close()
            self._connector = None

    async def download_file(
        self,
        url: str,
        dest_path: Path,
        expected_sha256: str,
        expected_size: int,
        timeout: int | None = None,
    ) -> DownloadResult:
        """Download a single file with SHA256 verification.

        Writes to .part file, verifies hash and size, atomically renames
        on success. Retries up to 3 times with exponential backoff for
        5xx/network errors.

        Args:
            url: URL to download from.
            dest_path: Final destination path for the file.
            expected_sha256: Expected SHA256 hex digest.
            expected_size: Expected file size in bytes.
            timeout: Per-file download timeout in seconds. Defaults to
                config.download_timeout.

        Returns:
            DownloadResult indicating success or failure.
        """
        if self._session is None:
            msg = "Session not started. Call start() first."
            raise RuntimeError(msg)

        effective_timeout = timeout or self._config.download_timeout
        last_error: Exception | None = None

        logger.debug(
            "Starting download",
            extra={"url": url, "dest_path": str(dest_path)},
        )

        for attempt in range(_MAX_ATTEMPTS):
            try:
                result = await self._attempt_download(
                    url=url,
                    dest_path=dest_path,
                    expected_sha256=expected_sha256,
                    expected_size=expected_size,
                    timeout=effective_timeout,
                    attempt=attempt,
                )
                logger.debug(
                    "Download completed successfully",
                    extra={
                        "url": url,
                        "bytes_transferred": result.bytes_transferred,
                    },
                )
                return result
            except HttpClientError:
                # 4xx errors are not retriable
                raise
            except (
                HttpServerError,
                NetworkError,
                ChecksumMismatchError,
                SizeMismatchError,
            ) as exc:
                last_error = exc
                if attempt < _MAX_ATTEMPTS - 1:
                    delay = _compute_backoff_delay(attempt)
                    logger.warning(
                        "Download failed, retrying",
                        extra={
                            "url": url,
                            "attempt": attempt + 1,
                            "max_attempts": _MAX_ATTEMPTS,
                            "backoff_seconds": round(delay, 2),
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        },
                    )
                    await asyncio.sleep(delay)

        # All retries exhausted
        error_msg = str(last_error) if last_error else "Unknown error"
        status_code = getattr(last_error, "status_code", None)

        logger.error(
            "Download failed after all retries",
            extra={
                "url": url,
                "attempts": _MAX_ATTEMPTS,
                "error_type": type(last_error).__name__ if last_error else "Unknown",
                "error": error_msg,
                "status_code": status_code,
            },
        )
        return DownloadResult(
            url=url,
            success=False,
            sha256_verified=False,
            bytes_transferred=0,
            error=error_msg,
            retry_count=_MAX_ATTEMPTS - 1,
            status_code=status_code,
        )

    async def download_batch(
        self,
        tasks: list[DownloadTask],
        max_concurrent: int,
    ) -> list[DownloadResult]:
        """Download multiple files concurrently using asyncio.TaskGroup.

        Args:
            tasks: List of download tasks to execute.
            max_concurrent: Maximum number of concurrent downloads.

        Returns:
            List of DownloadResult in the same order as the input tasks.
        """
        logger.debug(
            "Starting batch download",
            extra={"total_files": len(tasks), "max_concurrent": max_concurrent},
        )
        results: list[DownloadResult] = []
        semaphore = asyncio.Semaphore(max_concurrent)

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

        async with asyncio.TaskGroup() as tg:
            task_handles = [tg.create_task(_download_with_semaphore(task)) for task in tasks]

        results = [handle.result() for handle in task_handles]

        # Emit batch summary
        succeeded = sum(1 for r in results if r.success)
        failed = sum(1 for r in results if not r.success)
        total_bytes = sum(r.bytes_transferred for r in results)
        logger.info(
            "Batch download complete",
            extra={
                "total_files": len(tasks),
                "succeeded": succeeded,
                "failed": failed,
                "total_bytes": total_bytes,
            },
        )
        return results

    async def check_conditional(
        self,
        url: str,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> bool:
        """Send conditional request, return True if content is unchanged (304).

        Args:
            url: URL to check.
            etag: ETag value for If-None-Match header.
            last_modified: Last-Modified value for If-Modified-Since header.

        Returns:
            True if the server responds with 304 Not Modified.
        """
        if self._session is None:
            msg = "Session not started. Call start() first."
            raise RuntimeError(msg)

        headers: dict[str, str] = {}
        if etag is not None:
            headers["If-None-Match"] = etag
        if last_modified is not None:
            headers["If-Modified-Since"] = last_modified

        try:
            assert self._rate_limiter is not None  # noqa: S101
            await self._rate_limiter.acquire()
            async with self._session.head(url, headers=headers) as response:
                return response.status == 304
        except aiohttp.ClientError:
            return False

    async def _attempt_download(
        self,
        url: str,
        dest_path: Path,
        expected_sha256: str,
        expected_size: int,
        timeout: int,
        attempt: int,
    ) -> DownloadResult:
        """Execute a single download attempt.

        Args:
            url: URL to download.
            dest_path: Final destination path.
            expected_sha256: Expected SHA256 hex digest.
            expected_size: Expected file size in bytes.
            timeout: Download timeout in seconds.
            attempt: Current attempt number (0-indexed).

        Returns:
            DownloadResult on success.

        Raises:
            HttpClientError: On 4xx responses.
            HttpServerError: On 5xx responses.
            NetworkError: On connection failures.
            ChecksumMismatchError: On SHA256 mismatch.
            SizeMismatchError: On size mismatch.
        """
        assert self._session is not None  # noqa: S101
        assert self._rate_limiter is not None  # noqa: S101

        part_path = Path(str(dest_path) + ".part")

        # Create parent directories if needed
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Delete existing .part file
        if part_path.exists():
            part_path.unlink()

        client_timeout = aiohttp.ClientTimeout(total=timeout)

        await self._rate_limiter.acquire()

        response_headers: dict[str, str] = {}

        try:
            async with self._session.get(url, timeout=client_timeout) as response:
                status = response.status

                logger.debug(
                    "HTTP response received",
                    extra={"url": url, "status_code": status, "attempt": attempt},
                )

                if 400 <= status < 500:
                    raise HttpClientError(
                        url=url,
                        status_code=status,
                        retry_count=attempt,
                    )

                if status >= 500:
                    raise HttpServerError(
                        url=url,
                        status_code=status,
                        retry_count=attempt,
                    )

                # Capture ETag and Last-Modified headers
                if "ETag" in response.headers:
                    response_headers["ETag"] = response.headers["ETag"]
                if "Last-Modified" in response.headers:
                    response_headers["Last-Modified"] = response.headers["Last-Modified"]

                # Stream to .part file with SHA256 computation
                sha256_hash = hashlib.sha256()
                bytes_written = 0

                with part_path.open("wb") as f:
                    async for chunk in response.content.iter_chunked(_CHUNK_SIZE):
                        f.write(chunk)
                        sha256_hash.update(chunk)
                        bytes_written += len(chunk)

        except aiohttp.ClientError as exc:
            # Clean up .part file on network error
            if part_path.exists():
                part_path.unlink()
            raise NetworkError(
                url=url,
                message=str(exc),
                retry_count=attempt,
                cause=exc,
            ) from exc
        except (HttpClientError, HttpServerError):
            # Clean up .part file on HTTP errors
            if part_path.exists():
                part_path.unlink()
            raise

        # Verify size
        if expected_size and bytes_written != expected_size:
            logger.warning(
                "Size mismatch detected",
                extra={
                    "url": url,
                    "expected_size": expected_size,
                    "actual_size": bytes_written,
                    "attempt": attempt,
                },
            )
            part_path.unlink()
            raise SizeMismatchError(
                url=url,
                expected_bytes=expected_size,
                actual_bytes=bytes_written,
                retry_count=attempt,
            )

        # Verify SHA256
        actual_sha256 = sha256_hash.hexdigest()
        if expected_sha256 and actual_sha256 != expected_sha256:
            logger.warning(
                "SHA256 checksum mismatch",
                extra={
                    "url": url,
                    "expected_sha256": expected_sha256,
                    "actual_sha256": actual_sha256,
                    "attempt": attempt,
                },
            )
            part_path.unlink()
            raise ChecksumMismatchError(
                url=url,
                expected=expected_sha256,
                actual=actual_sha256,
                retry_count=attempt,
            )

        # Atomic rename
        os.replace(part_path, dest_path)

        # Set file mode to 0o644 (rw-r--r--) for apt compatibility
        os.chmod(dest_path, 0o644)

        logger.debug(
            "SHA256 verification passed, file committed",
            extra={"url": url, "dest_path": str(dest_path), "sha256": actual_sha256},
        )

        return DownloadResult(
            url=url,
            success=True,
            sha256_verified=True,
            bytes_transferred=bytes_written,
            retry_count=attempt,
            response_headers=response_headers if response_headers else None,
        )


def _compute_backoff_delay(attempt: int) -> float:
    """Compute exponential backoff delay with jitter.

    delay = min(1 * 2^attempt, 30) + random.uniform(0, delay * 0.25)

    Args:
        attempt: Current attempt number (0-indexed).

    Returns:
        Delay in seconds before the next retry.
    """
    delay = min(_BASE_BACKOFF * (2**attempt), _MAX_BACKOFF)
    jitter = random.uniform(0, delay * _JITTER_FACTOR)  # noqa: S311
    return delay + jitter
