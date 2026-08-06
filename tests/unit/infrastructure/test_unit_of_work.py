"""Unit tests for SqliteUnitOfWork.

Verifies context manager commit/rollback behavior, repository property
caching, and CancellationToken integration.

Requirements: 4.4, 4.5, 4.6, 4.9
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Import scan models to ensure all relationships resolve correctly
import debcraft.infrastructure.models.scan  # noqa: F401
from debcraft.infrastructure.database.unit_of_work import SqliteUnitOfWork
from debcraft.infrastructure.errors import StorageError
from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.models.metadata import SourcePackage
from debcraft.platform.contracts.persistence import DatabaseProvider
from debcraft.platform.contracts.workflow import CancellationToken


async def _create_in_memory_session_factory() -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    """Create an in-memory SQLite engine with all tables and return (factory, engine)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


def _make_mock_provider(session_factory: async_sessionmaker[AsyncSession]) -> DatabaseProvider:
    """Create a mock DatabaseProvider that returns sessions from the factory."""
    mock = MagicMock(spec=DatabaseProvider)

    async def _get_session(db_name: str) -> AsyncSession:
        return session_factory()

    mock.get_session = _get_session
    return mock


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestContextManagerCommit:
    """Test that the context manager commits on clean exit."""

    @pytest.mark.asyncio
    async def test_commit_on_clean_exit(self) -> None:
        """Entities added within the context are persisted after a clean exit."""
        factory, engine = await _create_in_memory_session_factory()
        provider = _make_mock_provider(factory)

        try:
            # Add an entity inside the UoW context
            async with SqliteUnitOfWork(provider, "metadata") as uow:
                pkg = SourcePackage(
                    name="test-pkg",
                    version="1.0.0",
                    maintainer="Test <test@example.com>",
                )
                await uow.source_packages.add(pkg)

            # Verify the entity persisted by querying with an independent session
            async with factory() as verify_session:
                result = await verify_session.execute(
                    text("SELECT name, version FROM source_packages WHERE name = 'test-pkg'")
                )
                row = result.one()
                assert row[0] == "test-pkg"
                assert row[1] == "1.0.0"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_multiple_entities_committed_atomically(self) -> None:
        """Multiple entities added in one context are all committed together."""
        factory, engine = await _create_in_memory_session_factory()
        provider = _make_mock_provider(factory)

        try:
            async with SqliteUnitOfWork(provider, "metadata") as uow:
                for i in range(3):
                    pkg = SourcePackage(
                        name=f"pkg-{i}",
                        version="1.0.0",
                        maintainer=None,
                    )
                    await uow.source_packages.add(pkg)

            # Verify all 3 entities persist
            async with factory() as verify_session:
                result = await verify_session.execute(text("SELECT COUNT(*) FROM source_packages"))
                count = result.scalar()
                assert count == 3
        finally:
            await engine.dispose()


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestContextManagerRollback:
    """Test that the context manager rolls back on exception."""

    @pytest.mark.asyncio
    async def test_rollback_on_exception(self) -> None:
        """Entities added within the context are discarded when an exception occurs."""
        factory, engine = await _create_in_memory_session_factory()
        provider = _make_mock_provider(factory)

        try:
            with pytest.raises(ValueError, match="intentional"):
                async with SqliteUnitOfWork(provider, "metadata") as uow:
                    pkg = SourcePackage(
                        name="should-not-persist",
                        version="2.0.0",
                        maintainer=None,
                    )
                    await uow.source_packages.add(pkg)
                    raise ValueError("intentional error")

            # Verify the entity was NOT persisted
            async with factory() as verify_session:
                result = await verify_session.execute(
                    text("SELECT COUNT(*) FROM source_packages WHERE name = 'should-not-persist'")
                )
                count = result.scalar()
                assert count == 0
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_exception_is_reraised(self) -> None:
        """The original exception is re-raised after rollback."""
        factory, engine = await _create_in_memory_session_factory()
        provider = _make_mock_provider(factory)

        try:
            with pytest.raises(RuntimeError, match="specific error"):
                async with SqliteUnitOfWork(provider, "metadata") as uow:
                    _ = uow  # Use the context
                    raise RuntimeError("specific error")
        finally:
            await engine.dispose()


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestRepositoryProperties:
    """Test repository properties return same instances on repeated access."""

    @pytest.mark.asyncio
    async def test_packages_returns_same_instance(self) -> None:
        """Accessing .packages twice returns the exact same repository instance."""
        factory, engine = await _create_in_memory_session_factory()
        provider = _make_mock_provider(factory)

        try:
            async with SqliteUnitOfWork(provider, "metadata") as uow:
                first = uow.packages
                second = uow.packages
                assert first is second
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_source_packages_returns_same_instance(self) -> None:
        """Accessing .source_packages twice returns the exact same instance."""
        factory, engine = await _create_in_memory_session_factory()
        provider = _make_mock_provider(factory)

        try:
            async with SqliteUnitOfWork(provider, "metadata") as uow:
                first = uow.source_packages
                second = uow.source_packages
                assert first is second
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_repository_files_returns_same_instance(self) -> None:
        """Accessing .repository_files twice returns the exact same instance."""
        factory, engine = await _create_in_memory_session_factory()
        provider = _make_mock_provider(factory)

        try:
            async with SqliteUnitOfWork(provider, "metadata") as uow:
                first = uow.repository_files
                second = uow.repository_files
                assert first is second
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_all_properties_are_distinct_repositories(self) -> None:
        """Each repository property returns a different repository instance."""
        factory, engine = await _create_in_memory_session_factory()
        provider = _make_mock_provider(factory)

        try:
            async with SqliteUnitOfWork(provider, "metadata") as uow:
                repos = [
                    uow.packages,
                    uow.source_packages,
                    uow.repository_files,
                    uow.snapshots,
                    uow.licenses,
                    uow.scan_sessions,
                    uow.sbom_documents,
                ]
                # All should be distinct objects
                ids = [id(r) for r in repos]
                assert len(set(ids)) == len(ids), "All repository properties should be distinct"
        finally:
            await engine.dispose()


