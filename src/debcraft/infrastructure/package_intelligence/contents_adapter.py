"""Contents lookup adapter implementing ContentsLookupPort.

Provides file-to-package ownership lookups and copyright content retrieval
using data from the Contents index (file_ownerships table) and the parsed
package cache (parsed_deb_packages table).

The adapter accepts pre-loaded data via constructor injection, allowing it
to satisfy the synchronous ContentsLookupPort protocol while the underlying
data is loaded asynchronously at initialization time.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from debcraft.infrastructure.models.cache import ParsedDebPackage
from debcraft.infrastructure.models.metadata import FileOwnership

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class ContentsLookupAdapter:
    """Implements ContentsLookupPort using pre-loaded Contents index data.

    This adapter bridges the async database layer with the synchronous
    domain port interface. Data is loaded once via the async factory method
    `from_session()`, then lookups are performed synchronously against
    in-memory dictionaries.

    For scenarios where data is already available (e.g., tests or
    pre-computed indexes), use the constructor directly with dictionaries.
    """

    def __init__(
        self,
        file_owners: dict[str, str] | None = None,
        copyright_contents: dict[str, str] | None = None,
    ) -> None:
        """Initialize with optional pre-loaded mappings.

        Args:
            file_owners: Maps file paths to qualified package names.
            copyright_contents: Maps package names to their copyright text.
        """
        self._file_owners: dict[str, str] = file_owners or {}
        self._copyright_contents: dict[str, str] = copyright_contents or {}

    def find_owner(self, file_path: str) -> str | None:
        """Return the qualified package name owning the given file path, or None.

        Performs a direct dictionary lookup against the pre-loaded
        file ownership data from the Contents index.

        Args:
            file_path: The absolute file path to look up (e.g.,
                "/usr/share/doc/libc6/copyright").

        Returns:
            The qualified package name owning the file, or None if not found.
        """
        return self._file_owners.get(file_path)

    def get_copyright_content(self, package_name: str) -> str | None:
        """Return the copyright text for a package, or None if not available.

        Performs a direct dictionary lookup against the pre-loaded
        copyright content data from the parsed package cache.

        Args:
            package_name: The qualified package name to look up copyright for.

        Returns:
            The raw copyright file text, or None if not available.
        """
        return self._copyright_contents.get(package_name)

    @classmethod
    async def from_session(
        cls,
        session: AsyncSession,
        snapshot_id: int | None = None,
    ) -> ContentsLookupAdapter:
        """Create an adapter by loading data from the database.

        Loads file ownership data from the file_ownerships table and
        copyright content from the parsed_deb_packages cache table.

        Args:
            session: An async SQLAlchemy session for querying the database.
            snapshot_id: Optional snapshot ID to scope file ownership queries.
                If None, loads all file ownerships.

        Returns:
            A fully populated ContentsLookupAdapter ready for synchronous use.
        """
        file_owners = await _load_file_owners(session, snapshot_id)
        copyright_contents = await _load_copyright_contents(session)

        logger.debug(
            "Loaded contents lookup data",
            extra={
                "file_owner_count": len(file_owners),
                "copyright_content_count": len(copyright_contents),
            },
        )

        return cls(file_owners=file_owners, copyright_contents=copyright_contents)


async def _load_file_owners(
    session: AsyncSession,
    snapshot_id: int | None,
) -> dict[str, str]:
    """Load file-to-package ownership mappings from the database.

    Args:
        session: The async database session.
        snapshot_id: Optional snapshot ID to scope the query.

    Returns:
        Dictionary mapping file paths to qualified package names.
    """
    stmt = select(FileOwnership.file_path, FileOwnership.package_name)
    if snapshot_id is not None:
        stmt = stmt.where(FileOwnership.snapshot_id == snapshot_id)

    result = await session.execute(stmt)
    return {row.file_path: row.package_name for row in result}


async def _load_copyright_contents(
    session: AsyncSession,
) -> dict[str, str]:
    """Load package copyright content from the parsed deb package cache.

    Extracts the package name from the cached control_metadata JSON and
    maps it to the stored copyright_text.

    Args:
        session: The async database session.

    Returns:
        Dictionary mapping package names to their copyright text.
    """
    stmt = select(
        ParsedDebPackage.control_metadata,
        ParsedDebPackage.copyright_text,
    ).where(ParsedDebPackage.copyright_text.isnot(None))

    result = await session.execute(stmt)
    copyright_contents: dict[str, str] = {}

    for row in result:
        try:
            control = json.loads(row.control_metadata)
            package_name = control.get("Package", "")
            if package_name and row.copyright_text:
                copyright_contents[package_name] = row.copyright_text
        except (json.JSONDecodeError, TypeError):
            logger.debug(
                "Skipping malformed control_metadata in parsed_deb_packages",
                exc_info=True,
            )
            continue

    return copyright_contents
