"""SQLite database provider implementation.

Manages async SQLAlchemy engines and sessions for the three logical
databases (mirror, metadata, cache). Engines are created lazily on
first access and cached. OperationalError exceptions are mapped to
domain-specific StorageError subclasses.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.sql import text

from debcraft.infrastructure.database.session import (
    create_async_engine_for,
    create_session_factory,
)
from debcraft.infrastructure.errors import (
    DatabaseConnectionError,
    StorageError,
    StorageTimeoutError,
)
from debcraft.platform.contracts.persistence import DatabaseName, DatabaseProvider

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

    from debcraft.platform.contracts.storage import StorageEngine

_VALID_DB_NAMES: frozenset[str] = frozenset({"mirror", "metadata", "cache"})

_DISPOSE_TIMEOUT_SECONDS: float = 10.0


_FailureType = Literal["corruption", "permission_denied", "not_found"]


def _classify_operational_error(exc: OperationalError) -> _FailureType:
    """Determine the failure type from an OperationalError message.

    Maps SQLite error messages to one of:
        - "corruption" for disk I/O errors or malformed databases.
        - "not_found" for missing files (unable to open, ENOENT).
        - "permission_denied" for access errors (EACCES, EPERM).

    Args:
        exc: The SQLAlchemy OperationalError to classify.

    Returns:
        A failure type string suitable for DatabaseConnectionError.
    """
    msg = str(exc).lower()
    if "disk i/o error" in msg or "database disk image is malformed" in msg:
        return "corruption"
    if "unable to open" in msg or "enoent" in msg or "no such file" in msg:
        return "not_found"
    if "eacces" in msg or "eperm" in msg or "permission denied" in msg or "readonly" in msg:
        return "permission_denied"
    # Default to corruption for unrecognized OperationalErrors
    return "corruption"


class SqliteDatabaseProvider(DatabaseProvider):
    """SQLite implementation of the DatabaseProvider contract.

    Creates one AsyncEngine per logical database, lazily on first session
    request. Engines are cached for the lifetime of the provider. Database
    files are resolved through the injected StorageEngine.
    """

    def __init__(self, storage_engine: StorageEngine) -> None:
        """Initialize the SQLite database provider.

        Args:
            storage_engine: The storage engine used to resolve database file paths.
        """
        self._storage_engine = storage_engine
        self._engines: dict[str, AsyncEngine] = {}
        self._session_factories: dict[str, async_sessionmaker[AsyncSession]] = {}

    def _get_or_create_engine(self, db_name: str) -> async_sessionmaker[AsyncSession]:
        """Get or create the session factory for the given database.

        Creates the engine and session factory on first access, then caches
        both for subsequent calls.

        Args:
            db_name: One of "mirror", "metadata", or "cache".

        Returns:
            The async session factory for the database.
        """
        if db_name not in self._session_factories:
            db_path = self._storage_engine.get_path("database", f"{db_name}.db")
            engine = create_async_engine_for(db_path)
            self._engines[db_name] = engine
            self._session_factories[db_name] = create_session_factory(engine)
        return self._session_factories[db_name]

    async def get_session(self, db_name: DatabaseName) -> AsyncSession:
        """Return an open async session bound to the named database.

        Validates the database name, creates the engine lazily if needed,
        and returns a new session. OperationalErrors during session creation
        are wrapped into DatabaseConnectionError with the appropriate failure type.

        Args:
            db_name: One of "mirror", "metadata", or "cache".

        Returns:
            An async database session bound to the requested logical database.

        Raises:
            StorageError: If the database name is unrecognized.
            DatabaseConnectionError: If the database is inaccessible.
        """
        if db_name not in _VALID_DB_NAMES:
            msg = f"Unrecognized database name: '{db_name}'. Must be one of: mirror, metadata, cache"
            raise StorageError(msg)

        try:
            session_factory = self._get_or_create_engine(db_name)
            return session_factory()
        except OperationalError as exc:
            failure_type = _classify_operational_error(exc)
            raise DatabaseConnectionError(
                db_name=db_name,
                failure_type=failure_type,
                cause=exc,
            ) from exc

    async def dispose(self) -> None:
        """Close all connection pools within 10 seconds.

        Iterates over all cached engines and calls dispose() on each.
        If disposal exceeds the timeout, raises StorageTimeoutError.

        Raises:
            StorageTimeoutError: If disposal exceeds 10 seconds.
        """

        async def _dispose_all() -> None:
            for engine in self._engines.values():
                await engine.dispose()

        try:
            await asyncio.wait_for(_dispose_all(), timeout=_DISPOSE_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            raise StorageTimeoutError(
                timeout_seconds=_DISPOSE_TIMEOUT_SECONDS,
                cause=exc,
            ) from exc
        finally:
            self._engines.clear()
            self._session_factories.clear()

    async def health_check(self) -> dict[str, bool]:
        """Return liveness status keyed by database name.

        Executes ``SELECT 1`` on each logical database engine. Returns
        True for databases that respond successfully, False for those
        that fail.

        Returns:
            A mapping of database name to boolean health status.
        """
        results: dict[str, bool] = {}
        for db_name in ("mirror", "metadata", "cache"):
            if db_name not in self._engines:
                # Engine not yet created — try to create it for health check
                try:
                    self._get_or_create_engine(db_name)
                except (SQLAlchemyError, OSError):
                    results[db_name] = False
                    continue

            engine = self._engines[db_name]
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                results[db_name] = True
            except SQLAlchemyError:
                results[db_name] = False

        return results
