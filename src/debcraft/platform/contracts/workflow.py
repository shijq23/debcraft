"""Workflow contract defining engine, lifecycle, and orchestration interfaces."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID

from debcraft.platform.contracts.container import Scope
from debcraft.platform.contracts.events import EventBus
from debcraft.platform.contracts.logging import Logger
from debcraft.platform.contracts.resources import ResourceManager


class WorkflowState(Enum):
    """Workflow lifecycle states.

    Represents the possible states in a workflow's lifecycle from
    creation through execution to a terminal state.
    """

    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class WorkflowSummary:
    """Summary report generated when a workflow reaches a terminal state.

    Captures timing information, final state, and any error details for
    completed workflow executions.

    Attributes:
        workflow_name: The name identifying the workflow that was run.
        start_time: UTC timestamp when workflow execution began.
        end_time: UTC timestamp when workflow reached terminal state.
        final_state: The terminal state the workflow reached.
        error_details: Error description if the workflow failed, None otherwise.
    """

    workflow_name: str
    start_time: datetime
    end_time: datetime
    final_state: WorkflowState
    error_details: str | None = None


class CancellationToken:
    """Cooperative cancellation mechanism.

    Provides a thread-safe, monotonic cancellation signal that workflows
    can check periodically to support graceful shutdown.
    """

    def __init__(self) -> None:
        """Initialize an uncancelled token."""
        self._cancelled: bool = False

    @property
    def is_cancelled(self) -> bool:
        """Check if cancellation has been requested.

        Returns:
            True if cancel() has been called, False otherwise.
        """
        return self._cancelled

    def cancel(self) -> None:
        """Request cancellation.

        Once called, is_cancelled will return True for all subsequent reads.
        This transition is irreversible.
        """
        self._cancelled = True


class ProgressReporter(ABC):
    """Interface for reporting workflow progress.

    Implementations may log progress, update a UI, or emit events
    depending on the execution context.
    """

    @abstractmethod
    def report(self, percentage: float, message: str = "") -> None:
        """Report progress with percentage and optional message.

        Args:
            percentage: Progress percentage from 0.0 to 100.0.
            message: Optional human-readable progress description.
        """
        ...


class WorkflowContext:
    """Context provided to workflows during execution.

    Bundles all services and utilities a workflow needs to execute,
    including scoped dependency resolution, cancellation support,
    progress reporting, resource management, logging, and event publishing.

    Attributes:
        scope: Scoped dependency injection container for this workflow.
        cancellation_token: Cooperative cancellation signal to check.
        progress: Reporter for workflow progress updates.
        resources: Manager for acquiring and cleaning up resources.
        logger: Structured logger for workflow log entries.
        event_bus: Event bus for publishing domain events.
    """

    def __init__(
        self,
        scope: Scope,
        cancellation_token: CancellationToken,
        progress_reporter: ProgressReporter,
        resource_manager: ResourceManager,
        logger: Logger,
        event_bus: EventBus,
    ) -> None:
        """Initialize workflow context with required services.

        Args:
            scope: Scoped dependency injection container.
            cancellation_token: Cooperative cancellation signal.
            progress_reporter: Reporter for progress updates.
            resource_manager: Manager for resource lifecycle.
            logger: Structured logger instance.
            event_bus: Event bus for domain events.
        """
        self.scope = scope
        self.cancellation_token = cancellation_token
        self.progress = progress_reporter
        self.resources = resource_manager
        self.logger = logger
        self.event_bus = event_bus


class Workflow(ABC):
    """Base class for workflow implementations.

    Concrete workflows implement the execute method containing the
    workflow logic. The engine manages lifecycle transitions and
    provides the execution context.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """The workflow name.

        Returns:
            A human-readable identifier for this workflow.
        """
        ...

    @abstractmethod
    async def execute(self, context: WorkflowContext) -> None:
        """Execute the workflow logic.

        Args:
            context: The execution context providing services and utilities.
        """
        ...


class WorkflowEngine(ABC):
    """Orchestrates workflow lifecycle.

    Manages state transitions, event publishing, timeout enforcement,
    retry logic, and graceful shutdown for running workflows.
    """

    @abstractmethod
    async def run(self, workflow: Workflow) -> WorkflowSummary:
        """Execute a workflow through its full lifecycle.

        Transitions the workflow from Created to Running, executes it,
        and transitions to the appropriate terminal state.

        Args:
            workflow: The workflow instance to execute.

        Returns:
            A summary of the workflow execution.
        """
        ...

    @abstractmethod
    async def cancel(self, workflow_id: UUID) -> None:
        """Request cancellation of a running workflow.

        Args:
            workflow_id: The unique identifier of the workflow to cancel.
        """
        ...

    @abstractmethod
    async def shutdown(self, timeout_seconds: float) -> None:
        """Gracefully shut down all running workflows.

        Triggers cancellation of all tracked workflows and waits for
        them to reach a terminal state within the given timeout.

        Args:
            timeout_seconds: Maximum time to wait for workflows to complete.
        """
        ...


class WorkflowFactory(ABC):
    """Creates workflow instances with dependencies.

    Resolves workflow dependencies from the container and injects
    them into new workflow instances.
    """

    @abstractmethod
    def create(self, workflow_type: type[Workflow], **kwargs: object) -> Workflow:
        """Create a workflow instance with injected dependencies.

        Args:
            workflow_type: The concrete workflow class to instantiate.
            **kwargs: Additional keyword arguments passed to the constructor.

        Returns:
            A fully configured workflow instance ready for execution.
        """
        ...
