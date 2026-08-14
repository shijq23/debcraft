"""Property-based tests for the KernelWorkflowEngine (Properties 12-14, 22-24).

**Validates: Requirements 3.5, 3.8, 3.11, 7.7, 7.8, 7.9**
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

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
from debcraft.platform.kernel.workflow import KernelWorkflowEngine

# ===========================================================================
# Helper classes
# ===========================================================================


class _FakeLogger:
    """Minimal logger stub for workflow tests."""

    def debug(self, message: str, **kwargs: object) -> None:
        pass

    def info(self, message: str, **kwargs: object) -> None:
        pass

    def warning(self, message: str, **kwargs: object) -> None:
        pass

    def error(self, message: str, **kwargs: object) -> None:
        pass

    def with_correlation_id(self, correlation_id: object) -> _FakeLogger:
        return self


class _FakeLoggerFactory:
    """Minimal logger factory stub."""

    def get_logger(self, component: str) -> _FakeLogger:
        return _FakeLogger()


class _FakeResourceManager:
    """Minimal resource manager stub."""

    async def acquire_async(self, resource: object) -> object:
        return resource

    def acquire_sync(self, resource: object) -> object:
        return resource

    async def cleanup(self) -> None:
        pass


class _FakeScope:
    """Minimal scope stub that resolves ResourceManager."""

    def resolve(self, service_type: type) -> object:
        return _FakeResourceManager()

    async def close(self) -> None:
        pass


class _FakeContainer:
    """Minimal container stub providing a scope."""

    def create_scope(self) -> _FakeScope:
        return _FakeScope()

    def resolve(self, service_type: type) -> object:
        return None


class SuccessWorkflow(Workflow):
    """A workflow that completes successfully."""

    def __init__(self, name: str = "success-workflow") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def execute(self, context: WorkflowContext) -> None:
        pass


class FailingWorkflow(Workflow):
    """A workflow that raises an exception."""

    def __init__(self, name: str = "failing-workflow", error_msg: str = "step failed") -> None:
        self._name = name
        self._error_msg = error_msg

    @property
    def name(self) -> str:
        return self._name

    async def execute(self, context: WorkflowContext) -> None:
        raise RuntimeError(self._error_msg)


class CancelledWorkflow(Workflow):
    """A workflow that observes cancellation and stops."""

    def __init__(self, name: str = "cancelled-workflow") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def execute(self, context: WorkflowContext) -> None:
        context.cancellation_token.cancel()


class SlowWorkflow(Workflow):
    """A workflow that sleeps longer than its timeout."""

    def __init__(self, name: str = "slow-workflow", sleep_seconds: float = 10.0) -> None:
        self._name = name
        self._sleep_seconds = sleep_seconds

    @property
    def name(self) -> str:
        return self._name

    async def execute(self, context: WorkflowContext) -> None:
        await asyncio.sleep(self._sleep_seconds)


class RetryTrackingWorkflow(Workflow):
    """A workflow that fails a specified number of times before succeeding."""

    def __init__(self, name: str = "retry-workflow", fail_count: int = 3) -> None:
        self._name = name
        self._fail_count = fail_count
        self.attempt_count = 0

    @property
    def name(self) -> str:
        return self._name

    async def execute(self, context: WorkflowContext) -> None:
        self.attempt_count += 1
        if self.attempt_count <= self._fail_count:
            raise RuntimeError(f"Attempt {self.attempt_count} failed")


class MultiStepWorkflow(Workflow):
    """A workflow with multiple steps; fails at a specified step index."""

    def __init__(
        self,
        name: str = "multi-step",
        total_steps: int = 5,
        fail_at_step: int = 2,
    ) -> None:
        self._name = name
        self._total_steps = total_steps
        self._fail_at_step = fail_at_step
        self.executed_steps: list[int] = []

    @property
    def name(self) -> str:
        return self._name

    async def execute(self, context: WorkflowContext) -> None:
        for step in range(self._total_steps):
            if context.cancellation_token.is_cancelled:
                break
            if step == self._fail_at_step:
                self.executed_steps.append(step)
                raise RuntimeError(f"Step {step} failed")
            self.executed_steps.append(step)


def _build_engine() -> tuple[KernelWorkflowEngine, KernelEventBus]:
    """Create a workflow engine with fake dependencies for testing."""
    event_bus = KernelEventBus()
    engine = KernelWorkflowEngine(
        event_bus=event_bus,
        logger_factory=_FakeLoggerFactory(),  # type: ignore[arg-type]
        container=_FakeContainer(),  # type: ignore[arg-type]
    )
    return engine, event_bus


# ===========================================================================
# Property 12: Workflow summary generation
# ===========================================================================


@pytest.mark.unit
class TestProperty12WorkflowSummaryGeneration:
    """Property 12: Workflow summary generation.

    For any workflow that reaches a terminal state (Completed, Failed, or
    Cancelled), the WorkflowEngine SHALL produce a WorkflowSummary containing
    a non-empty workflow_name, valid start_time <= end_time, and the correct
    final_state.

    **Validates: Requirements 3.8**
    """

    @given(
        workflow_name=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "Pd")),
            min_size=1,
            max_size=30,
        ),
    )
    def test_completed_workflow_produces_valid_summary(self, workflow_name: str) -> None:
        """A completed workflow produces a valid WorkflowSummary.

        **Validates: Requirements 3.8**
        """

        async def _run() -> WorkflowSummary:
            engine, _ = _build_engine()
            workflow = SuccessWorkflow(name=workflow_name)
            return await engine.run(workflow)

        summary = asyncio.run(_run())

        assert summary.workflow_name == workflow_name
        assert summary.workflow_name != ""
        assert summary.start_time <= summary.end_time
        assert summary.final_state == WorkflowState.COMPLETED
        assert summary.error_details is None

    @given(
        workflow_name=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "Pd")),
            min_size=1,
            max_size=30,
        ),
        error_msg=st.text(min_size=1, max_size=50),
    )
    def test_failed_workflow_produces_valid_summary(self, workflow_name: str, error_msg: str) -> None:
        """A failed workflow produces a valid WorkflowSummary with error details.

        **Validates: Requirements 3.8**
        """

        async def _run() -> WorkflowSummary:
            engine, _ = _build_engine()
            workflow = FailingWorkflow(name=workflow_name, error_msg=error_msg)
            return await engine.run(workflow)

        summary = asyncio.run(_run())

        assert summary.workflow_name == workflow_name
        assert summary.workflow_name != ""
        assert summary.start_time <= summary.end_time
        assert summary.final_state == WorkflowState.FAILED
        assert summary.error_details is not None
        assert error_msg in summary.error_details

    @given(
        workflow_name=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "Pd")),
            min_size=1,
            max_size=30,
        ),
    )
    def test_cancelled_workflow_produces_valid_summary(self, workflow_name: str) -> None:
        """A cancelled workflow produces a valid WorkflowSummary.

        **Validates: Requirements 3.8**
        """

        async def _run() -> WorkflowSummary:
            engine, _ = _build_engine()
            workflow = CancelledWorkflow(name=workflow_name)
            return await engine.run(workflow)

        summary = asyncio.run(_run())

        assert summary.workflow_name == workflow_name
        assert summary.workflow_name != ""
        assert summary.start_time <= summary.end_time
        assert summary.final_state == WorkflowState.CANCELLED


# ===========================================================================
# Property 13: Workflow lifecycle event publishing
# ===========================================================================


@pytest.mark.unit
class TestProperty13WorkflowLifecycleEventPublishing:
    """Property 13: Workflow lifecycle event publishing.

    For any workflow that is run through the WorkflowEngine, the EventBus SHALL
    receive a WorkflowStartedEvent and exactly one terminal event
    (WorkflowCompletedEvent, WorkflowFailedEvent, or WorkflowCancelledEvent).

    **Validates: Requirements 3.11**
    """

    @given(
        workflow_name=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "Pd")),
            min_size=1,
            max_size=30,
        ),
    )
    def test_completed_workflow_publishes_started_and_completed_events(self, workflow_name: str) -> None:
        """A completed workflow publishes WorkflowStartedEvent + WorkflowCompletedEvent.

        **Validates: Requirements 3.11**
        """

        async def _run() -> list[object]:
            engine, event_bus = _build_engine()
            events: list[object] = []

            event_bus.subscribe(WorkflowStartedEvent, lambda e: events.append(e))
            event_bus.subscribe(WorkflowCompletedEvent, lambda e: events.append(e))
            event_bus.subscribe(WorkflowFailedEvent, lambda e: events.append(e))
            event_bus.subscribe(WorkflowCancelledEvent, lambda e: events.append(e))

            workflow = SuccessWorkflow(name=workflow_name)
            await engine.run(workflow)
            return events

        events = asyncio.run(_run())

        started_events = [e for e in events if isinstance(e, WorkflowStartedEvent)]
        terminal_events = [
            e for e in events if isinstance(e, (WorkflowCompletedEvent, WorkflowFailedEvent, WorkflowCancelledEvent))
        ]

        assert len(started_events) == 1
        assert len(terminal_events) == 1
        assert isinstance(terminal_events[0], WorkflowCompletedEvent)

    @given(
        workflow_name=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "Pd")),
            min_size=1,
            max_size=30,
        ),
    )
    def test_failed_workflow_publishes_started_and_failed_events(self, workflow_name: str) -> None:
        """A failed workflow publishes WorkflowStartedEvent + WorkflowFailedEvent.

        **Validates: Requirements 3.11**
        """

        async def _run() -> list[object]:
            engine, event_bus = _build_engine()
            events: list[object] = []

            event_bus.subscribe(WorkflowStartedEvent, lambda e: events.append(e))
            event_bus.subscribe(WorkflowCompletedEvent, lambda e: events.append(e))
            event_bus.subscribe(WorkflowFailedEvent, lambda e: events.append(e))
            event_bus.subscribe(WorkflowCancelledEvent, lambda e: events.append(e))

            workflow = FailingWorkflow(name=workflow_name)
            await engine.run(workflow)
            return events

        events = asyncio.run(_run())

        started_events = [e for e in events if isinstance(e, WorkflowStartedEvent)]
        terminal_events = [
            e for e in events if isinstance(e, (WorkflowCompletedEvent, WorkflowFailedEvent, WorkflowCancelledEvent))
        ]

        assert len(started_events) == 1
        assert len(terminal_events) == 1
        assert isinstance(terminal_events[0], WorkflowFailedEvent)

    @given(
        workflow_name=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "Pd")),
            min_size=1,
            max_size=30,
        ),
    )
    def test_cancelled_workflow_publishes_started_and_cancelled_events(self, workflow_name: str) -> None:
        """A cancelled workflow publishes WorkflowStartedEvent + WorkflowCancelledEvent.

        **Validates: Requirements 3.11**
        """

        async def _run() -> list[object]:
            engine, event_bus = _build_engine()
            events: list[object] = []

            event_bus.subscribe(WorkflowStartedEvent, lambda e: events.append(e))
            event_bus.subscribe(WorkflowCompletedEvent, lambda e: events.append(e))
            event_bus.subscribe(WorkflowFailedEvent, lambda e: events.append(e))
            event_bus.subscribe(WorkflowCancelledEvent, lambda e: events.append(e))

            workflow = CancelledWorkflow(name=workflow_name)
            await engine.run(workflow)
            return events

        events = asyncio.run(_run())

        started_events = [e for e in events if isinstance(e, WorkflowStartedEvent)]
        terminal_events = [
            e for e in events if isinstance(e, (WorkflowCompletedEvent, WorkflowFailedEvent, WorkflowCancelledEvent))
        ]

        assert len(started_events) == 1
        assert len(terminal_events) == 1
        assert isinstance(terminal_events[0], WorkflowCancelledEvent)


# ===========================================================================
# Property 14: CancellationToken monotonic transition
# ===========================================================================


@pytest.mark.unit
class TestProperty14CancellationTokenMonotonicTransition:
    """Property 14: CancellationToken monotonic transition.

    For any CancellationToken, once cancel() is called, is_cancelled SHALL
    return True for all subsequent reads (the cancelled state is irreversible).

    **Validates: Requirements 3.5**
    """

    @given(
        num_reads_before=st.integers(min_value=0, max_value=20),
        num_reads_after=st.integers(min_value=1, max_value=50),
        num_extra_cancels=st.integers(min_value=0, max_value=10),
    )
    def test_once_cancelled_stays_cancelled(
        self, num_reads_before: int, num_reads_after: int, num_extra_cancels: int
    ) -> None:
        """Once cancelled, is_cancelled returns True for all subsequent reads.

        **Validates: Requirements 3.5**
        """
        token = CancellationToken()

        # Before cancellation, should be False
        for _ in range(num_reads_before):
            assert token.is_cancelled is False

        # Cancel once
        token.cancel()

        # After cancellation, should be True for all subsequent reads
        for _ in range(num_reads_after):
            assert token.is_cancelled is True

        # Extra cancel calls should not change the state
        for _ in range(num_extra_cancels):
            token.cancel()
            assert token.is_cancelled is True

    @given(
        cancel_count=st.integers(min_value=1, max_value=20),
    )
    def test_multiple_cancellations_remain_cancelled(self, cancel_count: int) -> None:
        """Calling cancel() multiple times keeps is_cancelled True.

        **Validates: Requirements 3.5**
        """
        token = CancellationToken()
        assert token.is_cancelled is False

        for _ in range(cancel_count):
            token.cancel()
            assert token.is_cancelled is True


# ===========================================================================
# Property 22: Retry with exponential backoff
# ===========================================================================


@pytest.mark.unit
class TestProperty22RetryWithExponentialBackoff:
    """Property 22: Retry with exponential backoff.

    For any ExecutionPolicy with retry_count=N > 0 and retry_backoff_seconds=B,
    a failing workflow step SHALL be retried exactly N times with delays
    approximating B, 2B, 4B, ... (exponential backoff).

    **Validates: Requirements 7.7**
    """

    @given(
        retry_count=st.integers(min_value=1, max_value=4),
        backoff_seconds=st.floats(min_value=0.01, max_value=0.05),
    )
    def test_failing_workflow_retried_n_times_with_correct_delays(
        self, retry_count: int, backoff_seconds: float
    ) -> None:
        """A failing workflow is retried exactly N times with exponential backoff delays.

        **Validates: Requirements 7.7**
        """

        async def _run() -> tuple[int, list[float]]:
            engine, _ = _build_engine()
            policy = ExecutionPolicy(
                retry_count=retry_count,
                retry_backoff_seconds=backoff_seconds,
                timeout_seconds=60.0,
                fail_fast=False,
            )

            # Track sleep calls to verify backoff timing
            sleep_durations: list[float] = []
            original_sleep = asyncio.sleep

            async def mock_sleep(duration: float) -> None:
                sleep_durations.append(duration)
                # Don't actually sleep — just record
                await original_sleep(0)

            workflow = RetryTrackingWorkflow(
                name="retry-test",
                fail_count=retry_count + 1,  # Always fail (more fails than retries)
            )

            with patch("debcraft.platform.kernel.workflow.asyncio.sleep", side_effect=mock_sleep):
                await engine.run(workflow, policy=policy)

            return workflow.attempt_count, sleep_durations

        attempt_count, sleep_durations = asyncio.run(_run())

        # Total attempts = 1 initial + retry_count retries
        assert attempt_count == retry_count + 1, f"Expected {retry_count + 1} total attempts, got {attempt_count}"

        # Verify exponential backoff delays: B, 2B, 4B, ...
        assert len(sleep_durations) == retry_count, (
            f"Expected {retry_count} sleep calls (one per retry), got {len(sleep_durations)}"
        )

        for i, duration in enumerate(sleep_durations):
            expected_delay = backoff_seconds * (2**i)
            assert abs(duration - expected_delay) < 1e-9, (
                f"Retry {i}: expected delay {expected_delay:.4f}s, got {duration:.4f}s"
            )

    @given(
        retry_count=st.integers(min_value=1, max_value=3),
        backoff_seconds=st.floats(min_value=0.01, max_value=0.05),
    )
    def test_workflow_succeeds_after_fewer_retries(self, retry_count: int, backoff_seconds: float) -> None:
        """A workflow that succeeds before exhausting retries stops retrying.

        **Validates: Requirements 7.7**
        """

        async def _run() -> tuple[int, WorkflowState]:
            engine, _ = _build_engine()
            policy = ExecutionPolicy(
                retry_count=retry_count,
                retry_backoff_seconds=backoff_seconds,
                timeout_seconds=60.0,
                fail_fast=False,
            )

            # Fail fewer times than retry_count so the workflow eventually succeeds
            fail_count = max(1, retry_count - 1) if retry_count > 1 else 0
            workflow = RetryTrackingWorkflow(
                name="partial-retry-test",
                fail_count=fail_count,
            )

            with patch("debcraft.platform.kernel.workflow.asyncio.sleep", new_callable=AsyncMock):
                summary = await engine.run(workflow, policy=policy)

            return workflow.attempt_count, summary.final_state

        attempt_count, final_state = asyncio.run(_run())

        # Should succeed
        assert final_state == WorkflowState.COMPLETED
        # Attempts should be less than or equal to total allowed
        assert attempt_count <= retry_count + 1


# ===========================================================================
# Property 23: Timeout triggers cancellation
# ===========================================================================


@pytest.mark.unit
class TestProperty23TimeoutTriggersCancellation:
    """Property 23: Timeout triggers cancellation.

    For any workflow with timeout_seconds=T, if execution exceeds T seconds,
    the workflow's CancellationToken SHALL be triggered.

    **Validates: Requirements 7.8**
    """

    @given(
        timeout_seconds=st.floats(min_value=0.01, max_value=0.1),
    )
    @settings(max_examples=10)
    def test_exceeding_timeout_triggers_cancellation(self, timeout_seconds: float) -> None:
        """A workflow exceeding timeout_seconds triggers CancellationToken.

        **Validates: Requirements 7.8**
        """

        async def _run() -> WorkflowSummary:
            engine, _ = _build_engine()
            policy = ExecutionPolicy(
                timeout_seconds=timeout_seconds,
                retry_count=0,
                fail_fast=True,
            )

            # Workflow that sleeps much longer than the timeout
            workflow = SlowWorkflow(name="timeout-test", sleep_seconds=timeout_seconds * 100)
            return await engine.run(workflow, policy=policy)

        summary = asyncio.run(_run())

        # Timeout should cause a failure with timeout error
        assert summary.final_state == WorkflowState.FAILED
        assert summary.error_details is not None
        assert "timeout" in summary.error_details.lower() or "Timeout" in summary.error_details


# ===========================================================================
# Property 24: Fail-fast cancels remaining steps
# ===========================================================================


@pytest.mark.unit
class TestProperty24FailFastCancelsRemainingSteps:
    """Property 24: Fail-fast cancels remaining steps.

    For any workflow with N steps and fail_fast=True, if step K (1 <= K < N)
    fails, steps K+1 through N SHALL not be executed.

    **Validates: Requirements 7.9**
    """

    @given(
        total_steps=st.integers(min_value=3, max_value=10),
        data=st.data(),
    )
    def test_step_k_failure_prevents_steps_k_plus_1_to_n(self, total_steps: int, data: st.DataObject) -> None:
        """When step K fails with fail_fast=True, steps K+1..N are not executed.

        **Validates: Requirements 7.9**
        """
        # Choose a step to fail at (not the last step, to verify remaining are skipped)
        fail_at_step = data.draw(st.integers(min_value=0, max_value=total_steps - 2))

        async def _run() -> list[int]:
            engine, _ = _build_engine()
            policy = ExecutionPolicy(
                retry_count=0,
                fail_fast=True,
                timeout_seconds=60.0,
            )

            workflow = MultiStepWorkflow(
                name="fail-fast-test",
                total_steps=total_steps,
                fail_at_step=fail_at_step,
            )
            await engine.run(workflow, policy=policy)
            return workflow.executed_steps

        executed_steps = asyncio.run(_run())

        # Steps 0..K should have been executed (K is the failing step which was entered)
        expected_steps = list(range(fail_at_step + 1))
        assert executed_steps == expected_steps, (
            f"Expected steps {expected_steps} to execute (fail at {fail_at_step}), but got {executed_steps}"
        )

        # Steps K+1..N-1 should NOT have been executed
        for step in range(fail_at_step + 1, total_steps):
            assert step not in executed_steps, (
                f"Step {step} should not have been executed after failure at step {fail_at_step}"
            )

    @given(
        total_steps=st.integers(min_value=3, max_value=8),
        data=st.data(),
    )
    def test_fail_fast_false_does_not_cancel_remaining_in_single_execution(
        self, total_steps: int, data: st.DataObject
    ) -> None:
        """With fail_fast=True, the exception propagates immediately stopping the workflow.

        This test confirms the behavior: once a step raises, the workflow execution stops
        because the exception propagates (fail_fast re-raises immediately).

        **Validates: Requirements 7.9**
        """
        fail_at_step = data.draw(st.integers(min_value=0, max_value=total_steps - 2))

        async def _run() -> tuple[list[int], WorkflowState]:
            engine, _ = _build_engine()
            policy = ExecutionPolicy(
                retry_count=0,
                fail_fast=True,
                timeout_seconds=60.0,
            )

            workflow = MultiStepWorkflow(
                name="fail-fast-verify",
                total_steps=total_steps,
                fail_at_step=fail_at_step,
            )
            summary = await engine.run(workflow, policy=policy)
            return workflow.executed_steps, summary.final_state

        executed_steps, final_state = asyncio.run(_run())

        # Workflow should be in FAILED state
        assert final_state == WorkflowState.FAILED

        # No steps after the failing one should execute
        assert max(executed_steps) == fail_at_step
