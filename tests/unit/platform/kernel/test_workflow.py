"""Unit tests for KernelWorkflowEngine and KernelWorkflowFactory.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.11, 7.7, 7.8, 7.9
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from debcraft.platform.contracts.events import DomainEvent
from debcraft.platform.contracts.policies import ExecutionPolicy
from debcraft.platform.contracts.workflow import (
    CancellationToken,
    Workflow,
    WorkflowContext,
    WorkflowState,
    WorkflowSummary,
)
from debcraft.platform.kernel.events import (
    KernelEventBus,
    WorkflowCancelledEvent,
    WorkflowCompletedEvent,
    WorkflowFailedEvent,
    WorkflowStartedEvent,
)
from debcraft.platform.kernel.workflow import (
    KernelWorkflowEngine,
    KernelWorkflowFactory,
    LoggingProgressReporter,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class SuccessWorkflow(Workflow):
    """A workflow that always succeeds."""

    @property
    def name(self) -> str:
        return "success-workflow"

    async def execute(self, context: WorkflowContext) -> None:
        pass


class FailingWorkflow(Workflow):
    """A workflow that always raises an error."""

    @property
    def name(self) -> str:
        return "failing-workflow"

    async def execute(self, context: WorkflowContext) -> None:
        msg = "something went wrong"
        raise RuntimeError(msg)


class SlowWorkflow(Workflow):
    """A workflow that takes longer than a short timeout."""

    @property
    def name(self) -> str:
        return "slow-workflow"

    async def execute(self, context: WorkflowContext) -> None:
        await asyncio.sleep(10)


class CooperativeCancelWorkflow(Workflow):
    """A workflow that checks cancellation token and stops."""

    @property
    def name(self) -> str:
        return "cooperative-cancel-workflow"

    async def execute(self, context: WorkflowContext) -> None:
        for _ in range(100):
            if context.cancellation_token.is_cancelled:
                return
            await asyncio.sleep(0.01)


class ProgressWorkflow(Workflow):
    """A workflow that reports progress."""

    @property
    def name(self) -> str:
        return "progress-workflow"

    async def execute(self, context: WorkflowContext) -> None:
        context.progress.report(0.0, "Starting")
        context.progress.report(50.0, "Halfway")
        context.progress.report(100.0, "Done")


class RetryableWorkflow(Workflow):
    """A workflow that fails a configurable number of times then succeeds."""

    def __init__(self, fail_count: int = 2) -> None:
        self._fail_count = fail_count
        self._attempt = 0

    @property
    def name(self) -> str:
        return "retryable-workflow"

    @property
    def attempts(self) -> int:
        return self._attempt

    async def execute(self, context: WorkflowContext) -> None:
        self._attempt += 1
        if self._attempt <= self._fail_count:
            msg = f"attempt {self._attempt} failed"
            raise RuntimeError(msg)


class AlwaysFailWorkflow(Workflow):
    """A workflow that always fails — for testing retry exhaustion."""

    def __init__(self) -> None:
        self._attempt = 0

    @property
    def name(self) -> str:
        return "always-fail-workflow"

    @property
    def attempts(self) -> int:
        return self._attempt

    async def execute(self, context: WorkflowContext) -> None:
        self._attempt += 1
        msg = f"fail on attempt {self._attempt}"
        raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_scope() -> MagicMock:
    """Create a mock scope that returns a mock ResourceManager."""
    scope = MagicMock()
    scope.resolve = MagicMock(return_value=MagicMock())
    scope.close = AsyncMock()
    return scope


def _make_mock_container() -> MagicMock:
    """Create a mock container that produces mock scopes."""
    container = MagicMock()
    container.create_scope = MagicMock(return_value=_make_mock_scope())
    return container


def _make_mock_logger_factory() -> MagicMock:
    """Create a mock logger factory."""
    factory = MagicMock()
    logger = MagicMock()
    factory.get_logger = MagicMock(return_value=logger)
    return factory


@pytest.fixture
def event_bus() -> KernelEventBus:
    """Provide a real KernelEventBus for event tracking."""
    return KernelEventBus()


@pytest.fixture
def engine(event_bus: KernelEventBus) -> KernelWorkflowEngine:
    """Create a KernelWorkflowEngine with mock dependencies."""
    return KernelWorkflowEngine(
        event_bus=event_bus,
        logger_factory=_make_mock_logger_factory(),
        container=_make_mock_container(),
    )


# ---------------------------------------------------------------------------
# Lifecycle Transitions (Req 3.1, 3.2, 3.3)
# ---------------------------------------------------------------------------


class TestWorkflowLifecycleTransitions:
    """Test workflow lifecycle transitions (Created → Running → Completed)."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_successful_workflow_reaches_completed(self, engine: KernelWorkflowEngine) -> None:
        summary = await engine.run(SuccessWorkflow())
        assert summary.final_state == WorkflowState.COMPLETED

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_successful_workflow_has_no_error_details(self, engine: KernelWorkflowEngine) -> None:
        summary = await engine.run(SuccessWorkflow())
        assert summary.error_details is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_successful_workflow_has_timing(self, engine: KernelWorkflowEngine) -> None:
        summary = await engine.run(SuccessWorkflow())
        assert summary.start_time <= summary.end_time

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_successful_workflow_has_correct_name(self, engine: KernelWorkflowEngine) -> None:
        summary = await engine.run(SuccessWorkflow())
        assert summary.workflow_name == "success-workflow"


