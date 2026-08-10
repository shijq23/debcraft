"""Integration test for the full repository indexing pipeline.

Exercises: read file → parse → persist → query with real in-memory SQLite.
Verifies schema creation applies cleanly and end-to-end indexing produces
correct PackageInstance records and a published RepositorySnapshot.

Requirements: 6.1, 8.2, 8.3
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from debcraft.domain.indexer.events import IndexingCompleted, IndexingStarted
from debcraft.domain.indexer.service import IndexerService
from debcraft.infrastructure.indexer.file_reader import LocalFileReader
from debcraft.infrastructure.indexer.mapper import IndexerMapper
from debcraft.infrastructure.indexer.mirror_file_repository import SqlAlchemyMirrorFileRepository
from debcraft.infrastructure.indexer.repository import SqlAlchemyMetadataRepository
from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.models.metadata import PackageInstance, RepositorySnapshot
from debcraft.infrastructure.models.mirror import RepositoryFile, RepositoryFileState
from debcraft.platform.contracts.events import DomainEvent, EventBus

# Sample Packages file content with two valid packages
SAMPLE_PACKAGES_CONTENT = """\
Package: libfoo-dev
Version: 1.2.3-1
Architecture: amd64
Filename: pool/main/libf/libfoo/libfoo-dev_1.2.3-1_amd64.deb
SHA256: a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2
Size: 102400
Source: libfoo (1.2.3-1)
Section: libdevel
Priority: optional
Maintainer: Debian Developer <dev@debian.org>
Depends: libc6 (>= 2.34), libfoo1 (= 1.2.3-1)
Description: Development files for libfoo

Package: hello-world
Version: 2.0-1
Architecture: all
Filename: pool/main/h/hello-world/hello-world_2.0-1_all.deb
SHA256: deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef
Size: 8192
Section: utils
Priority: optional
Maintainer: Hello Maintainer <hello@example.com>
Description: Simple hello world package
"""


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
    """Create an in-memory SQLite engine with all tables and return (engine, factory)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


