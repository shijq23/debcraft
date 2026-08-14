"""SBOM generation CLI command."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import AbstractAsyncContextManager, AbstractContextManager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, TypeVar

import typer
from rich.console import Console
from rich.table import Table

from debcraft.cli._formatting import format_bytes
from debcraft.cli._progress import create_progress_bar
from debcraft.domain.sbom.values import OutputFormat
from debcraft.platform.contracts.events import EventBus
from debcraft.platform.contracts.logging import Logger
from debcraft.platform.contracts.resources import ResourceManager
from debcraft.platform.contracts.workflow import CancellationToken, ProgressReporter, WorkflowContext

if TYPE_CHECKING:
    from uuid import UUID

    from rich.progress import Progress, TaskID

    from debcraft.cli._sbom_db import DatabaseEngines
    from debcraft.domain.sbom.values import WriterResult
    from debcraft.infrastructure.scanners.cache_adapter import EnrichmentCacheAdapter
    from debcraft.infrastructure.scanners.deb_extractor import DebExtractor
    from debcraft.infrastructure.scanners.registry import ScannerRegistry
    from debcraft.platform.contracts.events import DomainEvent, EventHandler

T = TypeVar("T")

console = Console()

# Valid format values (matching OutputFormat enum)
_VALID_FORMATS = {f.value for f in OutputFormat}

#: Maximum value for snapshot IDs (PostgreSQL int4 upper bound).
_MAX_SNAPSHOT_ID = 2_147_483_647


class _CliEventBus(EventBus):
    """No-op event bus for CLI context."""

    def subscribe(self, event_type: type, handler: EventHandler) -> None:
        """No-op subscribe."""

    def unsubscribe(self, event_type: type, handler: EventHandler) -> None:
        """No-op unsubscribe."""

    async def publish(self, event: DomainEvent) -> None:
        """No-op publish."""


class _CliLogger(Logger):
    """Logger that delegates to Python's logging module."""

    def __init__(self) -> None:
        self._log = logging.getLogger("debcraft.cli.sbom")

    def info(self, message: str, **kwargs: object) -> None:
        """Log at INFO level."""
        self._log.info(message, extra={"extra_data": kwargs})

    def debug(self, message: str, **kwargs: object) -> None:
        """Log at DEBUG level."""
        self._log.debug(message, extra={"extra_data": kwargs})

    def warning(self, message: str, **kwargs: object) -> None:
        """Log at WARNING level."""
        self._log.warning(message, extra={"extra_data": kwargs})

    def error(self, message: str, **kwargs: object) -> None:
        """Log at ERROR level."""
        self._log.error(message, extra={"extra_data": kwargs})

    def with_correlation_id(self, correlation_id: UUID) -> Logger:
        """Return self (no correlation ID tracking in CLI)."""
        return self


class _CliResourceManager(ResourceManager):
    """Minimal resource manager for CLI context."""

    def __init__(self) -> None:
        self._async_resources: list[AbstractAsyncContextManager[object]] = []
        self._sync_resources: list[AbstractContextManager[object]] = []

    async def acquire_async(self, resource: AbstractAsyncContextManager[T]) -> T:
        """Enter and track an async context manager."""
        value = await resource.__aenter__()  # pylint: disable=unnecessary-dunder-call  # Resource tracker must enter without with-block
        self._async_resources.append(resource)
        return value

    def acquire_sync(self, resource: AbstractContextManager[T]) -> T:
        """Enter and track a sync context manager."""
        value = resource.__enter__()  # pylint: disable=unnecessary-dunder-call  # Resource tracker must enter without with-block
        self._sync_resources.append(resource)
        return value

    async def cleanup(self) -> None:
        """Clean up all acquired resources in reverse order."""
        for async_resource in reversed(self._async_resources):
            with suppress(Exception):
                await async_resource.__aexit__(None, None, None)  # pylint: disable=unnecessary-dunder-call  # Explicit lifecycle cleanup
        self._async_resources.clear()

        for sync_resource in reversed(self._sync_resources):
            with suppress(Exception):
                sync_resource.__exit__(None, None, None)  # pylint: disable=unnecessary-dunder-call  # Explicit lifecycle cleanup
        self._sync_resources.clear()


