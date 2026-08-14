"""Kernel workflow engine and factory implementation.

Provides ``KernelWorkflowEngine`` for managing workflow lifecycle with timeout
enforcement, retry logic, cancellation support, and event publishing.
Includes ``KernelWorkflowFactory`` for creating workflow instances with injected
dependencies and a default ``LoggingProgressReporter``.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from debcraft.platform.contracts.policies import ExecutionPolicy
from debcraft.platform.contracts.resources import ResourceManager
from debcraft.platform.contracts.workflow import (
    CancellationToken,
    ProgressReporter,
    Workflow,
    WorkflowContext,
    WorkflowEngine,
    WorkflowFactory,
    WorkflowState,
    WorkflowSummary,
)
from debcraft.platform.kernel.errors import WorkflowTimeoutError
from debcraft.platform.kernel.events import (
    WorkflowCancelledEvent,
    WorkflowCompletedEvent,
    WorkflowFailedEvent,
    WorkflowStartedEvent,
)

if TYPE_CHECKING:
    from debcraft.platform.contracts.container import Container
    from debcraft.platform.contracts.events import EventBus
    from debcraft.platform.contracts.logging import LoggerFactory

_logger = logging.getLogger(__name__)


class LoggingProgressReporter(ProgressReporter):
    """Default progress reporter that logs progress at INFO level.

    Produces log entries with the format: ``[workflow_name] percentage% message``.
    """

    def __init__(self, workflow_name: str, logger: logging.Logger) -> None:
        """Initialize the progress reporter.

        Args:
            workflow_name: The name of the workflow for log context.
            logger: The stdlib logger to write progress entries to.
        """
        self._workflow_name = workflow_name
        self._logger = logger

    def report(self, percentage: float, message: str = "") -> None:
        """Report progress with percentage and optional message.

        Args:
            percentage: Progress percentage from 0.0 to 100.0.
            message: Optional human-readable progress description.
        """
        msg = f"[{self._workflow_name}] {percentage:.1f}%"
        if message:
            msg = f"{msg} {message}"
        self._logger.info(msg)


class KernelWorkflowEngine(WorkflowEngine):
    """Orchestrates workflow lifecycle with timeout, retry, and cancellation.

    Manages state transitions, publishes lifecycle domain events through the
    EventBus, enforces execution policy constraints (timeout, retry, fail-fast),
    and supports graceful shutdown via SIGINT handling.
    """

    def __init__(
        self,
        event_bus: EventBus,
        logger_factory: LoggerFactory,
        container: Container,
    ) -> None:
        """Initialize the workflow engine.

        Args:
            event_bus: Event bus for publishing workflow lifecycle events.
            logger_factory: Factory for creating scoped loggers.
            container: DI container for creating scopes and resolving services.
        """
        self._event_bus = event_bus
        self._logger_factory = logger_factory
        self._container = container
        self._active_workflows: dict[UUID, CancellationToken] = {}
        self._signal_handler_installed = False

    async def run(
        self,
        workflow: Workflow,
        policy: ExecutionPolicy | None = None,
    ) -> WorkflowSummary:
        """Execute a workflow through its full lifecycle.

        Transitions the workflow from Created to Running, executes it with
        timeout enforcement and retry logic, and transitions to the
        appropriate terminal state. Publishes lifecycle events at each
        state transition.

        Args:
            workflow: The workflow instance to execute.
            policy: Optional execution policy override. Defaults to standard
                policy if not provided.

        Returns:
            A summary of the workflow execution.
        """
        execution_policy = policy if policy is not None else ExecutionPolicy()
        workflow_id = uuid4()
        cancellation_token = CancellationToken()
        self._active_workflows[workflow_id] = cancellation_token

        self._install_signal_handler()

        start_time = datetime.now(UTC)
        logger = self._logger_factory.get_logger(f"workflow.{workflow.name}")
        error_details: str | None = None
        final_state = WorkflowState.FAILED
        scope = None

        try:
            # Transition: Created → Running
            logger.info("Workflow started", workflow_id=str(workflow_id))
            await self._event_bus.publish(
                WorkflowStartedEvent(
                    workflow_name=workflow.name,
                    workflow_id=workflow_id,
                )
            )

            # Create execution context
            scope = self._container.create_scope()
            resource_manager = scope.resolve(ResourceManager)  # type: ignore[type-abstract]
            progress_reporter = LoggingProgressReporter(
                workflow.name, logging.getLogger(f"debcraft.workflow.{workflow.name}")
            )
            context = WorkflowContext(
                scope=scope,
                cancellation_token=cancellation_token,
                progress_reporter=progress_reporter,
                resource_manager=resource_manager,
                logger=logger,
                event_bus=self._event_bus,
            )

            # Execute with timeout and retry
            await self._execute_with_policy(workflow, context, execution_policy, cancellation_token)

            # Check if cancelled during execution
            final_state = WorkflowState.CANCELLED if cancellation_token.is_cancelled else WorkflowState.COMPLETED

        except WorkflowTimeoutError as exc:
            final_state = WorkflowState.FAILED
            error_details = str(exc)
            logger.error("Workflow timed out", error=error_details)
        except asyncio.CancelledError:
            final_state = WorkflowState.CANCELLED
            cancellation_token.cancel()
            logger.info("Workflow cancelled")
        except Exception as exc:  # pylint: disable=broad-exception-caught  # Top-level workflow executor: must capture any failure
            final_state = WorkflowState.FAILED
            error_details = f"{type(exc).__name__}: {exc}"
            logger.error("Workflow failed", error=error_details)
        finally:
            self._active_workflows.pop(workflow_id, None)

            # Cleanup resources
            if scope is not None:
                try:
                    await scope.close()
                except Exception:  # pylint: disable=broad-exception-caught  # Scope cleanup: must not crash workflow finalization
                    _logger.exception("Failed to close workflow scope")

        end_time = datetime.now(UTC)
        duration = (end_time - start_time).total_seconds()

        # Publish terminal event
        await self._publish_terminal_event(final_state, workflow.name, workflow_id, duration, error_details)

        logger.info(
            "Workflow reached terminal state",
            final_state=final_state.value,
            duration_seconds=f"{duration:.3f}",
        )

        return WorkflowSummary(
            workflow_name=workflow.name,
            start_time=start_time,
            end_time=end_time,
            final_state=final_state,
            error_details=error_details,
        )

    async def cancel(self, workflow_id: UUID) -> None:
        """Request cancellation of a running workflow.

        Triggers the cancellation token associated with the given workflow ID.
        If the workflow is not currently tracked, this is a no-op.

        Args:
            workflow_id: The unique identifier of the workflow to cancel.
        """
        token = self._active_workflows.get(workflow_id)
        if token is not None:
            token.cancel()
            _logger.info("Cancellation requested for workflow %s", workflow_id)

    async def shutdown(self, timeout_seconds: float) -> None:
        """Gracefully shut down all running workflows.

        Triggers cancellation of all tracked workflows and waits for them
        to reach a terminal state within the given timeout.

        Args:
            timeout_seconds: Maximum time to wait for workflows to complete.
        """
        _logger.info(
            "Shutting down %d active workflow(s) with %.1fs timeout",
            len(self._active_workflows),
            timeout_seconds,
        )
        for workflow_id, token in list(self._active_workflows.items()):
            token.cancel()
            _logger.debug("Triggered cancellation for workflow %s", workflow_id)

        # Wait for all active workflows to drain
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        while self._active_workflows:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                _logger.warning(
                    "Shutdown timeout reached with %d workflow(s) still active",
                    len(self._active_workflows),
                )
                break
            await asyncio.sleep(min(0.1, remaining))

    async def _execute_with_policy(
        self,
        workflow: Workflow,
        context: WorkflowContext,
        policy: ExecutionPolicy,
        cancellation_token: CancellationToken,
    ) -> None:
        """Execute a workflow with timeout enforcement and retry logic.

        Args:
            workflow: The workflow to execute.
            context: The workflow execution context.
            policy: The execution policy governing behavior.
            cancellation_token: Token for cooperative cancellation.

        Raises:
            WorkflowTimeoutError: If the workflow exceeds its timeout.
            Exception: If all retry attempts are exhausted.
        """
        last_exception: Exception | None = None
        max_attempts = policy.retry_count + 1

        for attempt in range(max_attempts):
            if cancellation_token.is_cancelled:
                return

            if attempt > 0:
                # Exponential backoff: B, 2B, 4B, ...
                backoff = policy.retry_backoff_seconds * (2 ** (attempt - 1))
                _logger.info(
                    "Retrying workflow '%s' (attempt %d/%d) after %.1fs backoff",
                    workflow.name,
                    attempt + 1,
                    max_attempts,
                    backoff,
                )
                await asyncio.sleep(backoff)

                if cancellation_token.is_cancelled:
                    return

            try:
                await asyncio.wait_for(
                    workflow.execute(context),
                    timeout=policy.timeout_seconds,
                )
                return  # Success
            except TimeoutError:
                cancellation_token.cancel()
                raise WorkflowTimeoutError(workflow.name, policy.timeout_seconds) from None
            except Exception as exc:  # pylint: disable=broad-exception-caught  # Retry boundary: user workflows may raise arbitrary errors
                last_exception = exc
                if policy.fail_fast or attempt == max_attempts - 1:
                    raise
                _logger.warning(
                    "Workflow '%s' attempt %d failed: %s",
                    workflow.name,
                    attempt + 1,
                    exc,
                )

        # Should not reach here, but raise last exception if somehow we do
        if last_exception is not None:  # pragma: no cover
            raise last_exception

    async def _publish_terminal_event(
        self,
        state: WorkflowState,
        workflow_name: str,
        workflow_id: UUID,
        duration_seconds: float,
        error_details: str | None,
    ) -> None:
        """Publish the appropriate terminal lifecycle event.

        Args:
            state: The terminal state the workflow reached.
            workflow_name: The name of the workflow.
            workflow_id: The unique workflow instance ID.
            duration_seconds: Total execution duration.
            error_details: Error message if the workflow failed.
        """
        if state == WorkflowState.COMPLETED:
            await self._event_bus.publish(
                WorkflowCompletedEvent(
                    workflow_name=workflow_name,
                    workflow_id=workflow_id,
                    duration_seconds=duration_seconds,
                )
            )
        elif state == WorkflowState.FAILED:
            await self._event_bus.publish(
                WorkflowFailedEvent(
                    workflow_name=workflow_name,
                    workflow_id=workflow_id,
                    error_message=error_details or "",
                )
            )
        elif state == WorkflowState.CANCELLED:
            await self._event_bus.publish(
                WorkflowCancelledEvent(
                    workflow_name=workflow_name,
                    workflow_id=workflow_id,
                )
            )

    def _install_signal_handler(self) -> None:
        """Install SIGINT handler for graceful workflow shutdown.

        Registers a signal handler that triggers cancellation tokens for all
        active workflows. Handles platform limitations gracefully (e.g., when
        running in a non-main thread or on Windows without proper support).
        """
        if self._signal_handler_installed:
            return

        try:
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGINT, self._handle_sigint)
            self._signal_handler_installed = True
        except (NotImplementedError, RuntimeError, ValueError):
            # Not supported on this platform/thread — fail gracefully.
            _logger.debug("SIGINT handler not installed (unsupported environment)")

    def _handle_sigint(self) -> None:
        """Handle SIGINT by triggering cancellation for all active workflows."""
        _logger.info("SIGINT received, cancelling %d active workflow(s)", len(self._active_workflows))
        for token in self._active_workflows.values():
            token.cancel()


class KernelWorkflowFactory(WorkflowFactory):
    """Creates workflow instances with injected WorkflowContext dependencies.

    Resolves workflow types from the container and provides them with
    a fully configured execution context.
    """

    def __init__(self, container: Container) -> None:
        """Initialize the workflow factory.

        Args:
            container: DI container for resolving workflow dependencies.
        """
        self._container = container

    def create(self, workflow_type: type[Workflow], **kwargs: object) -> Workflow:
        """Create a workflow instance with injected dependencies.

        Resolves the workflow type from the container if possible, or
        instantiates it directly with the provided keyword arguments.

        Args:
            workflow_type: The concrete workflow class to instantiate.
            **kwargs: Additional keyword arguments passed to the constructor.

        Returns:
            A fully configured workflow instance ready for execution.
        """
        if kwargs:
            return workflow_type(**kwargs)
        return self._container.resolve(workflow_type)
