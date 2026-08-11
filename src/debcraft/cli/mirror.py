"""Mirror management CLI commands."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn, TimeElapsedColumn
from rich.table import Table
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from debcraft.infrastructure.mirror.config_reader import ConfigReader
from debcraft.infrastructure.mirror.errors import DownloadError, MirrorConfigurationError, MirrorError
from debcraft.infrastructure.storage.paths import resolve_xdg_path
from debcraft.platform.contracts.events import EventBus
from debcraft.platform.contracts.logging import Logger
from debcraft.platform.contracts.persistence import DatabaseProvider
from debcraft.platform.contracts.storage import StorageEngine
from debcraft.platform.contracts.workflow import CancellationToken, ProgressReporter

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from debcraft.domain.mirror.config import MirrorConfig
    from debcraft.platform.contracts.events import DomainEvent, EventHandler
    from debcraft.platform.contracts.persistence import DatabaseName
    from debcraft.platform.contracts.storage import StoragePurpose

mirror_app = typer.Typer(name="mirror", help="Repository mirror management commands.")
console = Console()


class _MinimalStorageEngine(StorageEngine):
    """Minimal storage engine for CLI config and mirror path resolution.

    Provides just enough of the StorageEngine interface (get_path)
    for ConfigReader and MirrorEngine to resolve paths without requiring
    full platform bootstrap.
    """

    def __init__(self) -> None:
        xdg_config = os.environ.get("XDG_CONFIG_HOME", "")
        if xdg_config:
            self._config_dir = Path(xdg_config) / "debcraft"
        else:
            self._config_dir = Path.home() / ".config" / "debcraft"

        xdg_cache = os.environ.get("XDG_CACHE_HOME", "")
        if xdg_cache:
            self._cache_dir = Path(xdg_cache) / "debcraft"
        else:
            self._cache_dir = Path.home() / ".cache" / "debcraft"

    async def initialize(self) -> None:
        """No-op for CLI context."""

    async def shutdown(self) -> None:
        """No-op for CLI context."""

    def get_path(self, purpose: StoragePurpose, relative: str = "") -> Path:
        """Resolve path for a storage purpose.

        Args:
            purpose: The named storage purpose ('config' or 'mirror').
            relative: Optional relative path within the purpose directory.

        Returns:
            Absolute path to the resolved location.
        """
        if purpose == "config":
            base = self._config_dir
        elif purpose == "mirror":
            base = self._cache_dir / "mirror"
        else:
            msg = f"Unsupported storage purpose for CLI: {purpose}"
            raise ValueError(msg)

        if relative:
            return base / relative
        return base

    async def __aenter__(self) -> _MinimalStorageEngine:
        """Enter async context."""
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Exit async context."""


class _CliDatabaseProvider(DatabaseProvider):
    """Persistent database provider for CLI context.

    Provides a valid AsyncSession backed by a file-backed SQLite database
    at the XDG data directory (e.g., ``~/.local/share/debcraft/mirror.db``
    on Linux). This satisfies the DatabaseProvider contract with persistent
    storage — records written in one process invocation are available in
    subsequent invocations.

    The database has the mirror schema (repository_files, sync_sessions)
    created on first use so that MirrorEngine queries execute without
    'no such table' errors.
    """

    def __init__(self) -> None:
        db_path = resolve_xdg_path("database") / "mirror.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)
        self._initialized = False

    async def _ensure_schema(self) -> None:
        """Create mirror tables in the database if not already done."""
        if not self._initialized:
            # Import models to register them with Base.metadata
            import debcraft.infrastructure.models.mirror  # noqa: F401
            from debcraft.infrastructure.models.base import Base

            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            self._initialized = True

    async def get_session(self, db_name: DatabaseName) -> AsyncSession:
        await self._ensure_schema()
        return self._session_factory()

    async def dispose(self) -> None:
        await self._engine.dispose()

    async def health_check(self) -> dict[str, bool]:
        return {"mirror": True, "metadata": True, "cache": True}


def _read_config() -> MirrorConfig:
    """Read mirror configuration using a minimal storage engine.

    Returns:
        The parsed MirrorConfig (defaults if mirrors.toml is absent).

    Raises:
        MirrorConfigurationError: If config file is invalid.
    """
    storage = _MinimalStorageEngine()
    reader = ConfigReader(storage)
    return reader.read()


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


