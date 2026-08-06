"""Property-based tests for timestamp invariants.

**Validates: Requirements 5.8**

Property 15: For any entity, after creation created_at is set to a UTC timestamp
and updated_at equals created_at. After any subsequent update(), updated_at is
greater than or equal to the previous updated_at value, and created_at remains
unchanged.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Import scan models to ensure all relationships resolve correctly
import debcraft.infrastructure.models.scan  # noqa: F401
from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.models.metadata import SourcePackage


def _safe_name() -> st.SearchStrategy[str]:
    """Generate safe non-empty strings for name/version fields."""
    return st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N"),
            blacklist_characters="\x00",
        ),
        min_size=1,
        max_size=30,
    )


def _source_package_strategy() -> st.SearchStrategy[dict[str, str | None]]:
    """Generate randomized field values for SourcePackage construction."""
    return st.fixed_dictionaries(
        {
            "name": _safe_name(),
            "version": _safe_name(),
        },
        optional={
            "maintainer": st.one_of(st.none(), _safe_name()),
        },
    )


def _parse_sqlite_timestamp(value: datetime | str) -> datetime:
    """Parse a timestamp that may be a string (from SQLite func.now()).

    SQLite's func.now() server_default produces ISO-format strings rather
    than native datetime objects, so we handle both representations.
    """
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


async def _setup_engine() -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    """Create in-memory SQLite engine with tables and return factory + engine."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


@pytest.mark.unit
@pytest.mark.storage
class TestTimestampInvariants:
    """Property 15: Timestamp Invariants.

    After creation, created_at == updated_at. After update, updated_at >=
    old updated_at, and created_at remains unchanged.
    """

    @settings(max_examples=200)
    @given(data=_source_package_strategy())
    def test_created_at_equals_updated_at_after_insert(
        self,
        data: dict[str, str | None],
    ) -> None:
        """After add(), created_at equals updated_at."""

        async def _run() -> None:
            factory, engine = await _setup_engine()
            try:
                async with factory() as session:
                    pkg = SourcePackage(
                        name=data["name"],
                        version=data["version"],
                        maintainer=data.get("maintainer"),
                    )
                    session.add(pkg)
                    await session.flush()
                    await session.refresh(pkg)

                    created = _parse_sqlite_timestamp(pkg.created_at)
                    updated = _parse_sqlite_timestamp(pkg.updated_at)
                    assert created == updated, (
                        f"After insert, created_at ({created}) should equal updated_at ({updated})"
                    )

                    await session.rollback()
            finally:
                await engine.dispose()

        asyncio.run(_run())

    @settings(max_examples=200)
    @given(
        data=_source_package_strategy(),
        new_maintainer=st.one_of(st.none(), _safe_name()),
    )
    def test_updated_at_advances_on_update(
        self,
        data: dict[str, str | None],
        new_maintainer: str | None,
    ) -> None:
        """After update(), updated_at >= old_updated_at and created_at unchanged."""

        async def _run() -> None:
            factory, engine = await _setup_engine()
            try:
                async with factory() as session:
                    pkg = SourcePackage(
                        name=data["name"],
                        version=data["version"],
                        maintainer=data.get("maintainer"),
                    )
                    session.add(pkg)
                    await session.flush()
                    await session.refresh(pkg)

                    original_created = _parse_sqlite_timestamp(pkg.created_at)
                    old_updated = _parse_sqlite_timestamp(pkg.updated_at)

                    # Update the entity
                    pkg.maintainer = new_maintainer
                    await session.flush()
                    await session.refresh(pkg)

                    new_created = _parse_sqlite_timestamp(pkg.created_at)
                    new_updated = _parse_sqlite_timestamp(pkg.updated_at)

                    # created_at must remain unchanged
                    assert new_created == original_created, (
                        f"created_at changed from {original_created} to {new_created}"
                    )

                    # updated_at must be >= old value
                    assert new_updated >= old_updated, (
                        f"updated_at ({new_updated}) should be >= old updated_at ({old_updated})"
                    )

                    await session.rollback()
            finally:
                await engine.dispose()

        asyncio.run(_run())
