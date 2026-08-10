"""Integration tests for incremental indexing behavior.

Verifies that:
1. A second indexing run skips already-indexed files (files_skipped > 0).
2. A parser version bump triggers re-indexing of previously-indexed files.

Uses real in-memory SQLite databases and the full IndexerService pipeline.

The incremental skip logic is exercised when a file appears in the VERIFIED
state (returned by get_verified_files) but has an existing indexing record
with matching SHA256 and parser version. After a first indexing run, the file
transitions to INDEXED state. To test the skip path, we reset the file state
back to VERIFIED — simulating a re-sync that re-verified the same content.

Requirements: 5.1, 5.2, 5.3
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from debcraft.domain.indexer.service import IndexerService
from debcraft.infrastructure.indexer.file_reader import LocalFileReader
from debcraft.infrastructure.indexer.mapper import IndexerMapper
from debcraft.infrastructure.indexer.mirror_file_repository import (
    SqlAlchemyMirrorFileRepository,
)
from debcraft.infrastructure.indexer.repository import SqlAlchemyMetadataRepository
from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.models.metadata import IndexingRecord
from debcraft.infrastructure.models.mirror import RepositoryFile, RepositoryFileState
from debcraft.platform.contracts.events import DomainEvent, EventBus

# Sample Packages file content with a single valid package
SAMPLE_PACKAGES_CONTENT = """\
Package: libfoo
Version: 1.2.3-1
Architecture: amd64
Filename: pool/main/libfoo_1.2.3-1_amd64.deb
SHA256: abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890
Size: 12345
Source: libfoo-src (1.2.3-1)
Maintainer: Test Maintainer <test@example.com>
Description: A test package
"""

FILE_SHA256 = "abc123def456abc123def456abc123def456abc123def456abc123def456abcd"


class MockEventBus(EventBus):
    """In-memory event bus that records all published events."""

    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    def subscribe(self, event_type: type, handler: Any) -> None:
        pass

    def unsubscribe(self, event_type: type, handler: Any) -> None:
        pass

    async def publish(self, event: DomainEvent) -> None:
        self.events.append(event)


async def _create_engine_and_factory() -> tuple[Any, async_sessionmaker[AsyncSession]]:
    """Create an in-memory SQLite engine with all tables."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


async def _seed_verified_packages_file(
    mirror_factory: async_sessionmaker[AsyncSession],
    local_path: str,
) -> int:
    """Insert a RepositoryFile in VERIFIED state pointing to a Packages file."""
    async with mirror_factory() as session:
        entity = RepositoryFile(
            url="https://deb.debian.org/debian/dists/bookworm/main/binary-amd64/Packages",
            sha256=FILE_SHA256,
            size_bytes=len(SAMPLE_PACKAGES_CONTENT.encode()),
            state=RepositoryFileState.VERIFIED,
            retry_count=0,
            local_path=local_path,
        )
        session.add(entity)
        await session.commit()
        return entity.id


