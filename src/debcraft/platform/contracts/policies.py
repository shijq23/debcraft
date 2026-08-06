"""Execution policy contract defining immutable operational behavior settings."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionPolicy:
    """Immutable execution policy controlling operational behavior.

    Controls concurrency limits, retry strategies, timeouts, and failure
    semantics for workflow execution. Frozen to guarantee immutability
    during workflow runs.

    Attributes:
        max_concurrency: Maximum number of concurrent operations.
        retry_count: Number of retry attempts for failed steps.
        retry_backoff_seconds: Initial backoff delay between retries (exponential).
        timeout_seconds: Maximum execution time before cancellation.
        fail_fast: Whether to cancel remaining steps on first failure.
    """

    max_concurrency: int = 4
    retry_count: int = 0
    retry_backoff_seconds: float = 1.0
    timeout_seconds: float = 300.0
    fail_fast: bool = True