def _display_sync_summary(result: dict[str, int]) -> None:
    """Display a Rich summary table with sync results.

    Args:
        result: Dictionary with downloaded, skipped, failed, bytes_transferred.
    """
    table = Table(title="Mirror Sync Summary")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Files downloaded", str(result["downloaded"]))
    table.add_row("Files skipped", str(result["skipped"]))
    table.add_row("Files failed", str(result["failed"]))
    table.add_row("Bytes transferred", _format_bytes(result["bytes_transferred"]))

    console.print(table)


async def _run_sync(config: MirrorConfig, progress: Progress, task_id: TaskID) -> dict[str, int]:
    """Run the mirror synchronization workflow.

    Creates the download coordinator and mirror engine, then syncs each
    configured repository. Reports progress through the Rich progress bar.

    Args:
        config: Validated mirror configuration.
        progress: Rich Progress instance for display updates.
        task_id: Rich progress task ID for updating the bar.

    Returns:
        Dictionary with keys: downloaded, skipped, failed, bytes_transferred.
    """
    from uuid import uuid4

    from debcraft.infrastructure.mirror.download import DownloadCoordinator
    from debcraft.infrastructure.mirror.engine import MirrorEngine

    storage_engine = _MinimalStorageEngine()

    class _CliProgressReporter(ProgressReporter):
        """Reports progress updates to the Rich progress bar."""

        def report(self, percentage: float, message: str = "") -> None:
            progress.update(task_id, completed=percentage, description=message)

    class _CliCancellationToken(CancellationToken):
        """Non-cancelling token for normal CLI operation."""

        def __init__(self) -> None:
            super().__init__()

    class _CliEventBus(EventBus):
        """No-op event bus for CLI context."""

        def subscribe(self, event_type: type, handler: EventHandler) -> None:
            pass

        def unsubscribe(self, event_type: type, handler: EventHandler) -> None:
            pass

        async def publish(self, event: DomainEvent) -> None:
            pass

    class _CliLogger(Logger):
        """Logger that delegates to Python's logging module.

        When --verbose is active the root 'debcraft' logger has a
        DEBUG-level handler, so messages propagate to stderr.  Without
        --verbose the root logger is at WARNING by default and these
        messages are silently discarded.
        """

        def __init__(self) -> None:
            import logging

            self._log = logging.getLogger("debcraft.cli.mirror")

        def info(self, message: str, **kwargs: object) -> None:
            self._log.info(message, extra={"extra_data": kwargs})

        def debug(self, message: str, **kwargs: object) -> None:
            self._log.debug(message, extra={"extra_data": kwargs})

        def warning(self, message: str, **kwargs: object) -> None:
            self._log.warning(message, extra={"extra_data": kwargs})

        def error(self, message: str, **kwargs: object) -> None:
            self._log.error(message, extra={"extra_data": kwargs})

        def with_correlation_id(self, correlation_id: UUID) -> Logger:
            return self

    event_bus = _CliEventBus()
    cancellation_token = _CliCancellationToken()
    progress_reporter = _CliProgressReporter()
    logger = _CliLogger()
    db_provider = _CliDatabaseProvider()

    download_coordinator = DownloadCoordinator(
        storage_engine=storage_engine,
        config=config,
    )

    total_result = {"downloaded": 0, "skipped": 0, "failed": 0, "bytes_transferred": 0}

    await download_coordinator.start()
    try:
        for repo_config in config.repositories:
            session_id = str(uuid4())
            progress.update(task_id, description=f"Syncing {repo_config.name}...")

            engine = MirrorEngine(
                download_coordinator=download_coordinator,
                db_provider=db_provider,
                storage_engine=storage_engine,
                event_bus=event_bus,
                cancellation_token=cancellation_token,
                progress=progress_reporter,
                logger=logger,
            )

            result = await engine.sync_repository(repo_config, session_id)
            total_result["downloaded"] += result.files_downloaded
            total_result["skipped"] += result.files_skipped
            total_result["failed"] += result.files_failed
            total_result["bytes_transferred"] += result.bytes_transferred
    finally:
        await download_coordinator.close()

    return total_result


