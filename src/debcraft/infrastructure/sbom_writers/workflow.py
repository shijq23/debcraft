"""SBOM Workflow implementing the complete SBOM generation pipeline.

Concrete Workflow that orchestrates scanning, enrichment, model assembly,
writing, and persistence into a complete SBOM generation pipeline. Resolves
all dependencies from the WorkflowContext's DI scope.

Publishes lifecycle events at workflow start, after each step completion,
and at workflow termination. Checks cancellation token between major steps
and reports progress at step boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from debcraft.domain.sbom.assembler import ModelAssembler
from debcraft.domain.sbom.values import OutputFormat
from debcraft.infrastructure.sbom_writers.registry import WriterRegistry
from debcraft.infrastructure.scanners.enricher import MetadataEnricher
from debcraft.infrastructure.scanners.registry import ScannerRegistry
from debcraft.platform.contracts.events import DomainEvent
from debcraft.platform.contracts.persistence import DatabaseProvider
from debcraft.platform.contracts.workflow import Workflow, WorkflowContext
from debcraft.platform.kernel.events import (
    WorkflowCancelledEvent,
    WorkflowCompletedEvent,
    WorkflowFailedEvent,
    WorkflowStartedEvent,
)

if TYPE_CHECKING:
    from debcraft.domain.sbom.values import SBOMDocument, WriterResult
    from debcraft.domain.scanner.values import ScanResult


@dataclass(frozen=True)
class WorkflowStepCompletedEvent(DomainEvent):
    """Published when a workflow step completes successfully.

    Attributes:
        event_type: Identifier for this event kind.
        workflow_name: Name of the workflow.
        workflow_id: Unique identifier for the workflow instance.
        step_name: Name of the step that completed.
        step_index: Zero-based index of the completed step.
    """

    event_type: str = "workflow.step_completed"
    workflow_name: str = ""
    workflow_id: UUID = field(default_factory=uuid4)
    step_name: str = ""
    step_index: int = 0


@dataclass
class SBOMWorkflowConfig:
    """Configuration for the SBOM workflow.

    Attributes:
        artifact_path: Path to the artifact to scan.
        output_dir: Directory where SBOM files are written.
        formats: Output formats to produce. Defaults to all three formats.
        artifact_type: Optional artifact type override for scanning.
        scan_session_id: Optional scan session ID for persistence.
        snapshot_id: Repository snapshot ID for enrichment (0 to skip).
    """

    artifact_path: str
    output_dir: Path = field(default_factory=lambda: Path("."))
    formats: list[OutputFormat] = field(default_factory=lambda: list(OutputFormat))
    artifact_type: str | None = None
    scan_session_id: int | None = None
    snapshot_id: int = 0


class SBOMWorkflow(Workflow):
    """Concrete Workflow implementing the SBOM generation pipeline.

    Resolves dependencies from the WorkflowContext's DI scope, executes
    the full pipeline (scan → enrich → assemble → write → persist), and
    handles cancellation, progress reporting, and lifecycle events.

    The workflow accepts configuration via the `config` constructor parameter
    specifying the artifact path and format selection.
    """

    def __init__(self, config: SBOMWorkflowConfig) -> None:
        """Initialize the SBOM workflow with configuration.

        Args:
            config: Workflow configuration specifying artifact and formats.
        """
        self._config = config
        self._workflow_id = uuid4()

    @property
    def name(self) -> str:
        """The workflow name.

        Returns:
            The string identifier "sbom".
        """
        return "sbom"

    async def execute(self, context: WorkflowContext) -> None:
        """Execute the full SBOM generation pipeline.

        Steps:
        1. Scan artifact (0% → 25%)
        2. Enrich packages (25% → 50%)
        3. Assemble SBOMDocument (50% → 75%)
        4. Write all requested formats (75% → 100%)
        5. Persist SBOMDocument records (100%)

        Checks CancellationToken between each major step. Publishes
        lifecycle events at start, after each step, and at termination.

        Args:
            context: The execution context providing services and utilities.
        """
        start_time = datetime.now(UTC)

        # Resolve dependencies from DI scope
        scanner_registry = context.scope.resolve(ScannerRegistry)
        enricher = context.scope.resolve(MetadataEnricher)
        assembler = context.scope.resolve(ModelAssembler)
        writer_registry = context.scope.resolve(WriterRegistry)
        db_provider = context.scope.resolve(DatabaseProvider)  # type: ignore[type-abstract]

        # Publish started event
        await context.event_bus.publish(
            WorkflowStartedEvent(
                workflow_name=self.name,
                workflow_id=self._workflow_id,
            )
        )

        # Report initial progress
        context.progress.report(0.0, "Starting SBOM generation")
        context.logger.info(
            "SBOM workflow started",
            workflow="sbom",
            artifact_path=self._config.artifact_path,
            formats=[f.value for f in self._config.formats],
        )

        try:
            # Step 1: Scan artifact
            if context.cancellation_token.is_cancelled:
                await self._handle_cancellation(context)
                return

            scan_result = await self._scan(context, scanner_registry)
            context.progress.report(25.0, "Scan complete")
            await self._publish_step_completed(context, "scan", 0)

            # Step 2: Enrich packages
            if context.cancellation_token.is_cancelled:
                await self._handle_cancellation(context)
                return

            enriched_scan_result = await self._enrich(context, enricher, scan_result)
            context.progress.report(50.0, "Enrichment complete")
            await self._publish_step_completed(context, "enrich", 1)

            # Step 3: Assemble SBOMDocument
            if context.cancellation_token.is_cancelled:
                await self._handle_cancellation(context)
                return

            sbom_document = self._assemble(context, assembler, enriched_scan_result)
            context.progress.report(75.0, "Assembly complete")
            await self._publish_step_completed(context, "assemble", 2)

            # Step 4: Write all requested formats
            if context.cancellation_token.is_cancelled:
                await self._handle_cancellation(context)
                return

            writer_results, write_failures = await self._write(context, writer_registry, sbom_document)
            await self._publish_step_completed(context, "write", 3)

            # Step 5: Persist SBOMDocument records
            if context.cancellation_token.is_cancelled:
                await self._handle_cancellation(context)
                return

            await self._persist(context, db_provider, writer_results)
            context.progress.report(100.0, "SBOM generation complete")
            await self._publish_step_completed(context, "persist", 4)

            # Handle partial write failures
            if write_failures:
                error_details = "; ".join(f"write[{fmt.value}]: {err}" for fmt, err in write_failures)
                await context.event_bus.publish(
                    WorkflowFailedEvent(
                        workflow_name=self.name,
                        workflow_id=self._workflow_id,
                        error_message=error_details,
                    )
                )
                context.logger.error(
                    "SBOM workflow completed with partial write failures",
                    workflow="sbom",
                    error_details=error_details,
                )
                raise _WorkflowPartialFailure(error_details)

            # Publish completed event
            end_time = datetime.now(UTC)
            duration = (end_time - start_time).total_seconds()
            await context.event_bus.publish(
                WorkflowCompletedEvent(
                    workflow_name=self.name,
                    workflow_id=self._workflow_id,
                    duration_seconds=duration,
                )
            )
            context.logger.info(
                "SBOM workflow completed successfully",
                workflow="sbom",
                duration_seconds=round(duration, 2),
                formats_written=len(writer_results),
            )

        except _WorkflowPartialFailure:
            # Re-raise partial failure to let the engine handle it
            raise
        except Exception as exc:
            step_name = self._get_current_step_name()
            error_details = f"{step_name}: {exc}"
            await context.event_bus.publish(
                WorkflowFailedEvent(
                    workflow_name=self.name,
                    workflow_id=self._workflow_id,
                    error_message=error_details,
                )
            )
            context.logger.error(
                "SBOM workflow failed",
                workflow="sbom",
                step=step_name,
                error=str(exc),
            )
            raise

    async def _scan(
        self,
        context: WorkflowContext,
        scanner_registry: ScannerRegistry,
    ) -> ScanResult:
        """Execute the scan step.

        Args:
            context: The workflow execution context.
            scanner_registry: Registry of available scanners.

        Returns:
            The scan result with identified packages.
        """
        from debcraft.domain.scanner.values import Artifact, ArtifactType

        context.logger.info(
            "Starting scan step",
            artifact_path=self._config.artifact_path,
        )

        # Determine artifact type
        if self._config.artifact_type:
            artifact_type = ArtifactType(self._config.artifact_type)
        else:
            artifact_type = ArtifactType.DIRECTORY

        artifact = Artifact(type=artifact_type, path=self._config.artifact_path)
        scanner = scanner_registry.get_scanner(artifact_type)
        return await scanner.scan(artifact, context)

    async def _enrich(
        self,
        context: WorkflowContext,
        enricher: MetadataEnricher,
        scan_result: ScanResult,
    ) -> ScanResult:
        """Execute the enrichment step.

        Args:
            context: The workflow execution context.
            enricher: The metadata enricher service.
            scan_result: Scan result with identified packages.

        Returns:
            Updated scan result with enriched packages.
        """
        from dataclasses import replace

        context.logger.info(
            "Starting enrichment step",
            package_count=len(scan_result.packages),
        )

        enriched_packages, diagnostics = await enricher.enrich(
            packages=scan_result.packages,
            snapshot_id=self._config.snapshot_id,
        )

        for diag in diagnostics:
            context.logger.debug("Enrichment diagnostic", diagnostic=diag)

        return replace(scan_result, enriched_packages=enriched_packages)

    def _assemble(
        self,
        context: WorkflowContext,
        assembler: ModelAssembler,
        scan_result: ScanResult,
    ) -> SBOMDocument:
        """Execute the assembly step.

        Args:
            context: The workflow execution context.
            assembler: The model assembler service.
            scan_result: Scan result with enriched packages.

        Returns:
            The assembled SBOMDocument.
        """
        context.logger.info(
            "Starting assembly step",
            enriched_package_count=len(scan_result.enriched_packages),
        )

        return assembler.assemble(
            scan_result=scan_result,
            enriched_packages=scan_result.enriched_packages,
        )

    async def _write(
        self,
        context: WorkflowContext,
        writer_registry: WriterRegistry,
        sbom_document: SBOMDocument,
    ) -> tuple[list[WriterResult], list[tuple[OutputFormat, str]]]:
        """Execute the write step for all requested formats.

        Handles partial failures: writes as many formats as possible,
        tracking both successes and failures.

        Args:
            context: The workflow execution context.
            writer_registry: Registry of available writers.
            sbom_document: The assembled SBOM document to write.

        Returns:
            Tuple of (successful_results, failures) where failures is a list
            of (format, error_message) tuples.
        """
        context.logger.info(
            "Starting write step",
            formats=[f.value for f in self._config.formats],
        )

        results: list[WriterResult] = []
        failures: list[tuple[OutputFormat, str]] = []

        for fmt in self._config.formats:
            try:
                writer = writer_registry.get_writer(fmt)
                output_path = self._config.output_dir / self._get_output_filename(fmt)
                result = await writer.write(sbom_document, output_path, context)
                results.append(result)
                context.logger.info(
                    "Format written successfully",
                    format=fmt.value,
                    output_path=str(result.output_path),
                    file_size=result.file_size,
                )
            except Exception as exc:
                failures.append((fmt, str(exc)))
                context.logger.error(
                    "Failed to write format",
                    format=fmt.value,
                    error=str(exc),
                )

        return results, failures

    async def _persist(
        self,
        context: WorkflowContext,
        db_provider: DatabaseProvider,
        writer_results: list[WriterResult],
    ) -> None:
        """Persist SBOMDocument records for each successfully written format.

        Args:
            context: The workflow execution context.
            db_provider: Database provider for session access.
            writer_results: List of successful writer results to persist.
        """
        if not writer_results or self._config.scan_session_id is None:
            context.logger.debug(
                "Skipping persistence",
                reason="no results or no scan session" if not writer_results else "no scan_session_id configured",
            )
            return

        from debcraft.infrastructure.models.scan import SBOMDocument as SBOMDocumentModel

        context.logger.info(
            "Persisting SBOM document records",
            count=len(writer_results),
        )

        session = await db_provider.get_session("metadata")
        async with session.begin():
            for result in writer_results:
                record = SBOMDocumentModel(
                    scan_session_id=self._config.scan_session_id,
                    format=result.format.value,
                    content_path=str(result.output_path),
                    sha256=result.sha256,
                )
                session.add(record)

    async def _handle_cancellation(
        self,
        context: WorkflowContext,
    ) -> None:
        """Handle workflow cancellation.

        Publishes a cancellation event and logs the cancellation.

        Args:
            context: The workflow execution context.
        """
        await context.event_bus.publish(
            WorkflowCancelledEvent(
                workflow_name=self.name,
                workflow_id=self._workflow_id,
            )
        )
        context.logger.info(
            "SBOM workflow cancelled",
            workflow="sbom",
        )
        raise _WorkflowCancelled()

    async def _publish_step_completed(
        self,
        context: WorkflowContext,
        step_name: str,
        step_index: int,
    ) -> None:
        """Publish a step-completion event.

        Args:
            context: The workflow execution context.
            step_name: Name of the step that completed.
            step_index: Zero-based index of the step.
        """
        await context.event_bus.publish(
            WorkflowStepCompletedEvent(
                workflow_name=self.name,
                workflow_id=self._workflow_id,
                step_name=step_name,
                step_index=step_index,
            )
        )

    def _get_output_filename(self, fmt: OutputFormat) -> str:
        """Generate the output filename for a given format.

        Args:
            fmt: The output format.

        Returns:
            The filename string.
        """
        filenames = {
            OutputFormat.SPDX_3_0: "sbom.spdx3.json",
            OutputFormat.SPDX_2_3: "sbom.spdx.json",
            OutputFormat.CYCLONEDX: "sbom.cdx.json",
        }
        return filenames.get(fmt, f"sbom.{fmt.value}.json")

    def _get_current_step_name(self) -> str:
        """Determine the current step name for error reporting.

        Returns:
            The name of the step that likely failed.
        """
        # This is used in the catch-all exception handler.
        # We cannot reliably determine the step from context alone,
        # so we return a generic label.
        return "workflow"


class _WorkflowCancelled(Exception):  # noqa: N818
    """Internal exception raised when workflow is cancelled."""


class _WorkflowPartialFailure(Exception):  # noqa: N818
    """Internal exception raised when write step has partial failures."""

    def __init__(self, error_details: str) -> None:
        self.error_details = error_details
        super().__init__(error_details)