async def _reset_file_to_verified(
    mirror_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reset a file's state from INDEXED back to VERIFIED.

    Simulates a re-sync cycle where the mirror re-downloads and re-verifies
    the file. The indexing record remains so the indexer can check skip logic.
    """
    async with mirror_factory() as session:
        stmt = select(RepositoryFile)
        result = await session.execute(stmt)
        entity = result.scalar_one()
        entity.state = RepositoryFileState.VERIFIED
        await session.commit()


def _build_service(
    mirror_factory: async_sessionmaker[AsyncSession],
    metadata_factory: async_sessionmaker[AsyncSession],
    event_bus: MockEventBus,
) -> IndexerService:
    """Build an IndexerService with real infrastructure dependencies."""
    file_reader = LocalFileReader()
    mapper = IndexerMapper()
    metadata_repo = SqlAlchemyMetadataRepository(metadata_factory, mapper)
    mirror_file_repo = SqlAlchemyMirrorFileRepository(mirror_factory, metadata_factory)
    return IndexerService(
        file_reader=file_reader,
        metadata_repository=metadata_repo,
        mirror_file_repository=mirror_file_repo,
        event_bus=event_bus,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_second_run_skips_already_indexed_files() -> None:
    """Verify that a second indexing run skips files already indexed.

    Requirements: 5.1
    - First run indexes the file (packages_indexed > 0)
    - Reset file to VERIFIED (simulating re-sync)
    - Second run skips it because indexing record matches (files_skipped > 0)
    """
    _, mirror_factory = await _create_engine_and_factory()
    _, metadata_factory = await _create_engine_and_factory()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".Packages", delete=False) as f:
        f.write(SAMPLE_PACKAGES_CONTENT)
        packages_path = f.name

    try:
        await _seed_verified_packages_file(mirror_factory, packages_path)
        event_bus = MockEventBus()
        service = _build_service(mirror_factory, metadata_factory, event_bus)

        # First run: should index the file
        result1 = await service.index_repository(
            repository_name="debian",
            base_url="https://deb.debian.org/debian",
            suite="bookworm",
            component="main",
        )

        assert result1.success is True
        assert result1.packages_indexed == 1
        assert result1.files_skipped == 0

        # Reset file back to VERIFIED to simulate a re-sync cycle.
        # The indexing record is preserved, so the skip logic can kick in.
        await _reset_file_to_verified(mirror_factory)

        # Second run: file is VERIFIED again but indexing record has
        # matching sha256 and parser version → should be skipped.
        result2 = await service.index_repository(
            repository_name="debian",
            base_url="https://deb.debian.org/debian",
            suite="bookworm",
            component="main",
        )

        assert result2.success is True
        assert result2.packages_indexed == 0
        assert result2.files_skipped == 1

    finally:
        Path(packages_path).unlink(missing_ok=True)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parser_version_bump_triggers_reindexing() -> None:
    """Verify that bumping the parser version causes re-indexing.

    Requirements: 5.2, 5.3
    - First run indexes the file with the current parser version
    - Reset file to VERIFIED and lower the recorded parser version
    - Second run re-indexes the file (files_skipped == 0)
    """
    _, mirror_factory = await _create_engine_and_factory()
    _, metadata_factory = await _create_engine_and_factory()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".Packages", delete=False) as f:
        f.write(SAMPLE_PACKAGES_CONTENT)
        packages_path = f.name

    try:
        await _seed_verified_packages_file(mirror_factory, packages_path)
        event_bus = MockEventBus()
        service = _build_service(mirror_factory, metadata_factory, event_bus)

        # First run: indexes normally
        result1 = await service.index_repository(
            repository_name="debian",
            base_url="https://deb.debian.org/debian",
            suite="bookworm",
            component="main",
        )

        assert result1.success is True
        assert result1.packages_indexed == 1
        assert result1.files_skipped == 0

        # Reset file state to VERIFIED (simulating re-sync).
        await _reset_file_to_verified(mirror_factory)

        # Simulate a parser version bump: lower the recorded version to 0.
        # The PackagesParser has PARSER_VERSION = 1 now, so the mismatch
        # means the file should be re-indexed.
        async with metadata_factory() as session:
            stmt = select(IndexingRecord)
            result = await session.execute(stmt)
            record = result.scalar_one()
            record.parser_version = 0
            await session.commit()

        # Second run: should re-index because parser version doesn't match.
        # The file is NOT skipped (files_skipped == 0), meaning it was re-parsed.
        # Note: packages_indexed may be 0 because the PackageInstance with the
        # same natural key already exists from the first run (duplicate skipping).
        # The key assertion is that files_skipped == 0 (file was re-processed).
        result2 = await service.index_repository(
            repository_name="debian",
            base_url="https://deb.debian.org/debian",
            suite="bookworm",
            component="main",
        )

        assert result2.success is True
        assert result2.files_skipped == 0

        # Verify the indexing record was updated with the current parser version
        async with metadata_factory() as session:
            stmt = select(IndexingRecord)
            result = await session.execute(stmt)
            record = result.scalar_one()
            # After re-indexing, the parser version should be back to 1
            assert record.parser_version == 1

    finally:
        Path(packages_path).unlink(missing_ok=True)
