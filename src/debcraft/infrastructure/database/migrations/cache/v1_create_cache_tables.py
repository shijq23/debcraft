"""Migration v1: Create cache tables for cache.db."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def migrate(session: AsyncSession) -> None:
    """Create parsed_dep5, normalized_licenses, and checksum_cache tables."""
    await session.execute(
        text("""
            CREATE TABLE parsed_dep5 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_sha256 TEXT NOT NULL UNIQUE,
                parsed_ast TEXT NOT NULL,
                valid INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    )
    await session.execute(text("CREATE UNIQUE INDEX ix_parsed_dep5_source_sha256 ON parsed_dep5 (source_sha256)"))

    await session.execute(
        text("""
            CREATE TABLE normalized_licenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_expression TEXT NOT NULL UNIQUE,
                normalized_expression TEXT NOT NULL,
                valid INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    )
    await session.execute(
        text("CREATE UNIQUE INDEX ix_normalized_licenses_raw_expression ON normalized_licenses (raw_expression)")
    )

    await session.execute(
        text("""
            CREATE TABLE checksum_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content_sha256 TEXT NOT NULL UNIQUE,
                computed_hash TEXT NOT NULL,
                valid INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    )
    await session.execute(
        text("CREATE UNIQUE INDEX ix_checksum_cache_content_sha256 ON checksum_cache (content_sha256)")
    )
