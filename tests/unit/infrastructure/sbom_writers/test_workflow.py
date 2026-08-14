"""Unit tests for infrastructure/sbom_writers/workflow.py SBOMWorkflow."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from debcraft.domain.sbom.assembler import ModelAssembler
from debcraft.domain.sbom.values import OutputFormat
from debcraft.domain.scanner.values import ScanResult
from debcraft.infrastructure.sbom_writers.registry import WriterRegistry
from debcraft.infrastructure.sbom_writers.workflow import (
    SBOMWorkflow,
    SBOMWorkflowConfig,
    WorkflowStepCompletedEvent,
    _WorkflowCancelled,
    _WorkflowPartialFailure,
)
from debcraft.infrastructure.scanners.enricher import MetadataEnricher
from debcraft.infrastructure.scanners.registry import ScannerRegistry
from debcraft.platform.contracts.persistence import DatabaseProvider
from debcraft.platform.contracts.workflow import (
    CancellationToken,
    Workflow,
    WorkflowContext,
)
from debcraft.platform.kernel.events import (
    WorkflowCancelledEvent,
    WorkflowCompletedEvent,
    WorkflowFailedEvent,
    WorkflowStartedEvent,
)

# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def workflow_config():
    """Default workflow configuration for tests."""
    return SBOMWorkflowConfig(
        artifact_path="/tmp/test-artifact",
        output_dir=Path("/tmp/output"),
        formats=[OutputFormat.SPDX_2_3, OutputFormat.CYCLONEDX],
        scan_session_id=42,
        snapshot_id=1,
    )


@pytest.fixture
def mock_scan_result():
    """A real ScanResult instance (frozen dataclass, needed for dataclasses.replace)."""
    return ScanResult(
        packages=[],
        strategy="test",
        diagnostics=[],
        duration_seconds=0.1,
        artifact_path="/tmp/test-artifact",
        enriched_packages=[],
    )


@pytest.fixture
def mock_scanner(mock_scan_result):
    """Mock scanner instance returned by scanner_registry.get_scanner()."""
    scanner = AsyncMock()
    scanner.scan = AsyncMock(return_value=mock_scan_result)
    return scanner


@pytest.fixture
def mock_scanner_registry(mock_scanner):
    """Mock ScannerRegistry that returns the mock scanner."""
    registry = MagicMock(spec=ScannerRegistry)
    registry.get_scanner.return_value = mock_scanner
    return registry


@pytest.fixture
def mock_enricher(mock_scan_result):
    """Mock MetadataEnricher."""
    enricher = MagicMock(spec=MetadataEnricher)
    enricher.enrich = AsyncMock(return_value=([], []))
    return enricher


@pytest.fixture
def mock_sbom_document():
    """A mock SBOMDocument returned by the assembler."""
    return MagicMock()


@pytest.fixture
def mock_assembler(mock_sbom_document):
    """Mock ModelAssembler."""
    assembler = MagicMock(spec=ModelAssembler)
    assembler.assemble.return_value = mock_sbom_document
    return assembler


@pytest.fixture
def mock_writer_result():
    """A fake WriterResult for successful writes."""
    result = MagicMock()
    result.output_path = Path("/tmp/output/sbom.spdx.json")
    result.format = OutputFormat.SPDX_2_3
    result.sha256 = "a" * 64
    result.file_size = 1024
    return result


@pytest.fixture
def mock_writer(mock_writer_result):
    """Mock SBOMWriter that writes successfully."""
    writer = AsyncMock()
    writer.write = AsyncMock(return_value=mock_writer_result)
    return writer


@pytest.fixture
def mock_writer_registry(mock_writer):
    """Mock WriterRegistry that returns the mock writer."""
    registry = MagicMock(spec=WriterRegistry)
    registry.get_writer.return_value = mock_writer
    return registry


@pytest.fixture
def mock_db_session():
    """Mock database session with begin context manager."""
    session = MagicMock()
    mock_begin_cm = AsyncMock()
    mock_begin_cm.__aenter__ = AsyncMock(return_value=None)
    mock_begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=mock_begin_cm)
    session.add = MagicMock()
    return session


@pytest.fixture
def mock_db_provider(mock_db_session):
    """Mock DatabaseProvider."""
    provider = AsyncMock()
    provider.get_session = AsyncMock(return_value=mock_db_session)
    return provider


@pytest.fixture
def mock_scope(
    mock_scanner_registry,
    mock_enricher,
    mock_assembler,
    mock_writer_registry,
    mock_db_provider,
):
    """Mock DI Scope that resolves all workflow dependencies."""
    scope = MagicMock()

    def resolve_side_effect(service_type):
        mapping = {
            ScannerRegistry: mock_scanner_registry,
            MetadataEnricher: mock_enricher,
            ModelAssembler: mock_assembler,
            WriterRegistry: mock_writer_registry,
            DatabaseProvider: mock_db_provider,
        }
        return mapping[service_type]

    scope.resolve.side_effect = resolve_side_effect
    return scope


@pytest.fixture
def mock_context(mock_scope):
    """Mock WorkflowContext with all services."""
    context = MagicMock(spec=WorkflowContext)
    context.scope = mock_scope
    context.cancellation_token = CancellationToken()
    context.progress = MagicMock()
    context.logger = MagicMock()
    context.event_bus = AsyncMock()
    return context


# ─── Identity Tests ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSBOMWorkflowIdentity:
    """Tests for SBOMWorkflow identity and protocol compliance."""

    def test_is_workflow_subclass(self, workflow_config):
        assert issubclass(SBOMWorkflow, Workflow)

    def test_name_returns_sbom(self, workflow_config):
        workflow = SBOMWorkflow(config=workflow_config)
        assert workflow.name == "sbom"

    def test_instantiates_with_config(self, workflow_config):
        workflow = SBOMWorkflow(config=workflow_config)
        assert workflow is not None


# ─── Step Sequencing Tests ───────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
class TestSBOMWorkflowStepSequencing:
    """Tests that the workflow calls steps in the correct order: scan → enrich → assemble → write → persist."""

    async def test_execute_calls_steps_in_order(
        self,
        workflow_config,
        mock_context,
        mock_scanner,
        mock_enricher,
        mock_assembler,
        mock_writer,
        mock_db_provider,
        mock_scan_result,
        mock_sbom_document,
    ):
        """Verify scan → enrich → assemble → write → persist ordering."""
        call_order = []

        async def scan_side_effect(*args, **kwargs):
            call_order.append("scan")
            return mock_scan_result

        async def enrich_side_effect(*args, **kwargs):
            call_order.append("enrich")
            return ([], [])

        def assemble_side_effect(*args, **kwargs):
            call_order.append("assemble")
            return mock_sbom_document

        async def write_side_effect(*args, **kwargs):
            call_order.append("write")
            result = MagicMock()
            result.output_path = Path("/tmp/output/sbom.json")
            result.format = OutputFormat.SPDX_2_3
            result.sha256 = "a" * 64
            result.file_size = 1024
            return result

        async def persist_side_effect(*args, **kwargs):
            call_order.append("persist")
            return MagicMock()

        mock_scanner.scan = AsyncMock(side_effect=scan_side_effect)
        mock_enricher.enrich = AsyncMock(side_effect=enrich_side_effect)
        mock_assembler.assemble.side_effect = assemble_side_effect
        mock_writer.write = AsyncMock(side_effect=write_side_effect)
        mock_db_provider.get_session = AsyncMock(side_effect=persist_side_effect)

        workflow = SBOMWorkflow(config=workflow_config)
        await workflow.execute(mock_context)

        assert call_order == ["scan", "enrich", "assemble", "write", "write", "persist"]

    async def test_execute_resolves_all_dependencies(self, workflow_config, mock_context):
        """Verify all 5 dependencies are resolved from scope."""
        workflow = SBOMWorkflow(config=workflow_config)
        await workflow.execute(mock_context)

        # Five dependencies resolved: ScannerRegistry, MetadataEnricher,
        # ModelAssembler, WriterRegistry, DatabaseProvider
        assert mock_context.scope.resolve.call_count == 5

    async def test_execute_publishes_started_event(self, workflow_config, mock_context):
        """Verify WorkflowStartedEvent is published at workflow start."""
        workflow = SBOMWorkflow(config=workflow_config)
        await workflow.execute(mock_context)

        published_events = [call.args[0] for call in mock_context.event_bus.publish.call_args_list]
        started_events = [e for e in published_events if isinstance(e, WorkflowStartedEvent)]
        assert len(started_events) == 1
        assert started_events[0].workflow_name == "sbom"

    async def test_execute_publishes_completed_event_on_success(self, workflow_config, mock_context):
        """Verify WorkflowCompletedEvent is published on success."""
        workflow = SBOMWorkflow(config=workflow_config)
        await workflow.execute(mock_context)

        published_events = [call.args[0] for call in mock_context.event_bus.publish.call_args_list]
        completed_events = [e for e in published_events if isinstance(e, WorkflowCompletedEvent)]
        assert len(completed_events) == 1
        assert completed_events[0].workflow_name == "sbom"
        assert completed_events[0].duration_seconds >= 0

    async def test_execute_publishes_step_completed_events(self, workflow_config, mock_context):
        """Verify step completion events are published for all 5 steps."""
        workflow = SBOMWorkflow(config=workflow_config)
        await workflow.execute(mock_context)

        published_events = [call.args[0] for call in mock_context.event_bus.publish.call_args_list]
        step_events = [e for e in published_events if isinstance(e, WorkflowStepCompletedEvent)]
        step_names = [e.step_name for e in step_events]
        assert step_names == ["scan", "enrich", "assemble", "write", "persist"]


# ─── Cancellation Tests ──────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
class TestSBOMWorkflowCancellation:
    """Tests that cancellation between steps terminates the workflow early."""

    async def test_cancellation_before_scan_publishes_cancelled_event(self, workflow_config, mock_context):
        """If cancelled before scan, WorkflowCancelledEvent is published."""
        mock_context.cancellation_token.cancel()

        workflow = SBOMWorkflow(config=workflow_config)
        with pytest.raises(_WorkflowCancelled):
            await workflow.execute(mock_context)

        published_events = [call.args[0] for call in mock_context.event_bus.publish.call_args_list]
        cancelled_events = [e for e in published_events if isinstance(e, WorkflowCancelledEvent)]
        assert len(cancelled_events) == 1
        assert cancelled_events[0].workflow_name == "sbom"

    async def test_cancellation_after_scan_skips_remaining_steps(
        self,
        workflow_config,
        mock_context,
        mock_scanner,
        mock_enricher,
        mock_scan_result,
    ):
        """If cancelled after scan, enrich/assemble/write/persist are skipped."""

        async def scan_and_cancel(*args, **kwargs):
            mock_context.cancellation_token.cancel()
            return mock_scan_result

        mock_scanner.scan = AsyncMock(side_effect=scan_and_cancel)

        workflow = SBOMWorkflow(config=workflow_config)
        with pytest.raises(_WorkflowCancelled):
            await workflow.execute(mock_context)

        # Enrich should not have been called
        mock_enricher.enrich.assert_not_awaited()

    async def test_cancellation_after_enrich_skips_assemble(
        self,
        workflow_config,
        mock_context,
        mock_enricher,
        mock_assembler,
        mock_scan_result,
    ):
        """If cancelled after enrich, assemble/write/persist are skipped."""

        async def enrich_and_cancel(*args, **kwargs):
            mock_context.cancellation_token.cancel()
            return ([], [])

        mock_enricher.enrich = AsyncMock(side_effect=enrich_and_cancel)

        workflow = SBOMWorkflow(config=workflow_config)
        with pytest.raises(_WorkflowCancelled):
            await workflow.execute(mock_context)

        # Assembler should not have been called
        mock_assembler.assemble.assert_not_called()

    async def test_cancellation_after_assemble_skips_write(
        self,
        workflow_config,
        mock_context,
        mock_assembler,
        mock_writer,
        mock_sbom_document,
    ):
        """If cancelled after assemble, write/persist are skipped."""

        def assemble_and_cancel(*args, **kwargs):
            mock_context.cancellation_token.cancel()
            return mock_sbom_document

        mock_assembler.assemble.side_effect = assemble_and_cancel

        workflow = SBOMWorkflow(config=workflow_config)
        with pytest.raises(_WorkflowCancelled):
            await workflow.execute(mock_context)

        # Writer should not have been called
        mock_writer.write.assert_not_awaited()

    async def test_cancellation_after_write_skips_persist(
        self,
        workflow_config,
        mock_context,
        mock_writer,
        mock_db_provider,
    ):
        """If cancelled after write, persist is skipped."""
        call_count = 0

        async def write_and_cancel(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # Cancel after the last write
            if call_count >= len(workflow_config.formats):
                mock_context.cancellation_token.cancel()
            result = MagicMock()
            result.output_path = Path("/tmp/output/sbom.json")
            result.format = OutputFormat.SPDX_2_3
            result.sha256 = "a" * 64
            result.file_size = 1024
            return result

        mock_writer.write = AsyncMock(side_effect=write_and_cancel)

        workflow = SBOMWorkflow(config=workflow_config)
        with pytest.raises(_WorkflowCancelled):
            await workflow.execute(mock_context)

        # DB persistence should not have been called
        mock_db_provider.get_session.assert_not_awaited()


# ─── Partial Failure Tests ───────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
class TestSBOMWorkflowPartialFailure:
    """Tests that partial write failures are handled correctly."""

    async def test_partial_failure_persists_successful_formats(
        self,
        mock_context,
        mock_writer_registry,
        mock_db_session,
    ):
        """When one format fails but others succeed, successful formats are persisted."""
        config = SBOMWorkflowConfig(
            artifact_path="/tmp/test",
            output_dir=Path("/tmp/output"),
            formats=[OutputFormat.SPDX_2_3, OutputFormat.CYCLONEDX],
            scan_session_id=42,
            snapshot_id=1,
        )

        successful_result = MagicMock()
        successful_result.output_path = Path("/tmp/output/sbom.spdx.json")
        successful_result.format = OutputFormat.SPDX_2_3
        successful_result.sha256 = "b" * 64
        successful_result.file_size = 2048

        # First writer succeeds, second raises
        success_writer = AsyncMock()
        success_writer.write = AsyncMock(return_value=successful_result)

        fail_writer = AsyncMock()
        fail_writer.write = AsyncMock(side_effect=RuntimeError("disk full"))

        def get_writer_side_effect(fmt):
            if fmt == OutputFormat.SPDX_2_3:
                return success_writer
            return fail_writer

        mock_writer_registry.get_writer.side_effect = get_writer_side_effect

        workflow = SBOMWorkflow(config=config)
        with pytest.raises(_WorkflowPartialFailure):
            await workflow.execute(mock_context)

        # Verify persistence was called (session.add was invoked for the successful result)
        mock_db_session.add.assert_called_once()

    async def test_partial_failure_publishes_failed_event_with_error_details(
        self,
        mock_context,
        mock_writer_registry,
    ):
        """Partial failure publishes WorkflowFailedEvent with format-specific error details."""
        config = SBOMWorkflowConfig(
            artifact_path="/tmp/test",
            output_dir=Path("/tmp/output"),
            formats=[OutputFormat.SPDX_2_3, OutputFormat.CYCLONEDX],
            scan_session_id=42,
            snapshot_id=1,
        )

        successful_result = MagicMock()
        successful_result.output_path = Path("/tmp/output/sbom.spdx.json")
        successful_result.format = OutputFormat.SPDX_2_3
        successful_result.sha256 = "c" * 64
        successful_result.file_size = 512

        success_writer = AsyncMock()
        success_writer.write = AsyncMock(return_value=successful_result)

        fail_writer = AsyncMock()
        fail_writer.write = AsyncMock(side_effect=RuntimeError("disk full"))

        def get_writer_side_effect(fmt):
            if fmt == OutputFormat.SPDX_2_3:
                return success_writer
            return fail_writer

        mock_writer_registry.get_writer.side_effect = get_writer_side_effect

        workflow = SBOMWorkflow(config=config)
        with pytest.raises(_WorkflowPartialFailure):
            await workflow.execute(mock_context)

        published_events = [call.args[0] for call in mock_context.event_bus.publish.call_args_list]
        failed_events = [e for e in published_events if isinstance(e, WorkflowFailedEvent)]
        assert len(failed_events) == 1
        assert "cyclonedx" in failed_events[0].error_message
        assert "disk full" in failed_events[0].error_message

    async def test_partial_failure_includes_format_in_error_details(
        self,
        mock_context,
        mock_writer_registry,
    ):
        """The error_details of WorkflowFailedEvent identifies the failing format."""
        config = SBOMWorkflowConfig(
            artifact_path="/tmp/test",
            output_dir=Path("/tmp/output"),
            formats=[OutputFormat.SPDX_3_0, OutputFormat.SPDX_2_3],
            scan_session_id=42,
            snapshot_id=1,
        )

        # SPDX 3.0 fails, SPDX 2.3 succeeds
        fail_writer = AsyncMock()
        fail_writer.write = AsyncMock(side_effect=RuntimeError("schema error"))

        successful_result = MagicMock()
        successful_result.output_path = Path("/tmp/output/sbom.spdx.json")
        successful_result.format = OutputFormat.SPDX_2_3
        successful_result.sha256 = "d" * 64
        successful_result.file_size = 256

        success_writer = AsyncMock()
        success_writer.write = AsyncMock(return_value=successful_result)

        def get_writer_side_effect(fmt):
            if fmt == OutputFormat.SPDX_3_0:
                return fail_writer
            return success_writer

        mock_writer_registry.get_writer.side_effect = get_writer_side_effect

        workflow = SBOMWorkflow(config=config)
        with pytest.raises(_WorkflowPartialFailure) as exc_info:
            await workflow.execute(mock_context)

        assert "spdx_3_0" in exc_info.value.error_details
        assert "schema error" in exc_info.value.error_details

    async def test_all_formats_fail_publishes_failed_event(
        self,
        mock_context,
        mock_writer_registry,
    ):
        """When all formats fail, WorkflowFailedEvent lists all failures."""
        config = SBOMWorkflowConfig(
            artifact_path="/tmp/test",
            output_dir=Path("/tmp/output"),
            formats=[OutputFormat.SPDX_2_3, OutputFormat.CYCLONEDX],
            scan_session_id=None,
            snapshot_id=1,
        )

        fail_writer = AsyncMock()
        fail_writer.write = AsyncMock(side_effect=RuntimeError("io error"))
        mock_writer_registry.get_writer.return_value = fail_writer

        workflow = SBOMWorkflow(config=config)
        with pytest.raises(_WorkflowPartialFailure) as exc_info:
            await workflow.execute(mock_context)

        # Both formats should appear in error details
        assert "spdx_2_3" in exc_info.value.error_details
        assert "cyclonedx" in exc_info.value.error_details


# ─── Progress Reporting Tests ────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
class TestSBOMWorkflowProgressReporting:
    """Tests that progress is reported at the correct step boundaries."""

    async def test_progress_reported_at_step_boundaries(self, workflow_config, mock_context):
        """Verify progress reported at 0%, 25%, 50%, 75%, 100%."""
        workflow = SBOMWorkflow(config=workflow_config)
        await workflow.execute(mock_context)

        # Collect all progress.report calls
        progress_calls = mock_context.progress.report.call_args_list
        percentages = [call.args[0] for call in progress_calls]

        assert 0.0 in percentages
        assert 25.0 in percentages
        assert 50.0 in percentages
        assert 75.0 in percentages
        assert 100.0 in percentages

    async def test_progress_0_percent_at_start(self, workflow_config, mock_context):
        """First progress report is 0%."""
        workflow = SBOMWorkflow(config=workflow_config)
        await workflow.execute(mock_context)

        first_call = mock_context.progress.report.call_args_list[0]
        assert first_call.args[0] == 0.0

    async def test_progress_order_is_monotonic(self, workflow_config, mock_context):
        """Progress percentages are reported in non-decreasing order."""
        workflow = SBOMWorkflow(config=workflow_config)
        await workflow.execute(mock_context)

        progress_calls = mock_context.progress.report.call_args_list
        percentages = [call.args[0] for call in progress_calls]

        for i in range(1, len(percentages)):
            assert percentages[i] >= percentages[i - 1], f"Progress went from {percentages[i - 1]} to {percentages[i]}"

    async def test_progress_100_percent_at_end(self, workflow_config, mock_context):
        """Last progress report is 100%."""
        workflow = SBOMWorkflow(config=workflow_config)
        await workflow.execute(mock_context)

        last_call = mock_context.progress.report.call_args_list[-1]
        assert last_call.args[0] == 100.0
