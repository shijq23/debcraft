"""Bootstrap function for the M6 scanner subsystem.

Registers scanner services in the dependency injection container.
The ScannerRegistry is created, populated from entry points, and
registered as a singleton instance. Enrichment services are registered
as scoped so they receive fresh database sessions per workflow scope.
"""

from debcraft.infrastructure.scanners.cache_adapter import EnrichmentCacheAdapter
from debcraft.infrastructure.scanners.enricher import MetadataEnricher
from debcraft.infrastructure.scanners.registry import ScannerRegistry
from debcraft.platform.contracts.container import Container


async def scanner_bootstrap(container: Container) -> None:
    """Register M6 scanner services in the DI container.

    Creates and loads the ScannerRegistry from entry points, then
    registers it as a singleton instance. Enrichment services are
    registered as scoped (one instance per workflow scope).

    Singleton registrations:
        - ScannerRegistry (loads entry points on creation)

    Scoped registrations:
        - EnrichmentCacheAdapter
        - MetadataEnricher

    Args:
        container: The M1 dependency injection container.
    """
    # Create and load registry
    registry = ScannerRegistry()
    registry.load_from_entry_points()

    # Register as singleton (one registry for all scopes)
    container.register_instance(ScannerRegistry, registry)

    # Scoped adapters for enrichment
    container.register_scoped(EnrichmentCacheAdapter)
    container.register_scoped(MetadataEnricher)
