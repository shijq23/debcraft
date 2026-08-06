"""Migration v1: Create core metadata tables and indexes for metadata.db."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def migrate(session: AsyncSession) -> None:
    """Create repositories, repository_snapshots, package_instances, source_packages, and license_expressions tables."""
    await session.execute(
        text("""
            CREATE TABLE repositories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                base_url TEXT NOT NULL,
                suite TEXT NOT NULL,
                component TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    )

    await session.execute(
        text("""
            CREATE TABLE repository_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repository_id INTEGER NOT NULL REFERENCES repositories(id),
                schema_version INTEGER NOT NULL,
                captured_at TEXT NOT NULL,
                published INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    )
    await session.execute(
        text("CREATE INDEX ix_repository_snapshots_repository_id ON repository_snapshots (repository_id)")
    )

    await session.execute(
        text("""
            CREATE TABLE package_instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                package_name TEXT NOT NULL,
                version TEXT NOT NULL,
                architecture TEXT NOT NULL,
                filename TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                snapshot_id INTEGER NOT NULL REFERENCES repository_snapshots(id),
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (package_name, version, architecture, filename)
            )
        """)
    )
    await session.execute(text("CREATE INDEX ix_package_instances_package_name ON package_instances (package_name)"))
    await session.execute(text("CREATE INDEX ix_package_instances_sha256 ON package_instances (sha256)"))
    await session.execute(text("CREATE INDEX ix_package_instances_snapshot_id ON package_instances (snapshot_id)"))

    await session.execute(
        text("""
            CREATE TABLE source_packages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                maintainer TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (name, version)
            )
        """)
    )

    await session.execute(
        text("""
            CREATE TABLE license_expressions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                package_id INTEGER NOT NULL REFERENCES package_instances(id),
                expression TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    )
    await session.execute(text("CREATE INDEX ix_license_expressions_package_id ON license_expressions (package_id)"))