@mirror_app.command()
def sync() -> None:
    """Synchronize all configured repositories."""
    # Read configuration
    try:
        config = _read_config()
    except MirrorConfigurationError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        console.print("[dim]Suggested fix: Check your mirrors.toml file for syntax errors.[/dim]")
        raise typer.Exit(code=1) from None

    # Check that repositories are configured (Req 10.8)
    if not config.repositories:
        console.print("[red]Error:[/red] No repositories configured.")
        console.print("[dim]Add repository entries to your mirrors.toml configuration file.[/dim]")
        raise typer.Exit(code=1)

    # Display what we're about to sync
    repo_names = ", ".join(r.name for r in config.repositories)
    console.print(f"Syncing {len(config.repositories)} repository(ies): {repo_names}")

    # Run sync with Rich progress bar (Req 10.6)
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task("Starting sync...", total=100)
            result = asyncio.run(_run_sync(config, progress, task_id))
    except MirrorError as exc:
        # Structured error message (Req 10.7)
        console.print(f"\n[red]Sync failed:[/red] {exc}")
        if isinstance(exc, DownloadError):
            console.print(f"[dim]  Affected URL: {exc.url}[/dim]")
        console.print("[dim]Suggested fix: Check network connectivity and repository availability.[/dim]")
        raise typer.Exit(code=1) from None
    except Exception as exc:
        console.print(f"\n[red]Unexpected error during sync:[/red] {exc}")
        # Unpack ExceptionGroup/BaseExceptionGroup sub-exceptions for visibility
        if isinstance(exc, BaseExceptionGroup):
            for i, sub_exc in enumerate(exc.exceptions, 1):
                console.print(f"[red]  Sub-exception {i}:[/red] {type(sub_exc).__name__}: {sub_exc}")
        import logging as _logging

        _logging.getLogger("debcraft.cli.mirror").debug("Full exception details", exc_info=exc)
        console.print("[dim]Suggested fix: Run with --verbose for more details or check logs.[/dim]")
        raise typer.Exit(code=1) from None

    # Display summary table (Req 10.1)
    console.print()
    _display_sync_summary(result)

    # Exit with appropriate code (Req 10.9)
    if result["failed"] > 0:
        console.print(f"\n[yellow]Warning:[/yellow] {result['failed']} file(s) failed to sync.")

    console.print("\n[green]Sync completed successfully.[/green]")
    raise typer.Exit(code=0)


@mirror_app.command()
def verify() -> None:
    """Verify checksums of all cached files in the mirror cache.

    Computes SHA256 of each file tracked in mirror.db (VERIFIED or INDEXED
    state) and compares against the stored checksum. Reports the number of
    files checked, any mismatches with their paths, and a final pass/fail
    status line.
    """
    import hashlib

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from debcraft.infrastructure.models.mirror import RepositoryFile, RepositoryFileState

    db_dir = resolve_xdg_path("database")
    db_path = db_dir / "mirror.db"

    if not db_path.exists():
        console.print("[red]Error:[/red] Mirror database not found. No repositories have been synced.")
        raise typer.Exit(code=1)

    # Connect to mirror.db using synchronous SQLAlchemy
    engine = create_engine(f"sqlite:///{db_path}", echo=False)

    # Query RepositoryFile entities in VERIFIED or INDEXED state with a local_path
    with Session(engine) as session:
        stmt = select(RepositoryFile).where(
            RepositoryFile.state.in_([RepositoryFileState.VERIFIED, RepositoryFileState.INDEXED]),
            RepositoryFile.local_path.isnot(None),
        )
        files = session.execute(stmt).scalars().all()
        # Detach from session so we can close it before the long verification
        file_data = [(entry.local_path, entry.sha256) for entry in files if entry.local_path is not None]

    engine.dispose()

    if not file_data:
        console.print("[yellow]No verified files found in mirror database.[/yellow]")
        raise typer.Exit(code=0)

    # Verify each file's SHA256 against the stored checksum
    mismatches: list[tuple[str, str, str]] = []  # (path, expected, actual_or_error)
    missing: list[str] = []
    checked = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Verifying checksums...", total=len(file_data))

        for local_path, expected_sha256 in file_data:
            file_path = Path(local_path)

            if not file_path.exists():
                missing.append(str(file_path))
                progress.advance(task)
                continue

            # Compute SHA256 in 64 KiB chunks
            try:
                sha256 = hashlib.sha256()
                with open(file_path, "rb") as f:
                    while chunk := f.read(65536):
                        sha256.update(chunk)
                computed = sha256.hexdigest()
            except OSError:
                computed = None

            checked += 1

            if computed is None:
                mismatches.append((str(file_path), expected_sha256, "<unreadable>"))
            elif computed != expected_sha256:
                mismatches.append((str(file_path), expected_sha256, computed))

            progress.advance(task)

    # Display results
    console.print()
    console.print(f"Files checked: [bold]{checked}[/bold]")
    if missing:
        console.print(f"Files missing: [yellow]{len(missing)}[/yellow]")
    console.print(f"Mismatches:    [bold]{len(mismatches)}[/bold]")
    console.print()

    if mismatches:
        console.print("[red]FAIL[/red] — Checksum mismatches detected:\n")
        for path, expected, actual in mismatches:
            console.print(f"  [red]✗[/red] {path}")
            console.print(f"    expected: {expected}")
            console.print(f"    actual:   {actual}")
        console.print()
        raise typer.Exit(code=1)

    console.print("[green]PASS[/green] — All cached files match stored checksums.")
    raise typer.Exit(code=0)


