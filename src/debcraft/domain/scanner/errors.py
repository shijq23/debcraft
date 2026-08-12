"""Domain-specific errors for the scanner subsystem.

Defines exception classes raised during artifact scanning, including
access errors, unsupported format errors, dependency availability errors,
and artifact format validation errors.
"""

from __future__ import annotations

from debcraft.platform.kernel.errors import PlatformError


class ScannerError(PlatformError):
    """Base error for all scanner domain errors."""


class ArtifactAccessError(ScannerError):
    """Raised when the artifact path is inaccessible."""

    def __init__(self, path: str, reason: str) -> None:
        """Initialize ArtifactAccessError.

        Args:
            path: Filesystem path to the artifact that could not be accessed.
            reason: Description of why the artifact is inaccessible.
        """
        self.path = path
        self.reason = reason
        super().__init__(f"Cannot access artifact at '{path}': {reason}")


class UnsupportedArtifactTypeError(ScannerError):
    """Raised when no scanner is registered for an artifact type."""

    def __init__(self, artifact_type: str, registered: list[str]) -> None:
        """Initialize UnsupportedArtifactTypeError.

        Args:
            artifact_type: The artifact type that has no registered scanner.
            registered: List of artifact type names that are currently registered.
        """
        self.artifact_type = artifact_type
        self.registered = registered
        super().__init__(f"No scanner for artifact type '{artifact_type}'. Registered types: {registered}")


class ScannerDependencyError(ScannerError):
    """Raised when a required runtime dependency is not available."""

    def __init__(self, dependency_name: str, reason: str) -> None:
        """Initialize ScannerDependencyError.

        Args:
            dependency_name: Name of the dependency that is unavailable
                (e.g. 'guestfs', 'squashfuse').
            reason: Description of why the dependency is unavailable.
        """
        self.dependency_name = dependency_name
        self.reason = reason
        super().__init__(f"Scanner dependency '{dependency_name}' is not available: {reason}")


class ArtifactFormatError(ScannerError):
    """Raised when the artifact file does not match the expected format."""

    def __init__(self, path: str, expected_format: str, reason: str) -> None:
        """Initialize ArtifactFormatError.

        Args:
            path: Filesystem path to the artifact with the format mismatch.
            expected_format: The format that was expected (e.g. 'QCOW2', 'ISO 9660').
            reason: Description of the format validation failure
                (e.g. bad magic bytes).
        """
        self.path = path
        self.expected_format = expected_format
        self.reason = reason
        super().__init__(f"Artifact at '{path}' does not match expected format '{expected_format}': {reason}")