class _CliScope:
    """Minimal DI scope for CLI context that holds pre-built instances."""

    def __init__(self) -> None:
        self._instances: dict[type, object] = {}

    def register(self, service_type: type[T], instance: T) -> None:
        """Register a pre-built instance."""
        self._instances[service_type] = instance

    def resolve(self, service_type: type[T]) -> T:
        """Resolve a registered instance."""
        instance = self._instances.get(service_type)
        if instance is None:
            msg = f"No instance registered for {service_type.__name__}"
            raise RuntimeError(msg)
        return instance  # type: ignore[return-value]

    async def close(self) -> None:
        """No-op close."""


class _CliProgressReporter(ProgressReporter):
    """Reports progress to a Rich progress bar."""

    def __init__(self, progress: Progress, task_id: TaskID, quiet: bool = False) -> None:
        self._progress = progress
        self._task_id = task_id
        self._quiet = quiet

    def report(self, percentage: float, message: str = "") -> None:
        """Report progress update."""
        if not self._quiet:
            self._progress.update(self._task_id, completed=percentage, description=message)


def _validate_formats(format_values: list[str] | None) -> list[OutputFormat]:
    """Validate and convert format string values to OutputFormat enums.

    Args:
        format_values: List of format strings from CLI, or None for all formats.

    Returns:
        List of validated OutputFormat values.

    Raises:
        typer.Exit: If any format value is invalid.
    """
    if not format_values:
        return list(OutputFormat)

    invalid = [f for f in format_values if f not in _VALID_FORMATS]
    if invalid:
        console.print(f"[red]Error:[/red] Invalid format value(s): {', '.join(invalid)}")
        console.print(f"[dim]Valid formats: {', '.join(sorted(_VALID_FORMATS))}[/dim]")
        raise typer.Exit(code=1)

    return [OutputFormat(f) for f in format_values]


def _validate_snapshot_id(value: str | None) -> int | None:
    """Validate that the --snapshot-id value is a positive integer in [1, 2_147_483_647].

    Args:
        value: Raw string value from the CLI option, or None if not provided.

    Returns:
        The validated integer, or None if not provided.

    Raises:
        typer.Exit: If value is not a positive integer in the valid range.
    """
    if value is None:
        return None

    try:
        # Reject floats: "3.5" should not be accepted
        if "." in value:
            raise ValueError
        parsed = int(value)
    except (ValueError, OverflowError):
        console.print(
            f"[red]Error:[/red] --snapshot-id must be a positive integer in range [1, {_MAX_SNAPSHOT_ID}], "
            f"got: {value!r}"
        )
        raise typer.Exit(code=1) from None

    if parsed < 1 or parsed > _MAX_SNAPSHOT_ID:
        console.print(
            f"[red]Error:[/red] --snapshot-id must be a positive integer in range [1, {_MAX_SNAPSHOT_ID}], "
            f"got: {parsed}"
        )
        raise typer.Exit(code=1)

    return parsed


def _validate_artifact_path(artifact_path: Path) -> None:
    """Validate that the artifact path exists.

    Args:
        artifact_path: Path to verify.

    Raises:
        typer.Exit: If path does not exist.
    """
    if not artifact_path.exists():
        console.print(f"[red]Error:[/red] Artifact path does not exist: {artifact_path}")
        raise typer.Exit(code=1)


def _validate_output_dir(output_dir: Path) -> None:
    """Validate that the output directory is writable.

    Args:
        output_dir: Directory path to verify.

    Raises:
        typer.Exit: If directory is not writable.
    """
    # Create directory if it doesn't exist
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        console.print(f"[red]Error:[/red] Cannot create output directory: {output_dir} ({exc})")
        raise typer.Exit(code=1) from None

    if not os.access(output_dir, os.W_OK):
        console.print(f"[red]Error:[/red] Output directory is not writable: {output_dir}")
        raise typer.Exit(code=1)


