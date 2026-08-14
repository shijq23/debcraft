"""Forward-only versioned migration runner.

Discovers, orders, and applies migration scripts for a logical database.
Each migration is a Python module with an ``async def migrate(session)``
function. Successful executions are recorded in a ``_migration_history``
table; failures roll back to a savepoint and halt further execution.
"""

from __future__ import annotations

import importlib.util
import re
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from debcraft.infrastructure.errors import MigrationError
from debcraft.infrastructure.events import MigrationAppliedEvent

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from debcraft.platform.contracts.events import EventBus

_VERSION_PATTERN = re.compile(r"^v(\d+)_.*\.py$")

_CREATE_HISTORY_TABLE = """\
CREATE TABLE IF NOT EXISTS _migration_history (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    duration_ms INTEGER NOT NULL
);
"""


class MigrationRunner:
    """Applies forward-only versioned migrations for a single logical database.

    The runner:
    1. Creates ``_migration_history`` if absent.
    2. Reads applied versions from the history table.
    3. Scans a migration directory for ``v{N}_*.py`` files, sorted ascending.
    4. For each unapplied version: starts a savepoint, imports and calls
       ``migrate(session)``, records success in the history table, and
       releases the savepoint.
    5. On failure: rolls back to the savepoint, raises ``MigrationError``,
       and halts further execution.

    On each successful migration, publishes a ``MigrationAppliedEvent``
    through the injected ``EventBus``.

    Args:
        session_factory: An async session factory bound to the target database.
        migration_directory: Path to the directory containing migration modules.
        event_bus: The platform event bus for publishing lifecycle events.
        db_name: The logical database name (for error messages and events).
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        migration_directory: Path,
        event_bus: EventBus,
        db_name: str,
    ) -> None:
        """Initialize MigrationRunner.

        Args:
            session_factory: An async session factory bound to the target database.
            migration_directory: Path to the directory containing migration modules.
            event_bus: The platform event bus for publishing lifecycle events.
            db_name: The logical database name (for error messages and events).
        """
        self._session_factory = session_factory
        self._migration_directory = migration_directory
        self._event_bus = event_bus
        self._db_name = db_name

    async def ensure_history_table(self, session: AsyncSession) -> None:
        """Create the ``_migration_history`` table if it does not exist.

        Uses raw DDL executed within the provided session.

        Args:
            session: An active async database session.
        """
        await session.execute(text(_CREATE_HISTORY_TABLE))
        await session.flush()

    async def get_applied_versions(self, session: AsyncSession) -> set[int]:
        """Query the history table and return the set of applied version numbers.

        Args:
            session: An active async database session.

        Returns:
            A set of integer version identifiers already recorded.
        """
        result = await session.execute(text("SELECT version FROM _migration_history"))
        return {row[0] for row in result.fetchall()}

    def discover_migrations(self, directory: Path) -> list[tuple[int, Path]]:
        """Scan a directory for migration files matching ``v{N}_*.py``.

        Files are matched by the pattern ``v<integer>_<description>.py``
        (e.g. ``v1_create_repository_files.py``). Results are sorted in
        ascending version order.

        Args:
            directory: The directory to scan for migration modules.

        Returns:
            A sorted list of (version, path) tuples.
        """
        migrations: list[tuple[int, Path]] = []
        if not directory.is_dir():
            return migrations

        for path in directory.iterdir():
            if not path.is_file():
                continue
            match = _VERSION_PATTERN.match(path.name)
            if match:
                version = int(match.group(1))
                migrations.append((version, path))

        migrations.sort(key=lambda m: m[0])
        return migrations

    async def run_pending(self, session: AsyncSession) -> None:
        """Apply all pending migrations in ascending version order.

        For each unapplied version:
        - Begins a savepoint.
        - Dynamically imports the migration module and calls its
          ``migrate(session)`` coroutine.
        - Records success in ``_migration_history`` with duration.
        - Releases the savepoint.
        - Publishes a ``MigrationAppliedEvent``.

        On failure, rolls back to the savepoint, raises ``MigrationError``,
        and halts further migration execution.

        Args:
            session: An active async database session.

        Raises:
            MigrationError: If a migration fails during execution.
        """
        await self.ensure_history_table(session)
        applied = await self.get_applied_versions(session)
        pending = [
            (version, path)
            for version, path in self.discover_migrations(self._migration_directory)
            if version not in applied
        ]

        for version, path in pending:
            # Begin a savepoint (nested transaction)
            savepoint = await session.begin_nested()
            start_time = time.perf_counter()
            try:
                # Dynamically import the migration module
                module = self._import_migration_module(version, path)
                migrate_fn = getattr(module, "migrate", None)
                if not callable(migrate_fn):
                    msg = f"Migration module {path.name} does not define a 'migrate' function"
                    raise AttributeError(msg)

                # migrate_fn is an async def — cast to suppress type narrowing issues
                coro: Any = migrate_fn(session)  # pylint: disable=not-callable
                await coro

                # Calculate duration
                elapsed_ms = int((time.perf_counter() - start_time) * 1000)

                # Record in history
                await session.execute(
                    text(
                        "INSERT INTO _migration_history (version, applied_at, duration_ms) "
                        "VALUES (:version, datetime('now'), :duration_ms)"
                    ),
                    {"version": version, "duration_ms": elapsed_ms},
                )

                # Release savepoint (commit nested transaction)
                await savepoint.commit()

            except Exception as exc:
                # Roll back to savepoint on any failure
                await savepoint.rollback()
                cause = exc.__cause__ if isinstance(exc, MigrationError) else exc
                raise MigrationError(
                    migration_version=version,
                    db_name=self._db_name,
                    cause=cause if isinstance(cause, Exception) else exc,
                ) from exc

            # Publish event after successful savepoint release
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            await self._event_bus.publish(
                MigrationAppliedEvent(
                    db_name=self._db_name,
                    version=version,
                    duration_ms=elapsed_ms,
                )
            )

    def _import_migration_module(self, version: int, path: Path) -> object:
        """Dynamically import a migration module from the given path.

        Uses ``importlib.util.spec_from_file_location`` to load the module
        without adding it to ``sys.modules`` permanently.

        Args:
            version: The migration version number (used for module naming).
            path: The filesystem path to the migration module.

        Returns:
            The imported module object.

        Raises:
            MigrationError: If the module cannot be loaded.
        """
        module_name = f"migration_v{version}_{self._db_name}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            msg = f"Cannot load migration module from {path}"
            raise MigrationError(
                migration_version=version,
                db_name=self._db_name,
                cause=ImportError(msg),
            )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