# ---------------------------------------------------------------------------
# Failed Workflow Transitions (Req 3.4)
# ---------------------------------------------------------------------------


class TestFailedWorkflowTransitions:
    """Test failed workflow transitions and error recording."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_failed_workflow_reaches_failed_state(self, engine: KernelWorkflowEngine) -> None:
        summary = await engine.run(FailingWorkflow())
        assert summary.final_state == WorkflowState.FAILED

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_failed_workflow_records_error_details(self, engine: KernelWorkflowEngine) -> None:
        summary = await engine.run(FailingWorkflow())
        assert summary.error_details is not None
        assert "RuntimeError" in summary.error_details
        assert "something went wrong" in summary.error_details

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_failed_workflow_has_timing(self, engine: KernelWorkflowEngine) -> None:
        summary = await engine.run(FailingWorkflow())
        assert summary.start_time <= summary.end_time


# ---------------------------------------------------------------------------
# CancellationToken (Req 3.5, 3.6)
# ---------------------------------------------------------------------------


class TestCancellationTokenCooperativeCancellation:
    """Test cancellation token cooperative cancellation."""

    @pytest.mark.unit
    def test_token_initially_not_cancelled(self) -> None:
        token = CancellationToken()
        assert token.is_cancelled is False

    @pytest.mark.unit
    def test_token_cancelled_after_cancel(self) -> None:
        token = CancellationToken()
        token.cancel()
        assert token.is_cancelled is True

    @pytest.mark.unit
    def test_token_cancel_is_idempotent(self) -> None:
        token = CancellationToken()
        token.cancel()
        token.cancel()
        assert token.is_cancelled is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_cooperative_cancel_reaches_cancelled_state(self, event_bus: KernelEventBus) -> None:
        engine = KernelWorkflowEngine(
            event_bus=event_bus,
            logger_factory=_make_mock_logger_factory(),
            container=_make_mock_container(),
        )
        workflow = CooperativeCancelWorkflow()

        # Start workflow and cancel after a short delay
        async def cancel_after_delay() -> None:
            await asyncio.sleep(0.05)
            for token in engine._active_workflows.values():
                token.cancel()

        task = asyncio.create_task(cancel_after_delay())
        summary = await engine.run(workflow)
        await task

        assert summary.final_state == WorkflowState.CANCELLED


# ---------------------------------------------------------------------------
# Progress Reporter (Req 3.7)
# ---------------------------------------------------------------------------


class TestProgressReporter:
    """Test progress reporter receives percentage and message."""

    @pytest.mark.unit
    def test_logging_progress_reporter_logs_percentage(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        logger = logging.getLogger("test.progress")
        reporter = LoggingProgressReporter("test-wf", logger)

        with caplog.at_level(logging.INFO, logger="test.progress"):
            reporter.report(50.0, "halfway there")

        assert any("50.0%" in record.message for record in caplog.records)
        assert any("halfway there" in record.message for record in caplog.records)

    @pytest.mark.unit
    def test_logging_progress_reporter_without_message(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        logger = logging.getLogger("test.progress.nomsg")
        reporter = LoggingProgressReporter("test-wf", logger)

        with caplog.at_level(logging.INFO, logger="test.progress.nomsg"):
            reporter.report(75.0)

        assert any("75.0%" in record.message for record in caplog.records)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_workflow_receives_progress_reporter(self, engine: KernelWorkflowEngine) -> None:
        """ProgressWorkflow uses context.progress — ensure no exception is raised."""
        summary = await engine.run(ProgressWorkflow())
        assert summary.final_state == WorkflowState.COMPLETED


# ---------------------------------------------------------------------------
# WorkflowSummary (Req 3.8)
# ---------------------------------------------------------------------------


class TestWorkflowSummaryGeneration:
    """Test WorkflowSummary generation on completion, failure, and cancellation."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_summary_on_completion(self, engine: KernelWorkflowEngine) -> None:
        summary = await engine.run(SuccessWorkflow())
        assert isinstance(summary, WorkflowSummary)
        assert summary.final_state == WorkflowState.COMPLETED
        assert summary.workflow_name == "success-workflow"
        assert summary.error_details is None
        assert summary.start_time is not None
        assert summary.end_time is not None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_summary_on_failure(self, engine: KernelWorkflowEngine) -> None:
        summary = await engine.run(FailingWorkflow())
        assert isinstance(summary, WorkflowSummary)
        assert summary.final_state == WorkflowState.FAILED
        assert summary.workflow_name == "failing-workflow"
        assert summary.error_details is not None
        assert "something went wrong" in summary.error_details

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_summary_on_cancellation(self, event_bus: KernelEventBus) -> None:
        engine = KernelWorkflowEngine(
            event_bus=event_bus,
            logger_factory=_make_mock_logger_factory(),
            container=_make_mock_container(),
        )

        async def cancel_soon() -> None:
            await asyncio.sleep(0.05)
            for token in engine._active_workflows.values():
                token.cancel()

        task = asyncio.create_task(cancel_soon())
        summary = await engine.run(CooperativeCancelWorkflow())
        await task

        assert isinstance(summary, WorkflowSummary)
        assert summary.final_state == WorkflowState.CANCELLED
        assert summary.workflow_name == "cooperative-cancel-workflow"

    @pytest.mark.unit
    def test_workflow_summary_is_frozen(self) -> None:
        from dataclasses import FrozenInstanceError
        from datetime import UTC, datetime

        summary = WorkflowSummary(
            workflow_name="test",
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC),
            final_state=WorkflowState.COMPLETED,
        )
        with pytest.raises(FrozenInstanceError):
            summary.workflow_name = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# WorkflowFactory (Req 3.9)
