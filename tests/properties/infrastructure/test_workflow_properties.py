"""Property-based tests for cancellation and repository isolation.

**Validates: Requirements 8.3, 9.3**

Property 18: Repository isolation on failure.
For any set of configured repositories where one repository's sync raises an
exception, the remaining repositories SHALL still be attempted (the failure
does not propagate).

Property 19: Cancellation state rollback rules.
When cancellation occurs, QUEUED entities SHALL transition to DISCOVERED,
DOWNLOADING entities SHALL transition to QUEUED, and DOWNLOADED/VERIFIED/
INDEXED entities SHALL remain unchanged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from debcraft.domain.mirror.config import MirrorConfig, RepositoryConfig
from debcraft.infrastructure.mirror.engine import SyncResult
from debcraft.infrastructure.mirror.workflow import MirrorWorkflow
from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.models.mirror import (
    RepositoryFile,
    RepositoryFileState,
)
from debcraft.platform.contracts.workflow import (
    CancellationToken,
    WorkflowContext,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate 2-5 repository configs with unique names
_repo_name_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=32,
)


def _repo_config_strategy() -> st.SearchStrategy[RepositoryConfig]:
    """Generate valid RepositoryConfig instances."""
    return st.builds(
        RepositoryConfig,
        name=_repo_name_strategy,
        base_url=st.just("https://repo.example.com/debian"),
        suites=st.just(["stable"]),
        components=st.just(["main"]),
        architectures=st.just(["amd64"]),
    )


# List of 2-5 repo configs with unique names
_repo_list_strategy = st.lists(
    _repo_config_strategy(),
    min_size=2,
    max_size=5,
    unique_by=lambda rc: rc.name,
)

# Boolean mask indicating which repos should fail
_failure_mask_strategy = st.lists(
    st.booleans(),
    min_size=2,
    max_size=5,
)

# States for cancellation rollback testing
_all_states = st.sampled_from(
    [
        RepositoryFileState.DISCOVERED,
        RepositoryFileState.QUEUED,
        RepositoryFileState.DOWNLOADING,
        RepositoryFileState.DOWNLOADED,
        RepositoryFileState.VERIFIED,
        RepositoryFileState.INDEXED,
        RepositoryFileState.FAILED,
    ]
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _setup_db() -> tuple[async_sessionmaker[AsyncSession], object]:
    """Create in-memory SQLite engine with all tables."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


def _make_workflow_context(
    *,
    cancelled: bool = False,
    scope_resolve_map: dict | None = None,
) -> WorkflowContext:
    """Create a mock WorkflowContext for testing."""
    scope = MagicMock()
    if scope_resolve_map:
        scope.resolve = MagicMock(side_effect=lambda t: scope_resolve_map[t])

    cancellation_token = CancellationToken()
    if cancelled:
        cancellation_token.cancel()

    progress = MagicMock()
    progress.report = MagicMock()

    resource_manager = MagicMock()
    logger = MagicMock()
    logger.info = MagicMock()
    logger.error = MagicMock()

    event_bus = MagicMock()
    event_bus.publish = AsyncMock()

    context = WorkflowContext(
        scope=scope,
        cancellation_token=cancellation_token,
        progress_reporter=progress,
        resource_manager=resource_manager,
        logger=logger,
        event_bus=event_bus,
    )
    return context


