"""Migration v2: Create scan_sessions and sbom_documents tables for metadata.db."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def migrate(session: AsyncSession) -> None:
    """Create scan_sessions and sbom_documents tables with indexes."""
    await session.execute(
        text("""
            CREATE TABLE scan_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL REFERENCES repository_snapshots(id),
                state TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    )
    await session.execute(text("CREATE INDEX ix_scan_sessions_snapshot_id ON scan_sessions (snapshot_id)"))
    await session.execute(text("CREATE INDEX ix_scan_sessions_state ON scan_sessions (state)"))

    await session.execute(
        text("""
            CREATE TABLE sbom_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_session_id INTEGER NOT NULL REFERENCES scan_sessions(id),
                format TEXT NOT NULL,
                content_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    )
    await session.execute(text("CREATE INDEX ix_sbom_documents_scan_session_id ON sbom_documents (scan_session_id)"))
    await session.execute(text("CREATE INDEX ix_sbom_documents_sha256 ON sbom_documents (sha256)"))
