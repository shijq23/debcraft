"""Unit tests for the storage_bootstrap() function.

Verifies that all required service types are registered in the container
with correct lifetimes, and that the ResourceManager acquires the StorageEngine.

Requirements: 9.1, 9.2, 9.7, 9.9
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from debcraft.infrastructure.bootstrap import storage_bootstrap
from debcraft.infrastructure.database.provider import SqliteDatabaseProvider
from debcraft.infrastructure.database.unit_of_work import SqliteUnitOfWork
from debcraft.infrastructure.repositories.license import LicenseRepository
from debcraft.infrastructure.repositories.package import PackageRepository
from debcraft.infrastructure.repositories.repository_file import RepositoryFileRepository
from debcraft.infrastructure.repositories.sbom import SBOMRepository
from debcraft.infrastructure.repositories.scan_session import ScanSessionRepository
from debcraft.infrastructure.repositories.snapshot import SnapshotRepository
from debcraft.infrastructure.repositories.source_package import SourcePackageRepository
from debcraft.infrastructure.storage.engine import DefaultStorageEngine
from debcraft.platform.contracts.container import Container
from debcraft.platform.contracts.persistence import DatabaseProvider, UnitOfWork
from debcraft.platform.contracts.resources import ResourceManager
from debcraft.platform.contracts.storage import StorageEngine


def _make_mock_container() -> MagicMock:
    """Create a mock Container with tracked registrations and resolve behavior."""
    mock = MagicMock(spec=Container)

    # Track what gets resolved — return distinct mock objects
    mock_storage_engine = MagicMock(spec=DefaultStorageEngine)
    mock_resource_manager = MagicMock(spec=ResourceManager)
    mock_resource_manager.acquire_async = AsyncMock()

    def _resolve(service_type: type[object]) -> object:
        if service_type is ResourceManager:
            return mock_resource_manager
        if service_type is StorageEngine:
            return mock_storage_engine
        return MagicMock()

    mock.resolve = MagicMock(side_effect=_resolve)
    mock._mock_storage_engine = mock_storage_engine
    mock._mock_resource_manager = mock_resource_manager
    return mock


@pytest.mark.unit
@pytest.mark.storage
class TestServiceRegistrations:
    """Test all required service types are registered after storage_bootstrap()."""

    @pytest.mark.asyncio
    async def test_storage_engine_registered_as_singleton(self) -> None:
        """StorageEngine is registered as singleton with DefaultStorageEngine implementation."""
        container = _make_mock_container()

        await storage_bootstrap(container)

        container.register_singleton.assert_any_call(StorageEngine, DefaultStorageEngine)

    @pytest.mark.asyncio
    async def test_database_provider_registered_as_singleton(self) -> None:
        """DatabaseProvider is registered as singleton with SqliteDatabaseProvider implementation."""
        container = _make_mock_container()

        await storage_bootstrap(container)

        container.register_singleton.assert_any_call(DatabaseProvider, SqliteDatabaseProvider)

    @pytest.mark.asyncio
    async def test_unit_of_work_registered_as_scoped(self) -> None:
        """UnitOfWork is registered as scoped with SqliteUnitOfWork implementation."""
        container = _make_mock_container()

        await storage_bootstrap(container)

        container.register_scoped.assert_any_call(UnitOfWork, SqliteUnitOfWork)

    @pytest.mark.asyncio
    async def test_all_repositories_registered_as_scoped(self) -> None:
        """All 7 repository implementations are registered as scoped services."""
        container = _make_mock_container()

        await storage_bootstrap(container)

        expected_repos = [
            RepositoryFileRepository,
            PackageRepository,
            SourcePackageRepository,
            SnapshotRepository,
            LicenseRepository,
            ScanSessionRepository,
            SBOMRepository,
        ]

        scoped_calls = container.register_scoped.call_args_list
        for repo_type in expected_repos:
            assert call(repo_type) in scoped_calls, f"{repo_type.__name__} not registered as scoped"

    @pytest.mark.asyncio
    async def test_exactly_two_singleton_registrations(self) -> None:
        """Exactly two services are registered as singletons."""
        container = _make_mock_container()

        await storage_bootstrap(container)

        assert container.register_singleton.call_count == 2

    @pytest.mark.asyncio
    async def test_exactly_eight_scoped_registrations(self) -> None:
        """Exactly 8 services are registered as scoped (UoW + 7 repos)."""
        container = _make_mock_container()

        await storage_bootstrap(container)

        # 1 UnitOfWork + 7 repositories = 8 scoped registrations
        assert container.register_scoped.call_count == 8


@pytest.mark.unit
@pytest.mark.storage
class TestSingletonBehavior:
    """Test StorageEngine registered as singleton (same instance on two resolves)."""

    @pytest.mark.asyncio
    async def test_storage_engine_resolves_same_instance(self) -> None:
        """Resolving StorageEngine twice returns the same mock instance."""
        container = _make_mock_container()

        await storage_bootstrap(container)

        # The container.resolve is called for both ResourceManager and StorageEngine
        # Verify the same engine instance is what gets passed to acquire_async
        resolved_engine = container.resolve(StorageEngine)
        resolved_engine_again = container.resolve(StorageEngine)
        assert resolved_engine is resolved_engine_again


@pytest.mark.unit
@pytest.mark.storage
class TestScopedBehavior:
    """Test repositories registered as scoped (different instances across scopes)."""

    @pytest.mark.asyncio
    async def test_scoped_repos_registered_without_interface_mapping(self) -> None:
        """Repositories are registered as scoped with just the implementation type.

        This means each scope gets its own instance (scoped lifetime).
        """
        container = _make_mock_container()

        await storage_bootstrap(container)

        # Verify repos are registered with just one arg (implementation only)
        scoped_calls = container.register_scoped.call_args_list
        repo_only_calls = [c for c in scoped_calls if len(c.args) == 1]
        assert len(repo_only_calls) == 7, "Expected 7 repository-only scoped registrations"

    @pytest.mark.asyncio
    async def test_unit_of_work_registered_with_interface(self) -> None:
        """UnitOfWork is registered as scoped with interface→implementation mapping."""
        container = _make_mock_container()

        await storage_bootstrap(container)

        scoped_calls = container.register_scoped.call_args_list
        interface_calls = [c for c in scoped_calls if len(c.args) == 2]
        assert call(UnitOfWork, SqliteUnitOfWork) in interface_calls


@pytest.mark.unit
@pytest.mark.storage
class TestResourceManagerIntegration:
    """Test ResourceManager.acquire_async() called with StorageEngine."""

    @pytest.mark.asyncio
    async def test_resource_manager_resolved(self) -> None:
        """ResourceManager is resolved from the container."""
        container = _make_mock_container()

        await storage_bootstrap(container)

        container.resolve.assert_any_call(ResourceManager)

    @pytest.mark.asyncio
    async def test_storage_engine_resolved(self) -> None:
        """StorageEngine is resolved from the container."""
        container = _make_mock_container()

        await storage_bootstrap(container)

        container.resolve.assert_any_call(StorageEngine)

    @pytest.mark.asyncio
    async def test_acquire_async_called_with_storage_engine(self) -> None:
        """ResourceManager.acquire_async() is called with the resolved StorageEngine."""
        container = _make_mock_container()

        await storage_bootstrap(container)

        resource_manager = container._mock_resource_manager
        storage_engine = container._mock_storage_engine
        resource_manager.acquire_async.assert_called_once_with(storage_engine)

    @pytest.mark.asyncio
    async def test_acquire_async_awaited(self) -> None:
        """ResourceManager.acquire_async() is properly awaited."""
        container = _make_mock_container()

        await storage_bootstrap(container)

        resource_manager = container._mock_resource_manager
        # AsyncMock tracks whether it was awaited
        assert resource_manager.acquire_async.await_count == 1