# ---------------------------------------------------------------------------
# Property 18: Repository isolation on failure
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestProperty18RepositoryIsolationOnFailure:
    """Property 18: Repository isolation on failure.

    For any set of configured repositories where one repository's sync
    raises an exception, the remaining repositories SHALL still be
    attempted (the failure does not propagate).
    """

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(
        repos=_repo_list_strategy,
        failure_mask=_failure_mask_strategy,
    )
    async def test_all_repos_attempted_despite_failures(
        self,
        repos: list[RepositoryConfig],
        failure_mask: list[bool],
    ) -> None:
        """**Validates: Requirements 8.3**.

        For any set of 2-5 repositories where some fail, the workflow
        SHALL still attempt all repositories. The count of
        sync_repository calls equals the count of repos.
        """
        # Align failure mask length with repos length
        mask = failure_mask[: len(repos)]
        while len(mask) < len(repos):
            mask.append(False)

        # Track which repos were called
        called_repos: list[str] = []

        async def mock_sync_repository(config, session_id):
            called_repos.append(config.name)
            if mask[repos.index(config)]:
                raise RuntimeError(f"Simulated failure for {config.name}")
            return SyncResult(
                files_downloaded=1,
                files_skipped=0,
                files_failed=0,
                bytes_transferred=100,
            )

        # Create a mock MirrorEngine class that tracks calls
        context = _make_workflow_context()

        # Create a MirrorConfig with our test repos
        config = MirrorConfig(
            repositories=repos,
            download_timeout=30,
            max_connections_per_repo=5,
            max_total_connections=10,
        )

        # Mock ConfigReader to return our config
        mock_config_reader = MagicMock()
        mock_config_reader.read = MagicMock(return_value=config)

        # Mock DownloadCoordinator
        mock_download_coordinator = MagicMock()
        mock_download_coordinator.start = AsyncMock()
        mock_download_coordinator.close = AsyncMock()

        # Mock DatabaseProvider
        mock_db_provider = MagicMock()

        # Mock StorageEngine
        mock_storage_engine = MagicMock()

        # Set up the scope to resolve dependencies
        from debcraft.infrastructure.mirror.config_reader import ConfigReader
        from debcraft.infrastructure.mirror.download import (
            DownloadCoordinator,
        )
        from debcraft.platform.contracts.persistence import DatabaseProvider
        from debcraft.platform.contracts.storage import StorageEngine

        resolve_map = {
            ConfigReader: mock_config_reader,
            DownloadCoordinator: mock_download_coordinator,
            DatabaseProvider: mock_db_provider,
            StorageEngine: mock_storage_engine,
        }
        context.scope.resolve = MagicMock(side_effect=lambda t: resolve_map[t])

        # Patch MirrorEngine to track sync_repository calls
        with patch("debcraft.infrastructure.mirror.workflow.MirrorEngine") as MockEngine:
            mock_engine_instance = MagicMock()
            mock_engine_instance.sync_repository = AsyncMock(side_effect=mock_sync_repository)
            MockEngine.return_value = mock_engine_instance

            workflow = MirrorWorkflow()
            await workflow.execute(context)

        # ALL repos must have been attempted
        assert len(called_repos) == len(repos), (
            f"Expected {len(repos)} repos attempted, got {len(called_repos)}: {called_repos}"
        )
        # Verify the exact set of repos was called
        assert set(called_repos) == {r.name for r in repos}

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(
        repos=_repo_list_strategy,
    )
    async def test_all_repos_fail_still_all_attempted(
        self,
        repos: list[RepositoryConfig],
    ) -> None:
        """**Validates: Requirements 8.3**.

        Even when ALL repositories fail, every single one is still
        attempted — the workflow does not bail out early.
        """
        called_repos: list[str] = []

        async def mock_sync_all_fail(config, session_id):
            called_repos.append(config.name)
            raise RuntimeError(f"Simulated failure for {config.name}")

        context = _make_workflow_context()

        config = MirrorConfig(
            repositories=repos,
            download_timeout=30,
            max_connections_per_repo=5,
            max_total_connections=10,
        )

        mock_config_reader = MagicMock()
        mock_config_reader.read = MagicMock(return_value=config)

        mock_download_coordinator = MagicMock()
        mock_download_coordinator.start = AsyncMock()
        mock_download_coordinator.close = AsyncMock()

        mock_db_provider = MagicMock()
        mock_storage_engine = MagicMock()

        from debcraft.infrastructure.mirror.config_reader import ConfigReader
        from debcraft.infrastructure.mirror.download import (
            DownloadCoordinator,
        )
        from debcraft.platform.contracts.persistence import DatabaseProvider
        from debcraft.platform.contracts.storage import StorageEngine

        resolve_map = {
            ConfigReader: mock_config_reader,
            DownloadCoordinator: mock_download_coordinator,
            DatabaseProvider: mock_db_provider,
            StorageEngine: mock_storage_engine,
        }
        context.scope.resolve = MagicMock(side_effect=lambda t: resolve_map[t])

        with patch("debcraft.infrastructure.mirror.workflow.MirrorEngine") as MockEngine:
            mock_engine_instance = MagicMock()
            mock_engine_instance.sync_repository = AsyncMock(side_effect=mock_sync_all_fail)
            MockEngine.return_value = mock_engine_instance

            workflow = MirrorWorkflow()
            await workflow.execute(context)

        assert len(called_repos) == len(repos)
        assert set(called_repos) == {r.name for r in repos}


