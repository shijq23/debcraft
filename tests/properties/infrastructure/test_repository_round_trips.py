"""Property-based tests for repository round-trip operations.

**Validates: Requirements 3.2, 3.7, 3.9, 3.10, 3.11, 11.10**

Property 4: Repository Round-Trip (Surrogate Key) — For any valid domain entity,
storing it via repository.add() then retrieving it via repository.get_by_id()
with the assigned surrogate key yields an entity with equivalent field values.

Property 5: Repository Round-Trip (Natural Key) — For any valid PackageInstance
with a unique natural key combination, storing it via repository.add() then
retrieving it via repository.get_by_natural_key() yields equivalent field values.

Property 6: Repository State Filtering — For any collection of RepositoryFile
entities persisted with varying lifecycle states, querying by a specific state
returns exactly the subset of entities whose state equals that state.

Property 7: Empty Find Returns Empty List — For any filter criteria that match
no stored entities, repository.find(**filters) returns an empty list.

Property 8: Missing Entity Lookup Raises StorageError — For any surrogate key
value that does not exist in the database, repository.get_by_id(key) raises
EntityNotFoundError.
"""

from __future__ import annotations

import asyncio

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
from debcraft.infrastructure.errors import EntityNotFoundError
from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.models.metadata import (
    PackageInstance,
    Repository,
    RepositorySnapshot,
)
from debcraft.infrastructure.models.mirror import RepositoryFile, RepositoryFileState
from debcraft.infrastructure.repositories.package import PackageRepository
from debcraft.infrastructure.repositories.repository_file import RepositoryFileRepository


def _safe_text(min_size: int = 1, max_size: int = 30) -> st.SearchStrategy[str]:
    """Generate safe non-empty strings suitable for DB columns."""
    return st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N"),
            blacklist_characters="\x00",
        ),
        min_size=min_size,
        max_size=max_size,
    )


def _sha256_hex() -> st.SearchStrategy[str]:
    """Generate valid 64-character hex strings representing SHA256 hashes."""
    return st.text(
        alphabet="0123456789abcdef",
        min_size=64,
        max_size=64,
    )


def _url_strategy() -> st.SearchStrategy[str]:
    """Generate unique URL-like strings for RepositoryFile."""
    return st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N"),
            blacklist_characters="\x00",
        ),
        min_size=5,
        max_size=100,
    ).map(lambda s: f"https://repo.example.com/{s}")


