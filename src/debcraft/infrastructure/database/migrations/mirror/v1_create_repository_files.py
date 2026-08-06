"""Migration v1: Create repository_files table and indexes for mirror.db."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def migrate(session: AsyncSession) -> None:
    """Create the repository_files table with indexes."""
    await session.execute(
        text("""
            CREATE TABLE repository_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                state TEXT NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                local_path TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    )
    await session.execute(text("CREATE UNIQUE INDEX ix_repository_files_url ON repository_files (url)"))
    await session.execute(text("CREATE INDEX ix_repository_files_sha256 ON repository_files (sha256)"))
    await session.execute(text("CREATE INDEX ix_repository_files_state ON repository_files (state)"))
