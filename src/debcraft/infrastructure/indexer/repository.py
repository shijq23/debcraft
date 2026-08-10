"""SQLAlchemy implementation of the MetadataRepository protocol.

This module provides the main persistence repository for the indexer,
handling creation of repositories, snapshots, package instances,
source packages, and file ownership records in metadata.db.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

from sqlalchemy import delete, select

from debcraft.domain.indexer.values import FileOwnership, PackageMetadata, SourcePackageMetadata
from debcraft.infrastructure.models.metadata import (
    FileOwnership as FileOwnershipModel,
)
from debcraft.infrastructure.models.metadata import (
    PackageInstance,
    Repository,
    RepositorySnapshot,
    SourcePackage,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from debcraft.infrastructure.indexer.mapper import IndexerMapper


class SqlAlchemyMetadataRepository:
    """Persists indexer domain objects as SQLAlchemy models in metadata.db.

    Implements the MetadataRepository protocol defined in the design document,
    providing methods for managing repositories, snapshots, package instances,
    source packages, and file ownership records.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        mapper: IndexerMapper,
    ) -> None:
        """Initialize the metadata repository.

        Args:
            session_factory: Async session factory for metadata.db operations.
            mapper: Mapper for converting domain value objects to ORM models.
        """
        self._session_factory = session_factory
        self._mapper = mapper

    async def find_or_create_repository(self, *, name: str, base_url: str, suite: str, component: str) -> int:
        """Return repository ID, creating if needed.

        Queries for a Repository by name. If not found, creates a new one
        with the provided base_url, suite, and component.

        Args:
            name: Repository name (e.g. "debian-bookworm-main").
            base_url: Base URL of the repository.
            suite: Repository suite (e.g. "bookworm").
            component: Repository component (e.g. "main").

        Returns:
            The database ID of the found or created Repository.
        """
        async with self._session_factory() as session:
            stmt = select(Repository).where(Repository.name == name)
            result = await session.execute(stmt)
            repo = result.scalar_one_or_none()

            if repo is not None:
                return repo.id

            repo = Repository(
                name=name,
                base_url=base_url,
                suite=suite,
                component=component,
            )
            session.add(repo)
            await session.commit()
            return repo.id

    async def create_snapshot(self, *, repository_id: int, schema_version: int) -> int:
        """Create a new RepositorySnapshot, return its ID.

        Creates a snapshot with captured_at set to the current UTC time
        and published set to False.

        Args:
            repository_id: ID of the repository this snapshot belongs to.
            schema_version: The schema version active at capture time.

        Returns:
            The database ID of the newly created RepositorySnapshot.
        """
        async with self._session_factory() as session:
            snapshot = RepositorySnapshot(
                repository_id=repository_id,
                schema_version=schema_version,
                captured_at=datetime.now(UTC),
                published=False,
            )
            session.add(snapshot)
            await session.commit()
            return snapshot.id

    async def publish_snapshot(self, snapshot_id: int) -> None:
        """Set snapshot.published = True.

        Args:
            snapshot_id: ID of the snapshot to publish.
        """
        async with self._session_factory() as session:
            stmt = select(RepositorySnapshot).where(RepositorySnapshot.id == snapshot_id)
            result = await session.execute(stmt)
            snapshot = result.scalar_one_or_none()
            if snapshot is not None:
                snapshot.published = True
            await session.commit()

    async def add_package_instances(
        self, *, snapshot_id: int, packages: Sequence[PackageMetadata], base_url: str
    ) -> int:
        """Bulk insert PackageInstance records, skipping duplicates.

        For each PackageMetadata, computes the download_url as
        base_url.rstrip('/') + '/' + filename, then checks whether a
        record with the same natural key (package_name, version,
        architecture, filename) already exists before inserting.

        Args:
            snapshot_id: ID of the snapshot these packages belong to.
            packages: List of PackageMetadata domain value objects to persist.
            base_url: Repository base URL for computing download URLs.

        Returns:
            Count of successfully inserted PackageInstance records.
        """
        inserted_count = 0
        normalized_base_url = base_url.rstrip("/")

        async with self._session_factory() as session:
            for pkg in packages:
                # Check for existing record with same natural key
                exists_stmt = select(PackageInstance.id).where(
                    PackageInstance.package_name == pkg.package_name,
                    PackageInstance.version == pkg.version,
                    PackageInstance.architecture == pkg.architecture,
                    PackageInstance.filename == pkg.filename,
                )
                exists_result = await session.execute(exists_stmt)
                if exists_result.scalar_one_or_none() is not None:
                    continue

                download_url = normalized_base_url + "/" + pkg.filename
                model = self._mapper.package_metadata_to_model(
                    metadata=pkg,
                    snapshot_id=snapshot_id,
                    download_url=download_url,
                )
                session.add(model)
                inserted_count += 1

            await session.commit()

        return inserted_count

    async def add_source_packages(self, *, packages: Sequence[SourcePackageMetadata]) -> int:
        """Upsert SourcePackage records. Returns count of new records.

        For each SourcePackageMetadata, checks if a SourcePackage with the
        same natural key (name, version) already exists. If not, inserts a
        new record.

        Args:
            packages: List of SourcePackageMetadata domain value objects.

        Returns:
            Count of newly inserted SourcePackage records.
        """
        inserted_count = 0

        async with self._session_factory() as session:
            for src_pkg in packages:
                stmt = select(SourcePackage).where(
                    SourcePackage.name == src_pkg.name,
                    SourcePackage.version == src_pkg.version,
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing is None:
                    model = self._mapper.source_metadata_to_model(
                        metadata=src_pkg,
                    )
                    session.add(model)
                    inserted_count += 1

            await session.commit()

        return inserted_count

    async def replace_file_ownerships(self, *, snapshot_id: int, ownerships: Sequence[FileOwnership]) -> int:
        """Delete existing ownerships for snapshot, insert new ones.

        Removes all FileOwnership records for the given snapshot_id,
        then bulk inserts the new set of ownership records.

        Args:
            snapshot_id: ID of the snapshot to replace ownerships for.
            ownerships: New list of FileOwnership domain value objects.

        Returns:
            Count of newly inserted FileOwnership records.
        """
        async with self._session_factory() as session:
            # Delete existing ownerships for this snapshot
            delete_stmt = delete(FileOwnershipModel).where(FileOwnershipModel.snapshot_id == snapshot_id)
            await session.execute(delete_stmt)

            # Insert new ownerships
            for ownership in ownerships:
                model = self._mapper.file_ownership_to_model(
                    ownership=ownership,
                    snapshot_id=snapshot_id,
                )
                session.add(model)

            await session.commit()

        return len(ownerships)

    async def get_package_metadata(self, package_name: str) -> PackageMetadata | None:
        """Look up latest indexed metadata for a package by name.

        Queries PackageInstance by package_name, ordered by created_at
        descending, and returns the first result converted back to a
        domain PackageMetadata value object.

        Args:
            package_name: The binary package name to look up.

        Returns:
            A PackageMetadata value object if found, or None.
        """
        async with self._session_factory() as session:
            stmt = (
                select(PackageInstance)
                .where(PackageInstance.package_name == package_name)
                .order_by(PackageInstance.created_at.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            instance = result.scalar_one_or_none()

            if instance is None:
                return None

            return PackageMetadata(
                package_name=instance.package_name,
                version=instance.version,
                architecture=instance.architecture,
                filename=instance.filename,
                sha256=instance.sha256,
                size_bytes=instance.size_bytes,
                source_package=instance.source_package or instance.package_name,
                source_version=instance.source_version or instance.version,
                homepage=instance.homepage,
                maintainer=instance.maintainer,
                depends=instance.depends,
                provides=instance.provides,
                section=instance.section,
                priority=instance.priority,
                description=instance.description,
            )
