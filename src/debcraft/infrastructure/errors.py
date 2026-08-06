"""Storage layer error hierarchy.

Defines all exception classes for the storage/persistence infrastructure.
All storage errors derive from StorageError, which extends M1's PlatformError.
The original cause is preserved via __cause__ for debugging.
"""

from __future__ import annotations

from typing import Literal

from debcraft.platform.kernel.errors import PlatformError


class StorageError(PlatformError):
    """Base exception for all storage layer errors.

    All storage-specific exceptions inherit from this class, allowing
    callers to catch any storage error with a single except clause.
    The original cause (if any) is preserved as __cause__.
    """

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        """Initialize StorageError.

        Args:
            message: Human-readable description of the error.
            cause: The underlying exception that triggered this error, if any.
        """
        super().__init__(message)
        self.cause = cause
        self.__cause__ = cause


class DatabaseConnectionError(StorageError):
    """Raised when a database engine or session cannot be created.

    Includes the logical database name and the nature of the failure
    (corruption, permission denied, or file not found).
    """

    def __init__(
        self,
        db_name: str,
        failure_type: Literal["corruption", "permission_denied", "not_found"],
        cause: Exception | None = None,
    ) -> None:
        """Initialize DatabaseConnectionError.

        Args:
            db_name: The logical database name (e.g. "mirror", "metadata", "cache").
            failure_type: The category of failure encountered.
            cause: The underlying exception that triggered this error, if any.
        """
        self.db_name = db_name
        self.failure_type = failure_type
        message = f"Cannot connect to database '{db_name}': {failure_type.replace('_', ' ')}"
        super().__init__(message, cause)


class EntityNotFoundError(StorageError):
    """Raised when get_by_id or get_by_natural_key finds no match.

    Identifies the entity type, the lookup key name, and the requested value.
    """

    def __init__(
        self,
        entity_type: str,
        key_name: str,
        key_value: object,
        cause: Exception | None = None,
    ) -> None:
        """Initialize EntityNotFoundError.

        Args:
            entity_type: The type of entity that was not found (e.g. "PackageInstance").
            key_name: The name of the key used for lookup (e.g. "id" or "natural_key").
            key_value: The value that was searched for.
            cause: The underlying exception that triggered this error, if any.
        """
        self.entity_type = entity_type
        self.key_name = key_name
        self.key_value = key_value
        message = f"{entity_type} not found: {key_name}={key_value!r}"
        super().__init__(message, cause)


class ImmutableEntityError(StorageError):
    """Raised when update/delete is attempted on a published snapshot.

    Published snapshots are immutable and cannot be modified or removed.
    """

    def __init__(
        self,
        entity_type: str,
        entity_id: int,
        cause: Exception | None = None,
    ) -> None:
        """Initialize ImmutableEntityError.

        Args:
            entity_type: The type of the immutable entity (e.g. "RepositorySnapshot").
            entity_id: The surrogate key of the entity.
            cause: The underlying exception that triggered this error, if any.
        """
        self.entity_type = entity_type
        self.entity_id = entity_id
        message = f"Cannot modify immutable {entity_type} with id={entity_id}: entity is published"
        super().__init__(message, cause)


class MigrationError(StorageError):
    """Raised when a migration fails execution.

    Identifies the migration version and the affected database.
    """

    def __init__(
        self,
        migration_version: int,
        db_name: str,
        cause: Exception | None = None,
    ) -> None:
        """Initialize MigrationError.

        Args:
            migration_version: The version identifier of the failed migration.
            db_name: The logical database name the migration targets.
            cause: The underlying exception that triggered this error, if any.
        """
        self.migration_version = migration_version
        self.db_name = db_name
        message = f"Migration v{migration_version} failed for database '{db_name}'"
        super().__init__(message, cause)


class StorageTimeoutError(StorageError):
    """Raised when shutdown or disposal exceeds the configured timeout.

    Indicates that a storage operation did not complete within the
    allowed time window.
    """

    def __init__(
        self,
        timeout_seconds: float,
        cause: Exception | None = None,
    ) -> None:
        """Initialize StorageTimeoutError.

        Args:
            timeout_seconds: The timeout duration that was exceeded.
            cause: The underlying exception that triggered this error, if any.
        """
        self.timeout_seconds = timeout_seconds
        message = f"Storage operation timed out after {timeout_seconds}s"
        super().__init__(message, cause)