# ---------------------------------------------------------------------------


class TestWorkflowFactory:
    """Test WorkflowFactory creates workflows with injected dependencies."""

    @pytest.mark.unit
    def test_factory_creates_workflow_from_container(self) -> None:
        container = MagicMock()
        expected_workflow = SuccessWorkflow()
        container.resolve = MagicMock(return_value=expected_workflow)

        factory = KernelWorkflowFactory(container)
        result = factory.create(SuccessWorkflow)

        container.resolve.assert_called_once_with(SuccessWorkflow)
        assert result is expected_workflow

    @pytest.mark.unit
    def test_factory_creates_workflow_with_kwargs(self) -> None:
        container = MagicMock()
        factory = KernelWorkflowFactory(container)

        result = factory.create(RetryableWorkflow, fail_count=3)

        assert isinstance(result, RetryableWorkflow)
        assert result._fail_count == 3
        # Should not have resolved from container since kwargs provided
        container.resolve.assert_not_called()


# ---------------------------------------------------------------------------
# Timeout Enforcement (Req 7.8)
# ---------------------------------------------------------------------------


class TestTimeoutEnforcement:
    """Test timeout enforcement triggers cancellation."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_timeout_triggers_failed_state(self, engine: KernelWorkflowEngine) -> None:
        policy = ExecutionPolicy(timeout_seconds=0.1)
        summary = await engine.run(SlowWorkflow(), policy=policy)
        assert summary.final_state == WorkflowState.FAILED

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_timeout_records_timeout_error(self, engine: KernelWorkflowEngine) -> None:
        policy = ExecutionPolicy(timeout_seconds=0.1)
        summary = await engine.run(SlowWorkflow(), policy=policy)
        assert summary.error_details is not None
        assert "timeout" in summary.error_details.lower()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_within_timeout_completes_normally(self, engine: KernelWorkflowEngine) -> None:
        policy = ExecutionPolicy(timeout_seconds=5.0)
        summary = await engine.run(SuccessWorkflow(), policy=policy)
        assert summary.final_state == WorkflowState.COMPLETED


# ---------------------------------------------------------------------------
# Retry with Exponential Backoff (Req 7.7)
# ---------------------------------------------------------------------------


class TestRetryWithExponentialBackoff:
    """Test retry with exponential backoff."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_retry_succeeds_after_transient_failures(self, engine: KernelWorkflowEngine) -> None:
        workflow = RetryableWorkflow(fail_count=2)
        policy = ExecutionPolicy(
            retry_count=3,
            retry_backoff_seconds=0.01,
            fail_fast=False,
        )
        summary = await engine.run(workflow, policy=policy)
        assert summary.final_state == WorkflowState.COMPLETED
        assert workflow.attempts == 3  # Failed 2 times, succeeded on 3rd

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_retry_exhaustion_reaches_failed_state(self, engine: KernelWorkflowEngine) -> None:
        workflow = AlwaysFailWorkflow()
        policy = ExecutionPolicy(
            retry_count=2,
            retry_backoff_seconds=0.01,
            fail_fast=False,
        )
        summary = await engine.run(workflow, policy=policy)
        assert summary.final_state == WorkflowState.FAILED
        assert workflow.attempts == 3  # initial + 2 retries

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_no_retry_when_retry_count_is_zero(self, engine: KernelWorkflowEngine) -> None:
        workflow = AlwaysFailWorkflow()
        policy = ExecutionPolicy(retry_count=0, fail_fast=False)
        summary = await engine.run(workflow, policy=policy)
        assert summary.final_state == WorkflowState.FAILED
        assert workflow.attempts == 1