def _display_summary(results: list[WriterResult]) -> None:
    """Display a Rich summary table with SBOM generation results.

    Args:
        results: List of WriterResult from the generation run.
    """
    table = Table(title="SBOM Generation Summary")
    table.add_column("Format", style="bold")
    table.add_column("Output File")
    table.add_column("File Size", justify="right")
    table.add_column("SHA-256")

    for result in results:
        table.add_row(
            result.format.value,
            str(result.output_path),
            format_bytes(result.file_size),
            result.sha256,
        )

    console.print(table)


def _display_diagnostics(results: list[WriterResult]) -> None:
    """Display validation diagnostics as a warnings section.

    Args:
        results: List of WriterResult that may contain diagnostics.
    """
    all_diagnostics: list[tuple[str, str]] = []
    for result in results:
        for diag in result.diagnostics:
            all_diagnostics.append((result.format.value, diag))

    if not all_diagnostics:
        return

    console.print()
    console.print("[yellow]Validation Warnings:[/yellow]")
    for fmt, diag in all_diagnostics:
        console.print(f"  [{fmt}] {diag}")


def _create_cli_infrastructure(
    progress: Progress, task_id: TaskID, quiet: bool
) -> tuple[_CliEventBus, CancellationToken, _CliProgressReporter, _CliResourceManager, _CliLogger]:
    """Create minimal CLI infrastructure services.

    Args:
        progress: Rich Progress instance.
        task_id: Rich progress task ID.
        quiet: Whether to suppress progress output.

    Returns:
        Tuple of (event_bus, cancellation_token, progress_reporter, resource_manager, logger).
    """
    event_bus = _CliEventBus()
    cancellation_token = CancellationToken()
    progress_reporter = _CliProgressReporter(progress, task_id, quiet)
    resource_manager = _CliResourceManager()
    logger = _CliLogger()
    return event_bus, cancellation_token, progress_reporter, resource_manager, logger


def _create_scanner_registry() -> ScannerRegistry:
    """Create and populate a scanner registry with production dependencies for CLI mode.

    Returns:
        A ScannerRegistry with all scanner types registered.

    Raises:
        ImportError: If pycdlib or PySquashfsImage dependencies are missing.
    """
    from debcraft.domain.scanner.values import ArtifactType
    from debcraft.infrastructure.scanners.ami import AMIScanner
    from debcraft.infrastructure.scanners.directory import DirectoryScanner
    from debcraft.infrastructure.scanners.docker import DockerScanner
    from debcraft.infrastructure.scanners.img import IMGScanner
    from debcraft.infrastructure.scanners.iso import ISOScanner
    from debcraft.infrastructure.scanners.oci import OCIScanner
    from debcraft.infrastructure.scanners.qcow2 import QCOW2Scanner
    from debcraft.infrastructure.scanners.registry import ScannerRegistry

    try:
        from debcraft.infrastructure.scanners.iso_reader_pycdlib import PyCdlibISOReader
        from debcraft.infrastructure.scanners.squashfs_reader_pysquashfsimage import PySquashfsImageReader
    except ImportError as exc:
        raise ImportError(
            f"Missing dependency for ISO scanning: {exc.name}. Install it with: uv add {exc.name}"
        ) from exc

    # Create no-op port adapters for CLI-mode scanning
    contents_port = _NoOpContentsIndexPort()
    package_port = _NoOpPackageLookupPort()

    # Instantiate scanners requiring shared dependencies
    qcow2_scanner = QCOW2Scanner(
        guestfs_inspector=None,
        contents_port=contents_port,
        package_port=package_port,
    )
    img_scanner = IMGScanner(
        guestfs_inspector=None,
        contents_port=contents_port,
        package_port=package_port,
    )

    # Register all scanners in the registry
    scanner_registry = ScannerRegistry()
    scanner_registry.register(
        ArtifactType.DIRECTORY,
        DirectoryScanner(contents_port=contents_port, package_port=package_port),
    )
    scanner_registry.register(
        ArtifactType.DOCKER,
        DockerScanner(contents_port=contents_port, package_port=package_port),
    )
    scanner_registry.register(ArtifactType.OCI, OCIScanner())
    scanner_registry.register(
        ArtifactType.ISO,
        ISOScanner(
            iso_reader=PyCdlibISOReader(),
            squashfs_reader=PySquashfsImageReader(),
            contents_port=contents_port,
            package_port=package_port,
        ),
    )
    scanner_registry.register(ArtifactType.QCOW2, qcow2_scanner)
    scanner_registry.register(ArtifactType.IMG, img_scanner)
    scanner_registry.register(ArtifactType.AMI, AMIScanner(qcow2_scanner=qcow2_scanner, img_scanner=img_scanner))
    return scanner_registry


