"""Checksum and deduplication helpers for the mirror engine.

Extracted from engine.py to reduce module size. These functions handle
local checksum lookups for index and artifact files, and deduplication
of file entries to avoid concurrent download races.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from debcraft.infrastructure.models.mirror import RepositoryFile, RepositoryFileState

if TYPE_CHECKING:
    from debcraft.domain.mirror.config import RepositoryConfig
    from debcraft.domain.mirror.values import FileEntry
    from debcraft.platform.contracts.logging import Logger
    from debcraft.platform.contracts.persistence import DatabaseProvider


def deduplicate_entries(
    entries: list[FileEntry],
    logger: Logger,
    session_id: str,
) -> list[FileEntry]:
    """Remove duplicate entries by relative_path.

    Deduplication avoids concurrent downloads racing on the same file,
    which happens when arch-independent packages appear in multiple indexes.

    Args:
        entries: List of artifact file entries.
        logger: Structured logger for debug reporting.
        session_id: Current sync session identifier for log context.

    Returns:
        Deduplicated list preserving first-seen order.
    """
    seen_paths: set[str] = set()
    unique_entries: list[FileEntry] = []
    for entry in entries:
        if entry.relative_path not in seen_paths:
            seen_paths.add(entry.relative_path)
            unique_entries.append(entry)
    if len(unique_entries) < len(entries):
        logger.debug(
            "Deduplicated artifact entries",
            original_count=len(entries),
            unique_count=len(unique_entries),
            duplicates_removed=len(entries) - len(unique_entries),
            session_id=session_id,
        )
    return unique_entries


async def get_local_checksums(
    db_provider: DatabaseProvider,
    config: RepositoryConfig,
    suite: str,
    paths: list[str],
) -> dict[str, str]:
    """Get local SHA256 checksums for index files from mirror.db.

    Queries RepositoryFile entities for the given index paths to
    build a mapping of relative_path → sha256 for comparison.

    Args:
        db_provider: Provides database sessions for mirror.db.
        config: Repository configuration.
        suite: Suite name.
        paths: List of relative index paths to look up.

    Returns:
        Dictionary mapping relative_path to sha256 hex digest.
    """
    base_url = config.base_url.rstrip("/")
    checksums: dict[str, str] = {}

    session = await db_provider.get_session("mirror")
    try:
        for path in paths:
            url = f"{base_url}/dists/{suite}/{path}"
            stmt = select(RepositoryFile).where(
                RepositoryFile.url == url,
                RepositoryFile.state == RepositoryFileState.VERIFIED,
            )
            result = await session.execute(stmt)
            entity = result.scalar_one_or_none()
            if entity is not None:
                checksums[path] = entity.sha256
    finally:
        await session.close()

    return checksums


async def get_artifact_checksums(
    db_provider: DatabaseProvider,
    config: RepositoryConfig,
    entries: list[FileEntry],
) -> dict[str, str]:
    """Get local SHA256 checksums for artifact files from mirror.db.

    Queries RepositoryFile entities for artifact paths to determine
    which ones are already cached.

    Args:
        db_provider: Provides database sessions for mirror.db.
        config: Repository configuration.
        entries: File entries to check.

    Returns:
        Dictionary mapping relative_path to sha256 hex digest.
    """
    base_url = config.base_url.rstrip("/")
    checksums: dict[str, str] = {}

    session = await db_provider.get_session("mirror")
    try:
        for entry in entries:
            url = f"{base_url}/{entry.relative_path}"
            stmt = select(RepositoryFile).where(
                RepositoryFile.url == url,
                RepositoryFile.state.in_(
                    [
                        RepositoryFileState.VERIFIED,
                        RepositoryFileState.INDEXED,
                    ]
                ),
            )
            result = await session.execute(stmt)
            entity = result.scalar_one_or_none()
            if entity is not None:
                checksums[entry.relative_path] = entity.sha256
    finally:
        await session.close()

    return checksums
