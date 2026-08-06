"""Bootstrap function for the platform kernel.

Registers all kernel service implementations against their contract interfaces
in the dependency injection container and returns the configured container.
"""

from debcraft.platform.contracts.configuration import ConfigurationService
from debcraft.platform.contracts.container import Container
from debcraft.platform.contracts.events import EventBus
from debcraft.platform.contracts.logging import LoggerFactory
from debcraft.platform.contracts.resources import ResourceManager
from debcraft.platform.contracts.workflow import WorkflowEngine, WorkflowFactory
from debcraft.platform.kernel.configuration import KernelConfigurationService
from debcraft.platform.kernel.container import KernelContainer
from debcraft.platform.kernel.events import KernelEventBus
from debcraft.platform.kernel.logging import KernelLoggerFactory
from debcraft.platform.kernel.resources import KernelResourceManager
from debcraft.platform.kernel.workflow import KernelWorkflowEngine, KernelWorkflowFactory


def bootstrap() -> Container:
    """Register all kernel services and return the configured container.

    Creates a KernelContainer, registers each kernel service implementation
    against its contract interface with the appropriate lifetime, and returns
    the fully configured container ready for service resolution.

    Singleton registrations:
        - ConfigurationService → KernelConfigurationService
        - EventBus → KernelEventBus
        - LoggerFactory → KernelLoggerFactory
        - WorkflowEngine → KernelWorkflowEngine
        - WorkflowFactory → KernelWorkflowFactory

    Scoped registrations:
        - ResourceManager → KernelResourceManager

    Instance registrations:
        - Container → the container itself (for self-referential resolution)

    Returns:
        The configured Container with all kernel services registered.
    """
    container = KernelContainer()

    # Register the container itself so services that depend on Container
    # (e.g., KernelWorkflowEngine, KernelWorkflowFactory) can be resolved
    # via constructor injection.
    container.register_instance(Container, container)

    container.register_singleton(ConfigurationService, KernelConfigurationService)
    container.register_singleton(EventBus, KernelEventBus)
    container.register_singleton(LoggerFactory, KernelLoggerFactory)
    container.register_singleton(WorkflowEngine, KernelWorkflowEngine)
    container.register_singleton(WorkflowFactory, KernelWorkflowFactory)
    container.register_scoped(ResourceManager, KernelResourceManager)

    return container