async def _setup_engine() -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    """Create in-memory SQLite engine with all tables and return factory + engine."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


async def _create_snapshot(session: AsyncSession) -> RepositorySnapshot:
    """Create a Repository and RepositorySnapshot parent for PackageInstance FK."""
    from datetime import UTC, datetime

    repo = Repository(
        name="test-repo",
        base_url="https://deb.example.com",
        suite="bookworm",
        component="main",
    )
    session.add(repo)
    await session.flush()

    snapshot = RepositorySnapshot(
        repository_id=repo.id,
        schema_version=1,
        captured_at=datetime.now(UTC),
        published=False,
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


@pytest.mark.unit
@pytest.mark.storage
class TestRepositoryRoundTripSurrogateKey:
    """Property 4: Repository Round-Trip (Surrogate Key).

    For any valid domain entity, storing via add() then retrieving via
    get_by_id() with the assigned surrogate key yields an entity with
    equivalent field values.
    """

    @settings(max_examples=200)
    @given(
        url=_url_strategy(),
        sha256=_sha256_hex(),
        size_bytes=st.integers(min_value=0, max_value=2**40),
        state=st.sampled_from(list(RepositoryFileState)),
    )
    def test_repository_file_round_trip(
        self,
        url: str,
        sha256: str,
        size_bytes: int,
        state: RepositoryFileState,
    ) -> None:
        """Store a RepositoryFile and retrieve by surrogate key.

        **Validates: Requirements 3.2, 11.10**
        """

        async def _run() -> None:
            factory, engine = await _setup_engine()
            try:
                async with factory() as session:
                    entity = RepositoryFile(
                        url=url,
                        sha256=sha256,
                        size_bytes=size_bytes,
                        state=state,
                        retry_count=0,
                    )
                    repo = RepositoryFileRepository(session)
                    added = await repo.add(entity)
                    assert added.id is not None

                    retrieved = await repo.get_by_id(added.id)
                    assert retrieved.url == url
                    assert retrieved.sha256 == sha256
                    assert retrieved.size_bytes == size_bytes
                    assert retrieved.state == state
                    assert retrieved.retry_count == 0

                    await session.rollback()
            finally:
                await engine.dispose()

        asyncio.run(_run())

    @settings(max_examples=200)
    @given(
        package_name=_safe_text(),
        version=_safe_text(),
        architecture=_safe_text(max_size=10),
        filename=_safe_text(max_size=50),
        sha256=_sha256_hex(),
        size_bytes=st.integers(min_value=0, max_value=2**40),
    )
    def test_package_instance_round_trip(
        self,
        package_name: str,
        version: str,
        architecture: str,
        filename: str,
        sha256: str,
        size_bytes: int,
    ) -> None:
        """Store a PackageInstance and retrieve by surrogate key.

        **Validates: Requirements 3.2, 11.10**
        """

        async def _run() -> None:
            factory, engine = await _setup_engine()
            try:
                async with factory() as session:
                    snapshot = await _create_snapshot(session)

                    entity = PackageInstance(
                        package_name=package_name,
                        version=version,
                        architecture=architecture,
                        filename=filename,
                        sha256=sha256,
                        size_bytes=size_bytes,
                        snapshot_id=snapshot.id,
                    )
                    repo = PackageRepository(session)
                    added = await repo.add(entity)
                    assert added.id is not None

                    retrieved = await repo.get_by_id(added.id)
                    assert retrieved.package_name == package_name
                    assert retrieved.version == version
                    assert retrieved.architecture == architecture
                    assert retrieved.filename == filename
                    assert retrieved.sha256 == sha256
                    assert retrieved.size_bytes == size_bytes
                    assert retrieved.snapshot_id == snapshot.id

                    await session.rollback()
            finally:
                await engine.dispose()

        asyncio.run(_run())


@pytest.mark.unit
@pytest.mark.storage
class TestRepositoryRoundTripNaturalKey:
    """Property 5: Repository Round-Trip (Natural Key).

    For any valid PackageInstance with a unique natural key combination,
    storing via add() then retrieving via get_by_natural_key() yields
    an entity with equivalent field values.
    """

    @settings(max_examples=200)
    @given(
        package_name=_safe_text(),
        version=_safe_text(),
        architecture=_safe_text(max_size=10),
        filename=_safe_text(max_size=50),
        sha256=_sha256_hex(),
        size_bytes=st.integers(min_value=0, max_value=2**40),
    )
    def test_package_instance_natural_key_round_trip(
        self,
        package_name: str,
        version: str,
        architecture: str,
        filename: str,
        sha256: str,
        size_bytes: int,
    ) -> None:
        """Store a PackageInstance and retrieve by natural key.

        **Validates: Requirements 3.9**
        """

        async def _run() -> None:
            factory, engine = await _setup_engine()
            try:
                async with factory() as session:
                    snapshot = await _create_snapshot(session)

                    entity = PackageInstance(
                        package_name=package_name,
                        version=version,
                        architecture=architecture,
                        filename=filename,
                        sha256=sha256,
                        size_bytes=size_bytes,
                        snapshot_id=snapshot.id,
                    )
                    repo = PackageRepository(session)
                    await repo.add(entity)

                    retrieved = await repo.get_by_natural_key(
                        package_name=package_name,
                        version=version,
                        architecture=architecture,
                        filename=filename,
                    )
                    assert retrieved.package_name == package_name
                    assert retrieved.version == version
                    assert retrieved.architecture == architecture
                    assert retrieved.filename == filename
                    assert retrieved.sha256 == sha256
                    assert retrieved.size_bytes == size_bytes

                    await session.rollback()
            finally:
                await engine.dispose()

        asyncio.run(_run())


@pytest.mark.unit
@pytest.mark.storage
class TestRepositoryStateFiltering:
    """Property 6: Repository State Filtering.

    For any collection of RepositoryFile entities persisted with varying
    lifecycle states, querying by a specific state returns exactly the
    subset of entities whose state equals that state.
    """

    @settings(max_examples=200)
    @given(
        states=st.lists(
            st.sampled_from(list(RepositoryFileState)),
            min_size=1,
            max_size=20,
        ),
        query_state=st.sampled_from(list(RepositoryFileState)),
    )
    def test_find_by_state_returns_correct_subset(
        self,
        states: list[RepositoryFileState],
        query_state: RepositoryFileState,
    ) -> None:
        """Querying by state returns exactly the entities with that state.

        **Validates: Requirements 3.10**
        """

        async def _run() -> None:
            factory, engine = await _setup_engine()
            try:
                async with factory() as session:
                    repo = RepositoryFileRepository(session)

                    # Create entities with given states, each with a unique URL
                    for i, state in enumerate(states):
                        entity = RepositoryFile(
                            url=f"https://repo.example.com/file_{i}_{state.value}",
                            sha256="a" * 64,
                            size_bytes=1024,
                            state=state,
                            retry_count=0,
                        )
                        await repo.add(entity)

                    # Query by the selected state
                    results = await repo.find_by_state(query_state)

                    # Count how many we expect
                    expected_count = sum(1 for s in states if s == query_state)
                    assert len(results) == expected_count, (
                        f"Expected {expected_count} entities with state {query_state}, got {len(results)}"
                    )

                    # All returned entities should have the queried state
                    for entity in results:
                        assert entity.state == query_state

                    await session.rollback()
            finally:
                await engine.dispose()

        asyncio.run(_run())


@pytest.mark.unit
@pytest.mark.storage
class TestEmptyFindReturnsEmptyList:
    """Property 7: Empty Find Returns Empty List.

    For any filter criteria that match no stored entities,
    repository.find(**filters) returns an empty list.
    """

    @settings(max_examples=200)
    @given(
        nonexistent_url=_url_strategy(),
    )
    def test_find_nonexistent_returns_empty_list(
        self,
        nonexistent_url: str,
    ) -> None:
        """Find with non-matching filters returns empty list.

        **Validates: Requirements 3.11**
        """

        async def _run() -> None:
            factory, engine = await _setup_engine()
            try:
                async with factory() as session:
                    repo = RepositoryFileRepository(session)

                    # Empty database, any filter should return []
                    results = await repo.find(url=nonexistent_url)
                    assert results == [], f"Expected empty list, got {results}"

                    await session.rollback()
            finally:
                await engine.dispose()

        asyncio.run(_run())

    @settings(max_examples=200)
    @given(
        package_name=_safe_text(),
        version=_safe_text(),
    )
    def test_find_nonexistent_package_returns_empty_list(
        self,
        package_name: str,
        version: str,
    ) -> None:
        """Find with non-matching package filters returns empty list.

        **Validates: Requirements 3.11**
        """

        async def _run() -> None:
            factory, engine = await _setup_engine()
            try:
                async with factory() as session:
                    repo = PackageRepository(session)

                    # Empty database, any filter should return []
                    results = await repo.find(
                        package_name=package_name,
                        version=version,
                    )
                    assert results == [], f"Expected empty list, got {results}"

                    await session.rollback()
            finally:
                await engine.dispose()

        asyncio.run(_run())


@pytest.mark.unit
@pytest.mark.storage
class TestMissingEntityLookupRaisesError:
    """Property 8: Missing Entity Lookup Raises StorageError.

    For any surrogate key value that does not exist in the database,
    repository.get_by_id(key) raises EntityNotFoundError identifying
    the entity type, key name, and requested key value.
    """

    @settings(max_examples=200)
    @given(
        nonexistent_id=st.integers(min_value=1, max_value=2**31),
    )
    def test_get_by_id_nonexistent_raises_entity_not_found(
        self,
        nonexistent_id: int,
    ) -> None:
        """get_by_id with non-existent key raises EntityNotFoundError.

        **Validates: Requirements 3.7**
        """

        async def _run() -> None:
            factory, engine = await _setup_engine()
            try:
                async with factory() as session:
                    repo = RepositoryFileRepository(session)

                    with pytest.raises(EntityNotFoundError) as exc_info:
                        await repo.get_by_id(nonexistent_id)

                    error = exc_info.value
                    assert error.entity_type == "RepositoryFile"
                    assert error.key_name == "id"
                    assert error.key_value == nonexistent_id

                    await session.rollback()
            finally:
                await engine.dispose()

        asyncio.run(_run())

    @settings(max_examples=200)
    @given(
        nonexistent_id=st.integers(min_value=1, max_value=2**31),
    )
    def test_package_get_by_id_nonexistent_raises_entity_not_found(
        self,
        nonexistent_id: int,
    ) -> None:
        """PackageRepository.get_by_id with non-existent key raises EntityNotFoundError.

        **Validates: Requirements 3.7**
        """

        async def _run() -> None:
            factory, engine = await _setup_engine()
            try:
                async with factory() as session:
                    repo = PackageRepository(session)

                    with pytest.raises(EntityNotFoundError) as exc_info:
                        await repo.get_by_id(nonexistent_id)

                    error = exc_info.value
                    assert error.entity_type == "PackageInstance"
                    assert error.key_name == "id"
                    assert error.key_value == nonexistent_id

                    await session.rollback()
            finally:
                await engine.dispose()

        asyncio.run(_run())
