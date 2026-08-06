"""Generic SQLAlchemy repository base implementation.

Provides a concrete base class implementing the Repository[T] contract
using SQLAlchemy's async session. Concrete repositories subclass this
base and add domain-specific query methods.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from sqlalchemy import delete, select

from debcraft.infrastructure.errors import EntityNotFoundError
from debcraft.platform.contracts.persistence import Repository

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


class SqlAlchemyRepository[T](Repository[T]):
    """Generic repository implementation backed by a SQLAlchemy AsyncSession.

    Provides default implementations of add, get_by_id, find, update, delete,
    batch_add, and stream. All operations go through the injected session
    without committing independently — the owning Unit of Work controls the
    transaction boundary.

    Args:
        session: The async session to execute operations against.
        model_class: The SQLAlchemy ORM mapped class for the entity type.
    """

    def __init__(self, session: AsyncSession, model_class: type[T]) -> None:
        """Initialize the repository.

        Args:
            session: The SQLAlchemy async session for database operations.
            model_class: The ORM model class this repository manages.
        """
        self._session = session
        self._model_class = model_class

    @override
    async def add(self, entity: T) -> T:
        """Insert entity and flush to populate the surrogate key.

        Args:
            entity: The entity instance to persist.

        Returns:
            The same entity instance with its primary key populated.
        """
        self._session.add(entity)
        await self._session.flush()
        return entity

    @override
    async def get_by_id(self, entity_id: int) -> T:
        """Lookup entity by integer surrogate key.

        Args:
            entity_id: The primary key value to search for.

        Returns:
            The entity matching the given identifier.

        Raises:
            EntityNotFoundError: If no entity with the given key exists.
        """
        result = await self._session.get(self._model_class, entity_id)
        if result is None:
            raise EntityNotFoundError(
                entity_type=self._model_class.__name__,
                key_name="id",
                key_value=entity_id,
            )
        return result

    @override
    async def find(self, **filters: object) -> list[T]:
        """Query returning zero or more entities matching keyword filters.

        Builds a SELECT statement with WHERE clauses from the provided
        keyword arguments. Each kwarg maps to an equality check on the
        corresponding model column.

        Args:
            **filters: Column-name/value pairs used as equality filters.

        Returns:
            A list of matching entities, or an empty list if none match.
        """
        stmt = select(self._model_class)
        for attr_name, value in filters.items():
            column = getattr(self._model_class, attr_name)
            stmt = stmt.where(column == value)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    @override
    async def update(self, entity: T) -> T:
        """Persist modifications to an existing entity.

        Uses session.merge() to reconcile the entity state with the
        session, then flushes to ensure changes are written.

        Args:
            entity: The modified entity instance.

        Returns:
            The merged entity instance.
        """
        merged = await self._session.merge(entity)
        await self._session.flush()
        return merged

    @override
    async def delete(self, entity_id: int) -> None:
        """Remove entity by surrogate key.

        Executes a DELETE statement filtering by the model's id column.

        Args:
            entity_id: The primary key value of the entity to remove.
        """
        stmt = delete(self._model_class).where(
            self._model_class.id == entity_id  # type: ignore[attr-defined]
        )
        await self._session.execute(stmt)

    async def batch_add(self, entities: list[T]) -> list[T]:
        """Insert multiple entities in a single round-trip.

        Uses session.add_all() followed by a flush to populate surrogate
        keys for all entities.

        Args:
            entities: The list of entity instances to persist.

        Returns:
            The same list of entities with their primary keys populated.
        """
        self._session.add_all(entities)
        await self._session.flush()
        return entities

    async def stream(self, *, yield_per: int = 1000, **filters: object) -> AsyncIterator[T]:
        """Stream query results using yield_per for bounded memory usage.

        Executes a SELECT statement with optional filters and yields
        results incrementally, never materializing the full result set
        in memory.

        Args:
            yield_per: Number of rows to fetch per batch from the database.
            **filters: Column-name/value pairs used as equality filters.

        Yields:
            Entity instances matching the filters, streamed in batches.
        """
        stmt = select(self._model_class)
        for attr_name, value in filters.items():
            column = getattr(self._model_class, attr_name)
            stmt = stmt.where(column == value)
        result = await self._session.stream_scalars(stmt.execution_options(yield_per=yield_per))
        async for entity in result:
            yield entity
