"""Unit of Work implementation for SQLite databases.

Coordinates transactions across repositories within a single logical
database session. Implements the async context manager protocol —
entering acquires a session and begins a transaction, exiting commits
on success or rolls back on failure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

from debcraft.infrastructure.errors import StorageError
from debcraft.infrastructure.repositories.license import LicenseRepository
from debcraft.infrastructure.repositories.package import PackageRepository
from debcraft.infrastructure.repositories.repository_file import RepositoryFileRepository
from debcraft.infrastructure.repositories.sbom import SBOMRepository
from debcraft.infrastructure.repositories.scan_session import ScanSessionRepository
from debcraft.infrastructure.repositories.snapshot import SnapshotRepository
from debcraft.infrastructure.repositories.source_package import SourcePackageRepository
from debcraft.platform.contracts.persistence import DatabaseProvider, UnitOfWork

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from debcraft.platform.contracts.workflow import CancellationToken


class SqliteUnitOfWork(UnitOfWork):
    """SQLite implementation of the UnitOfWork contract.

    Parameterised by a logical database name. Owns a single AsyncSession
    and exposes typed repository properties that share its managed session.
    Repositories are constructed lazily on first access.
    """

    def __init__(
        self,
        db_provider: DatabaseProvider,
        db_name: str,
        cancellation_token: CancellationToken | None = None,
    ) -> None:
        """Initialize the SqliteUnitOfWork.

        Args:
            db_provider: The database provider used to acquire sessions.
            db_name: The logical database name (e.g. "mirror", "metadata", "cache").
            cancellation_token: Optional cooperative cancellation signal. If
                provided, commit() will check it before persisting changes.
        """
        self._db_provider = db_provider
        self._db_name = db_name
        self._cancellation_token = cancellation_token
        self._session: AsyncSession | None = None

        # Lazy repository instances
        self._packages: PackageRepository | None = None
        self._source_packages: SourcePackageRepository | None = None
        self._repository_files: RepositoryFileRepository | None = None
        self._snapshots: SnapshotRepository | None = None
        self._licenses: LicenseRepository | None = None
        self._scan_sessions: ScanSessionRepository | None = None
        self._sbom_documents: SBOMRepository | None = None

    def _get_session(self) -> AsyncSession:
        """Return the active session, raising if not in a context.

        Returns:
            The active AsyncSession.

        Raises:
            StorageError: If the unit of work has not been entered.
        """
        if self._session is None:
            msg = "UnitOfWork must be used as an async context manager"
            raise StorageError(msg)
        return self._session

    @property
    def packages(self) -> PackageRepository:
        """Access the PackageRepository sharing this UoW's session."""
        if self._packages is None:
            self._packages = PackageRepository(self._get_session())
        return self._packages

    @property
    def source_packages(self) -> SourcePackageRepository:
        """Access the SourcePackageRepository sharing this UoW's session."""
        if self._source_packages is None:
            self._source_packages = SourcePackageRepository(self._get_session())
        return self._source_packages

    @property
    def repository_files(self) -> RepositoryFileRepository:
        """Access the RepositoryFileRepository sharing this UoW's session."""
        if self._repository_files is None:
            self._repository_files = RepositoryFileRepository(self._get_session())
        return self._repository_files

    @property
    def snapshots(self) -> SnapshotRepository:
        """Access the SnapshotRepository sharing this UoW's session."""
        if self._snapshots is None:
            self._snapshots = SnapshotRepository(self._get_session())
        return self._snapshots

    @property
    def licenses(self) -> LicenseRepository:
        """Access the LicenseRepository sharing this UoW's session."""
        if self._licenses is None:
            self._licenses = LicenseRepository(self._get_session())
        return self._licenses

    @property
    def scan_sessions(self) -> ScanSessionRepository:
        """Access the ScanSessionRepository sharing this UoW's session."""
        if self._scan_sessions is None:
            self._scan_sessions = ScanSessionRepository(self._get_session())
        return self._scan_sessions

    @property
    def sbom_documents(self) -> SBOMRepository:
        """Access the SBOMRepository sharing this UoW's session."""
        if self._sbom_documents is None:
            self._sbom_documents = SBOMRepository(self._get_session())
        return self._sbom_documents

    async def commit(self) -> None:
        """Persist all tracked changes as one atomic transaction.

        Checks the CancellationToken (if available) before committing.
        If cancelled, rolls back and raises StorageError. Otherwise,
        commits the session.

        Raises:
            StorageError: If the cancellation token is triggered, or if
                the commit operation fails.
        """
        session = self._get_session()
        if self._cancellation_token is not None and self._cancellation_token.is_cancelled:
            await session.rollback()
            msg = f"Commit aborted for database '{self._db_name}': operation was cancelled"
            raise StorageError(msg)
        await session.commit()

    async def rollback(self) -> None:
        """Discard all pending changes, leaving session usable for subsequent operations."""
        session = self._get_session()
        await session.rollback()

    async def __aenter__(self) -> SqliteUnitOfWork:
        """Enter the unit-of-work context, acquiring a session and beginning a transaction.

        Returns:
            This UnitOfWork instance with an active session.
        """
        self._session = await self._db_provider.get_session(self._db_name)  # type: ignore[arg-type]
        await self._session.begin()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
        """Exit the unit-of-work context.

        If no exception occurred, commits the transaction. If commit fails,
        rolls back and raises StorageError. If an exception occurred, rolls
        back and re-raises the original exception.

        Args:
            exc_type: The exception type, if any.
            exc_val: The exception value, if any.
            exc_tb: The traceback, if any.

        Returns:
            False — exceptions are never suppressed.
        """
        session = self._get_session()

        try:
            if exc_type is None:
                # Clean exit — attempt commit
                try:
                    await self.commit()
                except SQLAlchemyError as exc:
                    await session.rollback()
                    msg = f"Commit failed for database '{self._db_name}'"
                    raise StorageError(msg, cause=exc) from exc
            else:
                # Exception in the context body — rollback
                await session.rollback()
        finally:
            await session.close()

        return False
