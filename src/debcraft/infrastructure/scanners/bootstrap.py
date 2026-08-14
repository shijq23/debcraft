"""Bootstrap function for the M6 scanner subsystem.

Registers scanner services in the dependency injection container.
The ScannerRegistry is created, scanners are explicitly instantiated
with DI-resolved ports, and the registry is registered as a singleton.
Enrichment services are registered as scoped so they receive fresh
database sessions per workflow scope.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from debcraft.infrastructure.scanners.cache_adapter import EnrichmentCacheAdapter
from debcraft.infrastructure.scanners.enricher import MetadataEnricher
from debcraft.infrastructure.scanners.registry import ScannerRegistry
from debcraft.platform.kernel.errors import ServiceNotFoundError

if TYPE_CHECKING:
    from debcraft.domain.scanner.ports import GuestfsInspector
    from debcraft.infrastructure.scanners.iso import ISOReader, SquashfsReader
    from debcraft.platform.contracts.container import Container

logger = logging.getLogger(__name__)


def _resolve_optional_deps(
    container: Container,
) -> tuple[ISOReader | None, SquashfsReader | None, GuestfsInspector | None]:
    """Resolve optional scanner dependencies from the DI container.

    Returns:
        Tuple of (iso_reader, squashfs_reader, guestfs_inspector), each
        may be None if the service is not registered.
    """
    from debcraft.domain.scanner.ports import GuestfsInspector as GuestfsInspectorCls
    from debcraft.infrastructure.scanners.iso import ISOReader as ISOReaderCls
    from debcraft.infrastructure.scanners.iso import SquashfsReader as SquashfsReaderCls

    iso_reader: ISOReader | None = None
    squashfs_reader: SquashfsReader | None = None
    guestfs_inspector: GuestfsInspector | None = None

    try:
        iso_reader = container.resolve(ISOReaderCls)  # type: ignore[type-abstract]
    except ServiceNotFoundError:
        logger.debug("ISOReader not registered in container; ISOScanner will not be available")

    try:
        squashfs_reader = container.resolve(SquashfsReaderCls)  # type: ignore[type-abstract]
    except ServiceNotFoundError:
        logger.debug("SquashfsReader not registered in container; ISOScanner will not be available")

    try:
        guestfs_inspector = container.resolve(GuestfsInspectorCls)  # type: ignore[type-abstract]
    except ServiceNotFoundError:
        logger.debug("GuestfsInspector not registered in container; disk image scanners will use fallback mode")

    return iso_reader, squashfs_reader, guestfs_inspector


def _register_scanners(
    registry: ScannerRegistry,
    container: Container,
) -> None:
    """Instantiate and register all scanners in the registry.

    Args:
        registry: The scanner registry to populate.
        container: The DI container for resolving ports.
    """
    from debcraft.domain.scanner.ports import ContentsIndexPort, PackageLookupPort
    from debcraft.domain.scanner.values import ArtifactType
    from debcraft.infrastructure.scanners.ami import AMIScanner
    from debcraft.infrastructure.scanners.directory import DirectoryScanner
    from debcraft.infrastructure.scanners.docker import DockerScanner
    from debcraft.infrastructure.scanners.img import IMGScanner
    from debcraft.infrastructure.scanners.iso import ISOScanner
    from debcraft.infrastructure.scanners.oci import OCIScanner
    from debcraft.infrastructure.scanners.qcow2 import QCOW2Scanner

    # Resolve required ports from the DI container
    contents_port = container.resolve(ContentsIndexPort)  # type: ignore[type-abstract]
    package_port = container.resolve(PackageLookupPort)  # type: ignore[type-abstract]

    # Resolve optional dependencies — these may not be registered
    iso_reader, squashfs_reader, guestfs_inspector = _resolve_optional_deps(container)

    # Instantiate and register core scanners (always available)
    registry.register(
        ArtifactType.DIRECTORY,
        DirectoryScanner(contents_port=contents_port, package_port=package_port),
    )
    registry.register(
        ArtifactType.DOCKER,
        DockerScanner(contents_port=contents_port, package_port=package_port),
    )
    registry.register(ArtifactType.OCI, OCIScanner())

    # ISO scanner requires both iso_reader and squashfs_reader
    if iso_reader is not None and squashfs_reader is not None:
        registry.register(
            ArtifactType.ISO,
            ISOScanner(
                iso_reader=iso_reader,
                squashfs_reader=squashfs_reader,
                contents_port=contents_port,
                package_port=package_port,
            ),
        )

    # QCOW2 and IMG scanners accept None for guestfs_inspector (graceful degradation)
    qcow2_scanner = QCOW2Scanner(
        guestfs_inspector=guestfs_inspector,
        contents_port=contents_port,
        package_port=package_port,
    )
    img_scanner = IMGScanner(
        guestfs_inspector=guestfs_inspector,
        contents_port=contents_port,
        package_port=package_port,
    )
    registry.register(ArtifactType.QCOW2, qcow2_scanner)
    registry.register(ArtifactType.IMG, img_scanner)

    # AMI scanner delegates to QCOW2 and IMG scanners
    registry.register(ArtifactType.AMI, AMIScanner(qcow2_scanner=qcow2_scanner, img_scanner=img_scanner))


async def scanner_bootstrap(container: Container) -> None:
    """Register M6 scanner services in the DI container.

    Creates a ScannerRegistry, resolves required ports from the DI
    container, explicitly instantiates each scanner with its
    dependencies, and registers instances via ``registry.register()``.

    Entry points are still loaded (for diagnostics and any pre-built
    instances that may be registered as entry points in the future),
    but classes loaded from entry points are skipped with a diagnostic
    warning — the explicit instantiation below is the primary path.

    Singleton registrations:
        - ScannerRegistry (populated with scanner instances)

    Scoped registrations:
        - EnrichmentCacheAdapter
        - MetadataEnricher

    Args:
        container: The M1 dependency injection container.
    """
    # Create registry and load entry points (classes will be skipped with diagnostics)
    registry = ScannerRegistry()
    registry.load_from_entry_points()

    # Instantiate and register all scanners
    _register_scanners(registry, container)

    # Register as singleton (one registry for all scopes)
    container.register_instance(ScannerRegistry, registry)

    # Scoped adapters for enrichment
    container.register_scoped(EnrichmentCacheAdapter)
    container.register_scoped(MetadataEnricher)
