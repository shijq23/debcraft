"""Bootstrap function for the M3 mirror layer.

Registers all mirror service implementations in the dependency injection
container. Follows the same pattern as storage_bootstrap().
"""

from debcraft.infrastructure.mirror.config_reader import ConfigReader
from debcraft.infrastructure.mirror.download import DownloadCoordinator
from debcraft.infrastructure.mirror.engine import MirrorEngine
from debcraft.infrastructure.mirror.publisher import SnapshotPublisher
from debcraft.infrastructure.mirror.workflow import MirrorWorkflow
from debcraft.platform.contracts.container import Container


async def mirror_bootstrap(container: Container) -> None:
    """Register M3 mirror services in the DI container.

    Singleton registrations:
        - MirrorWorkflow: orchestrates the full mirror sync lifecycle
        - ConfigReader: reads and validates mirrors.toml configuration

    Scoped registrations:
        - DownloadCoordinator: manages concurrent HTTP downloads per workflow scope
        - MirrorEngine: orchestrates synchronization stages per workflow scope
        - SnapshotPublisher: publishes atomic RepositorySnapshots per workflow scope

    Follows the same function signature and registration pattern as
    storage_bootstrap().

    Args:
        container: The M1 dependency injection container to register services in.
    """
    # Singletons — one instance shared across the entire application
    container.register_singleton(MirrorWorkflow)
    container.register_singleton(ConfigReader)

    # Scoped — one instance per scope (workflow execution)
    container.register_scoped(DownloadCoordinator)
    container.register_scoped(MirrorEngine)
    container.register_scoped(SnapshotPublisher)
