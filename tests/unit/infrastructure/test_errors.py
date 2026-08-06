"""Unit tests for storage layer error hierarchy.

Verifies that all storage error classes correctly extend PlatformError,
set the right fields, produce descriptive messages, and preserve __cause__.
"""

from __future__ import annotations

import pytest

from debcraft.infrastructure.errors import (
    DatabaseConnectionError,
    EntityNotFoundError,
    ImmutableEntityError,
    MigrationError,
    StorageError,
    StorageTimeoutError,
)
from debcraft.platform.kernel.errors import PlatformError


@pytest.mark.unit
@pytest.mark.storage
class TestStorageErrorHierarchy:
    """Verify StorageError is a subclass of PlatformError (M1 integration)."""

    def test_storage_error_is_subclass_of_platform_error(self) -> None:
        assert issubclass(StorageError, PlatformError)

    def test_database_connection_error_is_subclass_of_storage_error(self) -> None:
        assert issubclass(DatabaseConnectionError, StorageError)

    def test_entity_not_found_error_is_subclass_of_storage_error(self) -> None:
        assert issubclass(EntityNotFoundError, StorageError)

    def test_immutable_entity_error_is_subclass_of_storage_error(self) -> None:
        assert issubclass(ImmutableEntityError, StorageError)

    def test_migration_error_is_subclass_of_storage_error(self) -> None:
        assert issubclass(MigrationError, StorageError)

    def test_storage_timeout_error_is_subclass_of_storage_error(self) -> None:
        assert issubclass(StorageTimeoutError, StorageError)

    def test_all_storage_errors_catchable_as_platform_error(self) -> None:
        """All storage errors can be caught with except PlatformError."""
        errors: list[PlatformError] = [
            StorageError("test"),
            DatabaseConnectionError("mirror", "corruption"),
            EntityNotFoundError("Package", "id", 42),
            ImmutableEntityError("RepositorySnapshot", 1),
            MigrationError(3, "metadata"),
            StorageTimeoutError(30.0),
        ]
        for error in errors:
            assert isinstance(error, PlatformError)


@pytest.mark.unit
@pytest.mark.storage
class TestStorageErrorFields:
    """Verify StorageError sets correct fields and produces descriptive messages."""

    def test_storage_error_message(self) -> None:
        err = StorageError("something went wrong")
        assert str(err) == "something went wrong"
        assert err.message == "something went wrong"

    def test_storage_error_cause_none_by_default(self) -> None:
        err = StorageError("no cause")
        assert err.cause is None
        assert err.__cause__ is None

    def test_storage_error_with_cause(self) -> None:
        original = RuntimeError("disk full")
        err = StorageError("write failed", cause=original)
        assert err.cause is original
        assert err.__cause__ is original


@pytest.mark.unit
@pytest.mark.storage
class TestDatabaseConnectionErrorFields:
    """Verify DatabaseConnectionError sets correct fields and message."""

    def test_corruption_fields(self) -> None:
        err = DatabaseConnectionError("mirror", "corruption")
        assert err.db_name == "mirror"
        assert err.failure_type == "corruption"
        assert "mirror" in str(err)
        assert "corruption" in str(err)

    def test_permission_denied_fields(self) -> None:
        err = DatabaseConnectionError("metadata", "permission_denied")
        assert err.db_name == "metadata"
        assert err.failure_type == "permission_denied"
        assert "metadata" in str(err)
        assert "permission denied" in str(err)

    def test_not_found_fields(self) -> None:
        err = DatabaseConnectionError("cache", "not_found")
        assert err.db_name == "cache"
        assert err.failure_type == "not_found"
        assert "cache" in str(err)
        assert "not found" in str(err)

    def test_with_cause(self) -> None:
        original = OSError("ENOENT")
        err = DatabaseConnectionError("mirror", "not_found", cause=original)
        assert err.__cause__ is original
        assert err.cause is original