@mirror_app.command()
def status() -> None:
    """Display mirror status information."""
    config = _read_config()
    repo_count = len(config.repositories)

    # Resolve mirror.db path via XDG
    db_path = resolve_xdg_path("database") / "mirror.db"

    cached_files = 0
    failed_files = 0
    cache_size_bytes = 0
    last_sync: str = "never"

    if db_path.exists():
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import Session

        engine = create_engine(f"sqlite:///{db_path}", echo=False)

        try:
            with engine.connect() as conn:
                # Check if repository_files table exists
                result = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table' AND name='repository_files'")
                )
                has_repo_files = result.fetchone() is not None

                # Check if sync_sessions table exists
                result = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table' AND name='sync_sessions'")
                )
                has_sync_sessions = result.fetchone() is not None

            if has_repo_files:
                with Session(engine) as session:
                    from sqlalchemy import func, select

                    from debcraft.infrastructure.models.mirror import (
                        RepositoryFile,
                        RepositoryFileState,
                    )

                    # Count cached files (VERIFIED + INDEXED)
                    cached_files = (
                        session.execute(
                            select(func.count(RepositoryFile.id)).where(
                                RepositoryFile.state.in_(
                                    [
                                        RepositoryFileState.VERIFIED,
                                        RepositoryFileState.INDEXED,
                                    ]
                                )
                            )
                        ).scalar()
                        or 0
                    )

                    # Count failed files
                    failed_files = (
                        session.execute(
                            select(func.count(RepositoryFile.id)).where(
                                RepositoryFile.state == RepositoryFileState.FAILED
                            )
                        ).scalar()
                        or 0
                    )

                    # Sum cache size of VERIFIED/INDEXED files
                    cache_size_bytes = (
                        session.execute(
                            select(func.coalesce(func.sum(RepositoryFile.size_bytes), 0)).where(
                                RepositoryFile.state.in_(
                                    [
                                        RepositoryFileState.VERIFIED,
                                        RepositoryFileState.INDEXED,
                                    ]
                                )
                            )
                        ).scalar()
                        or 0
                    )

            if has_sync_sessions:
                with Session(engine) as session:
                    from sqlalchemy import select

                    from debcraft.infrastructure.models.mirror import SyncSession

                    # Last sync timestamp (most recent completed_at)
                    last_completed = session.execute(
                        select(SyncSession.completed_at)
                        .where(SyncSession.completed_at.is_not(None))
                        .order_by(SyncSession.completed_at.desc())
                        .limit(1)
                    ).scalar()

                    if last_completed is not None:
                        last_sync = str(last_completed)

            engine.dispose()
        except Exception:
            # If database is corrupt or unreadable, show what we can
            engine.dispose()

    table = Table(title="Mirror Status")
    table.add_column("Metric", style="bold")
    table.add_column("Value")

    table.add_row("Configured repositories", str(repo_count))
    table.add_row("Last sync", last_sync)
    table.add_row("Cached files", str(cached_files))
    table.add_row("Failed files", str(failed_files))
    table.add_row("Cache size", _format_bytes(cache_size_bytes))

    console.print(table)
    raise typer.Exit(code=0)


@mirror_app.command("list")
def list_repos() -> None:
    """List configured repositories."""
    config = _read_config()

    table = Table(title="Configured Repositories")
    table.add_column("Name", style="bold")
    table.add_column("Base URL", no_wrap=True)
    table.add_column("Suites")
    table.add_column("Components")
    table.add_column("Architectures")

    for repo in config.repositories:
        table.add_row(
            repo.name,
            repo.base_url,
            ", ".join(repo.suites),
            ", ".join(repo.components),
            ", ".join(repo.architectures),
        )

    console.print(table)
    raise typer.Exit(code=0)


