"""Property-based tests for recovery mechanisms.

**Validates: Requirements 7.1, 7.3, 7.5, 10.5, 10.6**

Property 18: Download Recovery State Machine — For any RepositoryFile in
DOWNLOADING state when the StorageEngine initializes: if retry_count < 3,
the state transitions to QUEUED with retry_count incremented by 1; if
retry_count >= 3, the state transitions to FAILED.

Property 19: Cache Integrity Verification — For any file in the mirror cache
directory whose computed SHA256 does not match the stored checksum in mirror.db,
the file is removed from the filesystem during initialization.

Property 20: Cache Corruption Marking — For any cache.db entry detected as
corrupt (SHA256 mismatch), the entry is marked as requiring recomputation
(valid=False) rather than raising an error.

Property 23: cache.db Deletion Recovery — Deleting cache.db and reinitializing
the StorageEngine succeeds without error, recreating an empty cache.db, and
does not affect data in mirror.db or metadata.db.

Property 24: Cache/Metadata Conflict Resolution — When conflicting entries
exist in cache.db vs metadata.db, cache entries are marked invalid while
metadata remains unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.models.cache import ChecksumCache
from debcraft.infrastructure.models.mirror import RepositoryFile, RepositoryFileState
from debcraft.infrastructure.repositories.repository_file import RepositoryFileRepository
from debcraft.platform.contracts.persistence import DatabaseProvider

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _sha256_of(content: bytes) -> str:
    """Compute SHA256 hex digest of content."""
    return hashlib.sha256(content).hexdigest()


class _FakeDbProvider(DatabaseProvider):
    """A fake DatabaseProvider backed by in-memory SQLite engines."""

    def __init__(self) -> None:
        self._engines: dict[str, object] = {}
        self._factories: dict[str, async_sessionmaker[AsyncSession]] = {}

    async def add_database(self, db_name: str, db_path: str = "") -> None:
        """Create an in-memory engine and session factory for the given database name.

        The db_path parameter is accepted for API compatibility but ignored;
        all databases use in-memory SQLite for speed.
        """
        url = "sqlite+aiosqlite://"
        engine = create_async_engine(url, echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        self._engines[db_name] = engine
        self._factories[db_name] = factory

    async def get_session(self, db_name: str) -> AsyncSession:  # type: ignore[override]
        """Return a new session for the named database."""
        if db_name not in self._factories:
            msg = f"Database '{db_name}' not configured in fake provider"
            raise RuntimeError(msg)
        return self._factories[db_name]()

    async def dispose(self) -> None:
        """Dispose all engines."""
        for engine in self._engines.values():
            await engine.dispose()  # type: ignore[union-attr]
        self._engines.clear()
        self._factories.clear()

    async def health_check(self) -> dict[str, bool]:
        """Return health status for all databases."""
        return dict.fromkeys(self._engines, True)


class _FileDbProvider(DatabaseProvider):
    """A fake DatabaseProvider backed by file-based SQLite engines.

    Used only for tests that require actual file existence checks (e.g.
    cache.db deletion and recreation).
    """

    def __init__(self) -> None:
        self._engines: dict[str, object] = {}
        self._factories: dict[str, async_sessionmaker[AsyncSession]] = {}

    async def add_database(self, db_name: str, db_path: str) -> None:
        """Create a file-based engine and session factory."""
        url = f"sqlite+aiosqlite:///{db_path}"
        engine = create_async_engine(url, echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        self._engines[db_name] = engine
        self._factories[db_name] = factory

    async def get_session(self, db_name: str) -> AsyncSession:  # type: ignore[override]
        """Return a new session for the named database."""
        if db_name not in self._factories:
            msg = f"Database '{db_name}' not configured in file provider"
            raise RuntimeError(msg)
        return self._factories[db_name]()

    async def dispose(self) -> None:
        """Dispose all engines."""
        for engine in self._engines.values():
            await engine.dispose()  # type: ignore[union-attr]
        self._engines.clear()
        self._factories.clear()

    async def health_check(self) -> dict[str, bool]:
        """Return health status for all databases."""
        return dict.fromkeys(self._engines, True)


def _make_mock_provider(base_path: Path) -> AsyncMock:
    """Create a mock storage provider pointing at base_path."""
    provider = AsyncMock()
    provider.resolve_path = lambda purpose, relative="": base_path / purpose
    provider.create_directory = AsyncMock()
    provider.remove_matching = AsyncMock()
    provider.check_writable = AsyncMock(return_value=True)
    return provider


@pytest.mark.unit
@pytest.mark.storage
class TestDownloadRecoveryStateMachine:
    """Property 18: Download Recovery State Machine.

    For any RepositoryFile in DOWNLOADING state when the StorageEngine initializes:
    if retry_count < 3, the state transitions to QUEUED with retry_count incremented;
    if retry_count >= 3, the state transitions to FAILED.
    """

    @settings(max_examples=200, deadline=None)
    @given(
        retry_count=st.integers(min_value=0, max_value=5),
    )
    def test_download_recovery_transitions(self, retry_count: int) -> None:
        """Verify DOWNLOADING entries transition correctly based on retry_count.

        **Validates: Requirements 7.1**
        """

        async def _run() -> None:
            from debcraft.infrastructure.storage.engine import DefaultStorageEngine

            with tempfile.TemporaryDirectory() as tmp_dir:
                base_path = Path(tmp_dir)

                db_provider = _FakeDbProvider()
                await db_provider.add_database("mirror")

                # Insert a DOWNLOADING entry
                session = await db_provider.get_session("mirror")
                try:
                    await session.begin()
                    entry = RepositoryFile(
                        url=f"https://example.com/file_{retry_count}.deb",
                        sha256="a" * 64,
                        size_bytes=1024,
                        state=RepositoryFileState.DOWNLOADING,
                        retry_count=retry_count,
                        local_path=None,
                    )
                    session.add(entry)
                    await session.commit()
                finally:
                    await session.close()

                # Create engine and invoke recovery
                provider = _make_mock_provider(base_path)
                event_bus = AsyncMock()
                event_bus.publish = AsyncMock()

                engine = DefaultStorageEngine(
                    provider=provider,
                    event_bus=event_bus,
                    db_provider=db_provider,
                )
                await engine._recover_interrupted_downloads()

                # Verify transitions
                session2 = await db_provider.get_session("mirror")
                try:
                    repo = RepositoryFileRepository(session2)
                    all_entries = await repo.find()
                    assert len(all_entries) == 1
                    recovered = all_entries[0]

                    if retry_count < 3:
                        assert recovered.state == RepositoryFileState.QUEUED, (
                            f"Expected QUEUED for retry_count={retry_count}, got {recovered.state}"
                        )
                        assert recovered.retry_count == retry_count + 1, (
                            f"Expected retry_count={retry_count + 1}, got {recovered.retry_count}"
                        )
                    else:
                        assert recovered.state == RepositoryFileState.FAILED, (
                            f"Expected FAILED for retry_count={retry_count}, got {recovered.state}"
                        )
                finally:
                    await session2.close()

                await db_provider.dispose()

        asyncio.run(_run())


@pytest.mark.unit
@pytest.mark.storage
class TestCacheIntegrityVerification:
    """Property 19: Cache Integrity Verification.

    For any file in the mirror cache directory whose computed SHA256 does not
    match the stored checksum in mirror.db, the file is removed from the
    filesystem during initialization.
    """

    @settings(max_examples=20, deadline=None)
    @given(
        file_content=st.binary(min_size=1, max_size=100),
        mismatch=st.booleans(),
    )
    def test_mismatched_files_removed(self, file_content: bytes, mismatch: bool) -> None:
        """Files with SHA256 mismatch are removed; matching files are kept.

        **Validates: Requirements 7.5**
        """

        async def _run() -> None:
            from debcraft.infrastructure.storage.engine import DefaultStorageEngine

            with tempfile.TemporaryDirectory() as tmp_dir:
                base_path = Path(tmp_dir)
                mirror_dir = base_path / "mirror"
                mirror_dir.mkdir(parents=True)

                file_path = mirror_dir / "test_file.deb"
                file_path.write_bytes(file_content)
                actual_sha256 = _sha256_of(file_content)

                stored_sha256 = "b" * 64 if mismatch else actual_sha256

                db_provider = _FakeDbProvider()
                await db_provider.add_database("mirror")
                await db_provider.add_database("cache")

                session = await db_provider.get_session("mirror")
                try:
                    await session.begin()
                    entry = RepositoryFile(
                        url="https://example.com/test_file.deb",
                        sha256=stored_sha256,
                        size_bytes=len(file_content),
                        state=RepositoryFileState.DOWNLOADED,
                        retry_count=0,
                        local_path=str(file_path),
                    )
                    session.add(entry)
                    await session.commit()
                finally:
                    await session.close()

                provider = _make_mock_provider(base_path)
                event_bus = AsyncMock()
                event_bus.publish = AsyncMock()

                engine = DefaultStorageEngine(
                    provider=provider,
                    event_bus=event_bus,
                    db_provider=db_provider,
                )
                await engine._verify_cache_integrity()

                if mismatch:
                    assert not file_path.exists(), "File with SHA256 mismatch should have been removed"
                else:
                    assert file_path.exists(), "File with matching SHA256 should NOT have been removed"

                await db_provider.dispose()

        asyncio.run(_run())


@pytest.mark.unit
@pytest.mark.storage
class TestCacheCorruptionMarking:
    """Property 20: Cache Corruption Marking.

    For any cache.db entry detected as corrupt (SHA256 mismatch between stored
    and computed values), the entry is marked as valid=False rather than raising
    an error to the caller.
    """

    @settings(max_examples=20, deadline=None)
    @given(
        file_content=st.binary(min_size=1, max_size=100),
    )
    def test_cache_entries_marked_invalid_on_mismatch(self, file_content: bytes) -> None:
        """Cache entries are marked valid=False when integrity mismatch is detected.

        **Validates: Requirements 7.3**
        """

        async def _run() -> None:
            from debcraft.infrastructure.storage.engine import DefaultStorageEngine

            with tempfile.TemporaryDirectory() as tmp_dir:
                base_path = Path(tmp_dir)
                mirror_dir = base_path / "mirror"
                mirror_dir.mkdir(parents=True)

                file_path = mirror_dir / "cached_file.deb"
                file_path.write_bytes(file_content)

                actual_sha256 = _sha256_of(file_content)
                wrong_sha256 = "c" * 64

                db_provider = _FakeDbProvider()
                await db_provider.add_database("mirror")
                await db_provider.add_database("cache")

                session = await db_provider.get_session("mirror")
                try:
                    await session.begin()
                    entry = RepositoryFile(
                        url="https://example.com/cached_file.deb",
                        sha256=wrong_sha256,
                        size_bytes=len(file_content),
                        state=RepositoryFileState.DOWNLOADED,
                        retry_count=0,
                        local_path=str(file_path),
                    )
                    session.add(entry)
                    await session.commit()
                finally:
                    await session.close()

                cache_session = await db_provider.get_session("cache")
                try:
                    await cache_session.begin()
                    cache_entry = ChecksumCache(
                        content_sha256=actual_sha256,
                        computed_hash=actual_sha256,
                        valid=True,
                    )
                    cache_session.add(cache_entry)
                    await cache_session.commit()
                finally:
                    await cache_session.close()

                provider = _make_mock_provider(base_path)
                event_bus = AsyncMock()
                event_bus.publish = AsyncMock()

                engine = DefaultStorageEngine(
                    provider=provider,
                    event_bus=event_bus,
                    db_provider=db_provider,
                )

                # Should NOT raise — marks entries as invalid instead
                await engine._verify_cache_integrity()

                cache_session2 = await db_provider.get_session("cache")
                try:
                    result = await cache_session2.execute(
                        select(ChecksumCache).where(ChecksumCache.content_sha256 == actual_sha256)
                    )
                    cache_row = result.scalar_one_or_none()
                    assert cache_row is not None, "Cache entry should still exist"
                    assert cache_row.valid is False, "Cache entry should be marked valid=False after integrity mismatch"
                finally:
                    await cache_session2.close()

                await db_provider.dispose()

        asyncio.run(_run())


@pytest.mark.unit
@pytest.mark.storage
class TestCacheDbDeletionRecovery:
    """Property 23: cache.db Deletion Recovery.

    Deleting cache.db and reinitializing the StorageEngine succeeds without
    error, recreating an empty cache.db, and does not affect data in mirror.db
    or metadata.db.
    """

    @settings(max_examples=20, deadline=None)
    @given(
        url_suffix=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=1,
            max_size=20,
        ),
    )
    def test_cache_db_deletion_recovery(self, url_suffix: str) -> None:
        """Deleting cache.db and reinitializing succeeds; mirror/metadata unaffected.

        **Validates: Requirements 10.5**
        """

        async def _run() -> None:
            from debcraft.infrastructure.storage.engine import DefaultStorageEngine

            with tempfile.TemporaryDirectory() as tmp_dir:
                base_path = Path(tmp_dir)
                mirror_db_path = base_path / "mirror.db"
                cache_db_path = base_path / "cache.db"

                db_provider = _FileDbProvider()
                await db_provider.add_database("mirror", str(mirror_db_path))
                await db_provider.add_database("cache", str(cache_db_path))

                # Insert data into mirror.db
                session = await db_provider.get_session("mirror")
                try:
                    await session.begin()
                    entry = RepositoryFile(
                        url=f"https://example.com/{url_suffix}.deb",
                        sha256="a" * 64,
                        size_bytes=512,
                        state=RepositoryFileState.VERIFIED,
                        retry_count=0,
                        local_path=None,
                    )
                    session.add(entry)
                    await session.commit()
                finally:
                    await session.close()

                await db_provider.dispose()

                # Delete cache.db
                assert cache_db_path.exists()
                cache_db_path.unlink()
                assert not cache_db_path.exists()

                # Re-create provider — SQLite will recreate cache.db
                db_provider2 = _FileDbProvider()
                await db_provider2.add_database("mirror", str(mirror_db_path))
                await db_provider2.add_database("cache", str(cache_db_path))

                provider = _make_mock_provider(base_path)
                event_bus = AsyncMock()
                event_bus.publish = AsyncMock()

                engine = DefaultStorageEngine(
                    provider=provider,
                    event_bus=event_bus,
                    db_provider=db_provider2,
                )

                # Should not raise
                await engine.initialize()

                # cache.db recreated
                assert cache_db_path.exists(), "cache.db should be recreated"

                # mirror.db unaffected
                mirror_session = await db_provider2.get_session("mirror")
                try:
                    repo = RepositoryFileRepository(mirror_session)
                    all_entries = await repo.find()
                    assert len(all_entries) == 1
                    assert all_entries[0].url == f"https://example.com/{url_suffix}.deb"
                finally:
                    await mirror_session.close()

                await db_provider2.dispose()

        asyncio.run(_run())


@pytest.mark.unit
@pytest.mark.storage
class TestCacheMetadataConflictResolution:
    """Property 24: Cache/Metadata Conflict Resolution.

    When conflicting entries exist — cache.db has a value that differs from
    metadata.db for the same entity — the cache entry is marked invalid
    while metadata remains authoritative and unchanged.
    """

    @settings(max_examples=20, deadline=None)
    @given(
        cached_hash=st.text(
            alphabet="0123456789abcdef",
            min_size=64,
            max_size=64,
        ),
        file_content=st.binary(min_size=1, max_size=50),
    )
    def test_cache_marked_invalid_metadata_unchanged(
        self,
        cached_hash: str,
        file_content: bytes,
    ) -> None:
        """Conflicting cache entries are marked invalid; metadata is unchanged.

        **Validates: Requirements 10.6**
        """

        async def _run() -> None:
            from debcraft.infrastructure.storage.engine import DefaultStorageEngine

            with tempfile.TemporaryDirectory() as tmp_dir:
                base_path = Path(tmp_dir)
                mirror_dir = base_path / "mirror"
                mirror_dir.mkdir(parents=True)

                file_path = mirror_dir / "conflict_file.deb"
                file_path.write_bytes(file_content)
                actual_sha256 = _sha256_of(file_content)

                # Use a stored SHA that always differs from actual file content
                stored_mirror_sha = "f" * 64
                if stored_mirror_sha == actual_sha256:
                    stored_mirror_sha = "e" * 64

                db_provider = _FakeDbProvider()
                await db_provider.add_database("mirror")
                await db_provider.add_database("cache")

                session = await db_provider.get_session("mirror")
                try:
                    await session.begin()
                    entry = RepositoryFile(
                        url="https://example.com/conflict_file.deb",
                        sha256=stored_mirror_sha,
                        size_bytes=len(file_content),
                        state=RepositoryFileState.DOWNLOADED,
                        retry_count=0,
                        local_path=str(file_path),
                    )
                    session.add(entry)
                    await session.commit()
                finally:
                    await session.close()

                cache_session = await db_provider.get_session("cache")
                try:
                    await cache_session.begin()
                    cache_entry = ChecksumCache(
                        content_sha256=cached_hash,
                        computed_hash=cached_hash,
                        valid=True,
                    )
                    cache_session.add(cache_entry)
                    await cache_session.commit()
                finally:
                    await cache_session.close()

                provider = _make_mock_provider(base_path)
                event_bus = AsyncMock()
                event_bus.publish = AsyncMock()

                engine = DefaultStorageEngine(
                    provider=provider,
                    event_bus=event_bus,
                    db_provider=db_provider,
                )

                await engine._verify_cache_integrity()

                # Cache entry is marked invalid
                cache_session2 = await db_provider.get_session("cache")
                try:
                    result = await cache_session2.execute(
                        select(ChecksumCache).where(ChecksumCache.content_sha256 == cached_hash)
                    )
                    cache_row = result.scalar_one_or_none()
                    assert cache_row is not None, "Cache entry should still exist"
                    assert cache_row.valid is False, "Cache entry should be marked invalid after conflict"
                finally:
                    await cache_session2.close()

                # Mirror entry unchanged (authoritative)
                mirror_session2 = await db_provider.get_session("mirror")
                try:
                    repo = RepositoryFileRepository(mirror_session2)
                    entries = await repo.find()
                    assert len(entries) == 1
                    assert entries[0].sha256 == stored_mirror_sha, (
                        "Mirror DB entry should remain unchanged (authoritative)"
                    )
                finally:
                    await mirror_session2.close()

                await db_provider.dispose()

        asyncio.run(_run())
