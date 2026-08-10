"""Migration v3: Add indexer columns and tables for repository indexing support."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def migrate(session: AsyncSession) -> None:
    """Add indexer columns to package_instances and source_packages.

    Creates file_ownerships and indexing_records tables.
    """
    # Add new columns to package_instances
    await session.execute(text("ALTER TABLE package_instances ADD COLUMN source_package TEXT"))
    await session.execute(text("ALTER TABLE package_instances ADD COLUMN source_version TEXT"))
    await session.execute(text("ALTER TABLE package_instances ADD COLUMN homepage TEXT"))
    await session.execute(text("ALTER TABLE package_instances ADD COLUMN maintainer TEXT"))
    await session.execute(text("ALTER TABLE package_instances ADD COLUMN depends TEXT"))
    await session.execute(text("ALTER TABLE package_instances ADD COLUMN provides TEXT"))
    await session.execute(text("ALTER TABLE package_instances ADD COLUMN section TEXT"))
    await session.execute(text("ALTER TABLE package_instances ADD COLUMN priority TEXT"))
    await session.execute(text("ALTER TABLE package_instances ADD COLUMN description TEXT"))
    await session.execute(text("ALTER TABLE package_instances ADD COLUMN download_url TEXT"))

    # Add new columns to source_packages
    await session.execute(text("ALTER TABLE source_packages ADD COLUMN uploaders TEXT"))
    await session.execute(text("ALTER TABLE source_packages ADD COLUMN section TEXT"))
    await session.execute(text("ALTER TABLE source_packages ADD COLUMN homepage TEXT"))
    await session.execute(text("ALTER TABLE source_packages ADD COLUMN build_depends TEXT"))
    await session.execute(text("ALTER TABLE source_packages ADD COLUMN binary_packages TEXT"))
    await session.execute(
        text("ALTER TABLE source_packages ADD COLUMN snapshot_id INTEGER REFERENCES repository_snapshots(id)")
    )

    # Create file_ownerships table
    await session.execute(
        text("""
            CREATE TABLE file_ownerships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL REFERENCES repository_snapshots(id),
                file_path TEXT NOT NULL,
                package_name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    )
    await session.execute(text("CREATE INDEX ix_file_ownerships_snapshot_id ON file_ownerships (snapshot_id)"))
    await session.execute(text("CREATE INDEX ix_file_ownerships_file_path ON file_ownerships (file_path)"))
    await session.execute(text("CREATE INDEX ix_file_ownerships_package_name ON file_ownerships (package_name)"))

    # Create indexing_records table
    await session.execute(
        text("""
            CREATE TABLE indexing_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repository_file_id INTEGER NOT NULL,
                parser_version INTEGER NOT NULL,
                indexed_sha256 TEXT NOT NULL,
                indexed_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (repository_file_id)
            )
        """)
    )
    await session.execute(text("CREATE INDEX ix_indexing_records_file_id ON indexing_records (repository_file_id)"))
