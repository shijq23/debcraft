"""Unit tests for artifact deduplication in MirrorEngine._stage_artifacts.

Validates that arch-independent packages (_all.deb) appearing in multiple
architecture indexes are deduplicated before batch download, preventing
concurrent writes to the same destination path.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from debcraft.domain.mirror.values import DownloadResult, FileEntry
from debcraft.infrastructure.mirror.engine import MirrorEngine
from debcraft.infrastructure.models.base import Base

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _setup_db() -> tuple[async_sessionmaker[AsyncSession], object]:
    """Create in-memory SQLite engine with all tables."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


def _make_engine(session_factory: async_sessionmaker[AsyncSession]) -> MirrorEngine:
    """Create a MirrorEngine with mocked dependencies but a real DB session factory."""
    db_provider = MagicMock()

    async def get_session(name: str) -> AsyncSession:
        return session_factory()

    db_provider.get_session = AsyncMock(side_effect=get_session)

    storage_engine = MagicMock()
    storage_engine.get_path = MagicMock(return_value=Path("/tmp/mirror"))

    event_bus = MagicMock()
    cancellation_token = MagicMock()
    cancellation_token.is_cancelled = False
    progress = MagicMock()
    logger = MagicMock()

    download_coordinator = MagicMock()
    download_coordinator._config = MagicMock()
    download_coordinator._config.max_connections_per_repo = 10

    engine = MirrorEngine(
        download_coordinator=download_coordinator,
        db_provider=db_provider,
        storage_engine=storage_engine,
        event_bus=event_bus,
        cancellation_token=cancellation_token,
        progress=progress,
        logger=logger,
    )
    engine._session_id = "test-session"
    return engine


def _make_config() -> MagicMock:
    """Create a mock RepositoryConfig."""
    config = MagicMock()
    config.base_url = "https://mirror.example.com/repo"
    config.name = "test-repo"
    return config


