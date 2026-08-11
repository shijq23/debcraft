"""SQLAlchemy-backed implementation of ParseCachePort for .deb parse results.

Stores and retrieves parsed .deb metadata keyed by SHA256 and parser version,
using JSON serialization for structured fields (control_metadata, dependencies,
file_listing).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlalchemy import select

from debcraft.domain.package_intelligence.values import DebParseResult, DependencyRelation
from debcraft.infrastructure.models.cache import ParsedDebPackage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class ParseCacheAdapter:
    """SQLAlchemy adapter implementing the ParseCachePort protocol.

    Persists DebParseResult objects in the cache database, keyed by the
    SHA256 hash of the source .deb file and the parser version that
    produced the result.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Initialize the cache adapter.

        Args:
            session_factory: Async session factory for cache.db operations.
        """
        self._session_factory = session_factory

    async def get(self, sha256: str, parser_version: int) -> DebParseResult | None:
        """Retrieve a cached parse result matching the SHA256 and parser version.

        Returns None if no matching entry exists.
        """
        async with self._session_factory() as session:
            stmt = select(ParsedDebPackage).where(
                ParsedDebPackage.sha256 == sha256,
                ParsedDebPackage.parser_version == parser_version,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()

            if row is None:
                return None

            return self._to_domain(row)

    async def store(self, sha256: str, parser_version: int, result: DebParseResult) -> None:
        """Store a parse result in the cache keyed by SHA256 and parser version.

        If an entry with the same SHA256 already exists but with a different
        parser version, a new entry is created (old versions remain for
        potential rollback diagnostics).
        """
        async with self._session_factory() as session:
            # Check if an entry with this sha256 + parser_version already exists
            stmt = select(ParsedDebPackage).where(
                ParsedDebPackage.sha256 == sha256,
                ParsedDebPackage.parser_version == parser_version,
            )
            existing = await session.execute(stmt)
            row = existing.scalar_one_or_none()

            if row is not None:
                # Update existing entry
                row.control_metadata = json.dumps(result.control_fields)
                row.dependencies = json.dumps(self._serialize_dependencies(result.dependencies))
                row.copyright_text = result.copyright_text
                row.file_listing = json.dumps(result.file_listing)
            else:
                # Insert new entry
                entry = ParsedDebPackage(
                    sha256=sha256,
                    parser_version=parser_version,
                    control_metadata=json.dumps(result.control_fields),
                    dependencies=json.dumps(self._serialize_dependencies(result.dependencies)),
                    copyright_text=result.copyright_text,
                    file_listing=json.dumps(result.file_listing),
                )
                session.add(entry)

            await session.commit()

    @staticmethod
    def _to_domain(row: ParsedDebPackage) -> DebParseResult:
        """Convert a database row to a domain DebParseResult value object."""
        control_fields: dict[str, str] = json.loads(row.control_metadata)
        dependencies = ParseCacheAdapter._deserialize_dependencies(json.loads(row.dependencies))
        file_listing: list[str] = json.loads(row.file_listing)

        return DebParseResult(
            package_name=control_fields.get("Package", ""),
            version=control_fields.get("Version", ""),
            architecture=control_fields.get("Architecture", ""),
            control_fields=control_fields,
            dependencies=dependencies,
            file_listing=file_listing,
            copyright_text=row.copyright_text,
        )

    @staticmethod
    def _serialize_dependencies(deps: list[DependencyRelation]) -> list[dict[str, object]]:
        """Serialize dependency relations to JSON-compatible dicts."""
        result: list[dict[str, object]] = []
        for dep in deps:
            result.append(
                {
                    "package": dep.package,
                    "version_constraint": dep.version_constraint,
                    "alternatives": ParseCacheAdapter._serialize_dependencies(dep.alternatives),
                }
            )
        return result

    @staticmethod
    def _deserialize_dependencies(data: list[dict[str, object]]) -> list[DependencyRelation]:
        """Deserialize dependency relations from JSON-compatible dicts."""
        result: list[DependencyRelation] = []
        for item in data:
            alternatives_data = item.get("alternatives", [])
            alternatives = ParseCacheAdapter._deserialize_dependencies(
                alternatives_data,  # type: ignore[arg-type]
            )
            raw_constraint = item.get("version_constraint")
            version_constraint = str(raw_constraint) if raw_constraint is not None else None
            result.append(
                DependencyRelation(
                    package=str(item["package"]),
                    version_constraint=version_constraint,
                    alternatives=alternatives,
                )
            )
        return result
