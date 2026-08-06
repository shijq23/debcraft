"""Property-based tests for auto-incrementing surrogate keys.

**Validates: Requirements 5.1**

Property 13: For any sequence of entities inserted into a repository,
each entity shall receive a unique integer surrogate key, and keys shall
be assigned in strictly ascending order within a single session.
"""

from __future__ import annotations

import asyncio

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
from debcraft.infrastructure.repositories.source_package import SourcePackageRepository


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


def _unique_source_packages_strategy() -> st.SearchStrategy[list[dict[str, str | None]]]:
    """Generate lists of SourcePackage data with unique (name, version) combinations.

    Each entry has a unique (name, version) pair to avoid unique constraint violations.
    """
    return st.lists(
        st.fixed_dictionaries(
            {
                "name": _safe_name(),
                "version": _safe_name(),
            },
            optional={
                "maintainer": st.one_of(st.none(), _safe_name()),
            },
        ),
        min_size=2,
        max_size=50,
        unique_by=lambda d: (d["name"], d["version"]),
    )


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
class TestAutoIncrementingSurrogateKeys:
    """Property 13: Auto-Incrementing Surrogate Keys.

    For any sequence of entities inserted into a repository, each entity
    shall receive a unique integer surrogate key, and keys shall be
    assigned in strictly ascending order within a single session.
    """

    @settings(max_examples=200)
    @given(entities_data=_unique_source_packages_strategy())
    def test_ids_unique_and_strictly_ascending(
        self,
        entities_data: list[dict[str, str | None]],
    ) -> None:
        """All entity IDs are unique and strictly ascending within a session."""

        async def _run() -> None:
            factory, engine = await _setup_engine()
            try:
                async with factory() as session:
                    repo = SourcePackageRepository(session)
                    ids: list[int] = []

                    for data in entities_data:
                        pkg = SourcePackage(
                            name=data["name"],
                            version=data["version"],
                            maintainer=data.get("maintainer"),
                        )
                        added = await repo.add(pkg)
                        assert added.id is not None, "Entity should have an assigned ID after add"
                        ids.append(added.id)

                    # All IDs must be unique
                    assert len(ids) == len(set(ids)), f"IDs are not unique: {ids}"

                    # IDs must be strictly ascending
                    for i in range(1, len(ids)):
                        assert ids[i] > ids[i - 1], (
                            f"IDs are not strictly ascending: id[{i - 1}]={ids[i - 1]} >= id[{i}]={ids[i]}"
                        )

                    await session.rollback()
            finally:
                await engine.dispose()

        asyncio.run(_run())
