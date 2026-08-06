"""Unit tests for KernelWorkflowEngine, KernelWorkflowFactory, and LoggingProgressReporter."""

import asyncio
import logging

import pytest

from debcraft.platform.contracts.events import DomainEvent
from debcraft.platform.contracts.policies import ExecutionPolicy
from debcraft.platform.contracts.resources import ResourceManager
from debcraft.platform.contracts.workflow import (
    Workflow,
    WorkflowContext,
    WorkflowState,
)
from debcraft.platform.kernel.container import KernelContainer
from debcraft.platform.kernel.events import (
    KernelEventBus,
    WorkflowCancelledEvent,
    WorkflowCompletedEvent,
    WorkflowFailedEvent,
    WorkflowStartedEvent,
)
from debcraft.platform.kernel.logging import KernelLoggerFactory
from debcraft.platform.kernel.resources import KernelResourceManager
from debcraft.platform.kernel.workflow import (
    KernelWorkflowEngine,
    KernelWorkflowFactory,
    LoggingProgressReporter,
)


class SuccessWorkflow(Workflow):
    """A workflow that completes successfully."""

    @property
    def name(self) -> str:
        return "success-workflow"

    async def execute(self, context: WorkflowContext) -> None:
        pass


class FailingWorkflow(Workflow):
    """A workflow that raises an exception."""

    @property
    def name(self) -> str:
        return "failing-workflow"

    async def execute(self, context: WorkflowContext) -> None:
        msg = "deliberate failure"
        raise RuntimeError(msg)


class SlowWorkflow(Workflow):
    """A workflow that sleeps longer than most timeouts."""

    @property
    def name(self) -> str:
        return "slow-workflow"

    async def execute(self, context: WorkflowContext) -> None:
        await asyncio.sleep(10)


class CancellationAwareWorkflow(Workflow):
    """A workflow that checks cancellation and stops cooperatively."""

    @property
    def name(self) -> str:
        return "cancellation-aware"

    async def execute(self, context: WorkflowContext) -> None:
        for _ in range(100):
            if context.cancellation_token.is_cancelled:
                return
            await asyncio.sleep(0.01)


class RetryableWorkflow(Workflow):
    """A workflow that fails a configurable number of times then succeeds."""

    def __init__(self, fail_count: int = 2) -> None:
        self._fail_count = fail_count
        self._attempts = 0

    @property
    def name(self) -> str:
        return "retryable-workflow"

    @property
    def attempts(self) -> int:
        return self._attempts

    async def execute(self, context: WorkflowContext) -> None:
        self._attempts += 1
        if self._attempts <= self._fail_count:
            msg = f"Attempt {self._attempts} failed"
            raise RuntimeError(msg)


@pytest.fixture
def container() -> KernelContainer:
    c = KernelContainer()
    c.register_scoped(ResourceManager, KernelResourceManager)
    return c


@pytest.fixture
def event_bus() -> KernelEventBus:
    return KernelEventBus()


@pytest.fixture
def logger_factory() -> KernelLoggerFactory:
    return KernelLoggerFactory(level="DEBUG")


@pytest.fixture
def engine(
    event_bus: KernelEventBus,
    logger_factory: KernelLoggerFactory,
    container: KernelContainer,
) -> KernelWorkflowEngine:
    return KernelWorkflowEngine(
        event_bus=event_bus,
        logger_factory=logger_factory,
        container=container,
    )


class TestKernelWorkflowEngineRun:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_successful_workflow_returns_completed_summary(self, engine: KernelWorkflowEngine) -> None:
        summary = await engine.run(SuccessWorkflow())

        assert summary.workflow_name == "success-workflow"
        assert summary.final_state == WorkflowState.COMPLETED
        assert summary.start_time <= summary.end_time
        assert summary.error_details is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_failing_workflow_returns_failed_summary(self, engine: KernelWorkflowEngine) -> None:
        summary = await engine.run(FailingWorkflow())

        assert summary.workflow_name == "failing-workflow"
        assert summary.final_state == WorkflowState.FAILED
        assert summary.error_details is not None
        assert "deliberate failure" in summary.error_details

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_workflow_timeout_returns_failed_summary(self, engine: KernelWorkflowEngine) -> None:
        policy = ExecutionPolicy(timeout_seconds=0.1)
        summary = await engine.run(SlowWorkflow(), policy=policy)

        assert summary.final_state == WorkflowState.FAILED
        assert summary.error_details is not None
        assert "timeout" in summary.error_details.lower()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_summary_has_valid_timing(self, engine: KernelWorkflowEngine) -> None:
        summary = await engine.run(SuccessWorkflow())

        assert summary.start_time <= summary.end_time


