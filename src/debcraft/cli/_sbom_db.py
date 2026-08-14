"""Database engine factory and snapshot resolution for the SBOM CLI.

Creates async SQLAlchemy engines for metadata.db and cache.db, resolves
the latest published RepositorySnapshot ID, and manages engine lifecycle.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from debcraft.infrastructure.database.session import create_async_engine_for, create_session_factory
from debcraft.infrastructure.storage.paths import resolve_xdg_path

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


@dataclass
class DatabaseEngines:
    """Holds async SQLAlchemy engines and session factories for both databases.

    Attributes:
        metadata_engine: Engine for metadata.db, or None if unavailable.
        cache_engine: Engine for cache.db, or None if connection failed.
        metadata_session_factory: Session factory for metadata.db, or None.
        cache_session_factory: Session factory for cache.db, or None.
    """

    metadata_engine: AsyncEngine | None
    cache_engine: AsyncEngine | None
    metadata_session_factory: async_sessionmaker[AsyncSession] | None
    cache_session_factory: async_sessionmaker[AsyncSession] | None

    async def dispose(self) -> None:
        """Dispose all engines, releasing file handles and connections."""
        if self.metadata_engine is not None:
            try:
                await self.metadata_engine.dispose()
            except Exception:  # pylint: disable=broad-except  # Engine disposal must not raise
                logger.warning("Failed to dispose metadata engine", exc_info=True)

        if self.cache_engine is not None:
            try:
                await self.cache_engine.dispose()
            except Exception:  # pylint: disable=broad-except  # Engine disposal must not raise
                logger.warning("Failed to dispose cache engine", exc_info=True)


async def create_database_engines() -> DatabaseEngines:
    """Create async engines for metadata.db and cache.db.

    Path resolution:
        - metadata.db → resolve_xdg_path("database") / "metadata.db"
        - cache.db → resolve_xdg_path("cache") / "cache.db"

    Handles:
        - Missing metadata.db: returns None for metadata fields, logs warning.
        - Missing cache.db: creates file and initializes schema via Base.metadata.create_all.
        - Cache.db connection failure: returns None for cache fields, logs warning.

    Returns:
        A DatabaseEngines instance with available engines and session factories.
    """
    metadata_engine: AsyncEngine | None = None
    metadata_session_factory: async_sessionmaker[AsyncSession] | None = None
    cache_engine: AsyncEngine | None = None
    cache_session_factory: async_sessionmaker[AsyncSession] | None = None

    # --- metadata.db ---
    metadata_db_path = resolve_xdg_path("database") / "metadata.db"
    if metadata_db_path.exists():
        metadata_engine = create_async_engine_for(metadata_db_path)
        metadata_session_factory = create_session_factory(metadata_engine)
    else:
        logger.warning(
            "Metadata database not found at %s; enrichment will be skipped.",
            metadata_db_path,
        )

    # --- cache.db ---
    cache_dir = resolve_xdg_path("cache")
    cache_db_path = cache_dir / "cache.db"
    try:
        # Create directory and file if they don't exist
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_engine = create_async_engine_for(cache_db_path)

        if not cache_db_path.exists():
            # Initialize schema for cache tables
            await _initialize_cache_schema(cache_engine)
        else:
            # Verify connectivity by attempting schema creation (idempotent)
            await _initialize_cache_schema(cache_engine)

        cache_session_factory = create_session_factory(cache_engine)
    except Exception:  # pylint: disable=broad-except  # Graceful degradation: cache failure is non-fatal
        logger.warning(
            "Failed to connect to cache database at %s; caching will be unavailable.",
            cache_db_path,
            exc_info=True,
        )
        # Clean up partial engine
        if cache_engine is not None:
            import contextlib

            with contextlib.suppress(Exception):
                await cache_engine.dispose()
        cache_engine = None
        cache_session_factory = None

    return DatabaseEngines(
        metadata_engine=metadata_engine,
        cache_engine=cache_engine,
        metadata_session_factory=metadata_session_factory,
        cache_session_factory=cache_session_factory,
    )


async def _initialize_cache_schema(engine: AsyncEngine) -> None:
    """Create cache.db tables if they don't exist.

    Uses Base.metadata.create_all targeting only the CachedEnrichment table.

    Args:
        engine: The async engine for cache.db.
    """
    from debcraft.infrastructure.models.base import Base
    from debcraft.infrastructure.models.cache import CachedEnrichment

    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[CachedEnrichment.__table__],  # type: ignore[list-item]
        )


async def resolve_snapshot_id(
    session_factory: async_sessionmaker[AsyncSession] | None,
    explicit_id: int | None,
) -> int:
    """Resolve the snapshot_id to use for enrichment.

    Args:
        session_factory: Async session factory for metadata.db, or None if unavailable.
        explicit_id: Explicit --snapshot-id value from CLI, or None.

    Returns:
        The resolved snapshot_id (0 means skip enrichment).
    """
    # If explicit ID is provided, use it directly (no existence check)
    if explicit_id is not None:
        return explicit_id

    # If no session factory available, cannot query metadata.db
    if session_factory is None:
        logger.warning("No metadata database session available; setting snapshot_id to 0.")
        return 0

    # Query for the highest published snapshot ID
    from sqlalchemy import select

    from debcraft.infrastructure.models.metadata import RepositorySnapshot

    try:
        async with session_factory() as session:
            stmt = (
                select(RepositorySnapshot.id)
                .where(RepositorySnapshot.published.is_(True))
                .order_by(RepositorySnapshot.id.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()

            if row is not None:
                return row

            logger.warning(
                "No published RepositorySnapshot found in metadata.db; "
                "setting snapshot_id to 0 (enrichment will be skipped)."
            )
            return 0
    except Exception:  # pylint: disable=broad-except  # Graceful degradation: query failure is non-fatal
        logger.warning(
            "Failed to query metadata.db for published snapshots; setting snapshot_id to 0.",
            exc_info=True,
        )
        return 0
