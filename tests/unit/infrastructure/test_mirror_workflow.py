"""Unit tests for infrastructure/mirror/workflow.py MirrorWorkflow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from debcraft.domain.mirror.config import MirrorConfig, RepositoryConfig
from debcraft.infrastructure.mirror.engine import SyncResult
from debcraft.infrastructure.mirror.events import (
    MirrorSyncCompletedEvent,
    MirrorSyncFailedEvent,
    MirrorSyncStartedEvent,
)
from debcraft.infrastructure.mirror.workflow import MirrorWorkflow
from debcraft.platform.contracts.workflow import (
    CancellationToken,
    Workflow,
    WorkflowContext,
)


@pytest.fixture
def repo_config():
    return RepositoryConfig(
        name="test-repo",
        base_url="https://example.com/repo",
        suites=["stable"],
        components=["main"],
        architectures=["amd64"],
    )


@pytest.fixture
def mirror_config(repo_config):
    return MirrorConfig(
        repositories=[repo_config],
        download_timeout=300,
        max_connections_per_repo=20,
        max_total_connections=60,
    )


@pytest.fixture
def mock_config_reader(mirror_config):
    reader = MagicMock()
    reader.read.return_value = mirror_config
    return reader


@pytest.fixture
def mock_download_coordinator():
    coordinator = AsyncMock()
    coordinator.start = AsyncMock()
    coordinator.close = AsyncMock()
    return coordinator


@pytest.fixture
def mock_db_provider():
    provider = AsyncMock()
    mock_session = MagicMock()
    # session.begin() must be a sync call returning an async context manager
    mock_begin_cm = AsyncMock()
    mock_begin_cm.__aenter__ = AsyncMock(return_value=None)
    mock_begin_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_begin_cm)
    mock_session.execute = AsyncMock()
    provider.get_session = AsyncMock(return_value=mock_session)
    return provider


@pytest.fixture
def mock_storage_engine():
    return MagicMock()


@pytest.fixture
def mock_scope(mock_config_reader, mock_download_coordinator, mock_db_provider, mock_storage_engine):
    """Create a mock scope that resolves dependencies."""
    from debcraft.infrastructure.mirror.config_reader import ConfigReader
    from debcraft.infrastructure.mirror.download import DownloadCoordinator
    from debcraft.platform.contracts.persistence import DatabaseProvider
    from debcraft.platform.contracts.storage import StorageEngine

    scope = MagicMock()

    def resolve_side_effect(service_type):
        mapping = {
            ConfigReader: mock_config_reader,
            DownloadCoordinator: mock_download_coordinator,
            DatabaseProvider: mock_db_provider,
            StorageEngine: mock_storage_engine,
        }
        return mapping[service_type]

    scope.resolve.side_effect = resolve_side_effect
    return scope


@pytest.fixture
def mock_context(mock_scope):
    """Create a mock WorkflowContext."""
    context = MagicMock(spec=WorkflowContext)
    context.scope = mock_scope
    context.cancellation_token = CancellationToken()
    context.progress = MagicMock()
    context.resources = MagicMock()
    context.logger = MagicMock()
    context.event_bus = AsyncMock()
    return context


@pytest.mark.unit
@pytest.mark.mirror
class TestMirrorWorkflowIdentity:
    """Tests for MirrorWorkflow identity and ABC compliance."""

    def test_is_workflow_subclass(self):
        assert issubclass(MirrorWorkflow, Workflow)

    def test_name_returns_mirror_sync(self):
        workflow = MirrorWorkflow()
        assert workflow.name == "mirror-sync"

    def test_can_instantiate_without_args(self):
        workflow = MirrorWorkflow()
        assert workflow is not None


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestMirrorWorkflowExecution:
    """Tests for the execute method's orchestration logic."""

    async def test_execute_resolves_dependencies(self, mock_context):
        """execute() resolves ConfigReader, DownloadCoordinator, DatabaseProvider, StorageEngine."""
        workflow = MirrorWorkflow()

        with patch("debcraft.infrastructure.mirror.workflow.MirrorEngine") as mock_engine_cls:
            mock_engine = AsyncMock()
            mock_engine.sync_repository.return_value = SyncResult()
            mock_engine_cls.return_value = mock_engine

            await workflow.execute(mock_context)

        # Verify all dependencies were resolved
        assert mock_context.scope.resolve.call_count == 4

    async def test_execute_starts_and_closes_coordinator(self, mock_context, mock_download_coordinator):
        """execute() starts the download coordinator and closes it in finally."""
        workflow = MirrorWorkflow()

        with patch("debcraft.infrastructure.mirror.workflow.MirrorEngine") as mock_engine_cls:
            mock_engine = AsyncMock()
            mock_engine.sync_repository.return_value = SyncResult()
            mock_engine_cls.return_value = mock_engine

            await workflow.execute(mock_context)

        mock_download_coordinator.start.assert_awaited_once()
        mock_download_coordinator.close.assert_awaited_once()

    async def test_execute_closes_coordinator_on_engine_exception(self, mock_context, mock_download_coordinator):
        """execute() closes the download coordinator even if engine raises unexpectedly."""
        workflow = MirrorWorkflow()

        with (
            patch(
                "debcraft.infrastructure.mirror.workflow.MirrorWorkflow._sync_all_repositories",
                side_effect=RuntimeError("unexpected engine error"),
            ),
            pytest.raises(RuntimeError, match="unexpected engine error"),
        ):
            await workflow.execute(mock_context)

        mock_download_coordinator.close.assert_awaited_once()

    async def test_execute_publishes_started_event(self, mock_context, repo_config):
        """execute() publishes MirrorSyncStartedEvent for each repository."""
        workflow = MirrorWorkflow()

        with patch("debcraft.infrastructure.mirror.workflow.MirrorEngine") as mock_engine_cls:
            mock_engine = AsyncMock()
            mock_engine.sync_repository.return_value = SyncResult()
            mock_engine_cls.return_value = mock_engine

            await workflow.execute(mock_context)

        # Find the started event in published events
        published_events = [call.args[0] for call in mock_context.event_bus.publish.call_args_list]
        started_events = [e for e in published_events if isinstance(e, MirrorSyncStartedEvent)]
        assert len(started_events) == 1
        assert started_events[0].repository_name == "test-repo"
        assert started_events[0].suites == ("stable",)

    async def test_execute_publishes_completed_event_on_success(self, mock_context, repo_config):
        """execute() publishes MirrorSyncCompletedEvent on successful sync."""
        workflow = MirrorWorkflow()

        with patch("debcraft.infrastructure.mirror.workflow.MirrorEngine") as mock_engine_cls:
            mock_engine = AsyncMock()
            mock_engine.sync_repository.return_value = SyncResult(
                files_downloaded=10, files_skipped=5, bytes_transferred=1024
            )
            mock_engine_cls.return_value = mock_engine

            await workflow.execute(mock_context)

        published_events = [call.args[0] for call in mock_context.event_bus.publish.call_args_list]
        completed_events = [e for e in published_events if isinstance(e, MirrorSyncCompletedEvent)]
        assert len(completed_events) == 1
        assert completed_events[0].files_downloaded == 10
        assert completed_events[0].files_skipped == 5
        assert completed_events[0].bytes_transferred == 1024

    async def test_execute_publishes_failed_event_on_repo_error(self, mock_context, repo_config):
        """execute() publishes MirrorSyncFailedEvent when a repo sync raises."""
        workflow = MirrorWorkflow()

        with patch("debcraft.infrastructure.mirror.workflow.MirrorEngine") as mock_engine_cls:
            mock_engine = AsyncMock()
            mock_engine.sync_repository.side_effect = RuntimeError("network failure")
            mock_engine_cls.return_value = mock_engine

            await workflow.execute(mock_context)

        published_events = [call.args[0] for call in mock_context.event_bus.publish.call_args_list]
        failed_events = [e for e in published_events if isinstance(e, MirrorSyncFailedEvent)]
        assert len(failed_events) == 1
        assert failed_events[0].repository_name == "test-repo"
        assert "network failure" in failed_events[0].error_message


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestMirrorWorkflowIsolation:
    """Tests for per-repository isolation behavior."""

    async def test_one_repo_failure_does_not_stop_others(self, mock_context):
        """One repository failure doesn't prevent other repositories from syncing."""
        repo1 = RepositoryConfig(
            name="repo1",
            base_url="https://example.com/repo1",
            suites=["stable"],
            components=["main"],
            architectures=["amd64"],
        )
        repo2 = RepositoryConfig(
            name="repo2",
            base_url="https://example.com/repo2",
            suites=["stable"],
            components=["main"],
            architectures=["amd64"],
        )
        multi_config = MirrorConfig(
            repositories=[repo1, repo2],
            download_timeout=300,
            max_connections_per_repo=20,
            max_total_connections=60,
        )

        from debcraft.infrastructure.mirror.config_reader import ConfigReader

        mock_context.scope.resolve.side_effect = lambda t: {
            ConfigReader: MagicMock(read=MagicMock(return_value=multi_config)),
            __import__(
                "debcraft.infrastructure.mirror.download", fromlist=["DownloadCoordinator"]
            ).DownloadCoordinator: AsyncMock(start=AsyncMock(), close=AsyncMock()),
            __import__(
                "debcraft.platform.contracts.persistence", fromlist=["DatabaseProvider"]
            ).DatabaseProvider: AsyncMock(),
            __import__("debcraft.platform.contracts.storage", fromlist=["StorageEngine"]).StorageEngine: MagicMock(),
        }.get(t)

        # Simpler approach: just set up fresh mocks
        mock_reader = MagicMock()
        mock_reader.read.return_value = multi_config
        mock_coord = AsyncMock()
        mock_coord.start = AsyncMock()
        mock_coord.close = AsyncMock()
        mock_db = AsyncMock()
        mock_storage = MagicMock()

        from debcraft.infrastructure.mirror.download import DownloadCoordinator
        from debcraft.platform.contracts.persistence import DatabaseProvider
        from debcraft.platform.contracts.storage import StorageEngine

        def resolve(t):
            return {
                ConfigReader: mock_reader,
                DownloadCoordinator: mock_coord,
                DatabaseProvider: mock_db,
                StorageEngine: mock_storage,
            }[t]

        mock_context.scope.resolve.side_effect = resolve

        call_count = 0

        with patch("debcraft.infrastructure.mirror.workflow.MirrorEngine") as mock_engine_cls:
            mock_engine = AsyncMock()

            async def sync_side_effect(config, session_id):
                nonlocal call_count
                call_count += 1
                if config.name == "repo1":
                    raise RuntimeError("repo1 failed")
                return SyncResult(files_downloaded=5)

            mock_engine.sync_repository.side_effect = sync_side_effect
            mock_engine_cls.return_value = mock_engine

            await MirrorWorkflow().execute(mock_context)

        # Both repos were attempted
        assert call_count == 2


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestMirrorWorkflowCancellation:
    """Tests for cancellation token checking and state rollback."""

    async def test_cancellation_stops_iteration(self, mock_context):
        """Cancellation between repositories stops further iteration."""
        repo1 = RepositoryConfig(
            name="repo1",
            base_url="https://example.com/repo1",
            suites=["stable"],
            components=["main"],
            architectures=["amd64"],
        )
        repo2 = RepositoryConfig(
            name="repo2",
            base_url="https://example.com/repo2",
            suites=["stable"],
            components=["main"],
            architectures=["amd64"],
        )
        multi_config = MirrorConfig(
            repositories=[repo1, repo2],
            download_timeout=300,
            max_connections_per_repo=20,
            max_total_connections=60,
        )

        mock_reader = MagicMock()
        mock_reader.read.return_value = multi_config
        mock_coord = AsyncMock()
        mock_coord.start = AsyncMock()
        mock_coord.close = AsyncMock()
        mock_db = AsyncMock()
        mock_session = MagicMock()
        mock_begin_cm = AsyncMock()
        mock_begin_cm.__aenter__ = AsyncMock(return_value=None)
        mock_begin_cm.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(return_value=mock_begin_cm)
        mock_session.execute = AsyncMock()
        mock_db.get_session = AsyncMock(return_value=mock_session)
        mock_storage = MagicMock()

        from debcraft.infrastructure.mirror.config_reader import ConfigReader
        from debcraft.infrastructure.mirror.download import DownloadCoordinator
        from debcraft.platform.contracts.persistence import DatabaseProvider
        from debcraft.platform.contracts.storage import StorageEngine

        mock_context.scope.resolve.side_effect = lambda t: {
            ConfigReader: mock_reader,
            DownloadCoordinator: mock_coord,
            DatabaseProvider: mock_db,
            StorageEngine: mock_storage,
        }[t]

        sync_calls = []

        with patch("debcraft.infrastructure.mirror.workflow.MirrorEngine") as mock_engine_cls:
            mock_engine = AsyncMock()

            async def sync_side_effect(config, session_id):
                sync_calls.append(config.name)
                # Cancel after first repo sync
                mock_context.cancellation_token.cancel()
                return SyncResult()

            mock_engine.sync_repository.side_effect = sync_side_effect
            mock_engine_cls.return_value = mock_engine

            await MirrorWorkflow().execute(mock_context)

        # Only first repo was synced; second was skipped due to cancellation
        assert sync_calls == ["repo1"]

    async def test_cancellation_triggers_rollback(self, mock_context, mock_db_provider):
        """Cancellation triggers _rollback_cancellation_state."""
        workflow = MirrorWorkflow()

        # Pre-cancel the token
        mock_context.cancellation_token.cancel()

        from debcraft.infrastructure.mirror.config_reader import ConfigReader
        from debcraft.infrastructure.mirror.download import DownloadCoordinator
        from debcraft.platform.contracts.persistence import DatabaseProvider
        from debcraft.platform.contracts.storage import StorageEngine

        mock_reader = MagicMock()
        mock_reader.read.return_value = MirrorConfig(repositories=[], download_timeout=300)
        mock_coord = AsyncMock()
        mock_coord.start = AsyncMock()
        mock_coord.close = AsyncMock()

        mock_session = MagicMock()
        mock_begin_cm = AsyncMock()
        mock_begin_cm.__aenter__ = AsyncMock(return_value=None)
        mock_begin_cm.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(return_value=mock_begin_cm)
        mock_session.execute = AsyncMock()
        mock_db_provider.get_session = AsyncMock(return_value=mock_session)

        mock_context.scope.resolve.side_effect = lambda t: {
            ConfigReader: mock_reader,
            DownloadCoordinator: mock_coord,
            DatabaseProvider: mock_db_provider,
            StorageEngine: MagicMock(),
        }[t]

        await workflow.execute(mock_context)

        # Verify rollback was attempted (get_session was called for "mirror")
        mock_db_provider.get_session.assert_awaited_with("mirror")

    async def test_rollback_logs_error_on_db_failure(self, mock_context):
        """Rollback logs error if DB commit fails (Req 9.5)."""
        workflow = MirrorWorkflow()

        # Pre-cancel the token
        mock_context.cancellation_token.cancel()

        from sqlalchemy.exc import OperationalError

        from debcraft.infrastructure.mirror.config_reader import ConfigReader
        from debcraft.infrastructure.mirror.download import DownloadCoordinator
        from debcraft.platform.contracts.persistence import DatabaseProvider
        from debcraft.platform.contracts.storage import StorageEngine

        mock_reader = MagicMock()
        mock_reader.read.return_value = MirrorConfig(repositories=[], download_timeout=300)
        mock_coord = AsyncMock()
        mock_coord.start = AsyncMock()
        mock_coord.close = AsyncMock()
        mock_db = AsyncMock()
        mock_db.get_session.side_effect = OperationalError("", [], Exception("db connection failed"))

        mock_context.scope.resolve.side_effect = lambda t: {
            ConfigReader: mock_reader,
            DownloadCoordinator: mock_coord,
            DatabaseProvider: mock_db,
            StorageEngine: MagicMock(),
        }[t]

        # Should not raise - just logs the error
        await workflow.execute(mock_context)

        # Verify error was logged
        mock_context.logger.error.assert_called()