# ---------------------------------------------------------------------------
# Property 19: Cancellation state rollback rules
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestProperty19CancellationStateRollbackRules:
    """Property 19: Cancellation state rollback rules.

    When cancellation occurs, QUEUED entities SHALL transition to
    DISCOVERED, DOWNLOADING entities SHALL transition to QUEUED, and
    DOWNLOADED/VERIFIED/INDEXED/FAILED entities SHALL remain unchanged.
    """

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(
        num_queued=st.integers(min_value=0, max_value=10),
        num_downloading=st.integers(min_value=0, max_value=10),
        num_downloaded=st.integers(min_value=0, max_value=5),
        num_verified=st.integers(min_value=0, max_value=5),
        num_indexed=st.integers(min_value=0, max_value=5),
        num_failed=st.integers(min_value=0, max_value=5),
    )
    async def test_rollback_transitions_correct_states(
        self,
        num_queued: int,
        num_downloading: int,
        num_downloaded: int,
        num_verified: int,
        num_indexed: int,
        num_failed: int,
    ) -> None:
        """**Validates: Requirements 9.3**.

        After _rollback_cancellation_state():
        - QUEUED → DISCOVERED
        - DOWNLOADING → QUEUED
        - DOWNLOADED stays DOWNLOADED
        - VERIFIED stays VERIFIED
        - INDEXED stays INDEXED
        - FAILED stays FAILED
        """
        factory, engine_db = await _setup_db()
        try:
            # Create entities in various states
            async with factory() as session:
                for i in range(num_queued):
                    session.add(
                        RepositoryFile(
                            url=f"https://repo.example.com/queued_{i}",
                            sha256="a" * 64,
                            size_bytes=1024,
                            state=RepositoryFileState.QUEUED,
                            retry_count=0,
                        )
                    )
                for i in range(num_downloading):
                    session.add(
                        RepositoryFile(
                            url=f"https://repo.example.com/downloading_{i}",
                            sha256="b" * 64,
                            size_bytes=2048,
                            state=RepositoryFileState.DOWNLOADING,
                            retry_count=0,
                        )
                    )
                for i in range(num_downloaded):
                    session.add(
                        RepositoryFile(
                            url=f"https://repo.example.com/downloaded_{i}",
                            sha256="c" * 64,
                            size_bytes=3072,
                            state=RepositoryFileState.DOWNLOADED,
                            retry_count=0,
                        )
                    )
                for i in range(num_verified):
                    session.add(
                        RepositoryFile(
                            url=f"https://repo.example.com/verified_{i}",
                            sha256="d" * 64,
                            size_bytes=4096,
                            state=RepositoryFileState.VERIFIED,
                            retry_count=0,
                            local_path=f"/tmp/mirror/verified_{i}",
                        )
                    )
                for i in range(num_indexed):
                    session.add(
                        RepositoryFile(
                            url=f"https://repo.example.com/indexed_{i}",
                            sha256="e" * 64,
                            size_bytes=5120,
                            state=RepositoryFileState.INDEXED,
                            retry_count=0,
                            local_path=f"/tmp/mirror/indexed_{i}",
                        )
                    )
                for i in range(num_failed):
                    session.add(
                        RepositoryFile(
                            url=f"https://repo.example.com/failed_{i}",
                            sha256="f" * 64,
                            size_bytes=6144,
                            state=RepositoryFileState.FAILED,
                            retry_count=3,
                        )
                    )
                await session.commit()

            # Create mock db_provider that returns sessions from factory
            mock_db_provider = MagicMock()
            mock_db_provider.get_session = AsyncMock(side_effect=lambda name: factory())

            # Create workflow context with cancellation triggered
            context = _make_workflow_context(cancelled=True)

            # Call _rollback_cancellation_state directly
            workflow = MirrorWorkflow()
            await workflow._rollback_cancellation_state(context, mock_db_provider)

            # Verify state transitions
            async with factory() as session:
                # QUEUED → DISCOVERED
                for i in range(num_queued):
                    stmt = select(RepositoryFile).where(RepositoryFile.url == f"https://repo.example.com/queued_{i}")
                    result = await session.execute(stmt)
                    entity = result.scalar_one()
                    assert entity.state == RepositoryFileState.DISCOVERED, (
                        f"QUEUED entity should become DISCOVERED, got {entity.state}"
                    )

                # DOWNLOADING → QUEUED
                for i in range(num_downloading):
                    stmt = select(RepositoryFile).where(
                        RepositoryFile.url == f"https://repo.example.com/downloading_{i}"
                    )
                    result = await session.execute(stmt)
                    entity = result.scalar_one()
                    assert entity.state == RepositoryFileState.QUEUED, (
                        f"DOWNLOADING entity should become QUEUED, got {entity.state}"
                    )

                # DOWNLOADED stays DOWNLOADED
                for i in range(num_downloaded):
                    stmt = select(RepositoryFile).where(
                        RepositoryFile.url == f"https://repo.example.com/downloaded_{i}"
                    )
                    result = await session.execute(stmt)
                    entity = result.scalar_one()
                    assert entity.state == RepositoryFileState.DOWNLOADED, (
                        f"DOWNLOADED entity should stay DOWNLOADED, got {entity.state}"
                    )

                # VERIFIED stays VERIFIED
                for i in range(num_verified):
                    stmt = select(RepositoryFile).where(RepositoryFile.url == f"https://repo.example.com/verified_{i}")
                    result = await session.execute(stmt)
                    entity = result.scalar_one()
                    assert entity.state == RepositoryFileState.VERIFIED, (
                        f"VERIFIED entity should stay VERIFIED, got {entity.state}"
                    )

                # INDEXED stays INDEXED
                for i in range(num_indexed):
                    stmt = select(RepositoryFile).where(RepositoryFile.url == f"https://repo.example.com/indexed_{i}")
                    result = await session.execute(stmt)
                    entity = result.scalar_one()
                    assert entity.state == RepositoryFileState.INDEXED, (
                        f"INDEXED entity should stay INDEXED, got {entity.state}"
                    )

                # FAILED stays FAILED
                for i in range(num_failed):
                    stmt = select(RepositoryFile).where(RepositoryFile.url == f"https://repo.example.com/failed_{i}")
                    result = await session.execute(stmt)
                    entity = result.scalar_one()
                    assert entity.state == RepositoryFileState.FAILED, (
                        f"FAILED entity should stay FAILED, got {entity.state}"
                    )
        finally:
            await engine_db.dispose()

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(
        states=st.lists(
            _all_states,
            min_size=1,
            max_size=20,
        ),
    )
    async def test_rollback_preserves_entity_count(
        self,
        states: list[RepositoryFileState],
    ) -> None:
        """**Validates: Requirements 9.3**.

        The rollback operation never creates or deletes entities — it only
        transitions states. The total entity count remains identical.
        """
        factory, engine_db = await _setup_db()
        try:
            # Create entities in the generated states
            async with factory() as session:
                for i, state in enumerate(states):
                    entity = RepositoryFile(
                        url=f"https://repo.example.com/entity_{i}",
                        sha256="a" * 64,
                        size_bytes=1024,
                        state=state,
                        retry_count=3 if state == RepositoryFileState.FAILED else 0,
                        local_path=(
                            f"/tmp/mirror/entity_{i}"
                            if state
                            in (
                                RepositoryFileState.VERIFIED,
                                RepositoryFileState.INDEXED,
                            )
                            else None
                        ),
                    )
                    session.add(entity)
                await session.commit()

            mock_db_provider = MagicMock()
            mock_db_provider.get_session = AsyncMock(side_effect=lambda name: factory())

            context = _make_workflow_context(cancelled=True)
            workflow = MirrorWorkflow()
            await workflow._rollback_cancellation_state(context, mock_db_provider)

            # Verify total entity count is unchanged
            async with factory() as session:
                stmt = select(RepositoryFile)
                result = await session.execute(stmt)
                entities = result.scalars().all()
                assert len(entities) == len(states), f"Expected {len(states)} entities, got {len(entities)}"
        finally:
            await engine_db.dispose()

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(
        num_downloading=st.integers(min_value=1, max_value=10),
    )
    async def test_downloading_entities_become_queued_not_discovered(
        self,
        num_downloading: int,
    ) -> None:
        """**Validates: Requirements 9.3**.

        DOWNLOADING entities transition to QUEUED (not DISCOVERED) in a
        single rollback operation. The ordering in the implementation
        ensures entities moving from DOWNLOADING→QUEUED don't also get
        caught by the QUEUED→DISCOVERED transition.
        """
        factory, engine_db = await _setup_db()
        try:
            # Create only DOWNLOADING entities
            async with factory() as session:
                for i in range(num_downloading):
                    session.add(
                        RepositoryFile(
                            url=f"https://repo.example.com/dl_{i}",
                            sha256="b" * 64,
                            size_bytes=2048,
                            state=RepositoryFileState.DOWNLOADING,
                            retry_count=0,
                        )
                    )
                await session.commit()

            mock_db_provider = MagicMock()
            mock_db_provider.get_session = AsyncMock(side_effect=lambda name: factory())

            context = _make_workflow_context(cancelled=True)
            workflow = MirrorWorkflow()

            # Apply rollback once
            await workflow._rollback_cancellation_state(context, mock_db_provider)

            # Verify: DOWNLOADING → QUEUED (NOT DISCOVERED)
            async with factory() as session:
                stmt = select(RepositoryFile)
                result = await session.execute(stmt)
                entities = result.scalars().all()

                for entity in entities:
                    assert entity.state == RepositoryFileState.QUEUED, (
                        f"DOWNLOADING entity should become QUEUED (not DISCOVERED), got {entity.state}"
                    )
        finally:
            await engine_db.dispose()
