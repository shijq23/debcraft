"""Plugin registry for scanner discovery via entry points.

Discovers scanner implementations from the 'debcraft.scanners' entry point
group, validates protocol conformance at registration time, and maps each
entry point to an ArtifactType. Supports priority-based selection when
multiple implementations are registered for the same artifact type.
"""

from __future__ import annotations

import importlib.metadata
import inspect
from typing import TYPE_CHECKING

from debcraft.domain.scanner.errors import UnsupportedArtifactTypeError
from debcraft.domain.scanner.values import ArtifactType

if TYPE_CHECKING:
    from debcraft.domain.scanner.ports import ArtifactScanner


class ScannerRegistry:
    """Discovers and manages scanner implementations.

    Loads scanners from the 'debcraft.scanners' entry point group.
    Validates protocol conformance at registration time.
    Supports priority-based selection for multiple implementations
    of the same ArtifactType.
    """

    def __init__(self) -> None:
        """Initialize an empty scanner registry."""
        self._scanners: dict[ArtifactType, ArtifactScanner] = {}
        self._diagnostics: list[str] = []
        # Track priority and entry point name for tiebreaking
        self._priorities: dict[ArtifactType, tuple[int, str]] = {}

    @property
    def diagnostics(self) -> list[str]:
        """Warnings generated during scanner loading."""
        return list(self._diagnostics)

    @property
    def registered_types(self) -> list[ArtifactType]:
        """Currently registered artifact types."""
        return list(self._scanners.keys())

    def load_from_entry_points(self) -> None:
        """Discover and register scanners from entry points.

        Algorithm:
        1. Query importlib.metadata for 'debcraft.scanners' group
        2. For each entry point:
           a. Attempt to load the entry point
           b. Validate it has an async 'scan' method (protocol conformance)
           c. Map entry point name to ArtifactType enum value
           d. Register, respecting priority (higher wins, then lexicographic)
        3. Record diagnostics for failures without stopping
        """
        try:
            entry_points = importlib.metadata.entry_points(group="debcraft.scanners")
        except Exception as exc:
            self._diagnostics.append(f"Failed to query entry points for 'debcraft.scanners': {exc}")
            return

        for ep in entry_points:
            self._load_entry_point(ep)

    def get_scanner(self, artifact_type: ArtifactType) -> ArtifactScanner:
        """Get the registered scanner for an artifact type.

        Args:
            artifact_type: The type to look up.

        Returns:
            The scanner implementation.

        Raises:
            UnsupportedArtifactTypeError: If no scanner registered for the type.
        """
        scanner = self._scanners.get(artifact_type)
        if scanner is None:
            registered = [t.value for t in self._scanners]
            raise UnsupportedArtifactTypeError(
                artifact_type=artifact_type.value,
                registered=registered,
            )
        return scanner

    def _load_entry_point(self, ep: importlib.metadata.EntryPoint) -> None:
        """Load and register a single entry point.

        Args:
            ep: The entry point to load.
        """
        # Step a: Attempt to load
        try:
            loaded = ep.load()
        except Exception as exc:
            self._diagnostics.append(f"Failed to load entry point '{ep.name}': {exc}")
            return

        # Step b: Validate protocol conformance
        if not self._validate_protocol(loaded):
            self._diagnostics.append(
                f"Entry point '{ep.name}' does not conform to ArtifactScanner protocol: missing async 'scan' method"
            )
            return

        # Step c: Map entry point name to ArtifactType
        try:
            artifact_type = ArtifactType(ep.name)
        except ValueError:
            self._diagnostics.append(f"Entry point '{ep.name}' does not map to a valid ArtifactType enum value")
            return

        # Step d: Register with priority-based selection
        priority = getattr(loaded, "priority", 0)
        if not isinstance(priority, int):
            priority = 0

        existing = self._priorities.get(artifact_type)
        if existing is not None:
            existing_priority, existing_name = existing
            # Higher priority wins; equal priority → lexicographic first name
            if priority < existing_priority:
                return
            if priority == existing_priority and ep.name >= existing_name:
                return

        self._scanners[artifact_type] = loaded
        self._priorities[artifact_type] = (priority, ep.name)

    def _validate_protocol(self, scanner_class: object) -> bool:
        """Validate a class/instance conforms to the ArtifactScanner protocol.

        Checks for the presence of an async 'scan' method.

        Args:
            scanner_class: The loaded entry point object to validate.

        Returns:
            True if the object has an async 'scan' method, False otherwise.
        """
        scan_method = getattr(scanner_class, "scan", None)
        if scan_method is None:
            return False
        return inspect.iscoroutinefunction(scan_method)
