"""Unit tests for SqlAlchemyMirrorFileRepository.

Requirements: 5.1, 5.2
"""

from __future__ import annotations

import string
from datetime import UTC, datetime

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from debcraft.infrastructure.indexer.mirror_file_repository import (
    IndexingRecordInfo,
    RepositoryFileInfo,
    SqlAlchemyMirrorFileRepository,
)
from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.models.metadata import IndexingRecord
from debcraft.infrastructure.models.mirror import RepositoryFile, RepositoryFileState


async def _create_mirror_factory() -> async_sessionmaker[AsyncSession]:
    """Create an in-memory SQLite engine with mirror tables."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _create_metadata_factory() -> async_sessionmaker[AsyncSession]:
    """Create an in-memory SQLite engine with metadata tables."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed_mirror_file(
    factory: async_sessionmaker[AsyncSession],
    *,
    url: str = "https://deb.debian.org/debian/dists/bookworm/main/binary-amd64/Packages.gz",
    sha256: str = "abc123",
    size_bytes: int = 1024,
    state: RepositoryFileState = RepositoryFileState.VERIFIED,
    local_path: str = "/cache/Packages.gz",
) -> int:
    """Insert a RepositoryFile and return its ID."""
    async with factory() as session:
        entity = RepositoryFile(
            url=url,
            sha256=sha256,
            size_bytes=size_bytes,
            state=state,
            retry_count=0,
            local_path=local_path,
        )
        session.add(entity)
        await session.commit()
        return entity.id


@pytest.mark.unit
@pytest.mark.database
class TestGetVerifiedFiles:
    """Tests for get_verified_files method."""

    @pytest.mark.asyncio
    async def test_returns_verified_files(self) -> None:
        """Files in VERIFIED state are returned."""
        mirror_factory = await _create_mirror_factory()
        metadata_factory = await _create_metadata_factory()
        repo = SqlAlchemyMirrorFileRepository(mirror_factory, metadata_factory)

        file_id = await _seed_mirror_file(mirror_factory)

        result = await repo.get_verified_files()

        assert len(result) == 1
        assert isinstance(result[0], RepositoryFileInfo)
        assert result[0].id == file_id
        assert result[0].sha256 == "abc123"
        assert result[0].local_path == "/cache/Packages.gz"

    @pytest.mark.asyncio
    async def test_excludes_non_actionable_files_but_includes_indexed(self) -> None:
        """Files in non-actionable states are excluded; INDEXED files are included."""
        mirror_factory = await _create_mirror_factory()
        metadata_factory = await _create_metadata_factory()
        repo = SqlAlchemyMirrorFileRepository(mirror_factory, metadata_factory)

        await _seed_mirror_file(
            mirror_factory,
            url="https://example.com/downloaded",
            state=RepositoryFileState.DOWNLOADED,
        )
        await _seed_mirror_file(
            mirror_factory,
            url="https://example.com/indexed",
            state=RepositoryFileState.INDEXED,
        )

        result = await repo.get_verified_files()
        assert len(result) == 1
        assert result[0].url == "https://example.com/indexed"

    @pytest.mark.asyncio
    async def test_filters_by_repository_name(self) -> None:
        """When repository_name is provided, only matching URLs are returned."""
        mirror_factory = await _create_mirror_factory()
        metadata_factory = await _create_metadata_factory()
        repo = SqlAlchemyMirrorFileRepository(mirror_factory, metadata_factory)

        await _seed_mirror_file(
            mirror_factory,
            url="https://deb.debian.org/debian/dists/bookworm/main/Packages.gz",
        )
        await _seed_mirror_file(
            mirror_factory,
            url="https://archive.ubuntu.com/ubuntu/dists/jammy/main/Packages.gz",
        )

        result = await repo.get_verified_files(repository_name="debian")
        assert len(result) == 1
        assert "debian" in result[0].url

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_verified(self) -> None:
        """Returns empty list when no VERIFIED files exist."""
        mirror_factory = await _create_mirror_factory()
        metadata_factory = await _create_metadata_factory()
        repo = SqlAlchemyMirrorFileRepository(mirror_factory, metadata_factory)

        result = await repo.get_verified_files()
        assert result == []


@pytest.mark.unit
@pytest.mark.database
class TestGetIndexingRecord:
    """Tests for get_indexing_record method."""

    @pytest.mark.asyncio
    async def test_returns_none_when_not_indexed(self) -> None:
        """Returns None when no indexing record exists for file_id."""
        mirror_factory = await _create_mirror_factory()
        metadata_factory = await _create_metadata_factory()
        repo = SqlAlchemyMirrorFileRepository(mirror_factory, metadata_factory)

        result = await repo.get_indexing_record(file_id=999)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_record_when_exists(self) -> None:
        """Returns IndexingRecordInfo when an indexing record exists."""
        mirror_factory = await _create_mirror_factory()
        metadata_factory = await _create_metadata_factory()
        repo = SqlAlchemyMirrorFileRepository(mirror_factory, metadata_factory)

        # Seed an indexing record
        async with metadata_factory() as session:
            record = IndexingRecord(
                repository_file_id=42,
                parser_version=1,
                indexed_sha256="deadbeef",
                indexed_at=datetime(2024, 1, 1, tzinfo=UTC),
            )
            session.add(record)
            await session.commit()

        result = await repo.get_indexing_record(file_id=42)
        assert result is not None
        assert isinstance(result, IndexingRecordInfo)
        assert result.repository_file_id == 42
        assert result.parser_version == 1
        assert result.indexed_sha256 == "deadbeef"


