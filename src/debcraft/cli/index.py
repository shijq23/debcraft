"""Repository indexer CLI commands."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.table import Table

from debcraft.cli._progress import create_progress_bar
from debcraft.cli._storage import MinimalStorageEngine
from debcraft.infrastructure.storage.paths import resolve_xdg_path
from debcraft.platform.contracts.events import EventBus

if TYPE_CHECKING:
    from rich.progress import Progress, TaskID
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

    from debcraft.domain.indexer.service import IndexerService
    from debcraft.domain.indexer.values import IndexResult
    from debcraft.domain.mirror.config import MirrorConfig
    from debcraft.platform.contracts.events import DomainEvent, EventHandler

index_app = typer.Typer(name="index", help="Repository indexing commands.")
console = Console()


class _CliEventBus(EventBus):
    """No-op event bus for CLI context."""

    def subscribe(self, event_type: type, handler: EventHandler) -> None:
        pass

    def unsubscribe(self, event_type: type, handler: EventHandler) -> None:
        pass

    async def publish(self, event: DomainEvent) -> None:
        pass


def _create_session_factories() -> tuple[
    async_sessionmaker[AsyncSession], async_sessionmaker[AsyncSession], AsyncEngine, AsyncEngine
]:
    """Create async session factories for mirror.db and metadata.db.

    Returns:
        A tuple of (mirror_session_factory, metadata_session_factory, mirror_engine, metadata_engine).

    Raises:
        typer.Exit: If the databases cannot be accessed.
    """
    from debcraft.infrastructure.database.session import (
        create_async_engine_for,
        create_session_factory,
    )

    db_dir = resolve_xdg_path("database")
    db_dir.mkdir(parents=True, exist_ok=True)

    mirror_db_path = db_dir / "mirror.db"
    metadata_db_path = db_dir / "metadata.db"

    mirror_engine = create_async_engine_for(mirror_db_path)
    metadata_engine = create_async_engine_for(metadata_db_path)

    mirror_session_factory = create_session_factory(mirror_engine)
    metadata_session_factory = create_session_factory(metadata_engine)

    return mirror_session_factory, metadata_session_factory, mirror_engine, metadata_engine


def _build_indexer_service(
    mirror_session_factory: async_sessionmaker[AsyncSession],
    metadata_session_factory: async_sessionmaker[AsyncSession],
) -> IndexerService:
    """Build an IndexerService with all required dependencies.

    Args:
        mirror_session_factory: Session factory for mirror.db.
        metadata_session_factory: Session factory for metadata.db.

    Returns:
        A configured IndexerService instance.
    """
    from debcraft.domain.indexer.service import IndexerService
    from debcraft.infrastructure.indexer.file_reader import LocalFileReader
    from debcraft.infrastructure.indexer.mapper import IndexerMapper
    from debcraft.infrastructure.indexer.mirror_file_repository import (
        SqlAlchemyMirrorFileRepository,
    )
    from debcraft.infrastructure.indexer.repository import SqlAlchemyMetadataRepository

    file_reader = LocalFileReader()
    mapper = IndexerMapper()
    metadata_repository = SqlAlchemyMetadataRepository(
        session_factory=metadata_session_factory,
        mapper=mapper,
    )
    mirror_file_repository = SqlAlchemyMirrorFileRepository(
        mirror_session_factory=mirror_session_factory,
        metadata_session_factory=metadata_session_factory,
    )
    event_bus = _CliEventBus()

    return IndexerService(
        file_reader=file_reader,
        metadata_repository=metadata_repository,
        mirror_file_repository=mirror_file_repository,
        event_bus=event_bus,
    )


async def _ensure_schemas(mirror_engine: AsyncEngine, metadata_engine: AsyncEngine) -> None:
    """Create database tables if they don't already exist.

    Args:
        mirror_engine: Async engine for mirror.db.
        metadata_engine: Async engine for metadata.db.
    """
    import debcraft.infrastructure.models.metadata as _metadata_models  # noqa: F401
    import debcraft.infrastructure.models.mirror as _mirror_models  # noqa: F401
    from debcraft.infrastructure.models.base import Base

    async with mirror_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with metadata_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _display_index_summary(results: list[IndexResult]) -> None:
    """Display a Rich summary table with indexing results.

    Args:
        results: List of IndexResult from the indexing run.
    """
    table = Table(title="Indexing Summary")
    table.add_column("Repository", style="bold")
    table.add_column("Packages", justify="right")
    table.add_column("Source Pkgs", justify="right")
    table.add_column("File Ownerships", justify="right")
    table.add_column("Skipped", justify="right")
    table.add_column("Status")

    for result in results:
        status = "[green]OK[/green]" if result.success else f"[red]FAILED[/red] {result.error or ''}"
        table.add_row(
            result.repository_name,
            str(result.packages_indexed),
            str(result.source_packages_indexed),
            str(result.file_ownerships_indexed),
            str(result.files_skipped),
            status,
        )

    console.print(table)


def _discover_repos_to_index(config: MirrorConfig, repository: str | None) -> list[tuple[str, str, str, str]]:
    """Determine which repositories to index based on config and optional filter.

    Args:
        config: The parsed mirror configuration.
        repository: Optional repository name filter. If None, discovers all.

    Returns:
        List of (name, base_url, suite, component) tuples to index.
    """
    repo_configs = {repo.name: repo for repo in config.repositories}
    repos_to_index: list[tuple[str, str, str, str]] = []

    if repository:
        if repository in repo_configs:
            repo_cfg = repo_configs[repository]
            for suite in repo_cfg.suites:
                for component in repo_cfg.components:
                    repos_to_index.append((repo_cfg.name, repo_cfg.base_url, suite, component))
        else:
            repos_to_index.append((repository, "", "", ""))
    else:
        for repo_cfg in config.repositories:
            for suite in repo_cfg.suites:
                for component in repo_cfg.components:
                    repos_to_index.append((repo_cfg.name, repo_cfg.base_url, suite, component))

    return repos_to_index


async def _index_repositories(
    repos_to_index: list[tuple[str, str, str, str]],
    indexer_service: IndexerService,
    progress: Progress,
    task_id: TaskID,
) -> list[IndexResult]:
    """Index each repository and report progress.

    Args:
        repos_to_index: List of (name, base_url, suite, component) tuples.
        indexer_service: The indexer service to use for indexing.
        progress: Rich Progress instance for display updates.
        task_id: Rich progress task ID.

    Returns:
        List of IndexResult from the indexing run.
    """
    results: list[IndexResult] = []
    total = len(repos_to_index)

    for i, (name, base_url, suite, component) in enumerate(repos_to_index):
        progress.update(
            task_id,
            completed=(i / total) * 100,
            description=f"Indexing {name} ({suite}/{component})...",
        )

        result = await indexer_service.index_repository(
            repository_name=name,
            base_url=base_url,
            suite=suite,
            component=component,
        )
        results.append(result)

    progress.update(task_id, completed=100, description="Indexing complete")
    return results


async def _run_index(repository: str | None, progress: Progress, task_id: TaskID) -> list[IndexResult]:
    """Run the indexing workflow.

    Args:
        repository: Optional repository name filter. If None, indexes all.
        progress: Rich Progress instance for display updates.
        task_id: Rich progress task ID.

    Returns:
        List of IndexResult from the indexing run.

    Raises:
        Exception: On database failures.
    """
    from debcraft.infrastructure.mirror.config_reader import ConfigReader

    mirror_session_factory, metadata_session_factory, mirror_engine, metadata_engine = _create_session_factories()

    try:
        await _ensure_schemas(mirror_engine, metadata_engine)

        indexer_service = _build_indexer_service(mirror_session_factory, metadata_session_factory)

        from debcraft.infrastructure.indexer.mirror_file_repository import (
            SqlAlchemyMirrorFileRepository,
        )

        mirror_file_repo = SqlAlchemyMirrorFileRepository(
            mirror_session_factory=mirror_session_factory,
            metadata_session_factory=metadata_session_factory,
        )

        verified_files = await mirror_file_repo.get_verified_files(repository_name=repository)

        if not verified_files:
            return []

        storage_engine = MinimalStorageEngine()
        reader = ConfigReader(storage_engine)
        config = reader.read()

        repos_to_index = _discover_repos_to_index(config, repository)

        if not repos_to_index:
            return []

        return await _index_repositories(repos_to_index, indexer_service, progress, task_id)

    finally:
        await mirror_engine.dispose()
        await metadata_engine.dispose()


@index_app.command()
def package(
    name: str = typer.Argument(..., help="Package name to look up."),
) -> None:
    """Display the latest indexed metadata for a package.

    Looks up the package by name in the metadata database and displays
    version, architecture, source package, and other metadata fields.
    """
    from rich.panel import Panel

    from debcraft.infrastructure.database.session import (
        create_async_engine_for,
        create_session_factory,
    )
    from debcraft.infrastructure.indexer.mapper import IndexerMapper
    from debcraft.infrastructure.indexer.repository import (
        SqlAlchemyMetadataRepository,
    )

    db_dir = resolve_xdg_path("database")
    metadata_db_path = db_dir / "metadata.db"

    if not metadata_db_path.exists():
        console.print(f"[red]Package not found:[/red] {name}")
        raise typer.Exit(code=1)

    engine = create_async_engine_for(metadata_db_path)

    async def _lookup() -> None:
        session_factory = create_session_factory(engine)
        mapper = IndexerMapper()
        repo = SqlAlchemyMetadataRepository(
            session_factory=session_factory,
            mapper=mapper,
        )

        try:
            metadata = await repo.get_package_metadata(name)
        finally:
            await engine.dispose()

        if metadata is None:
            console.print(f"[red]Package not found:[/red] {name}")
            raise typer.Exit(code=1)

        # Build a table showing package details
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("Field", style="bold cyan")
        table.add_column("Value")

        table.add_row("Package", metadata.package_name)
        table.add_row("Version", metadata.version)
        table.add_row("Architecture", metadata.architecture)
        table.add_row("Source", metadata.source_package)
        table.add_row("Source Version", metadata.source_version)

        if metadata.section:
            table.add_row("Section", metadata.section)
        if metadata.priority:
            table.add_row("Priority", metadata.priority)
        if metadata.maintainer:
            table.add_row("Maintainer", metadata.maintainer)
        if metadata.homepage:
            table.add_row("Homepage", metadata.homepage)
        if metadata.description:
            desc = metadata.description
            if len(desc) > 200:
                desc = desc[:200] + "..."
            table.add_row("Description", desc)

        console.print(Panel(table, title=f"[bold]{name}[/bold]"))

    asyncio.run(_lookup())


@index_app.callback(invoke_without_command=True)
def index(
    ctx: typer.Context,
    repository: str | None = typer.Option(None, "--repository", "-r", help="Index only the specified repository."),
) -> None:
    """Index repositories that have VERIFIED files in the mirror cache.

    Parses Debian repository metadata files (Packages, Sources, Contents,
    Release) from the local mirror cache and persists structured package
    metadata into the metadata database.
    """
    if ctx.invoked_subcommand is not None:
        return

    logger = logging.getLogger("debcraft.cli.index")

    try:
        with create_progress_bar() as progress:
            task_id = progress.add_task("Starting indexing...", total=100)
            results = asyncio.run(_run_index(repository, progress, task_id))
    except Exception as exc:
        error_msg = str(exc)
        logger.debug("Indexing failed", exc_info=exc)
        console.print(f"[red]Error:[/red] Indexing failed: {error_msg}")
        console.print(
            "[dim]Suggested fix: Check database connectivity and ensure mirror.db "
            "and metadata.db are accessible. Run 'debcraft doctor' for diagnostics.[/dim]"
        )
        raise typer.Exit(code=1) from None

    # Handle no VERIFIED files case
    if not results:
        console.print(
            "[yellow]No VERIFIED files found for indexing.[/yellow] "
            "Run 'debcraft mirror sync' first to download and verify repository files."
        )
        raise typer.Exit(code=0)

    # Display summary
    console.print()
    _display_index_summary(results)

    # Check for failures
    failed = [r for r in results if not r.success]
    if failed:
        console.print(f"\n[yellow]Warning:[/yellow] {len(failed)} repository(ies) failed to index.")

    total_packages = sum(r.packages_indexed for r in results)
    console.print(f"\n[green]Indexing completed.[/green] {total_packages} packages indexed.")
    raise typer.Exit(code=0)
