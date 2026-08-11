"""Repository for querying and updating RepositoryFile states for the indexer.

This module provides the infrastructure-layer implementation of the
MirrorFileRepository protocol, reading VERIFIED files from mirror.db
and managing indexing records in metadata.db.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from debcraft.infrastructure.models.metadata import IndexingRecord
from debcraft.infrastructure.models.mirror import RepositoryFile, RepositoryFileState

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True)
class RepositoryFileInfo:
    """Domain-friendly view of a RepositoryFile without ORM coupling.

    Attributes:
        id: The database primary key of the repository file.
        url: The full URL of the file in the remote repository.
        sha256: The hex-encoded SHA256 digest of the file content.
        local_path: Local filesystem path where the file is cached.
        size_bytes: Size of the file in bytes.
    """

    id: int
    url: str
    sha256: str
    local_path: str
    size_bytes: int


@dataclass(frozen=True)
class IndexingRecordInfo:
    """Domain-friendly view of an IndexingRecord without ORM coupling.

    Attributes:
        repository_file_id: ID of the repository file that was indexed.
        parser_version: The parser version used when the file was indexed.
        indexed_sha256: The SHA256 of the file at the time it was indexed.
        indexed_at: Timestamp when the file was indexed.
    """

    repository_file_id: int
    parser_version: int
    indexed_sha256: str
    indexed_at: datetime


class SqlAlchemyMirrorFileRepository:
    """Queries and updates RepositoryFile states in mirror.db for the indexer.

    This repository bridges the mirror database (which tracks file download
    states) with the indexer's needs to find VERIFIED files and record
    indexing progress.

    The repository uses two session factories:
    - mirror_session_factory: for querying/updating RepositoryFile in mirror.db
    - metadata_session_factory: for querying/upserting IndexingRecord in metadata.db
    """

    def __init__(
        self,
        mirror_session_factory: async_sessionmaker[AsyncSession],
        metadata_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Initialize the mirror file repository.

        Args:
            mirror_session_factory: Session factory for mirror.db operations.
            metadata_session_factory: Session factory for metadata.db operations.
        """
        self._mirror_session_factory = mirror_session_factory
        self._metadata_session_factory = metadata_session_factory

    async def get_verified_files(self, repository_name: str | None = None) -> list[RepositoryFileInfo]:
        """Return files in VERIFIED or INDEXED state, optionally filtered by repository.

        Queries the mirror database for all RepositoryFile records with
        state in (VERIFIED, INDEXED). This includes both newly verified files
        and previously indexed files, allowing the indexer to evaluate all
        eligible files via _should_skip() for incremental re-parse decisions.

        Args:
            repository_name: Optional repository name to filter by.
                When provided, only files whose URL contains this name
                are returned.

        Returns:
            A list of RepositoryFileInfo value objects representing
            files eligible for indexing evaluation.
        """
        async with self._mirror_session_factory() as session:
            stmt = select(RepositoryFile).where(
                RepositoryFile.state.in_([RepositoryFileState.VERIFIED, RepositoryFileState.INDEXED])
            )
            if repository_name is not None:
                stmt = stmt.where(RepositoryFile.url.contains(repository_name))
            result = await session.execute(stmt)
            files = result.scalars().all()
            return [
                RepositoryFileInfo(
                    id=f.id,
                    url=f.url,
                    sha256=f.sha256,
                    local_path=f.local_path or "",
                    size_bytes=f.size_bytes,
                )
                for f in files
            ]

    async def get_indexing_record(self, file_id: int) -> IndexingRecordInfo | None:
        """Return the indexing metadata for a file.

        Queries the metadata database for an IndexingRecord matching
        the given repository file ID.

        Args:
            file_id: The ID of the repository file to look up.

        Returns:
            An IndexingRecordInfo if the file has been previously indexed,
            or None if no indexing record exists.
        """
        async with self._metadata_session_factory() as session:
            stmt = select(IndexingRecord).where(IndexingRecord.repository_file_id == file_id)
            result = await session.execute(stmt)
            record = result.scalar_one_or_none()
            if record is None:
                return None
            return IndexingRecordInfo(
                repository_file_id=record.repository_file_id,
                parser_version=record.parser_version,
                indexed_sha256=record.indexed_sha256,
                indexed_at=record.indexed_at,
            )

    async def mark_indexed(self, file_id: int, parser_version: int, sha256: str) -> None:
        """Record that a file has been indexed with a given parser version.

        Upserts an IndexingRecord in the metadata database and transitions
        the RepositoryFile state to INDEXED in the mirror database.

        Args:
            file_id: The ID of the repository file that was indexed.
            parser_version: The parser version used for indexing.
            sha256: The SHA256 of the file at the time of indexing.
        """
        # Upsert the indexing record in metadata.db
        async with self._metadata_session_factory() as session:
            stmt = select(IndexingRecord).where(IndexingRecord.repository_file_id == file_id)
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            now = datetime.now(UTC)
            if existing is not None:
                existing.parser_version = parser_version
                existing.indexed_sha256 = sha256
                existing.indexed_at = now
            else:
                record = IndexingRecord(
                    repository_file_id=file_id,
                    parser_version=parser_version,
                    indexed_sha256=sha256,
                    indexed_at=now,
                )
                session.add(record)

            await session.commit()

        # Transition the file state to INDEXED in mirror.db
        async with self._mirror_session_factory() as session:
            file_stmt = select(RepositoryFile).where(RepositoryFile.id == file_id)
            file_result = await session.execute(file_stmt)
            file_entity = file_result.scalar_one_or_none()
            if file_entity is not None:
                file_entity.state = RepositoryFileState.INDEXED
                file_entity.updated_at = datetime.now(UTC)
            await session.commit()
