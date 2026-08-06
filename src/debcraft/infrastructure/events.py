"""Storage lifecycle domain events.

Frozen dataclass events published through the EventBus during
storage initialization, shutdown, and migration operations.
"""

from __future__ import annotations

from dataclasses import dataclass

from debcraft.platform.contracts.events import DomainEvent


@dataclass(frozen=True)
class StorageInitializedEvent(DomainEvent):
    """Published when the StorageEngine completes initialization.

    Attributes:
        base_path: The resolved root path for storage directories.
    """

    event_type: str = "storage.initialized"
    base_path: str = ""


@dataclass(frozen=True)
class StorageShutdownEvent(DomainEvent):
    """Published when the StorageEngine begins shutdown."""

    event_type: str = "storage.shutdown"


@dataclass(frozen=True)
class MigrationAppliedEvent(DomainEvent):
    """Published when a migration is successfully applied.

    Attributes:
        db_name: The logical database name the migration was applied to.
        version: The migration version identifier.
        duration_ms: How long the migration took in milliseconds.
    """

    event_type: str = "storage.migration_applied"
    db_name: str = ""
    version: int = 0
    duration_ms: int = 0