@pytest.mark.unit
@pytest.mark.storage
class TestEntityNotFoundErrorFields:
    """Verify EntityNotFoundError sets correct fields and message."""

    def test_fields_with_integer_key(self) -> None:
        err = EntityNotFoundError("PackageInstance", "id", 42)
        assert err.entity_type == "PackageInstance"
        assert err.key_name == "id"
        assert err.key_value == 42
        assert "PackageInstance" in str(err)
        assert "id" in str(err)
        assert "42" in str(err)

    def test_fields_with_tuple_key(self) -> None:
        key = ("nginx", "1.0", "amd64", "nginx_1.0_amd64.deb")
        err = EntityNotFoundError("PackageInstance", "natural_key", key)
        assert err.entity_type == "PackageInstance"
        assert err.key_name == "natural_key"
        assert err.key_value == key
        assert "PackageInstance" in str(err)
        assert "natural_key" in str(err)

    def test_with_cause(self) -> None:
        original = ValueError("bad lookup")
        err = EntityNotFoundError("RepositoryFile", "id", 99, cause=original)
        assert err.__cause__ is original
        assert err.cause is original


@pytest.mark.unit
@pytest.mark.storage
class TestImmutableEntityErrorFields:
    """Verify ImmutableEntityError sets correct fields and message."""

    def test_fields(self) -> None:
        err = ImmutableEntityError("RepositorySnapshot", 7)
        assert err.entity_type == "RepositorySnapshot"
        assert err.entity_id == 7
        assert "RepositorySnapshot" in str(err)
        assert "7" in str(err)
        assert "immutable" in str(err).lower() or "published" in str(err).lower()

    def test_with_cause(self) -> None:
        original = RuntimeError("attempted modification")
        err = ImmutableEntityError("RepositorySnapshot", 3, cause=original)
        assert err.__cause__ is original
        assert err.cause is original


@pytest.mark.unit
@pytest.mark.storage
class TestMigrationErrorFields:
    """Verify MigrationError sets correct fields and message."""

    def test_fields(self) -> None:
        err = MigrationError(5, "metadata")
        assert err.migration_version == 5
        assert err.db_name == "metadata"
        assert "5" in str(err)
        assert "metadata" in str(err)

    def test_with_cause(self) -> None:
        original = RuntimeError("SQL syntax error")
        err = MigrationError(2, "mirror", cause=original)
        assert err.__cause__ is original
        assert err.cause is original


@pytest.mark.unit
@pytest.mark.storage
class TestStorageTimeoutErrorFields:
    """Verify StorageTimeoutError sets correct fields and message."""

    def test_fields(self) -> None:
        err = StorageTimeoutError(30.0)
        assert err.timeout_seconds == 30.0
        assert "30" in str(err)

    def test_fractional_timeout(self) -> None:
        err = StorageTimeoutError(10.5)
        assert err.timeout_seconds == 10.5
        assert "10.5" in str(err)

    def test_with_cause(self) -> None:
        original = TimeoutError("async timeout")
        err = StorageTimeoutError(30.0, cause=original)
        assert err.__cause__ is original
        assert err.cause is original


@pytest.mark.unit
@pytest.mark.storage
class TestCausePreservation:
    """Verify __cause__ is preserved for wrapped exceptions across all subclasses."""

    def test_storage_error_cause_chain(self) -> None:
        root = OSError("disk error")
        err = StorageError("storage failure", cause=root)
        assert err.__cause__ is root

    def test_database_connection_error_cause_chain(self) -> None:
        root = PermissionError("access denied")
        err = DatabaseConnectionError("metadata", "permission_denied", cause=root)
        assert err.__cause__ is root

    def test_entity_not_found_error_cause_chain(self) -> None:
        root = KeyError("missing")
        err = EntityNotFoundError("Package", "id", 1, cause=root)
        assert err.__cause__ is root

    def test_immutable_entity_error_cause_chain(self) -> None:
        root = ValueError("cannot modify")
        err = ImmutableEntityError("Snapshot", 10, cause=root)
        assert err.__cause__ is root

    def test_migration_error_cause_chain(self) -> None:
        root = RuntimeError("bad SQL")
        err = MigrationError(1, "cache", cause=root)
        assert err.__cause__ is root

    def test_storage_timeout_error_cause_chain(self) -> None:
        root = TimeoutError("exceeded")
        err = StorageTimeoutError(60.0, cause=root)
        assert err.__cause__ is root

    def test_cause_is_none_when_not_provided(self) -> None:
        """Verify that __cause__ is None when no cause is given."""
        err = StorageError("standalone error")
        assert err.__cause__ is None

    def test_nested_cause_chain(self) -> None:
        """Verify a multi-level exception chain is preserved."""
        root = OSError("fs error")
        mid = StorageError("wrapped", cause=root)
        top = MigrationError(1, "mirror", cause=mid)
        assert top.__cause__ is mid
        assert mid.__cause__ is root