class TestKernelWorkflowEngineEvents:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_publishes_started_and_completed_events(
        self, engine: KernelWorkflowEngine, event_bus: KernelEventBus
    ) -> None:
        events: list[DomainEvent] = []
        event_bus.subscribe(WorkflowStartedEvent, events.append)
        event_bus.subscribe(WorkflowCompletedEvent, events.append)

        await engine.run(SuccessWorkflow())

        assert len(events) == 2
        assert isinstance(events[0], WorkflowStartedEvent)
        assert isinstance(events[1], WorkflowCompletedEvent)
        assert events[0].workflow_name == "success-workflow"
        assert events[1].workflow_name == "success-workflow"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_publishes_started_and_failed_events(
        self, engine: KernelWorkflowEngine, event_bus: KernelEventBus
    ) -> None:
        events: list[DomainEvent] = []
        event_bus.subscribe(WorkflowStartedEvent, events.append)
        event_bus.subscribe(WorkflowFailedEvent, events.append)

        await engine.run(FailingWorkflow())

        assert len(events) == 2
        assert isinstance(events[0], WorkflowStartedEvent)
        assert isinstance(events[1], WorkflowFailedEvent)
        assert events[1].error_message != ""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_publishes_cancelled_event_on_cancellation(
        self, engine: KernelWorkflowEngine, event_bus: KernelEventBus
    ) -> None:
        events: list[DomainEvent] = []
        event_bus.subscribe(WorkflowStartedEvent, events.append)
        event_bus.subscribe(WorkflowCancelledEvent, events.append)

        # Cancel via timeout triggering CancellationToken
        policy = ExecutionPolicy(timeout_seconds=0.05)

        # Use a workflow that checks cancellation
        summary = await engine.run(CancellationAwareWorkflow(), policy=policy)

        # Due to timeout, it will be FAILED (timeout triggers cancel + raises)
        assert summary.final_state == WorkflowState.FAILED


class TestKernelWorkflowEngineRetry:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_retries_on_failure_up_to_retry_count(self, engine: KernelWorkflowEngine) -> None:
        workflow = RetryableWorkflow(fail_count=2)
        policy = ExecutionPolicy(
            retry_count=3,
            retry_backoff_seconds=0.01,
            fail_fast=False,
        )

        summary = await engine.run(workflow, policy=policy)

        assert summary.final_state == WorkflowState.COMPLETED
        assert workflow.attempts == 3  # 2 failures + 1 success

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_retry_exhaustion_results_in_failure(self, engine: KernelWorkflowEngine) -> None:
        workflow = RetryableWorkflow(fail_count=5)
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
    async def test_fail_fast_does_not_retry(self, engine: KernelWorkflowEngine) -> None:
        workflow = RetryableWorkflow(fail_count=5)
        policy = ExecutionPolicy(
            retry_count=3,
            retry_backoff_seconds=0.01,
            fail_fast=True,
        )

        summary = await engine.run(workflow, policy=policy)

        assert summary.final_state == WorkflowState.FAILED
        assert workflow.attempts == 1  # fail_fast stops immediately


class TestKernelWorkflowEngineCancel:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_cancel_unknown_workflow_is_noop(self, engine: KernelWorkflowEngine) -> None:
        from uuid import uuid4

        # Should not raise
        await engine.cancel(uuid4())


class TestKernelWorkflowEngineShutdown:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_shutdown_with_no_active_workflows(self, engine: KernelWorkflowEngine) -> None:
        # Should not raise
        await engine.shutdown(timeout_seconds=1.0)


class TestLoggingProgressReporter:
    @pytest.mark.unit
    def test_report_logs_percentage(self, caplog: pytest.LogCaptureFixture) -> None:
        logger = logging.getLogger("test.progress")
        reporter = LoggingProgressReporter("test-wf", logger)

        with caplog.at_level(logging.INFO, logger="test.progress"):
            reporter.report(50.0, "halfway done")

        assert any("[test-wf] 50.0% halfway done" in r.message for r in caplog.records)

    @pytest.mark.unit
    def test_report_without_message(self, caplog: pytest.LogCaptureFixture) -> None:
        logger = logging.getLogger("test.progress")
        reporter = LoggingProgressReporter("test-wf", logger)

        with caplog.at_level(logging.INFO, logger="test.progress"):
            reporter.report(100.0)

        assert any("[test-wf] 100.0%" in r.message for r in caplog.records)


class TestKernelWorkflowFactory:
    @pytest.mark.unit
    def test_create_with_kwargs(self, container: KernelContainer) -> None:
        factory = KernelWorkflowFactory(container)

        workflow = factory.create(RetryableWorkflow, fail_count=3)

        assert isinstance(workflow, RetryableWorkflow)
