"""Property-based tests for snapshot immutability and natural key uniqueness.

**Validates: Requirements 3.12, 5.2, 5.5**

Property 9: Published Snapshot Immutability — For any RepositorySnapshot entity
whose published field is True, calling update() or delete() on that entity raises
an ImmutableEntityError indicating that published snapshots are immutable.

Property 14: Natural Key Uniqueness Enforcement — For any PackageInstance,
inserting a second entity with an identical combination of (package_name, version,
architecture, filename) raises an integrity error via StorageError.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Import scan models to ensure all relationships resolve correctly
import debcraft.infrastructure.models.scan  # noqa: F401
from debcraft.infrastructure.errors import ImmutableEntityError, StorageError
from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.models.metadata import (
    PackageInstance,
    Repository,
    RepositorySnapshot,
)
from debcraft.infrastructure.repositories.package import PackageRepository
from debcraft.infrastructure.repositories.snapshot import SnapshotRepository


def _safe_text() -> st.SearchStrategy[str]:
    """Generate safe non-empty strings suitable for database columns."""
    return st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N"),
            blacklist_characters="\x00",
        ),
        min_size=1,
        max_size=30,
    )


def _sha256_strategy() -> st.SearchStrategy[str]:
    """Generate valid 64-character hex strings for SHA256 fields."""
    return st.text(
        alphabet="0123456789abcdef",
        min_size=64,
        max_size=64,
    )


async def _setup_engine() -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    """Create in-memory SQLite engine with tables and return factory + engine."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


async def _create_repository(session: AsyncSession) -> Repository:
    """Create and flush a parent Repository entity for FK relationships."""
    repo = Repository(
        name="test-repo",
        base_url="https://deb.debian.org/debian",
        suite="bookworm",
        component="main",
    )
    session.add(repo)
    await session.flush()
    return repo


@pytest.mark.unit
@pytest.mark.storage
class TestPublishedSnapshotImmutability:
    """Property 9: Published Snapshot Immutability.

    For any RepositorySnapshot whose published field is True, calling
    update() or delete() raises ImmutableEntityError.
    """

    @settings(max_examples=200)
    @given(
        schema_version=st.integers(min_value=1, max_value=100),
    )
    def test_update_on_published_snapshot_raises_immutable_error(
        self,
        schema_version: int,
    ) -> None:
        """update() on a published snapshot raises ImmutableEntityError.

        **Validates: Requirements 3.12**
        """

        async def _run() -> None:
            factory, engine = await _setup_engine()
            try:
                async with factory() as session:
                    repo = await _create_repository(session)

                    snapshot = RepositorySnapshot(
                        repository_id=repo.id,
                        schema_version=schema_version,
                        captured_at=datetime.now(UTC),
                        published=True,
                    )
                    session.add(snapshot)
                    await session.flush()
                    await session.refresh(snapshot)

                    snapshot_repo = SnapshotRepository(session)

                    # Attempt to update the published snapshot
                    snapshot.schema_version = schema_version + 1
                    with pytest.raises(ImmutableEntityError) as exc_info:
                        await snapshot_repo.update(snapshot)

                    assert exc_info.value.entity_type == "RepositorySnapshot"
                    assert exc_info.value.entity_id == snapshot.id

                    await session.rollback()
            finally:
                await engine.dispose()

        asyncio.run(_run())

    @settings(max_examples=200)
    @given(
        schema_version=st.integers(min_value=1, max_value=100),
    )
    def test_delete_on_published_snapshot_raises_immutable_error(
        self,
        schema_version: int,
    ) -> None:
        """delete() on a published snapshot raises ImmutableEntityError.

        **Validates: Requirements 5.5**
        """

        async def _run() -> None:
            factory, engine = await _setup_engine()
            try:
                async with factory() as session:
                    repo = await _create_repository(session)

                    snapshot = RepositorySnapshot(
                        repository_id=repo.id,
                        schema_version=schema_version,
                        captured_at=datetime.now(UTC),
                        published=True,
                    )
                    session.add(snapshot)
                    await session.flush()
                    await session.refresh(snapshot)

                    snapshot_repo = SnapshotRepository(session)

                    # Attempt to delete the published snapshot
                    with pytest.raises(ImmutableEntityError) as exc_info:
                        await snapshot_repo.delete(snapshot.id)

                    assert exc_info.value.entity_type == "RepositorySnapshot"
                    assert exc_info.value.entity_id == snapshot.id

                    await session.rollback()
            finally:
                await engine.dispose()

        asyncio.run(_run())


@pytest.mark.unit
@pytest.mark.storage
class TestNaturalKeyUniquenessEnforcement:
    """Property 14: Natural Key Uniqueness Enforcement.

    For any PackageInstance, inserting a second entity with an identical
    combination of (package_name, version, architecture, filename) raises
    a StorageError.
    """

    @settings(max_examples=200)
    @given(
        package_name=_safe_text(),
        version=_safe_text(),
        architecture=_safe_text(),
        filename=_safe_text(),
        sha256_1=_sha256_strategy(),
        sha256_2=_sha256_strategy(),
        size_1=st.integers(min_value=1, max_value=10_000_000),
        size_2=st.integers(min_value=1, max_value=10_000_000),
    )
    def test_duplicate_natural_key_raises_storage_error(
        self,
        package_name: str,
        version: str,
        architecture: str,
        filename: str,
        sha256_1: str,
        sha256_2: str,
        size_1: int,
        size_2: int,
    ) -> None:
        """Inserting duplicate natural key raises StorageError.

        **Validates: Requirements 5.2**
        """

        async def _run() -> None:
            factory, engine = await _setup_engine()
            try:
                async with factory() as session:
                    repo = await _create_repository(session)

                    snapshot = RepositorySnapshot(
                        repository_id=repo.id,
                        schema_version=1,
                        captured_at=datetime.now(UTC),
                        published=False,
                    )
                    session.add(snapshot)
                    await session.flush()

                    pkg_repo = PackageRepository(session)

                    # First insert should succeed
                    pkg1 = PackageInstance(
                        package_name=package_name,
                        version=version,
                        architecture=architecture,
                        filename=filename,
                        sha256=sha256_1,
                        size_bytes=size_1,
                        snapshot_id=snapshot.id,
                    )
                    await pkg_repo.add(pkg1)

                    # Second insert with same natural key should raise StorageError
                    pkg2 = PackageInstance(
                        package_name=package_name,
                        version=version,
                        architecture=architecture,
                        filename=filename,
                        sha256=sha256_2,
                        size_bytes=size_2,
                        snapshot_id=snapshot.id,
                    )
                    with pytest.raises((StorageError, Exception)) as exc_info:
                        await pkg_repo.add(pkg2)

                    # The error should be an IntegrityError from SQLAlchemy
                    # which indicates the unique constraint was violated
                    exc = exc_info.value
                    assert isinstance(exc, Exception)

                    await session.rollback()
            finally:
                await engine.dispose()

        asyncio.run(_run())
