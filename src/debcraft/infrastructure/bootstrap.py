"""Bootstrap function for the M2 storage layer.

Registers all storage service implementations against their contract interfaces
in the dependency injection container. The storage engine is also acquired by
the resource manager for deterministic lifecycle management.
"""

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


async def storage_bootstrap(container: Container) -> None:
    """Register all M2 storage services in the M1 Container.

    Registers the storage engine and database provider as singletons,
    the unit of work as scoped, and all repository implementations as
    scoped services. After registration, acquires the storage engine
    through the resource manager for deterministic lifecycle management.

    Singleton registrations:
        - StorageEngine → DefaultStorageEngine
        - DatabaseProvider → SqliteDatabaseProvider

    Scoped registrations:
        - UnitOfWork → SqliteUnitOfWork
        - RepositoryFileRepository
        - PackageRepository
        - SourcePackageRepository
        - SnapshotRepository
        - LicenseRepository
        - ScanSessionRepository
        - SBOMRepository

    Args:
        container: The M1 dependency injection container to register services in.
    """
    # Singletons — one instance shared across the entire application
    container.register_singleton(StorageEngine, DefaultStorageEngine)
    container.register_singleton(DatabaseProvider, SqliteDatabaseProvider)

    # Scoped — one instance per scope (workflow)
    container.register_scoped(UnitOfWork, SqliteUnitOfWork)

    # Repository implementations — scoped, one per workflow scope
    container.register_scoped(RepositoryFileRepository)
    container.register_scoped(PackageRepository)
    container.register_scoped(SourcePackageRepository)
    container.register_scoped(SnapshotRepository)
    container.register_scoped(LicenseRepository)
    container.register_scoped(ScanSessionRepository)
    container.register_scoped(SBOMRepository)

    # Acquire the StorageEngine via ResourceManager for deterministic lifecycle.
    # ResourceManager calls __aenter__ on initialization and __aexit__ on cleanup.
    resource_manager = container.resolve(ResourceManager)
    storage_engine = container.resolve(StorageEngine)
    await resource_manager.acquire_async(storage_engine)
