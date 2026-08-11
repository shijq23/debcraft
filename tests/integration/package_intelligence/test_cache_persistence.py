"""Integration tests for ParseCacheAdapter SQLAlchemy persistence.

Verifies that:
1. Storing a parse result and retrieving by SHA256 + parser version works.
2. Retrieving with a different parser version returns None (cache invalidation).
3. Retrieving with a different SHA256 returns None.
4. Storing twice with same SHA256 + parser_version updates without error.
5. Stored data accurately reflects all fields including dependencies with alternatives.

Requirements: 11.1, 11.2, 11.3, 11.4
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from debcraft.domain.package_intelligence.values import DebParseResult, DependencyRelation
from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.package_intelligence.cache_adapter import ParseCacheAdapter


async def _create_session_factory() -> async_sessionmaker[AsyncSession]:
    """Create an in-memory SQLite engine with all tables and return session factory."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def _make_sample_parse_result() -> DebParseResult:
    """Create a representative DebParseResult for testing."""
    return DebParseResult(
        package_name="libfoo",
        version="1.2.3-1",
        architecture="amd64",
        control_fields={
            "Package": "libfoo",
            "Version": "1.2.3-1",
            "Architecture": "amd64",
            "Maintainer": "Test Dev <dev@example.com>",
            "Description": "A test library\n Extended description here.",
            "Section": "libs",
            "Priority": "optional",
        },
        dependencies=[
            DependencyRelation(
                package="libc6",
                version_constraint=">= 2.17",
                alternatives=[],
            ),
            DependencyRelation(
                package="libssl3",
                version_constraint=">= 3.0.0",
                alternatives=[
                    DependencyRelation(
                        package="libssl1.1",
                        version_constraint=">= 1.1.0",
                        alternatives=[],
                    ),
                ],
            ),
            DependencyRelation(
                package="zlib1g",
                version_constraint=None,
                alternatives=[],
            ),
        ],
        file_listing=[
            "./usr/lib/libfoo.so.1",
            "./usr/lib/libfoo.so.1.2.3",
            "./usr/share/doc/libfoo/copyright",
            "./usr/share/doc/libfoo/changelog.Debian.gz",
        ],
        copyright_text="Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n"
        "Upstream-Name: libfoo\n\n"
        "Files: *\n"
        "Copyright: 2024 Test Dev\n"
        "License: MIT\n",
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_store_and_retrieve_by_sha256() -> None:
    """Store a result, then retrieve it by SHA256 with matching parser version."""
    factory = await _create_session_factory()
    adapter = ParseCacheAdapter(factory)
    sample = _make_sample_parse_result()
    sha256 = "a" * 64
    parser_version = 1

    await adapter.store(sha256, parser_version, sample)
    retrieved = await adapter.get(sha256, parser_version)

    assert retrieved is not None
    assert retrieved.package_name == sample.package_name
    assert retrieved.version == sample.version
    assert retrieved.architecture == sample.architecture
    assert retrieved.control_fields == sample.control_fields
    assert retrieved.file_listing == sample.file_listing
    assert retrieved.copyright_text == sample.copyright_text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retrieve_with_different_parser_version_returns_none() -> None:
    """Retrieve by SHA256 with a DIFFERENT parser version returns None (cache invalidation)."""
    factory = await _create_session_factory()
    adapter = ParseCacheAdapter(factory)
    sample = _make_sample_parse_result()
    sha256 = "b" * 64
    parser_version = 1

    await adapter.store(sha256, parser_version, sample)
    retrieved = await adapter.get(sha256, parser_version + 1)

    assert retrieved is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retrieve_with_different_sha256_returns_none() -> None:
    """Retrieve by a different SHA256 returns None."""
    factory = await _create_session_factory()
    adapter = ParseCacheAdapter(factory)
    sample = _make_sample_parse_result()
    sha256 = "c" * 64
    different_sha256 = "d" * 64
    parser_version = 1

    await adapter.store(sha256, parser_version, sample)
    retrieved = await adapter.get(different_sha256, parser_version)

    assert retrieved is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_store_update_same_sha256_and_version() -> None:
    """Store again with same SHA256 + parser_version updates without error."""
    factory = await _create_session_factory()
    adapter = ParseCacheAdapter(factory)
    sample = _make_sample_parse_result()
    sha256 = "e" * 64
    parser_version = 1

    # Store original
    await adapter.store(sha256, parser_version, sample)

    # Store updated result with same key
    updated_result = DebParseResult(
        package_name="libfoo",
        version="1.2.4-1",
        architecture="amd64",
        control_fields={
            "Package": "libfoo",
            "Version": "1.2.4-1",
            "Architecture": "amd64",
        },
        dependencies=[],
        file_listing=["./usr/lib/libfoo.so.1.2.4"],
        copyright_text="Updated copyright",
    )
    await adapter.store(sha256, parser_version, updated_result)

    # Retrieve and verify it reflects the updated data
    retrieved = await adapter.get(sha256, parser_version)
    assert retrieved is not None
    assert retrieved.version == "1.2.4-1"
    assert retrieved.file_listing == ["./usr/lib/libfoo.so.1.2.4"]
    assert retrieved.copyright_text == "Updated copyright"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stored_data_preserves_dependencies_with_alternatives() -> None:
    """Verify stored data accurately reflects dependencies including alternatives."""
    factory = await _create_session_factory()
    adapter = ParseCacheAdapter(factory)
    sample = _make_sample_parse_result()
    sha256 = "f" * 64
    parser_version = 1

    await adapter.store(sha256, parser_version, sample)
    retrieved = await adapter.get(sha256, parser_version)

    assert retrieved is not None
    assert len(retrieved.dependencies) == 3

    # First dep: libc6 (>= 2.17), no alternatives
    dep0 = retrieved.dependencies[0]
    assert dep0.package == "libc6"
    assert dep0.version_constraint == ">= 2.17"
    assert dep0.alternatives == []

    # Second dep: libssl3 (>= 3.0.0) with alternative libssl1.1 (>= 1.1.0)
    dep1 = retrieved.dependencies[1]
    assert dep1.package == "libssl3"
    assert dep1.version_constraint == ">= 3.0.0"
    assert len(dep1.alternatives) == 1
    alt = dep1.alternatives[0]
    assert alt.package == "libssl1.1"
    assert alt.version_constraint == ">= 1.1.0"
    assert alt.alternatives == []

    # Third dep: zlib1g, no version constraint, no alternatives
    dep2 = retrieved.dependencies[2]
    assert dep2.package == "zlib1g"
    assert dep2.version_constraint is None
    assert dep2.alternatives == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stored_data_preserves_none_copyright() -> None:
    """Verify that None copyright_text is preserved through store/retrieve cycle."""
    factory = await _create_session_factory()
    adapter = ParseCacheAdapter(factory)
    sha256 = "0" * 64
    parser_version = 1

    result = DebParseResult(
        package_name="no-copyright-pkg",
        version="0.1.0",
        architecture="all",
        control_fields={
            "Package": "no-copyright-pkg",
            "Version": "0.1.0",
            "Architecture": "all",
        },
        dependencies=[],
        file_listing=["./usr/bin/tool"],
        copyright_text=None,
    )

    await adapter.store(sha256, parser_version, result)
    retrieved = await adapter.get(sha256, parser_version)

    assert retrieved is not None
    assert retrieved.copyright_text is None
    assert retrieved.package_name == "no-copyright-pkg"