def _create_di_scope(
    snapshot_id: int = 0,  # pylint: disable=unused-argument
    engines: DatabaseEngines | None = None,
    deb_extractor: DebExtractor | None = None,
) -> _CliScope:
    """Create and populate the DI scope with domain services.

    Builds registries, enricher, assembler, and database provider,
    then registers them all in a CLI scope.  Scanners are explicitly
    instantiated with no-op port adapters rather than loaded via
    entry points (which would store classes, not instances).

    When a DatabaseEngines instance is provided with a cache_session_factory,
    the real EnrichmentCacheAdapter is used. Otherwise, falls back to the
    _NoOpCacheAdapter. The MetadataEnricher receives the metadata_session_factory
    (if available) and optional DebExtractor for ISO fallback.

    Args:
        snapshot_id: Resolved snapshot ID for enrichment (0 = skip).
        engines: Optional DatabaseEngines with session factories for
            cache.db and metadata.db.
        deb_extractor: Optional DebExtractor for direct .deb extraction
            from ISO artifacts.

    Returns:
        Configured _CliScope with all dependencies registered.
    """
    from debcraft.domain.sbom.assembler import ModelAssembler
    from debcraft.infrastructure.sbom_writers.registry import WriterRegistry
    from debcraft.infrastructure.scanners.enricher import MetadataEnricher
    from debcraft.infrastructure.scanners.registry import ScannerRegistry
    from debcraft.platform.contracts.persistence import DatabaseProvider

    scanner_registry = _create_scanner_registry()

    writer_registry = WriterRegistry()
    writer_registry.load_from_entry_points()

    # Resolve cache adapter: use real EnrichmentCacheAdapter when cache session factory
    # is available, otherwise fall back to _NoOpCacheAdapter
    cache_adapter: _NoOpCacheAdapter | EnrichmentCacheAdapter
    if engines is not None and engines.cache_session_factory is not None:
        try:
            from debcraft.infrastructure.scanners.cache_adapter import EnrichmentCacheAdapter

            cache_adapter = EnrichmentCacheAdapter(session_factory=engines.cache_session_factory)
        except (ImportError, OSError, ValueError, TypeError, RuntimeError) as exc:
            logging.getLogger(__name__).warning(
                "Failed to create EnrichmentCacheAdapter, falling back to no-op cache: %s",
                exc,
            )
            cache_adapter = _NoOpCacheAdapter()
    else:
        cache_adapter = _NoOpCacheAdapter()

    # Resolve metadata session factory for MetadataEnricher
    metadata_session_factory = engines.metadata_session_factory if engines is not None else None

    enricher = MetadataEnricher(
        cache_adapter=cache_adapter,  # type: ignore[arg-type]
        metadata_session_factory=metadata_session_factory,
        deb_extractor=deb_extractor,
    )
    assembler = ModelAssembler()
    db_provider = _NoOpDatabaseProvider()

    scope = _CliScope()
    scope.register(ScannerRegistry, scanner_registry)
    scope.register(WriterRegistry, writer_registry)
    scope.register(MetadataEnricher, enricher)
    scope.register(ModelAssembler, assembler)
    scope.register(DatabaseProvider, db_provider)
    return scope


