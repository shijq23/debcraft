"""Platform kernel error hierarchy.

Defines the exception classes used across all platform kernel components.
All platform errors derive from PlatformError, with specialized sub-hierarchies
for container, configuration, workflow, and resource management errors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


class PlatformError(Exception):
    """Base exception for all platform kernel errors.

    All platform-specific exceptions inherit from this class, allowing
    callers to catch any platform error with a single except clause.
    """

    def __init__(self, message: str) -> None:
        """Initialize PlatformError.

        Args:
            message: Human-readable description of the error.
        """
        self.message = message
        super().__init__(message)


class ContainerError(PlatformError):
    """Base exception for dependency injection container errors."""


class ServiceNotFoundError(ContainerError):
    """Raised when resolving a service type that has no registration."""

    def __init__(self, service_type: type) -> None:
        """Initialize ServiceNotFoundError.

        Args:
            service_type: The type that was requested but not registered.
        """
        self.service_type = service_type
        super().__init__(f"No registration found for service type '{service_type.__qualname__}'")


class CircularDependencyError(ContainerError):
    """Raised when a circular dependency chain is detected during resolution."""

    def __init__(self, chain: Sequence[type]) -> None:
        """Initialize CircularDependencyError.

        Args:
            chain: The sequence of types forming the dependency cycle.
        """
        self.chain = list(chain)
        chain_str = " -> ".join(t.__qualname__ for t in self.chain)
        super().__init__(f"Circular dependency detected: {chain_str}")


class ConfigurationError(PlatformError):
    """Base exception for configuration subsystem errors."""


class ConfigurationSyntaxError(ConfigurationError):
    """Raised when a configuration file has TOML syntax errors."""

    def __init__(self, file_path: str, detail: str) -> None:
        """Initialize ConfigurationSyntaxError.

        Args:
            file_path: Path to the configuration file with the syntax error.
            detail: Description of the syntax error from the parser.
        """
        self.file_path = file_path
        self.detail = detail
        super().__init__(f"Syntax error in configuration file '{file_path}': {detail}")


class ConfigurationValidationError(ConfigurationError):
    """Raised when configuration values fail validation constraints."""

    def __init__(self, field_name: str, value: object, reason: str) -> None:
        """Initialize ConfigurationValidationError.

        Args:
            field_name: The name of the field that failed validation.
            value: The invalid value that was provided.
            reason: Explanation of why the value is invalid.
        """
        self.field_name = field_name
        self.value = value
        self.reason = reason
        super().__init__(f"Invalid configuration for '{field_name}': {reason} (got {value!r})")


class WorkflowError(PlatformError):
    """Base exception for workflow engine errors."""


class WorkflowTimeoutError(WorkflowError):
    """Raised when a workflow exceeds its configured timeout."""

    def __init__(self, workflow_name: str, timeout_seconds: float) -> None:
        """Initialize WorkflowTimeoutError.

        Args:
            workflow_name: The name of the workflow that timed out.
            timeout_seconds: The timeout duration that was exceeded.
        """
        self.workflow_name = workflow_name
        self.timeout_seconds = timeout_seconds
        super().__init__(f"Workflow '{workflow_name}' exceeded timeout of {timeout_seconds}s")


class ResourceCleanupError(PlatformError):
    """Raised when resource cleanup fails.

    This error is typically logged rather than propagated, to allow
    remaining resources to still be cleaned up.
    """

    def __init__(self, resource_name: str, cause: Exception) -> None:
        """Initialize ResourceCleanupError.

        Args:
            resource_name: Identifier of the resource that failed cleanup.
            cause: The underlying exception that caused the cleanup failure.
        """
        self.resource_name = resource_name
        self.cause = cause
        super().__init__(f"Failed to clean up resource '{resource_name}': {cause}")
