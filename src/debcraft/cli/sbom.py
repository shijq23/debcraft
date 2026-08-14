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
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from debcraft.domain.sbom.values import OutputFormat
from debcraft.platform.contracts.events import EventBus
from debcraft.platform.contracts.logging import Logger
from debcraft.platform.contracts.resources import ResourceManager
from debcraft.platform.contracts.workflow import CancellationToken, ProgressReporter, WorkflowContext

if TYPE_CHECKING:
    from uuid import UUID

    from rich.progress import TaskID

    from debcraft.domain.sbom.values import WriterResult
    from debcraft.platform.contracts.events import DomainEvent, EventHandler

T = TypeVar("T")

console = Console()

# Valid format values (matching OutputFormat enum)
_VALID_FORMATS = {f.value for f in OutputFormat}


def _format_bytes(n: int) -> str:
    """Format byte count as human-readable string.

    Args:
        n: Number of bytes.

    Returns:
        Human-readable string (e.g., "1.5 MiB").
    """
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KiB"
    elif n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MiB"
    else:
        return f"{n / (1024 * 1024 * 1024):.1f} GiB"


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
        value = await resource.__aenter__()
        self._async_resources.append(resource)
        return value

    def acquire_sync(self, resource: AbstractContextManager[T]) -> T:
        """Enter and track a sync context manager."""
        value = resource.__enter__()
        self._sync_resources.append(resource)
        return value

    async def cleanup(self) -> None:
        """Clean up all acquired resources in reverse order."""
        for async_resource in reversed(self._async_resources):
            with suppress(Exception):
                await async_resource.__aexit__(None, None, None)
        self._async_resources.clear()

        for sync_resource in reversed(self._sync_resources):
            with suppress(Exception):
                sync_resource.__exit__(None, None, None)
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
            _format_bytes(result.file_size),
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


async def _run_sbom(
    artifact_path: Path,
    formats: list[OutputFormat],
    output_dir: Path,
    artifact_type: str | None,
    quiet: bool,
    progress: Progress,
    task_id: TaskID,
) -> list[WriterResult]:
    """Run the SBOM generation workflow.

    Creates the workflow and all minimal CLI dependencies, then executes
    the SBOM generation pipeline.

    Args:
        artifact_path: Path to the artifact to scan.
        formats: Output formats to produce.
        output_dir: Directory for output files.
        artifact_type: Optional artifact type override.
        quiet: Whether to suppress progress output.
        progress: Rich Progress instance.
        task_id: Rich progress task ID.

    Returns:
        List of WriterResult from successful writes.

    Raises:
        Exception: On workflow failures.
    """
    from debcraft.domain.sbom.assembler import ModelAssembler
    from debcraft.infrastructure.sbom_writers.registry import WriterRegistry
    from debcraft.infrastructure.sbom_writers.workflow import SBOMWorkflow, SBOMWorkflowConfig
    from debcraft.infrastructure.scanners.enricher import MetadataEnricher
    from debcraft.infrastructure.scanners.registry import ScannerRegistry
    from debcraft.platform.contracts.persistence import DatabaseProvider

    # Build workflow configuration
    config = SBOMWorkflowConfig(
        artifact_path=str(artifact_path),
        output_dir=output_dir,
        formats=formats,
        artifact_type=artifact_type,
    )

    # Create minimal CLI infrastructure
    event_bus = _CliEventBus()
    cancellation_token = CancellationToken()
    progress_reporter = _CliProgressReporter(progress, task_id, quiet)
    resource_manager = _CliResourceManager()
    logger = _CliLogger()

    # Build registries
    scanner_registry = ScannerRegistry()
    scanner_registry.load_from_entry_points()

    writer_registry = WriterRegistry()
    writer_registry.load_from_entry_points()

    # Build enricher with a no-op cache adapter
    cache_adapter = _NoOpCacheAdapter()
    enricher = MetadataEnricher(cache_adapter=cache_adapter)  # type: ignore[arg-type]

    # Build assembler
    assembler = ModelAssembler()

    # Build a no-op database provider
    db_provider = _NoOpDatabaseProvider()

    # Create the DI scope with all dependencies
    scope = _CliScope()
    scope.register(ScannerRegistry, scanner_registry)
    scope.register(WriterRegistry, writer_registry)
    scope.register(MetadataEnricher, enricher)
    scope.register(ModelAssembler, assembler)
    scope.register(DatabaseProvider, db_provider)

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
    results: list[WriterResult] = []
    for fmt in formats:
        output_path = output_dir / _get_output_filename(fmt)
        if output_path.exists():
            import hashlib

            content = output_path.read_bytes()
            sha256 = hashlib.sha256(content).hexdigest()
            file_size = len(content)

            from debcraft.domain.sbom.values import WriterResult

            results.append(
                WriterResult(
                    output_path=output_path,
                    format=fmt,
                    sha256=sha256,
                    file_size=file_size,
                )
            )

    return results


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
        package_name: str,
        version: str,
        architecture: str,
        snapshot_id: int,
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

    async def get_session(self, db_name: str) -> None:
        """Return None (no database in CLI mode)."""
        return None

    async def dispose(self) -> None:
        """No-op dispose."""

    async def health_check(self) -> dict[str, bool]:
        """Always healthy."""
        return {"metadata": True}


def sbom(
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

    # Run workflow with Rich progress
    results: list[WriterResult] = []
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
            disable=quiet,
        ) as progress:
            task_id = progress.add_task("Starting SBOM generation...", total=100)
            results = asyncio.run(
                _run_sbom(
                    artifact_path=artifact_path,
                    formats=formats,
                    output_dir=output_dir,
                    artifact_type=type,
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
