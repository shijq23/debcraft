"""Release staging helpers for the mirror engine.

Extracted from engine.py to reduce module size. These functions handle
downloading, validating, and persisting Release/InRelease files during
mirror synchronization.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from debcraft.infrastructure.mirror.errors import DownloadError, ReleaseParseError
from debcraft.infrastructure.models.mirror import RepositoryFile, RepositoryFileState

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from pathlib import Path

    from debcraft.domain.mirror.config import RepositoryConfig
    from debcraft.domain.mirror.release_parser import ReleaseMetadata, ReleaseParser
    from debcraft.infrastructure.mirror.download import DownloadCoordinator
    from debcraft.platform.contracts.logging import Logger
    from debcraft.platform.contracts.persistence import DatabaseProvider


async def check_release_unchanged(
    db_provider: DatabaseProvider,
    download_coordinator: DownloadCoordinator,
    *,
    url: str,
    cached_path: Path,
) -> bool:
    """Check if a cached Release file is still current via conditional request.

    Args:
        db_provider: Provides database sessions for mirror.db.
        download_coordinator: HTTP download coordinator for conditional requests.
        url: URL of the Release/InRelease file.
        cached_path: Local path to the cached copy.

    Returns:
        True if the remote file is unchanged (304 response), False otherwise.
    """
    if not cached_path.exists():
        return False

    stored_etag: str | None = None
    stored_last_modified: str | None = None
    session = await db_provider.get_session("mirror")
    try:
        stmt = select(RepositoryFile).where(RepositoryFile.url == url)
        db_result = await session.execute(stmt)
        existing = db_result.scalar_one_or_none()
        if existing is not None:
            stored_etag = existing.etag
            stored_last_modified = existing.last_modified
    finally:
        await session.close()

    return await download_coordinator.check_conditional(
        url,
        etag=stored_etag,
        last_modified=stored_last_modified,
    )


async def download_release_file(
    download_coordinator: DownloadCoordinator,
    *,
    url: str,
    dest_path: Path,
) -> tuple[str, str, dict[str, str] | None] | None:
    """Download a Release/InRelease file and return its content.

    Creates parent directories as needed. Returns None if the
    download fails (e.g., 404 for InRelease).

    Args:
        download_coordinator: HTTP download coordinator.
        url: URL of the Release file to download.
        dest_path: Local destination path.

    Returns:
        Tuple of (file_content, url, response_headers) on success,
        None on failure.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # For Release files, we download without strict size/hash validation
    # since the Release file IS the source of truth for hashes.
    # Download with relaxed validation for the Release file itself.
    try:
        result = await download_coordinator.download_file(
            url=url,
            dest_path=dest_path,
            expected_sha256="",  # Will be overridden
            expected_size=0,
            timeout=60,
        )
    except (DownloadError, OSError, RuntimeError):
        # If download_file raises (e.g., 404 HttpClientError), treat as failure
        return None

    # If the coordinator reports failure (e.g., 404), return None
    if not result.success:
        return None

    # Read the downloaded content
    try:
        content = dest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    return (content, url, result.response_headers)


async def parse_and_store_release(
    release_parser: ReleaseParser,
    logger: Logger,
    session_id: str,
    *,
    download_result: tuple[str, str, dict[str, str] | None],
    dest_path: Path,
    upsert_fn: Callable[..., Coroutine[Any, Any, None]],
) -> tuple[ReleaseMetadata | None, int, int]:
    """Parse a downloaded Release file and persist it as a RepositoryFile.

    Args:
        release_parser: Parser for Release file content.
        logger: Structured logger for error reporting.
        session_id: Current sync session identifier for log context.
        download_result: Tuple of (content, url, response_headers).
        dest_path: Local filesystem path where the file was downloaded.
        upsert_fn: Async callable to persist the RepositoryFile entity.

    Returns:
        Tuple of (parsed ReleaseMetadata or None, files_downloaded_delta,
        files_failed_delta).
    """
    content, file_url, response_headers = download_result
    try:
        release = release_parser.parse(content, url=file_url)
    except ReleaseParseError as exc:
        logger.error(
            "Failed to parse Release file",
            url=file_url,
            error=str(exc),
            session_id=session_id,
        )
        return (None, 0, 1)

    etag = None
    last_modified = None
    if response_headers:
        etag = response_headers.get("ETag")
        last_modified = response_headers.get("Last-Modified")

    release_content_bytes = content.encode()
    release_sha256 = hashlib.sha256(release_content_bytes).hexdigest()
    await upsert_fn(
        url=file_url,
        sha256=release_sha256,
        size_bytes=len(release_content_bytes),
        state=RepositoryFileState.VERIFIED,
        local_path=str(dest_path),
        etag=etag,
        last_modified=last_modified,
    )

    logger.debug(
        "Release file downloaded and verified",
        url=file_url,
        session_id=session_id,
        state="VERIFIED",
    )

    return (release, 1, 0)


async def stage_release(
    db_provider: DatabaseProvider,
    download_coordinator: DownloadCoordinator,
    release_parser: ReleaseParser,
    logger: Logger,
    session_id: str,
    *,
    config: RepositoryConfig,
    suite: str,
    mirror_root: Path,
    upsert_fn: Callable[..., Coroutine[Any, Any, None]],
) -> tuple[ReleaseMetadata | None, int, int]:
    """Download and parse Release file for a suite.

    Attempts to download InRelease first, falling back to Release
    on HTTP 404. Uses conditional requests if a cached version exists.

    Args:
        db_provider: Provides database sessions for mirror.db.
        download_coordinator: HTTP download coordinator.
        release_parser: Parser for Release file content.
        logger: Structured logger for error reporting.
        session_id: Current sync session identifier for log context.
        config: Repository configuration.
        suite: The distribution suite to download Release for.
        mirror_root: Local mirror root path.
        upsert_fn: Async callable to persist RepositoryFile entities.

    Returns:
        Tuple of (parsed ReleaseMetadata or None, files_downloaded_delta,
        files_failed_delta).
    """
    base_url = config.base_url.rstrip("/")
    inrelease_url = f"{base_url}/dists/{suite}/InRelease"
    release_url = f"{base_url}/dists/{suite}/Release"

    # Check conditional request for cached InRelease
    if await check_release_unchanged(
        db_provider,
        download_coordinator,
        url=inrelease_url,
        cached_path=mirror_root / "dists" / suite / "InRelease",
    ):
        logger.info(
            "Suite is up-to-date (conditional request)",
            suite=suite,
            session_id=session_id,
        )
        return (None, 0, 0)

    # Try InRelease first, fall back to Release
    dest_path = mirror_root / "dists" / suite / "InRelease"
    result = await download_release_file(
        download_coordinator,
        url=inrelease_url,
        dest_path=dest_path,
    )

    if result is None:
        dest_path = mirror_root / "dists" / suite / "Release"
        result = await download_release_file(
            download_coordinator,
            url=release_url,
            dest_path=dest_path,
        )

    if result is None:
        logger.error(
            "Failed to download Release file",
            suite=suite,
            repository=config.name,
            session_id=session_id,
        )
        return (None, 0, 1)

    return await parse_and_store_release(
        release_parser,
        logger,
        session_id,
        download_result=result,
        dest_path=dest_path,
        upsert_fn=upsert_fn,
    )