def _create_deb_extractor_for_iso(
    artifact_path: Path, artifact_type: str | None
) -> tuple[DebExtractor | None, object | None]:
    """Create a DebExtractor if the artifact is an ISO with a pool/ directory.

    Detects whether the artifact is an ISO (via explicit type override or
    file extension detection), opens a dedicated ISOReader on the file, and
    wires up the DebParser, DEP5Parser, and LicenseMapper needed for direct
    .deb extraction from the ISO's pool/ directory.

    The caller is responsible for closing the returned ISOReader when it is
    no longer needed.

    Args:
        artifact_path: Path to the artifact file.
        artifact_type: Optional artifact type override string, or None.

    Returns:
        A tuple of (DebExtractor, ISOReader) if the artifact is ISO,
        or (None, None) if the artifact is not ISO or initialization fails.
        The ISOReader must be closed by the caller after use.
    """
    from debcraft.domain.scanner.values import ArtifactType, detect_artifact_type

    # Determine artifact type
    if artifact_type:
        try:
            detected_type = ArtifactType(artifact_type)
        except ValueError:
            return None, None
    else:
        detected_type = detect_artifact_type(str(artifact_path))

    if detected_type != ArtifactType.ISO:
        return None, None

    # Create a dedicated ISOReader for .deb extraction (separate from the scanner's reader)
    try:
        from debcraft.domain.package_intelligence.deb_parser import DebParser
        from debcraft.domain.package_intelligence.dep5_parser import DEP5Parser
        from debcraft.domain.package_intelligence.license_mapper import LicenseMapper
        from debcraft.domain.package_intelligence.spdx_license_data import load_spdx_license_data
        from debcraft.infrastructure.package_intelligence.iso_file_reader import ISODebFileReader
        from debcraft.infrastructure.scanners.deb_extractor import DebExtractor
        from debcraft.infrastructure.scanners.iso_reader_pycdlib import PyCdlibISOReader

        iso_reader = PyCdlibISOReader()
        iso_reader.open(str(artifact_path))

        iso_file_reader = ISODebFileReader(iso_reader)
        deb_parser = DebParser(file_reader=iso_file_reader)
        dep5_parser = DEP5Parser()
        spdx_data = load_spdx_license_data()
        license_mapper = LicenseMapper(spdx_license_data=spdx_data)

        extractor = DebExtractor(
            iso_reader=iso_reader,
            deb_parser=deb_parser,
            dep5_parser=dep5_parser,
            license_mapper=license_mapper,
        )
        return extractor, iso_reader
    except (ImportError, OSError, ValueError, TypeError) as exc:
        logging.getLogger(__name__).warning(
            "Failed to create DebExtractor for ISO artifact: %s",
            exc,
        )
        return None, None


def _collect_results(formats: list[OutputFormat], output_dir: Path) -> list[WriterResult]:
    """Collect WriterResult entries from written SBOM files.

    Reads each output file, computes its SHA-256 hash and size,
    and returns results for files that exist.

    Args:
        formats: Output formats that were requested.
        output_dir: Directory where output files were written.

    Returns:
        List of WriterResult for existing output files.
    """
    import hashlib

    from debcraft.domain.sbom.values import WriterResult

    results: list[WriterResult] = []
    for fmt in formats:
        output_path = output_dir / _get_output_filename(fmt)
        if output_path.exists():
            content = output_path.read_bytes()
            sha256 = hashlib.sha256(content).hexdigest()
            file_size = len(content)
            results.append(
                WriterResult(
                    output_path=output_path,
                    format=fmt,
                    sha256=sha256,
                    file_size=file_size,
                )
            )
    return results


