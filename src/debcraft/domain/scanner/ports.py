"""Port interfaces (Protocols) for the artifact scanner domain.

These define the contracts that infrastructure adapters must satisfy,
allowing the domain layer to remain decoupled from concrete implementations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from debcraft.domain.scanner.values import (
        Artifact,
        ScanResult,
    )
    from debcraft.platform.contracts.workflow import WorkflowContext


class ArtifactScanner(Protocol):
    """Protocol that all scanner implementations must satisfy."""

    async def scan(self, artifact: Artifact, context: WorkflowContext) -> ScanResult:
        """Scan an artifact and return identified packages.

        Args:
            artifact: The artifact descriptor (type, path, options).
            context: Workflow context providing cancellation, progress, logging.

        Returns:
            ScanResult with identified packages and metadata.

        Raises:
            ArtifactAccessError: If the artifact path is inaccessible.
        """
        ...


class ContentsIndexPort(Protocol):
    """Queries file ownership from Contents index data (domain port)."""

    async def find_owners(self, file_paths: list[str], snapshot_id: int) -> dict[str, str]:
        """Map filesystem paths to owning package names.

        Args:
            file_paths: List of filesystem paths to look up.
            snapshot_id: RepositorySnapshot to query against.

        Returns:
            Dict mapping path -> qualified package name for found entries.
        """
        ...


class PackageLookupPort(Protocol):
    """Queries package metadata for filesystem analysis enrichment."""

    async def find_by_name(self, package_name: str, snapshot_id: int) -> tuple[str, str, str] | None:
        """Look up package version and architecture by name.

        Args:
            package_name: The package name to look up.
            snapshot_id: RepositorySnapshot to query against.

        Returns:
            Tuple of (version, architecture, status) or None if not found.
        """
        ...


class GuestfsInspector(Protocol):
    """Abstraction over libguestfs for disk image inspection."""

    def open_image(self, path: str, readonly: bool = True) -> None:
        """Open a disk image for inspection."""
        ...

    def inspect_os(self) -> list[str]:
        """Inspect the image and return root filesystem device paths."""
        ...

    def mount_readonly(self, device: str, mountpoint: str) -> None:
        """Mount a device read-only at the given mountpoint."""
        ...

    def read_file(self, path: str) -> bytes:
        """Read file contents from the mounted filesystem."""
        ...

    def ls(self, directory: str) -> list[str]:
        """List directory contents."""
        ...

    def close(self) -> None:
        """Close the image and release resources."""
        ...