# ---------------------------------------------------------------------------
# Fail-Fast (Req 7.9)
# ---------------------------------------------------------------------------


class TestFailFastCancelsRemainingSteps:
    """Test fail-fast cancels remaining steps."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_fail_fast_prevents_retries(self, engine: KernelWorkflowEngine) -> None:
        """With fail_fast=True the engine does not retry — it fails immediately."""
        workflow = AlwaysFailWorkflow()
        policy = ExecutionPolicy(
            retry_count=3,
            retry_backoff_seconds=0.01,
            fail_fast=True,
        )
        summary = await engine.run(workflow, policy=policy)
        assert summary.final_state == WorkflowState.FAILED
        # fail-fast means no retries: only one attempt
        assert workflow.attempts == 1

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_fail_fast_false_allows_retries(self, engine: KernelWorkflowEngine) -> None:
        workflow = AlwaysFailWorkflow()
        policy = ExecutionPolicy(
            retry_count=2,
            retry_backoff_seconds=0.01,
            fail_fast=False,
        )
        summary = await engine.run(workflow, policy=policy)
        assert summary.final_state == WorkflowState.FAILED
        assert workflow.attempts == 3  # initial + 2 retries


# ---------------------------------------------------------------------------
# Event Publishing (Req 3.11)
# ---------------------------------------------------------------------------


class TestWorkflowEventPublishing:
    """Test that workflow lifecycle events are published."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_completed_workflow_publishes_started_and_completed(self, event_bus: KernelEventBus) -> None:
        events: list[DomainEvent] = []
        event_bus.subscribe(WorkflowStartedEvent, events.append)
        event_bus.subscribe(WorkflowCompletedEvent, events.append)

        engine = KernelWorkflowEngine(
            event_bus=event_bus,
            logger_factory=_make_mock_logger_factory(),
            container=_make_mock_container(),
        )
        await engine.run(SuccessWorkflow())

        assert len(events) == 2
        assert isinstance(events[0], WorkflowStartedEvent)
        assert isinstance(events[1], WorkflowCompletedEvent)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_failed_workflow_publishes_started_and_failed(self, event_bus: KernelEventBus) -> None:
        events: list[DomainEvent] = []
        event_bus.subscribe(WorkflowStartedEvent, events.append)
        event_bus.subscribe(WorkflowFailedEvent, events.append)

        engine = KernelWorkflowEngine(
            event_bus=event_bus,
            logger_factory=_make_mock_logger_factory(),
            container=_make_mock_container(),
        )
        await engine.run(FailingWorkflow())

        assert len(events) == 2
        assert isinstance(events[0], WorkflowStartedEvent)
        assert isinstance(events[1], WorkflowFailedEvent)
        assert events[1].error_message != ""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_cancelled_workflow_publishes_started_and_cancelled(self, event_bus: KernelEventBus) -> None:
        events: list[DomainEvent] = []
        event_bus.subscribe(WorkflowStartedEvent, events.append)
        event_bus.subscribe(WorkflowCancelledEvent, events.append)

        engine = KernelWorkflowEngine(
            event_bus=event_bus,
            logger_factory=_make_mock_logger_factory(),
            container=_make_mock_container(),
        )

        async def cancel_soon() -> None:
            await asyncio.sleep(0.05)
            for token in engine._active_workflows.values():
                token.cancel()

        task = asyncio.create_task(cancel_soon())
        await engine.run(CooperativeCancelWorkflow())
        await task

        assert len(events) == 2
        assert isinstance(events[0], WorkflowStartedEvent)
        assert isinstance(events[1], WorkflowCancelledEvent)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_started_event_has_workflow_name(self, event_bus: KernelEventBus) -> None:
        events: list[WorkflowStartedEvent] = []
        event_bus.subscribe(WorkflowStartedEvent, events.append)

        engine = KernelWorkflowEngine(
            event_bus=event_bus,
            logger_factory=_make_mock_logger_factory(),
            container=_make_mock_container(),
        )
        await engine.run(SuccessWorkflow())

        assert events[0].workflow_name == "success-workflow"
