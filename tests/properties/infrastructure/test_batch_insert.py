"""Property-based tests for batched insert correctness.

**Validates: Requirements 8.1**

Property 21: For any list of N valid entities passed to batch_add(),
after the containing UnitOfWork commits, all N entities shall be
individually retrievable by their assigned surrogate keys with correct
field values.
"""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import given
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
        min_size=1,
        max_size=100,
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
class TestBatchedInsertCorrectness:
    """Property 21: Batched Insert Correctness.

    For any list of N valid entities passed to batch_add(), after the
    containing UnitOfWork commits, all N entities shall be individually
    retrievable by their assigned surrogate keys with correct field values.
    """

    @given(entities_data=_unique_source_packages_strategy())
    def test_batch_add_all_retrievable_with_correct_fields(
        self,
        entities_data: list[dict[str, str | None]],
    ) -> None:
        """All N entities are individually retrievable with correct field values after batch_add + commit."""

        async def _run() -> None:
            factory, engine = await _setup_engine()
            try:
                # Insert entities using batch_add and commit
                async with factory() as session:
                    repo = SourcePackageRepository(session)
                    entities = [
                        SourcePackage(
                            name=data["name"],
                            version=data["version"],
                            maintainer=data.get("maintainer"),
                        )
                        for data in entities_data
                    ]
                    added = await repo.batch_add(entities)

                    # Verify all entities received surrogate keys
                    assert len(added) == len(entities_data)
                    ids = [e.id for e in added]
                    assert all(eid is not None for eid in ids)

                    await session.commit()

                # Open a fresh session and retrieve each entity by surrogate key
                async with factory() as fresh_session:
                    fresh_repo = SourcePackageRepository(fresh_session)
                    for i, entity_id in enumerate(ids):
                        retrieved = await fresh_repo.get_by_id(entity_id)
                        expected = entities_data[i]

                        assert retrieved.name == expected["name"], (
                            f"Entity {entity_id}: expected name={expected['name']!r}, got {retrieved.name!r}"
                        )
                        assert retrieved.version == expected["version"], (
                            f"Entity {entity_id}: expected version={expected['version']!r}, got {retrieved.version!r}"
                        )
                        assert retrieved.maintainer == expected.get("maintainer"), (
                            f"Entity {entity_id}: expected maintainer={expected.get('maintainer')!r}, "
                            f"got {retrieved.maintainer!r}"
                        )
            finally:
                await engine.dispose()

        asyncio.run(_run())