async def _run_sbom(  # pylint: disable=too-many-locals
    artifact_path: Path,
    formats: list[OutputFormat],
    output_dir: Path,
    artifact_type: str | None,
    *,
    snapshot_id: int | None = None,
    quiet: bool,
    progress: Progress,
    task_id: TaskID,
) -> list[WriterResult]:
    """Run the SBOM generation workflow.

    Creates the workflow and all minimal CLI dependencies, then executes
    the SBOM generation pipeline. Manages the database engine lifecycle
    (create engines → resolve snapshot → execute workflow → dispose engines).

    When the artifact is an ISO, creates a DebExtractor for direct .deb
    extraction from the ISO's pool/ directory and passes it to the DI scope
    so the MetadataEnricher can use it as a fallback enrichment source.

    Args:
        artifact_path: Path to the artifact to scan.
        formats: Output formats to produce.
        output_dir: Directory for output files.
        artifact_type: Optional artifact type override.
        snapshot_id: Explicit snapshot ID from CLI flag, or None for auto-detection.
        quiet: Whether to suppress progress output.
        progress: Rich Progress instance.
        task_id: Rich progress task ID.

    Returns:
        List of WriterResult from successful writes.

    Raises:
        Exception: On workflow failures.
    """
    from debcraft.cli._sbom_db import create_database_engines, resolve_snapshot_id
    from debcraft.infrastructure.sbom_writers.workflow import SBOMWorkflow, SBOMWorkflowConfig

    # Create database engines for metadata.db and cache.db
    engines = await create_database_engines()
    iso_reader_for_deb_extraction: object | None = None

    try:
        # Resolve the snapshot ID (auto-detect from metadata.db or use explicit value)
        resolved_snapshot_id = await resolve_snapshot_id(
            session_factory=engines.metadata_session_factory,
            explicit_id=snapshot_id,
        )

        # Create DebExtractor for ISO artifacts so the MetadataEnricher can
        # fall back to direct .deb extraction from the ISO's pool/ directory
        deb_extractor, iso_reader_for_deb_extraction = _create_deb_extractor_for_iso(artifact_path, artifact_type)

        # Build workflow configuration
        config = SBOMWorkflowConfig(
            artifact_path=str(artifact_path),
            output_dir=output_dir,
            formats=formats,
            artifact_type=artifact_type,
            snapshot_id=resolved_snapshot_id,
        )

        # Create minimal CLI infrastructure
        event_bus, cancellation_token, progress_reporter, resource_manager, logger = _create_cli_infrastructure(
            progress, task_id, quiet
        )

        # Create the DI scope with all domain services
        scope = _create_di_scope(snapshot_id=resolved_snapshot_id, engines=engines, deb_extractor=deb_extractor)

        # Create workflow context
        context = WorkflowContext(
            scope=scope,  # type: ignore[arg-type]
            cancellation_token=cancellation_token,
            progress_reporter=progress_reporter,
            resource_manager=resource_manager,
            logger=logger,
            event_bus=event_bus,
        )

        # Create and execute workflow
        workflow = SBOMWorkflow(config=config)
        await workflow.execute(context)

        # Collect results from the written files
        return _collect_results(formats, output_dir)
    finally:
        await engines.dispose()
        # Close the ISO reader used for .deb extraction if one was opened
        if iso_reader_for_deb_extraction is not None:
            with suppress(Exception):
                iso_reader_for_deb_extraction.close()  # type: ignore[attr-defined]


def _get_output_filename(fmt: OutputFormat) -> str:
    """Generate the output filename for a given format.

    Args:
        fmt: The output format.

    Returns:
        The filename string.
    """
    filenames = {
        OutputFormat.SPDX_3_0: "sbom.spdx3.json",
        OutputFormat.SPDX_2_3: "sbom.spdx.json",
        OutputFormat.CYCLONEDX: "sbom.cdx.json",
    }
    return filenames.get(fmt, f"sbom.{fmt.value}.json")


