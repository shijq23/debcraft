"""Unit tests for DI wiring and fallback scenarios in the SBOM CLI.

Tests cover:
- _create_di_scope uses real EnrichmentCacheAdapter when cache.db is available
- Fallback to _NoOpCacheAdapter when cache.db connection fails
- resolve_snapshot_id is called and result flows to SBOMWorkflowConfig.snapshot_id
- Engine disposal on both success and error paths

Requirements: 2.1, 2.2, 2.4, 6.2
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from debcraft.infrastructure.models.base import Base

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# _create_di_scope: Real EnrichmentCacheAdapter vs NoOp fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCreateDiScopeCacheAdapter:
    """Tests for _create_di_scope cache adapter selection."""

    async def test_uses_real_cache_adapter_when_cache_session_available(self) -> None:
        """When engines have a cache_session_factory, real EnrichmentCacheAdapter is used.

        Validates: Requirement 2.1, 2.2
        """
        from debcraft.cli._sbom_db import DatabaseEngines
        from debcraft.cli.sbom import _create_di_scope
        from debcraft.infrastructure.scanners.cache_adapter import EnrichmentCacheAdapter
        from debcraft.infrastructure.scanners.enricher import MetadataEnricher

        # Create a real in-memory engine with cache schema
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        engines = DatabaseEngines(
            metadata_engine=None,
            cache_engine=engine,
            metadata_session_factory=None,
            cache_session_factory=session_factory,
        )

        try:
            scope = _create_di_scope(snapshot_id=5, engines=engines)
            enricher = scope.resolve(MetadataEnricher)
            # The enricher should have been given the real cache adapter
            assert isinstance(enricher._cache, EnrichmentCacheAdapter)
        finally:
            await engine.dispose()

    async def test_falls_back_to_noop_when_no_engines(self) -> None:
        """When engines is None, _NoOpCacheAdapter is used.

        Validates: Requirement 2.4
        """
        from debcraft.cli.sbom import _create_di_scope, _NoOpCacheAdapter
        from debcraft.infrastructure.scanners.enricher import MetadataEnricher

        scope = _create_di_scope(snapshot_id=0, engines=None)
        enricher = scope.resolve(MetadataEnricher)
        assert isinstance(enricher._cache, _NoOpCacheAdapter)

    async def test_falls_back_to_noop_when_cache_session_factory_is_none(self) -> None:
        """When engines exist but cache_session_factory is None, _NoOpCacheAdapter is used.

        Validates: Requirement 2.4
        """
        from debcraft.cli._sbom_db import DatabaseEngines
        from debcraft.cli.sbom import _create_di_scope, _NoOpCacheAdapter
        from debcraft.infrastructure.scanners.enricher import MetadataEnricher

        engines = DatabaseEngines(
            metadata_engine=None,
            cache_engine=None,
            metadata_session_factory=None,
            cache_session_factory=None,
        )

        scope = _create_di_scope(snapshot_id=0, engines=engines)
        enricher = scope.resolve(MetadataEnricher)
        assert isinstance(enricher._cache, _NoOpCacheAdapter)

    async def test_falls_back_to_noop_when_cache_adapter_creation_raises(self) -> None:
        """When EnrichmentCacheAdapter construction raises, falls back to _NoOpCacheAdapter.

        Validates: Requirement 2.4
        """
        from debcraft.cli._sbom_db import DatabaseEngines
        from debcraft.cli.sbom import _create_di_scope, _NoOpCacheAdapter
        from debcraft.infrastructure.scanners.enricher import MetadataEnricher

        # Create engines with a non-None cache_session_factory that will trigger
        # an exception during EnrichmentCacheAdapter instantiation
        mock_session_factory = MagicMock()

        engines = DatabaseEngines(
            metadata_engine=None,
            cache_engine=None,
            metadata_session_factory=None,
            cache_session_factory=mock_session_factory,
        )

        # Patch EnrichmentCacheAdapter at the import location within _create_di_scope
        with patch(
            "debcraft.infrastructure.scanners.cache_adapter.EnrichmentCacheAdapter",
            side_effect=RuntimeError("Connection failed"),
        ):
            scope = _create_di_scope(snapshot_id=0, engines=engines)
            enricher = scope.resolve(MetadataEnricher)
            assert isinstance(enricher._cache, _NoOpCacheAdapter)


# ---------------------------------------------------------------------------
# _create_di_scope: metadata session factory flows to MetadataEnricher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCreateDiScopeMetadataSessionFactory:
    """Tests for metadata session factory wiring in _create_di_scope."""

    async def test_metadata_session_factory_passed_to_enricher(self) -> None:
        """MetadataEnricher receives the metadata_session_factory from engines.

        Validates: Requirement 2.2
        """
        from debcraft.cli._sbom_db import DatabaseEngines
        from debcraft.cli.sbom import _create_di_scope
        from debcraft.infrastructure.scanners.enricher import MetadataEnricher

        # Create a real in-memory engine for metadata
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        metadata_session_factory = async_sessionmaker(engine, expire_on_commit=False)

        engines = DatabaseEngines(
            metadata_engine=engine,
            cache_engine=None,
            metadata_session_factory=metadata_session_factory,
            cache_session_factory=None,
        )

        try:
            scope = _create_di_scope(snapshot_id=3, engines=engines)
            enricher = scope.resolve(MetadataEnricher)
            assert enricher._metadata_session_factory is metadata_session_factory
        finally:
            await engine.dispose()

    async def test_metadata_session_factory_none_when_no_engines(self) -> None:
        """MetadataEnricher gets None metadata_session_factory when engines is None."""
        from debcraft.cli.sbom import _create_di_scope
        from debcraft.infrastructure.scanners.enricher import MetadataEnricher

        scope = _create_di_scope(snapshot_id=0, engines=None)
        enricher = scope.resolve(MetadataEnricher)
        assert enricher._metadata_session_factory is None


# ---------------------------------------------------------------------------
# _run_sbom: resolve_snapshot_id flows to SBOMWorkflowConfig.snapshot_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRunSbomSnapshotIdFlow:
    """Tests that resolve_snapshot_id result flows to SBOMWorkflowConfig.snapshot_id."""

    async def test_resolved_snapshot_id_flows_to_config(self, tmp_path: Path) -> None:
        """The resolved snapshot_id is set on SBOMWorkflowConfig.

        Validates: Requirement 6.2
        """
        from debcraft.domain.sbom.values import OutputFormat

        artifact = tmp_path / "artifact.deb"
        artifact.write_text("fake")

        captured_config = {}

        async def fake_workflow_execute(self, context):
            """Capture the config used by the workflow."""
            captured_config["snapshot_id"] = self._config.snapshot_id

        with (
            patch("debcraft.cli._sbom_db.create_database_engines", new_callable=AsyncMock) as mock_engines_fn,
            patch("debcraft.cli._sbom_db.resolve_snapshot_id", new_callable=AsyncMock) as mock_resolve,
            patch(
                "debcraft.infrastructure.sbom_writers.workflow.SBOMWorkflow.execute",
                fake_workflow_execute,
            ),
        ):
            from debcraft.cli._sbom_db import DatabaseEngines

            mock_engines = DatabaseEngines(
                metadata_engine=None,
                cache_engine=None,
                metadata_session_factory=None,
                cache_session_factory=None,
            )
            mock_engines_fn.return_value = mock_engines
            mock_resolve.return_value = 42

            # Import after patching
            from rich.progress import Progress

            from debcraft.cli.sbom import _run_sbom

            with Progress() as progress:
                task_id = progress.add_task("test", total=100)
                await _run_sbom(
                    artifact_path=artifact,
                    formats=[OutputFormat.SPDX_2_3],
                    output_dir=tmp_path,
                    artifact_type=None,
                    snapshot_id=None,
                    quiet=True,
                    progress=progress,
                    task_id=task_id,
                )

        assert captured_config["snapshot_id"] == 42

    async def test_explicit_snapshot_id_forwarded_to_resolve(self, tmp_path: Path) -> None:
        """When explicit snapshot_id is given, it is forwarded to resolve_snapshot_id.

        Validates: Requirement 6.2
        """
        from debcraft.domain.sbom.values import OutputFormat

        artifact = tmp_path / "artifact.deb"
        artifact.write_text("fake")

        async def fake_workflow_execute(self, context):
            pass

        with (
            patch("debcraft.cli._sbom_db.create_database_engines", new_callable=AsyncMock) as mock_engines_fn,
            patch("debcraft.cli._sbom_db.resolve_snapshot_id", new_callable=AsyncMock) as mock_resolve,
            patch(
                "debcraft.infrastructure.sbom_writers.workflow.SBOMWorkflow.execute",
                fake_workflow_execute,
            ),
        ):
            from debcraft.cli._sbom_db import DatabaseEngines

            mock_engines = DatabaseEngines(
                metadata_engine=None,
                cache_engine=None,
                metadata_session_factory=None,
                cache_session_factory=None,
            )
            mock_engines_fn.return_value = mock_engines
            mock_resolve.return_value = 99

            from rich.progress import Progress

            from debcraft.cli.sbom import _run_sbom

            with Progress() as progress:
                task_id = progress.add_task("test", total=100)
                await _run_sbom(
                    artifact_path=artifact,
                    formats=[OutputFormat.SPDX_2_3],
                    output_dir=tmp_path,
                    artifact_type=None,
                    snapshot_id=99,
                    quiet=True,
                    progress=progress,
                    task_id=task_id,
                )

        # resolve_snapshot_id should have been called with explicit_id=99
        mock_resolve.assert_called_once_with(
            session_factory=mock_engines.metadata_session_factory,
            explicit_id=99,
        )


# ---------------------------------------------------------------------------
# _run_sbom: Engine disposal on success and error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRunSbomEngineDisposal:
    """Tests that engines.dispose() is called on success and failure."""

    async def test_engines_disposed_on_success(self, tmp_path: Path) -> None:
        """Engines are disposed after successful workflow execution.

        Validates: Requirement 6.2
        """
        from debcraft.domain.sbom.values import OutputFormat

        artifact = tmp_path / "artifact.deb"
        artifact.write_text("fake")

        async def fake_workflow_execute(self, context):
            pass

        with (
            patch("debcraft.cli._sbom_db.create_database_engines", new_callable=AsyncMock) as mock_engines_fn,
            patch("debcraft.cli._sbom_db.resolve_snapshot_id", new_callable=AsyncMock) as mock_resolve,
            patch(
                "debcraft.infrastructure.sbom_writers.workflow.SBOMWorkflow.execute",
                fake_workflow_execute,
            ),
        ):
            mock_engines = AsyncMock()
            mock_engines.metadata_session_factory = None
            mock_engines.cache_session_factory = None
            mock_engines.metadata_engine = None
            mock_engines.cache_engine = None
            mock_engines_fn.return_value = mock_engines
            mock_resolve.return_value = 0

            from rich.progress import Progress

            from debcraft.cli.sbom import _run_sbom

            with Progress() as progress:
                task_id = progress.add_task("test", total=100)
                await _run_sbom(
                    artifact_path=artifact,
                    formats=[OutputFormat.SPDX_2_3],
                    output_dir=tmp_path,
                    artifact_type=None,
                    snapshot_id=None,
                    quiet=True,
                    progress=progress,
                    task_id=task_id,
                )

        mock_engines.dispose.assert_awaited_once()

    async def test_engines_disposed_on_workflow_error(self, tmp_path: Path) -> None:
        """Engines are disposed even when the workflow raises an exception.

        Validates: Requirement 6.2
        """
        from debcraft.domain.sbom.values import OutputFormat

        artifact = tmp_path / "artifact.deb"
        artifact.write_text("fake")

        async def failing_workflow_execute(self, context):
            raise RuntimeError("Workflow exploded")

        with (
            patch("debcraft.cli._sbom_db.create_database_engines", new_callable=AsyncMock) as mock_engines_fn,
            patch("debcraft.cli._sbom_db.resolve_snapshot_id", new_callable=AsyncMock) as mock_resolve,
            patch(
                "debcraft.infrastructure.sbom_writers.workflow.SBOMWorkflow.execute",
                failing_workflow_execute,
            ),
        ):
            mock_engines = AsyncMock()
            mock_engines.metadata_session_factory = None
            mock_engines.cache_session_factory = None
            mock_engines.metadata_engine = None
            mock_engines.cache_engine = None
            mock_engines_fn.return_value = mock_engines
            mock_resolve.return_value = 0

            from rich.progress import Progress

            from debcraft.cli.sbom import _run_sbom

            with Progress() as progress:
                task_id = progress.add_task("test", total=100)
                with pytest.raises(RuntimeError, match="Workflow exploded"):
                    await _run_sbom(
                        artifact_path=artifact,
                        formats=[OutputFormat.SPDX_2_3],
                        output_dir=tmp_path,
                        artifact_type=None,
                        snapshot_id=None,
                        quiet=True,
                        progress=progress,
                        task_id=task_id,
                    )

        # Engines should still be disposed despite the exception
        mock_engines.dispose.assert_awaited_once()

    async def test_engines_disposed_on_resolve_error(self, tmp_path: Path) -> None:
        """Engines are disposed even when resolve_snapshot_id raises.

        Validates: Requirement 6.2
        """
        from debcraft.domain.sbom.values import OutputFormat

        artifact = tmp_path / "artifact.deb"
        artifact.write_text("fake")

        with (
            patch("debcraft.cli._sbom_db.create_database_engines", new_callable=AsyncMock) as mock_engines_fn,
            patch("debcraft.cli._sbom_db.resolve_snapshot_id", new_callable=AsyncMock) as mock_resolve,
        ):
            mock_engines = AsyncMock()
            mock_engines.metadata_session_factory = None
            mock_engines.cache_session_factory = None
            mock_engines.metadata_engine = None
            mock_engines.cache_engine = None
            mock_engines_fn.return_value = mock_engines
            mock_resolve.side_effect = RuntimeError("DB connection failure")

            from rich.progress import Progress

            from debcraft.cli.sbom import _run_sbom

            with Progress() as progress:
                task_id = progress.add_task("test", total=100)
                with pytest.raises(RuntimeError, match="DB connection failure"):
                    await _run_sbom(
                        artifact_path=artifact,
                        formats=[OutputFormat.SPDX_2_3],
                        output_dir=tmp_path,
                        artifact_type=None,
                        snapshot_id=None,
                        quiet=True,
                        progress=progress,
                        task_id=task_id,
                    )

        # Engines should still be disposed in the finally block
        mock_engines.dispose.assert_awaited_once()