def _make_file_entry(relative_path: str, sha256: str = "", size_bytes: int = 1024) -> FileEntry:
    """Create a FileEntry with sensible defaults."""
    if not sha256:
        # Generate a deterministic sha256 from the path
        import hashlib

        sha256 = hashlib.sha256(relative_path.encode()).hexdigest()
    return FileEntry(relative_path=relative_path, sha256=sha256, size_bytes=size_bytes)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestArtifactDeduplication:
    """Tests for duplicate artifact entry filtering in _stage_artifacts."""

    def test_duplicate_all_deb_entries_are_deduplicated(self) -> None:
        """Identical relative_path entries are reduced to one download task.

        Simulates an _all.deb package appearing in both binary-amd64
        and binary-arm64 Packages indexes.
        """

        async def _run() -> None:
            factory, db_engine = await _setup_db()
            try:
                engine = _make_engine(factory)
                config = _make_config()

                # Same package listed twice (from two arch indexes)
                entries = [
                    _make_file_entry("pool/main/c/chromium/chromium-l10n_151_all.deb"),
                    _make_file_entry("pool/main/c/chromium/chromium-l10n_151_all.deb"),
                ]

                # Mock download_batch to capture what tasks it receives
                captured_tasks: list = []

                async def mock_download_batch(tasks, max_concurrent):
                    captured_tasks.extend(tasks)
                    return [
                        DownloadResult(
                            url=t.url,
                            success=True,
                            sha256_verified=True,
                            bytes_transferred=1024,
                        )
                        for t in tasks
                    ]

                engine._download_coordinator.download_batch = AsyncMock(side_effect=mock_download_batch)

                await engine._stage_artifacts(config, entries)

                # Should only create 1 download task, not 2
                assert len(captured_tasks) == 1
                assert "chromium-l10n_151_all.deb" in captured_tasks[0].url
            finally:
                await db_engine.dispose()

        asyncio.run(_run())

    def test_unique_entries_are_not_filtered(self) -> None:
        """Entries with distinct relative_paths are all preserved."""

        async def _run() -> None:
            factory, db_engine = await _setup_db()
            try:
                engine = _make_engine(factory)
                config = _make_config()

                entries = [
                    _make_file_entry("pool/main/c/chromium/chromium_151_amd64.deb"),
                    _make_file_entry("pool/main/c/chromium/chromium_151_arm64.deb"),
                    _make_file_entry("pool/main/c/chromium/chromium-l10n_151_all.deb"),
                ]

                captured_tasks: list = []

                async def mock_download_batch(tasks, max_concurrent):
                    captured_tasks.extend(tasks)
                    return [
                        DownloadResult(
                            url=t.url,
                            success=True,
                            sha256_verified=True,
                            bytes_transferred=1024,
                        )
                        for t in tasks
                    ]

                engine._download_coordinator.download_batch = AsyncMock(side_effect=mock_download_batch)

                await engine._stage_artifacts(config, entries)

                # All 3 unique entries should be downloaded
                assert len(captured_tasks) == 3
            finally:
                await db_engine.dispose()

        asyncio.run(_run())

    def test_first_occurrence_wins_on_duplicate(self) -> None:
        """When duplicates exist, the first entry's sha256 is used."""

        async def _run() -> None:
            factory, db_engine = await _setup_db()
            try:
                engine = _make_engine(factory)
                config = _make_config()

                # Same path but different sha256 (shouldn't happen in practice,
                # but verifies first-wins semantics)
                entries = [
                    FileEntry(
                        relative_path="pool/main/p/pkg/pkg_1.0_all.deb",
                        sha256="a" * 64,
                        size_bytes=100,
                    ),
                    FileEntry(
                        relative_path="pool/main/p/pkg/pkg_1.0_all.deb",
                        sha256="b" * 64,
                        size_bytes=200,
                    ),
                ]

                captured_tasks: list = []

                async def mock_download_batch(tasks, max_concurrent):
                    captured_tasks.extend(tasks)
                    return [
                        DownloadResult(
                            url=t.url,
                            success=True,
                            sha256_verified=True,
                            bytes_transferred=100,
                        )
                        for t in tasks
                    ]

                engine._download_coordinator.download_batch = AsyncMock(side_effect=mock_download_batch)

                await engine._stage_artifacts(config, entries)

                assert len(captured_tasks) == 1
                assert captured_tasks[0].expected_sha256 == "a" * 64
                assert captured_tasks[0].expected_size == 100
            finally:
                await db_engine.dispose()

        asyncio.run(_run())

    def test_empty_entries_skips_processing(self) -> None:
        """An empty entry list returns immediately without calling download_batch."""

        async def _run() -> None:
            factory, db_engine = await _setup_db()
            try:
                engine = _make_engine(factory)
                config = _make_config()

                await engine._stage_artifacts(config, [])

                engine._download_coordinator.download_batch.assert_not_called()
            finally:
                await db_engine.dispose()

        asyncio.run(_run())

    def test_deduplication_logs_debug_message(self) -> None:
        """When duplicates are removed, a debug log is emitted with counts."""

        async def _run() -> None:
            factory, db_engine = await _setup_db()
            try:
                engine = _make_engine(factory)
                config = _make_config()

                entries = [
                    _make_file_entry("pool/main/a/acl/acl_2.3_all.deb"),
                    _make_file_entry("pool/main/a/acl/acl_2.3_all.deb"),
                    _make_file_entry("pool/main/a/acl/acl_2.3_all.deb"),
                ]

                async def mock_download_batch(tasks, max_concurrent):
                    return [
                        DownloadResult(
                            url=t.url,
                            success=True,
                            sha256_verified=True,
                            bytes_transferred=1024,
                        )
                        for t in tasks
                    ]

                engine._download_coordinator.download_batch = AsyncMock(side_effect=mock_download_batch)

                await engine._stage_artifacts(config, entries)

                # Verify the debug log was called with deduplication info
                engine._logger.debug.assert_any_call(
                    "Deduplicated artifact entries",
                    original_count=3,
                    unique_count=1,
                    duplicates_removed=2,
                    session_id="test-session",
                )
            finally:
                await db_engine.dispose()

        asyncio.run(_run())

    def test_no_debug_log_when_no_duplicates(self) -> None:
        """When all entries are unique, no deduplication debug log is emitted."""

        async def _run() -> None:
            factory, db_engine = await _setup_db()
            try:
                engine = _make_engine(factory)
                config = _make_config()

                entries = [
                    _make_file_entry("pool/main/a/pkg1_1.0_amd64.deb"),
                    _make_file_entry("pool/main/b/pkg2_2.0_arm64.deb"),
                ]

                async def mock_download_batch(tasks, max_concurrent):
                    return [
                        DownloadResult(
                            url=t.url,
                            success=True,
                            sha256_verified=True,
                            bytes_transferred=1024,
                        )
                        for t in tasks
                    ]

                engine._download_coordinator.download_batch = AsyncMock(side_effect=mock_download_batch)

                await engine._stage_artifacts(config, entries)

                # Should NOT have logged deduplication
                for call in engine._logger.debug.call_args_list:
                    assert call[0][0] != "Deduplicated artifact entries"
            finally:
                await db_engine.dispose()

        asyncio.run(_run())

    @settings(deadline=None)
    @given(
        num_unique=st.integers(min_value=1, max_value=20),
        num_duplicates=st.integers(min_value=1, max_value=10),
    )
    def test_deduplication_preserves_count_invariant(self, num_unique: int, num_duplicates: int) -> None:
        """Property: len(deduplicated) == len(set(relative_paths)).

        For any combination of unique and duplicate entries, the
        deduplication produces exactly as many entries as there are
        distinct relative_paths.
        """

        async def _run() -> None:
            factory, db_engine = await _setup_db()
            try:
                engine = _make_engine(factory)
                config = _make_config()

                # Build entries with known unique paths + duplicates of the first
                unique_entries = [
                    _make_file_entry(f"pool/main/p/pkg{i}/pkg{i}_1.0_amd64.deb") for i in range(num_unique)
                ]
                # Duplicate the first entry multiple times
                duplicates = [unique_entries[0]] * num_duplicates
                all_entries = unique_entries + duplicates

                captured_tasks: list = []

                async def mock_download_batch(tasks, max_concurrent):
                    captured_tasks.extend(tasks)
                    return [
                        DownloadResult(
                            url=t.url,
                            success=True,
                            sha256_verified=True,
                            bytes_transferred=1024,
                        )
                        for t in tasks
                    ]

                engine._download_coordinator.download_batch = AsyncMock(side_effect=mock_download_batch)

                await engine._stage_artifacts(config, all_entries)

                # The number of tasks should equal the number of unique paths
                assert len(captured_tasks) == num_unique
            finally:
                await db_engine.dispose()

        asyncio.run(_run())