@pytest.mark.unit
@pytest.mark.database
class TestMarkIndexed:
    """Tests for mark_indexed method."""

    @pytest.mark.asyncio
    async def test_creates_indexing_record(self) -> None:
        """Creates a new IndexingRecord when none exists."""
        mirror_factory = await _create_mirror_factory()
        metadata_factory = await _create_metadata_factory()
        repo = SqlAlchemyMirrorFileRepository(mirror_factory, metadata_factory)

        file_id = await _seed_mirror_file(mirror_factory)

        await repo.mark_indexed(file_id=file_id, parser_version=1, sha256="abc123")

        # Verify indexing record was created
        result = await repo.get_indexing_record(file_id=file_id)
        assert result is not None
        assert result.parser_version == 1
        assert result.indexed_sha256 == "abc123"

    @pytest.mark.asyncio
    async def test_updates_existing_indexing_record(self) -> None:
        """Updates an existing IndexingRecord on re-index."""
        mirror_factory = await _create_mirror_factory()
        metadata_factory = await _create_metadata_factory()
        repo = SqlAlchemyMirrorFileRepository(mirror_factory, metadata_factory)

        file_id = await _seed_mirror_file(mirror_factory)

        # First indexing
        await repo.mark_indexed(file_id=file_id, parser_version=1, sha256="abc123")
        # Re-index with new parser version
        await repo.mark_indexed(file_id=file_id, parser_version=2, sha256="def456")

        result = await repo.get_indexing_record(file_id=file_id)
        assert result is not None
        assert result.parser_version == 2
        assert result.indexed_sha256 == "def456"

    @pytest.mark.asyncio
    async def test_transitions_file_state_to_indexed(self) -> None:
        """Transitions the RepositoryFile state to INDEXED in mirror.db."""
        mirror_factory = await _create_mirror_factory()
        metadata_factory = await _create_metadata_factory()
        repo = SqlAlchemyMirrorFileRepository(mirror_factory, metadata_factory)

        file_id = await _seed_mirror_file(mirror_factory)

        await repo.mark_indexed(file_id=file_id, parser_version=1, sha256="abc123")

        # Verify state transition
        async with mirror_factory() as session:
            from sqlalchemy import select

            stmt = select(RepositoryFile).where(RepositoryFile.id == file_id)
            result = await session.execute(stmt)
            entity = result.scalar_one()
            assert entity.state == RepositoryFileState.INDEXED


# --- Bug Condition Exploration Test (Property-Based) ---
# This test is EXPECTED TO FAIL on unfixed code, confirming the bug exists.


def _metadata_url_strategy() -> st.SearchStrategy[str]:
    """Generate URLs containing metadata file keywords."""
    metadata_keywords = st.sampled_from(["Packages.gz", "Sources.gz", "Contents", "Release"])
    repo_name = st.sampled_from(["debian", "ubuntu", "bookworm", "jammy"])
    return st.builds(
        lambda repo, keyword: f"https://deb.example.org/{repo}/dists/main/{keyword}",
        repo_name,
        metadata_keywords,
    )


def _sha256_strategy() -> st.SearchStrategy[str]:
    """Generate valid SHA256 hex strings (64 hex characters)."""
    return st.text(alphabet=string.hexdigits.lower(), min_size=64, max_size=64)


def _size_bytes_strategy() -> st.SearchStrategy[int]:
    """Generate positive file sizes."""
    return st.integers(min_value=1, max_value=10**9)


