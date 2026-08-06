"""Unit tests for storage and persistence contract definitions.

Validates that abstract interfaces are correctly defined and cannot be
instantiated directly, ensuring the contracts-first design principle holds.
"""

from typing import get_args

import pytest

from debcraft.platform.contracts.persistence import DatabaseProvider, Repository, UnitOfWork
from debcraft.platform.contracts.storage import StorageEngine, StorageProvider, StoragePurpose


@pytest.mark.unit
@pytest.mark.storage
class TestStoragePurposeLiteral:
    """Verify StoragePurpose literal type contains all expected values."""

    def test_storage_purpose_contains_all_seven_values(self) -> None:
        values = get_args(StoragePurpose)
        assert set(values) == {"mirror", "workspace", "outputs", "logs", "cache", "database", "config"}

    def test_storage_purpose_has_exactly_seven_values(self) -> None:
        values = get_args(StoragePurpose)
        assert len(values) == 7


@pytest.mark.unit
@pytest.mark.storage
class TestStorageProviderABC:
    """Verify StorageProvider is abstract and cannot be instantiated."""

    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            StorageProvider()  # type: ignore[abstract]

    def test_declares_create_directory_as_abstract(self) -> None:
        assert "create_directory" in StorageProvider.__abstractmethods__

    def test_declares_remove_matching_as_abstract(self) -> None:
        assert "remove_matching" in StorageProvider.__abstractmethods__

    def test_declares_resolve_path_as_abstract(self) -> None:
        assert "resolve_path" in StorageProvider.__abstractmethods__

    def test_declares_check_writable_as_abstract(self) -> None:
        assert "check_writable" in StorageProvider.__abstractmethods__


@pytest.mark.unit
@pytest.mark.storage
class TestStorageEngineABC:
    """Verify StorageEngine is abstract and cannot be instantiated."""

    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            StorageEngine()  # type: ignore[abstract]

    def test_declares_initialize_as_abstract(self) -> None:
        assert "initialize" in StorageEngine.__abstractmethods__

    def test_declares_shutdown_as_abstract(self) -> None:
        assert "shutdown" in StorageEngine.__abstractmethods__

    def test_declares_get_path_as_abstract(self) -> None:
        assert "get_path" in StorageEngine.__abstractmethods__

    def test_declares_aenter_as_abstract(self) -> None:
        assert "__aenter__" in StorageEngine.__abstractmethods__

    def test_declares_aexit_as_abstract(self) -> None:
        assert "__aexit__" in StorageEngine.__abstractmethods__


@pytest.mark.unit
@pytest.mark.storage
class TestRepositoryABC:
    """Verify Repository[T] is abstract and cannot be instantiated."""

    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            Repository()  # type: ignore[abstract]

    def test_declares_add_as_abstract(self) -> None:
        assert "add" in Repository.__abstractmethods__

    def test_declares_get_by_id_as_abstract(self) -> None:
        assert "get_by_id" in Repository.__abstractmethods__

    def test_declares_find_as_abstract(self) -> None:
        assert "find" in Repository.__abstractmethods__

    def test_declares_update_as_abstract(self) -> None:
        assert "update" in Repository.__abstractmethods__

    def test_declares_delete_as_abstract(self) -> None:
        assert "delete" in Repository.__abstractmethods__


@pytest.mark.unit
@pytest.mark.storage
class TestUnitOfWorkABC:
    """Verify UnitOfWork is abstract and cannot be instantiated."""

    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            UnitOfWork()  # type: ignore[abstract]

    def test_declares_commit_as_abstract(self) -> None:
        assert "commit" in UnitOfWork.__abstractmethods__

    def test_declares_rollback_as_abstract(self) -> None:
        assert "rollback" in UnitOfWork.__abstractmethods__

    def test_declares_aenter_as_abstract(self) -> None:
        assert "__aenter__" in UnitOfWork.__abstractmethods__

    def test_declares_aexit_as_abstract(self) -> None:
        assert "__aexit__" in UnitOfWork.__abstractmethods__


@pytest.mark.unit
@pytest.mark.storage
class TestDatabaseProviderABC:
    """Verify DatabaseProvider is abstract and cannot be instantiated."""

    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            DatabaseProvider()  # type: ignore[abstract]

    def test_declares_get_session_as_abstract(self) -> None:
        assert "get_session" in DatabaseProvider.__abstractmethods__

    def test_declares_dispose_as_abstract(self) -> None:
        assert "dispose" in DatabaseProvider.__abstractmethods__

    def test_declares_health_check_as_abstract(self) -> None:
        assert "health_check" in DatabaseProvider.__abstractmethods__