class _NoOpCacheAdapter:
    """No-op enrichment cache adapter for CLI context."""

    async def get(
        self,
        _package_name: str,
        _version: str,
        _architecture: str,
        _snapshot_id: int,
    ) -> None:
        """Always returns None (no cache available)."""
        return None

    async def put(
        self,
        package_name: str,
        version: str,
        architecture: str,
        snapshot_id: int,
        enrichment: object,
    ) -> None:
        """No-op put."""


class _NoOpDatabaseProvider:
    """No-op database provider for CLI context when persistence is not needed."""

    async def get_session(self, _db_name: str) -> None:
        """Return None (no database in CLI mode)."""
        return None

    async def dispose(self) -> None:
        """No-op dispose."""

    async def health_check(self) -> dict[str, bool]:
        """Always healthy."""
        return {"metadata": True}


class _NoOpContentsIndexPort:
    """No-op contents index port for CLI context (conforms to ContentsIndexPort protocol)."""

    async def find_owners(  # pylint: disable=unused-argument
        self, file_paths: list[str], snapshot_id: int
    ) -> dict[str, str]:
        """Always returns empty results (no contents index available in CLI mode)."""
        return {}


class _NoOpPackageLookupPort:
    """No-op package lookup port for CLI context (conforms to PackageLookupPort protocol)."""

    async def find_by_name(  # pylint: disable=unused-argument
        self, package_name: str, snapshot_id: int
    ) -> tuple[str, str, str] | None:
        """Always returns None (no package lookup available in CLI mode)."""
        return None


def sbom(  # pylint: disable=too-many-positional-arguments
    artifact_path: Annotated[Path, typer.Argument(help="Path to the artifact to generate SBOM for.")],
    format: Annotated[  # noqa: A002
        list[str] | None,
        typer.Option("--format", "-f", help="Output format(s). Repeatable. Valid: spdx_3_0, spdx_2_3, cyclonedx."),
    ] = None,
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o", help="Output directory for SBOM files.")] = Path(
        "."
    ),
    type: Annotated[  # noqa: A002
        str | None, typer.Option("--type", "-t", help="Artifact type override for scanner.")
    ] = None,
    snapshot_id: Annotated[
        str | None,
        typer.Option("--snapshot-id", help="Repository snapshot ID for enrichment (positive integer)."),
    ] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Suppress progress output.")] = False,
) -> None:
    """Generate SBOM for an artifact in one or more formats.

    Scans the artifact, enriches package metadata, assembles an internal
    SBOM model, and writes output in the requested format(s).
    """
    # Validate inputs before workflow execution
    _validate_artifact_path(artifact_path)
    formats = _validate_formats(format)
    _validate_output_dir(output_dir)
    validated_snapshot_id = _validate_snapshot_id(snapshot_id)

    # Run workflow with Rich progress
    results: list[WriterResult] = []
    try:
        with create_progress_bar(disabled=quiet) as progress:
            task_id = progress.add_task("Starting SBOM generation...", total=100)
            results = asyncio.run(
                _run_sbom(
                    artifact_path=artifact_path,
                    formats=formats,
                    output_dir=output_dir,
                    artifact_type=type,
                    snapshot_id=validated_snapshot_id,
                    quiet=quiet,
                    progress=progress,
                    task_id=task_id,
                )
            )
    except (SystemExit, typer.Exit):
        raise
    except Exception as exc:
        console.print(f"\n[red]SBOM generation failed:[/red] {exc}")
        logging.getLogger("debcraft.cli.sbom").debug("Full exception details", exc_info=exc)
        # Clean up partial files on failure
        for fmt in formats:
            partial_path = output_dir / _get_output_filename(fmt)
            if partial_path.exists():
                with suppress(OSError):
                    partial_path.unlink()
        raise typer.Exit(code=1) from None

    if not results:
        console.print("[red]Error:[/red] No SBOM files were generated.")
        raise typer.Exit(code=1)

    # Display summary table
    if not quiet:
        console.print()
    _display_summary(results)

    # Display diagnostics warnings if present
    _display_diagnostics(results)

    raise typer.Exit(code=0)
