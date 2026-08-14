"""Property-based tests for Unit of Work commit atomicity and rollback.

**Validates: Requirements 3.3, 4.2, 4.3, 4.9, 9.8**

Property 10: Commit Atomicity — For any set of N entities added to repositories
within a single UnitOfWork, before commit() is called none shall be visible in an
independent session, and after commit() completes successfully all N entities shall
be retrievable from a fresh session.

Property 11: Rollback Discards All Changes — For any set of entities added to
repositories within a UnitOfWork, after rollback() is called, none of those
entities shall be retrievable from a fresh session, and the UnitOfWork shall
accept subsequent operations.

Property 12: Cancellation Prevents Commit — For any UnitOfWork whose associated
CancellationToken has been cancelled, calling commit() shall roll back any
uncommitted changes and raise a StorageError, preventing data persistence.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Ensure all relationship models are loaded
import debcraft.infrastructure.models.scan  # noqa: F401
from debcraft.infrastructure.errors import StorageError
from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.models.metadata import SourcePackage
from debcraft.infrastructure.repositories.source_package import SourcePackageRepository
from debcraft.platform.contracts.persistence import DatabaseProvider
from debcraft.platform.contracts.workflow import CancellationToken


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


def _make_mock_provider(
    factory: async_sessionmaker[AsyncSession],
) -> DatabaseProvider:
    """Create a mock DatabaseProvider that returns sessions from the given factory.

    The mock's get_session method returns a new session from the factory each time.
    """
    provider = AsyncMock(spec=DatabaseProvider)
    provider.get_session = AsyncMock(side_effect=lambda _db_name: factory())
    return provider


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestCommitAtomicity:
    """Property 10: Commit Atomicity.

    For any set of N entities added to repositories within a single UnitOfWork,
    before commit() is called none shall be visible in an independent session,
    and after commit() completes successfully all N entities shall be retrievable
    from a fresh session.

    Uses a file-based temporary SQLite database to ensure proper transaction
    isolation between sessions (in-memory SQLite shares a single connection).
    """

    @settings(deadline=None)
    @given(
        entities=st.lists(
            st.tuples(
                _safe_text(),
                _safe_text(),
            ),
            min_size=1,
            max_size=10,
            unique_by=lambda t: (t[0], t[1]),
        ),
    )
    def test_commit_atomicity(
        self,
        entities: list[tuple[str, str]],
    ) -> None:
        """Add N entities inside UoW, verify none visible before commit, all visible after.

        **Validates: Requirements 3.3, 4.2**
        """

        async def _run() -> None:
            import tempfile
            from pathlib import Path

            from debcraft.infrastructure.database.unit_of_work import SqliteUnitOfWork

            # Use a temporary file-based SQLite to get real transaction isolation
            with tempfile.TemporaryDirectory() as tmpdir:
                db_path = Path(tmpdir) / "test.db"
                db_url = f"sqlite+aiosqlite:///{db_path}"

                engine = create_async_engine(db_url, echo=False)
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                factory = async_sessionmaker(engine, expire_on_commit=False)

                try:
                    provider = _make_mock_provider(factory)
                    uow = SqliteUnitOfWork(provider, "metadata")

                    # Enter UoW manually to control when commit happens
                    await uow.__aenter__()
                    repo = SourcePackageRepository(uow._get_session())

                    # Add entities (flush but no commit yet)
                    added_ids: list[int] = []
                    for name, version in entities:
                        entity = SourcePackage(name=name, version=version)
                        added = await repo.add(entity)
                        added_ids.append(added.id)

                    # Before commit: verify none visible in an independent session
                    async with factory() as independent_session:
                        independent_repo = SourcePackageRepository(independent_session)
                        all_results = await independent_repo.find()
                        assert all_results == [], f"Expected no entities visible before commit, got {len(all_results)}"

                    # Now exit cleanly (triggers commit)
                    await uow.__aexit__(None, None, None)

                    # After commit: verify all entities are visible from a fresh session
                    async with factory() as verify_session:
                        verify_repo = SourcePackageRepository(verify_session)
                        for i, entity_id in enumerate(added_ids):
                            retrieved = await verify_repo.get_by_id(entity_id)
                            assert retrieved.name == entities[i][0]
                            assert retrieved.version == entities[i][1]
                finally:
                    await engine.dispose()

        asyncio.run(_run())


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestRollbackDiscardsAllChanges:
    """Property 11: Rollback Discards All Changes.

    For any set of entities added to repositories within a UnitOfWork,
    after rollback() is called, none of those entities shall be retrievable
    from a fresh session, and the UnitOfWork shall accept subsequent operations.
    """

    @settings(deadline=None)
    @given(
        entities=st.lists(
            st.tuples(
                _safe_text(),
                _safe_text(),
            ),
            min_size=1,
            max_size=10,
            unique_by=lambda t: (t[0], t[1]),
        ),
    )
    def test_rollback_discards_all_changes(
        self,
        entities: list[tuple[str, str]],
    ) -> None:
        """Add entities, call rollback(), verify none retrievable.

        **Validates: Requirements 4.3**
        """

        async def _run() -> None:
            from debcraft.infrastructure.database.unit_of_work import SqliteUnitOfWork

            factory, engine = await _setup_engine()
            try:
                provider = _make_mock_provider(factory)
                uow = SqliteUnitOfWork(provider, "metadata")

                # Manually enter the context to control commit/rollback
                await uow.__aenter__()
                repo = SourcePackageRepository(uow._get_session())

                # Add entities
                added_ids: list[int] = []
                for name, version in entities:
                    entity = SourcePackage(name=name, version=version)
                    added = await repo.add(entity)
                    added_ids.append(added.id)

                # Call rollback explicitly
                await uow.rollback()

                # Verify the UoW still accepts subsequent operations after rollback
                subsequent_entity = SourcePackage(name="after-rollback", version="1.0")
                repo_after = SourcePackageRepository(uow._get_session())
                await repo_after.add(subsequent_entity)
                await uow.rollback()

                # Exit without committing (pass an exception type to trigger rollback path)
                await uow.__aexit__(RuntimeError, RuntimeError("test"), None)

                # Verify none of the rolled-back entities are retrievable
                async with factory() as verify_session:
                    verify_repo = SourcePackageRepository(verify_session)
                    all_results = await verify_repo.find()
                    assert all_results == [], f"Expected no entities after rollback, got {len(all_results)}"
            finally:
                await engine.dispose()

        asyncio.run(_run())


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestCancellationPreventsCommit:
    """Property 12: Cancellation Prevents Commit.

    For any UnitOfWork whose associated CancellationToken has been cancelled,
    calling commit() shall roll back any uncommitted changes and raise a
    StorageError, preventing data persistence.
    """

    @settings(deadline=None)
    @given(
        entities=st.lists(
            st.tuples(
                _safe_text(),
                _safe_text(),
            ),
            min_size=1,
            max_size=10,
            unique_by=lambda t: (t[0], t[1]),
        ),
    )
    def test_cancellation_prevents_commit(
        self,
        entities: list[tuple[str, str]],
    ) -> None:
        """Set CancellationToken → commit() raises StorageError and no entities persisted.

        **Validates: Requirements 4.9, 9.8**
        """

        async def _run() -> None:
            from debcraft.infrastructure.database.unit_of_work import SqliteUnitOfWork

            factory, engine = await _setup_engine()
            try:
                provider = _make_mock_provider(factory)
                token = CancellationToken()
                uow = SqliteUnitOfWork(provider, "metadata", cancellation_token=token)

                # Enter the UoW context manually
                await uow.__aenter__()
                repo = SourcePackageRepository(uow._get_session())

                # Add entities
                for name, version in entities:
                    entity = SourcePackage(name=name, version=version)
                    await repo.add(entity)

                # Cancel the token before commit
                token.cancel()

                # Attempting commit should raise StorageError
                with pytest.raises(StorageError, match="cancelled"):
                    await uow.commit()

                # Clean up the session (exit with the exception we caused)
                await uow.__aexit__(StorageError, StorageError("cancelled"), None)

                # Verify no entities persisted
                async with factory() as verify_session:
                    verify_repo = SourcePackageRepository(verify_session)
                    all_results = await verify_repo.find()
                    assert all_results == [], f"Expected no entities after cancellation, got {len(all_results)}"
            finally:
                await engine.dispose()

        asyncio.run(_run())
