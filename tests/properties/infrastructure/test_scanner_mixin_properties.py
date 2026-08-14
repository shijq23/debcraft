"""Property-based tests for ScannerMixin.

**Validates: Requirements 1.1, 1.3, 5.4**

# Feature: pylint-cleanup, Property 1: Cancellation check raises for cancelled tokens

For any valid artifact path string and any step description string, when the
mixin's cancellation-check method is called with a WorkflowContext whose
cancellation_token.is_cancelled is True, the method SHALL raise a ScannerError
whose message contains both the artifact path and the step description.
Conversely, when is_cancelled is False, the method SHALL return without raising.

# Feature: pylint-cleanup, Property 2: Progress delegation preserves arguments

For any percentage value in the range [0.0, 100.0] and any descriptive message
string of at most 256 characters, the mixin's progress-report method SHALL
invoke context.progress.report with exactly those same percentage and message
values, unmodified.

# Feature: pylint-refactoring, Property 3: Package-Iteration Cancellation Correctness

For any package list of length M and any cancellation position N (0 ≤ N < M),
the _iterate_packages_with_cancellation method SHALL return a ScanResult
containing exactly the first N packages, plus a diagnostic message stating
that N of M packages were processed before cancellation.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.domain.scanner.errors import ScannerError
from debcraft.domain.scanner.values import IdentifiedPackage
from debcraft.infrastructure.scanners._mixin import ScannerMixin
from debcraft.platform.contracts.workflow import CancellationToken, WorkflowContext


def _make_context(*, cancelled: bool) -> WorkflowContext:
    """Create a mock WorkflowContext with the given cancellation state."""
    token = CancellationToken()
    if cancelled:
        token.cancel()

    scope = MagicMock()
    progress = MagicMock()
    resource_manager = MagicMock()
    logger = MagicMock()
    event_bus = MagicMock()

    return WorkflowContext(
        scope=scope,
        cancellation_token=token,
        progress_reporter=progress,
        resource_manager=resource_manager,
        logger=logger,
        event_bus=event_bus,
    )


@pytest.mark.property
@pytest.mark.unit
class TestProperty1CancellationCheckRaisesForCancelledTokens:
    """Property 1: Cancellation check raises for cancelled tokens.

    For any artifact_path and step, _check_cancellation raises ScannerError
    with both values in the message when cancelled, and returns normally
    when not cancelled.
    """

    @given(
        artifact_path=st.text(),
        step=st.text(),
        is_cancelled=st.booleans(),
    )
    def test_cancellation_check_behavior(self, artifact_path: str, step: str, is_cancelled: bool) -> None:
        """**Validates: Requirements 1.1**.

        When is_cancelled is True, ScannerError is raised with artifact_path
        and step in the message. When is_cancelled is False, no exception
        is raised.
        """
        mixin = ScannerMixin()
        context = _make_context(cancelled=is_cancelled)

        if is_cancelled:
            with pytest.raises(ScannerError) as exc_info:
                mixin._check_cancellation(context, artifact_path, step)

            message = str(exc_info.value)
            assert artifact_path in message, (
                f"Expected artifact_path '{artifact_path}' in error message, got: {message}"
            )
            assert step in message, f"Expected step '{step}' in error message, got: {message}"
        else:
            # Should not raise any exception
            mixin._check_cancellation(context, artifact_path, step)


# Feature: pylint-cleanup, Property 2: Progress delegation preserves arguments


@pytest.mark.property
@pytest.mark.unit
class TestProperty2ProgressDelegationPreservesArguments:
    """Property 2: Progress delegation preserves arguments.

    For any percentage in [0.0, 100.0] and any message of at most 256
    characters, _report_progress SHALL invoke context.progress.report
    with exactly those same percentage and message values, unmodified.
    """

    @given(
        percentage=st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False),
        message=st.text(max_size=256),
    )
    def test_progress_report_delegates_with_identical_arguments(self, percentage: float, message: str) -> None:
        """**Validates: Requirements 1.3**.

        context.progress.report is called exactly once with the identical
        percentage and message arguments passed to _report_progress.
        """
        mixin = ScannerMixin()
        context = _make_context(cancelled=False)

        mixin._report_progress(context, percentage, message)

        context.progress.report.assert_called_once_with(percentage, message)


# Strategies for Property 3

# Strategy to generate valid IdentifiedPackage instances
_package_strategy = st.builds(
    IdentifiedPackage,
    name=st.text(min_size=1, max_size=50, alphabet=st.characters(categories=("L", "N", "P"))),
    version=st.text(min_size=1, max_size=30, alphabet=st.characters(categories=("L", "N", "P"))),
    architecture=st.sampled_from(["amd64", "arm64", "i386", "all", "armhf"]),
    status=st.sampled_from(["installed", "config-files", "half-installed", "unpacked"]),
)


class _CountingCancellationToken:
    """A cancellation token that becomes cancelled after N checks.

    This simulates a token that is not cancelled for the first N calls
    to is_cancelled, then returns True on the (N+1)th call and beyond.
    """

    def __init__(self, cancel_after: int) -> None:
        self._cancel_after = cancel_after
        self._check_count = 0

    @property
    def is_cancelled(self) -> bool:
        result = self._check_count >= self._cancel_after
        self._check_count += 1
        return result

    def cancel(self) -> None:
        self._cancel_after = 0


def _make_context_with_counting_token(cancel_after: int) -> WorkflowContext:
    """Create a mock WorkflowContext with a counting cancellation token.

    The token returns is_cancelled=False for the first `cancel_after` checks,
    then is_cancelled=True on subsequent checks.
    """
    token = _CountingCancellationToken(cancel_after=cancel_after)

    scope = MagicMock()
    progress = MagicMock()
    resource_manager = MagicMock()
    logger = MagicMock()
    event_bus = MagicMock()

    context = WorkflowContext(
        scope=scope,
        cancellation_token=token,  # type: ignore[arg-type]
        progress_reporter=progress,
        resource_manager=resource_manager,
        logger=logger,
        event_bus=event_bus,
    )

    return context


# Feature: pylint-refactoring, Property 3: Package-Iteration Cancellation Correctness


@pytest.mark.property
@pytest.mark.unit
class TestProperty3PackageIterationCancellationCorrectness:
    """Property 3: Package-Iteration Cancellation Correctness.

    For any package list of length M and any cancellation position N (0 ≤ N < M),
    the _iterate_packages_with_cancellation method SHALL return a ScanResult
    containing exactly the first N packages, plus a diagnostic message stating
    that N of M packages were processed before cancellation.
    """

    @given(data=st.data())
    def test_cancellation_at_position_n_returns_first_n_packages(self, data: st.DataObject) -> None:
        """**Validates: Requirements 5.4**.

        Generate a random package list of length M and a cancellation position N
        (0 ≤ N < M). Assert the result contains exactly the first N packages
        and a diagnostic stating "N of M" processed.
        """
        # Generate a non-empty package list (M >= 1)
        packages = data.draw(
            st.lists(_package_strategy, min_size=1, max_size=50),
            label="packages",
        )
        m = len(packages)

        # Generate a cancellation position N where 0 <= N < M
        n = data.draw(st.integers(min_value=0, max_value=m - 1), label="cancel_position")

        mixin = ScannerMixin()
        start_time = time.perf_counter()
        strategy = "test_strategy"
        artifact_path = "/test/artifact"
        diagnostics: list[str] = []

        # Create context that cancels after N checks (so the first N packages pass)
        context = _make_context_with_counting_token(cancel_after=n)

        result = mixin._iterate_packages_with_cancellation(
            packages, context, start_time, strategy, artifact_path=artifact_path, diagnostics=diagnostics
        )

        # Assert: result contains exactly the first N packages
        assert result.packages == packages[:n], f"Expected first {n} packages, got {len(result.packages)} packages"

        # Assert: diagnostic message states "N of M" processed
        expected_diagnostic = f"Scan cancelled after processing {n} of {m} packages"
        assert expected_diagnostic in result.diagnostics, (
            f"Expected diagnostic '{expected_diagnostic}' in {result.diagnostics}"
        )

        # Assert: other fields are set correctly
        assert result.strategy == strategy
        assert result.artifact_path == artifact_path
        assert result.duration_seconds >= 0.0