@mirror_app.command()
def clean(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Clean unreferenced artifacts from mirror cache."""
    asyncio.run(_clean_async(yes=yes))


async def _clean_async(*, yes: bool) -> None:
    """Async implementation of the mirror clean command.

    Identifies unreferenced files in the mirror cache (those not tracked
    as VERIFIED or INDEXED in mirror.db), prompts for confirmation, and
    removes them while displaying a Rich progress bar.

    Args:
        yes: If True, skip the confirmation prompt.
    """
    # Step 1: Read configuration
    try:
        config = _read_config()
    except MirrorConfigurationError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(code=1) from None

    # Step 2: Check that repositories are configured (Req 10.8)
    if not config.repositories:
        console.print("[red]Error:[/red] No repositories configured.")
        console.print("[dim]Add repository entries to your mirrors.toml configuration file.[/dim]")
        raise typer.Exit(code=1)

    # Step 3: Scan the mirror cache directory for all files
    storage = _MinimalStorageEngine()
    mirror_dir = storage.get_path("mirror")
    all_files = _scan_mirror_cache(mirror_dir)

    # Step 4: Query mirror.db for referenced files (VERIFIED/INDEXED)
    referenced_paths = await _get_referenced_paths()

    # Step 5: Compute unreferenced files
    unreferenced = [f for f in all_files if str(f) not in referenced_paths]

    # Step 6: If no unreferenced files, print "Cache is clean" and exit 0
    if not unreferenced:
        console.print("[green]Cache is clean[/green]")
        raise typer.Exit(code=0)

    # Step 7: Display summary (count and total size)
    total_size = sum(_safe_file_size(f) for f in unreferenced)
    console.print(
        f"Found [bold]{len(unreferenced)}[/bold] unreferenced file(s) totaling [bold]{_format_bytes(total_size)}[/bold]"
    )

    # Step 8: Prompt for confirmation (unless --yes flag)
    if not yes:
        confirmed = typer.confirm("Remove unreferenced files?")
        if not confirmed:
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(code=0)

    # Step 9: Remove unreferenced files with Rich progress bar
    reclaimed = _remove_files_with_progress(unreferenced)

    # Step 10: Display reclaimed space and exit 0
    console.print(f"[green]Removed {len(unreferenced)} file(s), reclaimed {_format_bytes(reclaimed)}[/green]")
    raise typer.Exit(code=0)


def _scan_mirror_cache(mirror_dir: Path) -> list[Path]:
    """Walk the mirror cache directory and return all regular files.

    Excludes .part files (incomplete downloads) from the scan.

    Args:
        mirror_dir: Root of the mirror cache directory.

    Returns:
        List of Path objects for all regular files found.
    """
    if not mirror_dir.exists():
        return []
    return [p for p in mirror_dir.rglob("*") if p.is_file() and p.suffix != ".part"]


async def _get_referenced_paths() -> set[str]:
    """Query mirror.db for all file paths in VERIFIED or INDEXED state.

    Returns:
        Set of local_path strings for referenced files.
    """
    from debcraft.infrastructure.database.session import (
        create_async_engine_for,
        create_session_factory,
    )

    db_path = resolve_xdg_path("database") / "mirror.db"
    if not db_path.exists():
        return set()

    from sqlalchemy import select

    from debcraft.infrastructure.models.mirror import RepositoryFile, RepositoryFileState

    engine = create_async_engine_for(db_path)
    session_factory = create_session_factory(engine)

    referenced: set[str] = set()
    try:
        async with session_factory() as session:
            stmt = select(RepositoryFile.local_path).where(
                RepositoryFile.state.in_(
                    [
                        RepositoryFileState.VERIFIED,
                        RepositoryFileState.INDEXED,
                    ]
                )
            )
            result = await session.execute(stmt)
            for row in result.scalars():
                if row is not None:
                    referenced.add(row)
    finally:
        await engine.dispose()

    return referenced


def _safe_file_size(path: Path) -> int:
    """Get the file size, returning 0 if the file is inaccessible.

    Args:
        path: Path to the file.

    Returns:
        File size in bytes, or 0 on error.
    """
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _remove_files_with_progress(files: list[Path]) -> int:
    """Remove files while displaying a Rich progress bar.

    Args:
        files: List of file paths to remove.

    Returns:
        Total bytes reclaimed.
    """
    reclaimed = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Cleaning cache...", total=len(files))

        for file_path in files:
            try:
                size = file_path.stat().st_size if file_path.exists() else 0
                file_path.unlink(missing_ok=True)
                reclaimed += size
            except OSError:
                pass  # Skip files that can't be removed
            progress.advance(task_id)

    return reclaimed
