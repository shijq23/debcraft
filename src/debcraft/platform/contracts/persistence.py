"""Persistence contracts defining repository, unit-of-work, and database provider interfaces.

These abstract base classes decouple the domain and application layers from
concrete database mechanics. Business logic interacts exclusively through
these interfaces — never touching SQLAlchemy sessions or engines directly.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

DatabaseName = Literal["mirror", "metadata", "cache"]
"""The three logical databases managed by the platform."""


class Repository[T](ABC):
    """Collection-like access to aggregate root entities.

    A generic abstract interface providing CRUD operations parameterized
    by entity type. Concrete repositories implement domain-specific query
    methods in addition to this base contract.
    """

    @abstractmethod
    async def add(self, entity: T) -> T:
        """Insert entity; return the persisted instance with surrogate key set.

        Args:
            entity: The domain entity to persist.

        Returns:
            The persisted entity with its surrogate key populated.
        """
        ...

    @abstractmethod
    async def get_by_id(self, entity_id: int) -> T:
        """Lookup by surrogate key; raises StorageError if not found.

        Args:
            entity_id: The integer surrogate key identifying the entity.

        Returns:
            The entity matching the given identifier.

        Raises:
            StorageError: If no entity with the given key exists.
        """
        ...

    @abstractmethod
    async def find(self, **filters: object) -> list[T]:
        """Query returning zero or more entities matching keyword filters.

        Args:
            **filters: Keyword arguments used as equality filters on entity fields.

        Returns:
            A list of matching entities, or an empty list if none match.
        """
        ...

    @abstractmethod
    async def update(self, entity: T) -> T:
        """Persist modifications; raises StorageError on immutable entities.

        Args:
            entity: The modified entity to persist.

        Returns:
            The updated entity instance.

        Raises:
            StorageError: If the entity is immutable and cannot be modified.
        """
        ...

    @abstractmethod
    async def delete(self, entity_id: int) -> None:
        """Remove entity by surrogate key.

        Args:
            entity_id: The integer surrogate key of the entity to remove.
        """
        ...


class UnitOfWork(ABC):
    """Transaction coordinator for a single logical database.

    Groups repository operations into a single atomic commit. Implements
    the async context manager protocol — entering begins a transaction,
    exiting commits on success or rolls back on failure.
    """

    @abstractmethod
    async def commit(self) -> None:
        """Persist all tracked changes as one atomic transaction."""
        ...

    @abstractmethod
    async def rollback(self) -> None:
        """Discard all pending changes."""
        ...

    @abstractmethod
    async def __aenter__(self) -> "UnitOfWork":
        """Enter the unit-of-work context, beginning a transaction."""
        ...

    @abstractmethod
    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
        """Exit the unit-of-work context.

        Commits if no exception occurred; rolls back otherwise.

        Args:
            exc_type: The exception type, if any.
            exc_val: The exception value, if any.
            exc_tb: The traceback, if any.

        Returns:
            False to propagate exceptions, True to suppress them.
        """
        ...


class DatabaseProvider(ABC):
    """Manages engines and sessions for the three logical databases.

    Abstracts engine creation, session acquisition, connection pooling,
    and health checking behind a stable interface, allowing the default
    SQLite implementation to be replaced without changing business logic.
    """

    @abstractmethod
    async def get_session(self, db_name: DatabaseName) -> "AsyncSession":
        """Return an open async session bound to the named database.

        Args:
            db_name: One of "mirror", "metadata", or "cache".

        Returns:
            An async database session bound to the requested logical database.

        Raises:
            StorageError: If the database name is unrecognized or the
                database is inaccessible.
        """
        ...

    @abstractmethod
    async def dispose(self) -> None:
        """Close all connection pools within 10 seconds."""
        ...

    @abstractmethod
    async def health_check(self) -> dict[str, bool]:
        """Return liveness status keyed by database name.

        Returns:
            A mapping of database name to boolean indicating whether
            the database is accessible and responsive.
        """
        ...
