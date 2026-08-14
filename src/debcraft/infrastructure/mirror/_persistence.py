"""Persistence helpers for the mirror engine.

Extracted from engine.py to reduce module size. These functions manage
RepositoryFile entity lifecycle: creation, update, state transitions,
and failure marking with retry logic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from debcraft.infrastructure.models.mirror import RepositoryFile, RepositoryFileState

if TYPE_CHECKING:
    from debcraft.domain.mirror.config import RepositoryConfig
    from debcraft.domain.mirror.values import FileEntry
    from debcraft.platform.contracts.logging import Logger
    from debcraft.platform.contracts.persistence import DatabaseProvider


_BATCH_SIZE = 500
_MAX_RETRIES = 3


async def upsert_repository_file(
    db_provider: DatabaseProvider,
    logger: Logger,
    session_id: str,
    *,
    url: str,
    sha256: str,
    size_bytes: int,
    state: RepositoryFileState,
    local_path: str | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
) -> None:
    """Create or update a RepositoryFile entity keyed by URL.

    If a RepositoryFile with the given URL exists, updates its fields.
    Otherwise creates a new entity with the specified state.

    Args:
        db_provider: Provides database sessions for mirror.db.
        logger: Structured logger for error reporting.
        session_id: Current sync session identifier for log context.
        url: Unique URL identifying the file.
        sha256: SHA256 checksum of the file.
        size_bytes: File size in bytes.
        state: Target lifecycle state.
        local_path: Local filesystem path (set on VERIFIED).
        etag: ETag header from the HTTP response.
        last_modified: Last-Modified header from the HTTP response.
    """
    session = await db_provider.get_session("mirror")
    try:
        stmt = select(RepositoryFile).where(RepositoryFile.url == url)
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing is not None:
            existing.sha256 = sha256
            existing.size_bytes = size_bytes
            existing.state = state
            existing.updated_at = datetime.now(UTC)
            if local_path is not None:
                existing.local_path = local_path
            if etag is not None:
                existing.etag = etag
            if last_modified is not None:
                existing.last_modified = last_modified
        else:
            entity = RepositoryFile(
                url=url,
                sha256=sha256,
                size_bytes=size_bytes,
                state=state,
                retry_count=0,
                local_path=local_path,
                etag=etag,
                last_modified=last_modified,
            )
            session.add(entity)

        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.error(
            "Failed to upsert RepositoryFile",
            url=url,
            session_id=session_id,
        )
    finally:
        await session.close()


async def batch_create_repository_files(
    db_provider: DatabaseProvider,
    logger: Logger,
    session_id: str,
    *,
    config: RepositoryConfig,
    entries: list[FileEntry],
    state: RepositoryFileState,
) -> None:
    """Create RepositoryFile entities in batches of ≤500.

    Creates or updates entities for each file entry, committing
    in batches to bound memory usage.

    Args:
        db_provider: Provides database sessions for mirror.db.
        logger: Structured logger for error reporting.
        session_id: Current sync session identifier for log context.
        config: Repository configuration for URL construction.
        entries: File entries to create entities for.
        state: Initial state for created entities.
    """
    base_url = config.base_url.rstrip("/")
    session = await db_provider.get_session("mirror")
    try:
        batch_count = 0
        for entry in entries:
            url = f"{base_url}/{entry.relative_path}"
            stmt = select(RepositoryFile).where(RepositoryFile.url == url)
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing is not None:
                existing.sha256 = entry.sha256
                existing.size_bytes = entry.size_bytes
                existing.state = state
                existing.updated_at = datetime.now(UTC)
            else:
                entity = RepositoryFile(
                    url=url,
                    sha256=entry.sha256,
                    size_bytes=entry.size_bytes,
                    state=state,
                    retry_count=0,
                )
                session.add(entity)

            batch_count += 1
            if batch_count >= _BATCH_SIZE:
                await session.commit()
                batch_count = 0

        # Commit remaining
        if batch_count > 0:
            await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.error(
            "Failed to batch create RepositoryFiles",
            session_id=session_id,
        )
    finally:
        await session.close()


async def batch_update_state(
    db_provider: DatabaseProvider,
    logger: Logger,
    session_id: str,
    *,
    succeeded: list[tuple[str, str, int]],
    state: RepositoryFileState,
) -> None:
    """Update RepositoryFile entities to a new state in batches.

    Commits in batches of ≤500 entities per transaction.

    Args:
        db_provider: Provides database sessions for mirror.db.
        logger: Structured logger for error reporting.
        session_id: Current sync session identifier for log context.
        succeeded: List of (url, local_path, bytes_transferred) tuples.
        state: Target state for the entities.
    """
    if not succeeded:
        return

    session = await db_provider.get_session("mirror")
    try:
        batch_count = 0
        for url, local_path, _bytes in succeeded:
            stmt = select(RepositoryFile).where(RepositoryFile.url == url)
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing is not None:
                existing.state = state
                existing.local_path = local_path
                existing.updated_at = datetime.now(UTC)

            batch_count += 1
            if batch_count >= _BATCH_SIZE:
                await session.commit()
                batch_count = 0

        if batch_count > 0:
            await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.error(
            "Failed to batch update RepositoryFile states",
            session_id=session_id,
        )
    finally:
        await session.close()


async def batch_mark_failed(
    db_provider: DatabaseProvider,
    logger: Logger,
    session_id: str,
    *,
    failed_urls: list[str],
) -> None:
    """Mark RepositoryFile entities as FAILED in batches.

    Increments retry_count and transitions to FAILED state.
    Commits in batches of ≤500.

    Args:
        db_provider: Provides database sessions for mirror.db.
        logger: Structured logger for error reporting.
        session_id: Current sync session identifier for log context.
        failed_urls: URLs of files that failed download.
    """
    if not failed_urls:
        return

    session = await db_provider.get_session("mirror")
    try:
        batch_count = 0
        for url in failed_urls:
            stmt = select(RepositoryFile).where(RepositoryFile.url == url)
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing is not None:
                existing.retry_count += 1
                if existing.retry_count >= _MAX_RETRIES:
                    existing.state = RepositoryFileState.FAILED
                else:
                    existing.state = RepositoryFileState.QUEUED
                existing.updated_at = datetime.now(UTC)

            batch_count += 1
            if batch_count >= _BATCH_SIZE:
                await session.commit()
                batch_count = 0

        if batch_count > 0:
            await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.error(
            "Failed to batch mark RepositoryFiles as failed",
            session_id=session_id,
        )
    finally:
        await session.close()