@pytest.mark.unit
@pytest.mark.database
class TestBugConditionIndexedFilesInvisible:
    """Property test: INDEXED metadata files should be returned by get_verified_files().

    **Validates: Requirements 1.1, 1.2, 2.2**

    This test encodes the EXPECTED (correct) behavior: files with state=INDEXED
    should appear in the results of get_verified_files(). On unfixed code, this
    test FAILS because get_verified_files() only queries state==VERIFIED.

    The failure confirms the bug exists — INDEXED files are invisible to the indexer.
    """

    @pytest.mark.asyncio
    @given(
        url=_metadata_url_strategy(),
        sha256=_sha256_strategy(),
        size_bytes=_size_bytes_strategy(),
    )
    async def test_indexed_files_returned_by_get_verified_files(
        self,
        url: str,
        sha256: str,
        size_bytes: int,
    ) -> None:
        """Property: for all INDEXED files seeded into mirror DB, get_verified_files() returns them.

        **Validates: Requirements 1.1, 1.2**

        This property asserts the expected behavior: any file in INDEXED state
        should be visible to get_verified_files(). On unfixed code, Hypothesis
        will find a counterexample (any INDEXED file) proving the bug.
        """
        mirror_factory = await _create_mirror_factory()
        metadata_factory = await _create_metadata_factory()
        repo = SqlAlchemyMirrorFileRepository(mirror_factory, metadata_factory)

        # Seed a file with state=INDEXED (simulating a previously indexed metadata file)
        file_id = await _seed_mirror_file(
            mirror_factory,
            url=url,
            sha256=sha256,
            size_bytes=size_bytes,
            state=RepositoryFileState.INDEXED,
            local_path=f"/cache/{url.split('/')[-1]}",
        )

        # Property: get_verified_files() MUST return INDEXED files
        result = await repo.get_verified_files()

        # Assert the INDEXED file appears in results
        result_ids = [f.id for f in result]
        assert file_id in result_ids, (
            f"Bug confirmed: INDEXED file (id={file_id}, url={url}) is NOT returned "
            f"by get_verified_files(). The query only matches state==VERIFIED, "
            f"making previously-indexed metadata files invisible to the indexer."
        )


# ---------------------------------------------------------------------------
# Preservation Property Tests (Task 2)
# ---------------------------------------------------------------------------


# Strategies for generating random file attributes
_sha256_strategy = st.text(alphabet=string.hexdigits, min_size=64, max_size=64)
_size_bytes_strategy = st.integers(min_value=1, max_value=10**9)

# Non-actionable states: files in these states should never be returned
_NON_ACTIONABLE_STATES = [
    RepositoryFileState.DISCOVERED,
    RepositoryFileState.QUEUED,
    RepositoryFileState.DOWNLOADING,
    RepositoryFileState.DOWNLOADED,
    RepositoryFileState.FAILED,
]


@pytest.mark.unit
@pytest.mark.database
class TestPreservationProperties:
    """Preservation property tests for get_verified_files().

    **Validates: Requirements 3.1, 3.2, 3.5**

    These tests establish baseline behavior that must be preserved after the fix:
    - Non-actionable file states are NEVER returned by get_verified_files()
    - VERIFIED files are ALWAYS returned by get_verified_files()
    """

    @settings(
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        sha256=_sha256_strategy,
        size_bytes=_size_bytes_strategy,
        state=st.sampled_from(_NON_ACTIONABLE_STATES),
        uuid=st.uuids(),
    )
    @pytest.mark.asyncio
    async def test_non_actionable_states_excluded(
        self,
        sha256: str,
        size_bytes: int,
        state: RepositoryFileState,
        uuid: object,
    ) -> None:
        """Files with non-actionable states are never returned by get_verified_files().

        **Validates: Requirements 3.1, 3.2, 3.5**

        Property: for all generated files with state in
        {DISCOVERED, QUEUED, DOWNLOADING, DOWNLOADED, FAILED},
        get_verified_files() never includes them in results.
        """
        mirror_factory = await _create_mirror_factory()
        metadata_factory = await _create_metadata_factory()
        repo = SqlAlchemyMirrorFileRepository(mirror_factory, metadata_factory)

        url = f"https://{uuid}.example.com/repo/Packages.gz"
        await _seed_mirror_file(
            mirror_factory,
            url=url,
            sha256=sha256,
            size_bytes=size_bytes,
            state=state,
            local_path="/cache/Packages.gz",
        )

        result = await repo.get_verified_files()

        # Non-actionable state files must NEVER appear in results
        returned_urls = [f.url for f in result]
        assert url not in returned_urls, (
            f"File with state={state.value} was returned by get_verified_files() but should be excluded. URL: {url}"
        )

    @settings(
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        sha256=_sha256_strategy,
        size_bytes=_size_bytes_strategy,
        uuid=st.uuids(),
    )
    @pytest.mark.asyncio
    async def test_verified_files_always_returned(
        self,
        sha256: str,
        size_bytes: int,
        uuid: object,
    ) -> None:
        """Files with state=VERIFIED are always returned by get_verified_files().

        **Validates: Requirements 3.1, 3.2, 3.5**

        Property: for all generated VERIFIED files,
        get_verified_files() always includes them in results.
        """
        mirror_factory = await _create_mirror_factory()
        metadata_factory = await _create_metadata_factory()
        repo = SqlAlchemyMirrorFileRepository(mirror_factory, metadata_factory)

        url = f"https://{uuid}.example.com/repo/Packages.gz"
        await _seed_mirror_file(
            mirror_factory,
            url=url,
            sha256=sha256,
            size_bytes=size_bytes,
            state=RepositoryFileState.VERIFIED,
            local_path="/cache/Packages.gz",
        )

        result = await repo.get_verified_files()

        # VERIFIED files must ALWAYS appear in results
        returned_urls = [f.url for f in result]
        assert url in returned_urls, (
            f"File with state=VERIFIED was NOT returned by get_verified_files() but should be included. URL: {url}"
        )