async def _seed_verified_packages_file(
    mirror_factory: async_sessionmaker[AsyncSession],
    local_path: str,
    repository_name: str = "debian-bookworm-main",
) -> int:
    """Insert a RepositoryFile in VERIFIED state pointing to a Packages file.

    The URL is constructed to contain the repository_name so that
    get_verified_files filtering by name works correctly.
    """
    async with mirror_factory() as session:
        entity = RepositoryFile(
            url=f"https://deb.debian.org/{repository_name}/dists/bookworm/main/binary-amd64/Packages",
            sha256="abc123def456",
            size_bytes=len(SAMPLE_PACKAGES_CONTENT.encode()),
            state=RepositoryFileState.VERIFIED,
            retry_count=0,
            local_path=local_path,
        )
        session.add(entity)
        await session.commit()
        return entity.id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_indexing_pipeline() -> None:
    """Test end-to-end: read file → parse → persist → query with real in-memory SQLite.

    Validates Requirements 6.1, 8.2, 8.3:
    - PackageInstance records are created in the database (6.1)
    - RepositorySnapshot is created and published (8.2, 8.3)
    - Package can be queried via get_package_metadata()
    """
    # Step 1: Create in-memory SQLite databases for mirror.db and metadata.db
    _, mirror_factory = await _create_engine_and_factory()
    _, metadata_factory = await _create_engine_and_factory()

    # Step 2: Create a temp file with real Packages content
    with tempfile.NamedTemporaryFile(mode="w", suffix=".Packages", delete=False) as f:
        f.write(SAMPLE_PACKAGES_CONTENT)
        packages_path = f.name

    try:
        # Step 3: Seed a RepositoryFile in VERIFIED state pointing to the temp file
        await _seed_verified_packages_file(mirror_factory, packages_path)

        # Step 4: Build the full IndexerService with real dependencies
        file_reader = LocalFileReader()
        mapper = IndexerMapper()
        metadata_repo = SqlAlchemyMetadataRepository(metadata_factory, mapper)
        mirror_file_repo = SqlAlchemyMirrorFileRepository(mirror_factory, metadata_factory)
        event_bus = MockEventBus()

        service = IndexerService(
            file_reader=file_reader,
            metadata_repository=metadata_repo,
            mirror_file_repository=mirror_file_repo,
            event_bus=event_bus,
        )

        # Step 5: Run the indexer
        result = await service.index_repository(
            repository_name="debian-bookworm-main",
            base_url="https://deb.debian.org/debian",
            suite="bookworm",
            component="main",
        )

        # Step 6: Verify IndexResult
        assert result.success is True
        assert result.packages_indexed == 2
        assert result.files_skipped == 0

        # Step 7: Verify PackageInstance records in the database
        async with metadata_factory() as session:
            stmt = select(PackageInstance).order_by(PackageInstance.package_name)
            query_result = await session.execute(stmt)
            packages = query_result.scalars().all()

            assert len(packages) == 2

            hello_pkg = next(p for p in packages if p.package_name == "hello-world")
            assert hello_pkg.version == "2.0-1"
            assert hello_pkg.architecture == "all"
            assert hello_pkg.sha256 == "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
            assert hello_pkg.size_bytes == 8192
            assert (
                hello_pkg.download_url
                == "https://deb.debian.org/debian/pool/main/h/hello-world/hello-world_2.0-1_all.deb"
            )

            libfoo_pkg = next(p for p in packages if p.package_name == "libfoo-dev")
            assert libfoo_pkg.version == "1.2.3-1"
            assert libfoo_pkg.architecture == "amd64"
            assert libfoo_pkg.source_package == "libfoo"
            assert libfoo_pkg.source_version == "1.2.3-1"
            assert libfoo_pkg.maintainer == "Debian Developer <dev@debian.org>"

        # Step 8: Verify RepositorySnapshot was published
        async with metadata_factory() as session:
            stmt = select(RepositorySnapshot)
            query_result = await session.execute(stmt)
            snapshot = query_result.scalar_one()

            assert snapshot.published is True
            assert snapshot.schema_version == 3

        # Step 9: Verify package can be queried via get_package_metadata()
        pkg_meta = await metadata_repo.get_package_metadata("libfoo-dev")
        assert pkg_meta is not None
        assert pkg_meta.package_name == "libfoo-dev"
        assert pkg_meta.version == "1.2.3-1"
        assert pkg_meta.source_package == "libfoo"

        # Step 10: Verify events were published
        assert len(event_bus.events) == 2
        assert isinstance(event_bus.events[0], IndexingStarted)
        assert event_bus.events[0].repository_name == "debian-bookworm-main"
        assert isinstance(event_bus.events[1], IndexingCompleted)
        assert event_bus.events[1].packages_indexed == 2

    finally:
        Path(packages_path).unlink(missing_ok=True)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_schema_creation_applies_cleanly() -> None:
    """Verify that Base.metadata.create_all applies all tables without errors.

    This validates that the schema (including v3 additions like indexing_records,
    file_ownerships, and extended columns) can be created from scratch on a
    fresh database, simulating a clean schema migration.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Verify key tables exist by querying them
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        # package_instances table
        stmt = select(PackageInstance)
        result = await session.execute(stmt)
        assert result.scalars().all() == []

        # repository_snapshots table
        stmt = select(RepositorySnapshot)
        result = await session.execute(stmt)
        assert result.scalars().all() == []

        # indexing_records table (v3 addition)
        from debcraft.infrastructure.models.metadata import IndexingRecord

        stmt = select(IndexingRecord)
        result = await session.execute(stmt)
        assert result.scalars().all() == []

        # file_ownerships table (v3 addition)
        from debcraft.infrastructure.models.metadata import FileOwnership

        stmt = select(FileOwnership)
        result = await session.execute(stmt)
        assert result.scalars().all() == []

    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_package_not_found_returns_none() -> None:
    """Verify that querying a non-existent package returns None."""
    _, metadata_factory = await _create_engine_and_factory()
    mapper = IndexerMapper()
    metadata_repo = SqlAlchemyMetadataRepository(metadata_factory, mapper)

    result = await metadata_repo.get_package_metadata("nonexistent-package")
    assert result is None