@pytest.mark.unit
@pytest.mark.storage
@pytest.mark.database
class TestCancellationToken:
    """Test CancellationToken prevents commit."""

    @pytest.mark.asyncio
    async def test_cancelled_token_prevents_commit(self) -> None:
        """When CancellationToken is cancelled, commit raises StorageError."""
        factory, engine = await _create_in_memory_session_factory()
        provider = _make_mock_provider(factory)
        token = CancellationToken()
        token.cancel()

        try:
            with pytest.raises(StorageError, match="cancelled"):
                async with SqliteUnitOfWork(provider, "metadata", cancellation_token=token) as uow:
                    pkg = SourcePackage(
                        name="cancelled-pkg",
                        version="1.0.0",
                        maintainer=None,
                    )
                    await uow.source_packages.add(pkg)

            # Verify the entity was NOT persisted
            async with factory() as verify_session:
                result = await verify_session.execute(
                    text("SELECT COUNT(*) FROM source_packages WHERE name = 'cancelled-pkg'")
                )
                count = result.scalar()
                assert count == 0
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_uncancelled_token_allows_commit(self) -> None:
        """When CancellationToken is not cancelled, commit proceeds normally."""
        factory, engine = await _create_in_memory_session_factory()
        provider = _make_mock_provider(factory)
        token = CancellationToken()

        try:
            async with SqliteUnitOfWork(provider, "metadata", cancellation_token=token) as uow:
                pkg = SourcePackage(
                    name="allowed-pkg",
                    version="1.0.0",
                    maintainer=None,
                )
                await uow.source_packages.add(pkg)

            # Verify the entity WAS persisted
            async with factory() as verify_session:
                result = await verify_session.execute(
                    text("SELECT COUNT(*) FROM source_packages WHERE name = 'allowed-pkg'")
                )
                count = result.scalar()
                assert count == 1
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_no_token_allows_commit(self) -> None:
        """When no CancellationToken is provided, commit proceeds normally."""
        factory, engine = await _create_in_memory_session_factory()
        provider = _make_mock_provider(factory)

        try:
            async with SqliteUnitOfWork(provider, "metadata") as uow:
                pkg = SourcePackage(
                    name="no-token-pkg",
                    version="1.0.0",
                    maintainer=None,
                )
                await uow.source_packages.add(pkg)

            # Verify the entity was persisted
            async with factory() as verify_session:
                result = await verify_session.execute(
                    text("SELECT COUNT(*) FROM source_packages WHERE name = 'no-token-pkg'")
                )
                count = result.scalar()
                assert count == 1
        finally:
            await engine.dispose()
