"""Async SQLAlchemy engine and session factory helpers for SQLite.

Provides factory functions that create properly configured async engines
with WAL mode, foreign key enforcement, and connection pooling, plus
session factories with ``expire_on_commit=False`` for post-commit access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path


def _set_sqlite_pragmas(dbapi_connection: object, _connection_record: object) -> None:
    """Apply PRAGMA settings on every new SQLite connection.

    Executes:
        - ``PRAGMA foreign_keys = ON`` — enforce FK constraints.
        - ``PRAGMA journal_mode = WAL`` — enable Write-Ahead Logging.
        - ``PRAGMA synchronous = NORMAL`` — balance durability and speed.

    Args:
        dbapi_connection: The raw DBAPI connection object.
        _connection_record: SQLAlchemy connection record (unused).
    """
    conn = cast("sqlite3.Connection", dbapi_connection)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA synchronous = NORMAL")
    cursor.close()


def create_async_engine_for(db_path: Path) -> AsyncEngine:
    """Create an async SQLAlchemy engine for the given SQLite database path.

    The engine is configured with:
        - ``aiosqlite`` async driver.
        - ``AsyncAdaptedQueuePool`` with ``pool_size=5`` and ``max_overflow=0``.
        - PRAGMA settings (WAL, foreign_keys, synchronous) applied on every
          new connection via an event listener on the sync engine.

    Args:
        db_path: Absolute path to the SQLite database file.

    Returns:
        A configured ``AsyncEngine`` bound to the database.
    """
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(
        url,
        poolclass=AsyncAdaptedQueuePool,
        pool_size=5,
        max_overflow=0,
    )
    event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the given engine.

    The factory is configured with ``expire_on_commit=False`` so that
    entity attributes remain accessible after a commit within the same
    unit of work.

    Args:
        engine: The ``AsyncEngine`` to bind sessions to.

    Returns:
        An ``async_sessionmaker`` producing ``AsyncSession`` instances.
    """
    return async_sessionmaker(engine, expire_on_commit=False)
