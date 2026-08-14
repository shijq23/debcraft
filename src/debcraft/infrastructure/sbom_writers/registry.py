"""Plugin registry for SBOM writer discovery via entry points.

Discovers writer implementations from the 'debcraft.sbom_writers' entry point
group, validates protocol conformance at registration time, and maps each
entry point to an OutputFormat. Mirrors the ScannerRegistry pattern with
the same error handling and protocol validation approach.
"""

from __future__ import annotations

import importlib.metadata
import inspect
from typing import TYPE_CHECKING

from debcraft.domain.sbom.errors import UnsupportedFormatError
from debcraft.domain.sbom.values import OutputFormat

if TYPE_CHECKING:
    from debcraft.domain.sbom.ports import SBOMWriter


class WriterRegistry:
    """Discovers and manages SBOM writer implementations.

    Loads writers from the 'debcraft.sbom_writers' entry point group.
    Validates protocol conformance (async write method) at registration time.
    Maps entry point names to OutputFormat enum values.
    """

    def __init__(self) -> None:
        """Initialize an empty writer registry."""
        self._writers: dict[OutputFormat, SBOMWriter] = {}
        self._diagnostics: list[str] = []

    @property
    def diagnostics(self) -> list[str]:
        """Warnings generated during writer loading."""
        return list(self._diagnostics)

    @property
    def registered_formats(self) -> list[OutputFormat]:
        """Currently registered output formats."""
        return list(self._writers.keys())

    def load_from_entry_points(self) -> None:
        """Discover and register writers from entry points.

        Algorithm:
        1. Query importlib.metadata for 'debcraft.sbom_writers' group
        2. For each entry point:
           a. Attempt to load the entry point
           b. Validate it has an async 'write' method (protocol conformance)
           c. Map entry point name to OutputFormat enum value
           d. Register, using last loaded for duplicate formats
        3. Record diagnostics for failures without stopping
        """
        try:
            entry_points = importlib.metadata.entry_points(group="debcraft.sbom_writers")
        except Exception as exc:  # pylint: disable=broad-exception-caught  # Plugin loader: entry point discovery can fail unpredictably
            self._diagnostics.append(f"Failed to query entry points for 'debcraft.sbom_writers': {exc}")
            return

        for ep in entry_points:
            self._load_entry_point(ep)

    def get_writer(self, format: OutputFormat) -> SBOMWriter:  # noqa: A002
        """Get the registered writer for an output format.

        Args:
            format: The output format to look up.

        Returns:
            The writer implementation for the given format.

        Raises:
            UnsupportedFormatError: If no writer is registered for the format.
        """
        writer = self._writers.get(format)
        if writer is None:
            registered = [f.value for f in self._writers]
            raise UnsupportedFormatError(
                format_name=format.value,
                registered=registered,
            )
        return writer

    def _load_entry_point(self, ep: importlib.metadata.EntryPoint) -> None:
        """Load and register a single entry point.

        Args:
            ep: The entry point to load.
        """
        # Step a: Map entry point name to OutputFormat
        try:
            output_format = OutputFormat(ep.name)
        except ValueError:
            self._diagnostics.append(f"Entry point '{ep.name}' does not map to a valid OutputFormat enum value")
            return

        # Step b: Attempt to load
        try:
            loaded = ep.load()
        except Exception as exc:  # pylint: disable=broad-exception-caught  # Plugin loader: arbitrary plugin code may raise anything
            self._diagnostics.append(f"Failed to load entry point '{ep.name}': {exc}")
            return

        # Step c: Instantiate the writer class
        try:
            instance = loaded()
        except Exception as exc:  # pylint: disable=broad-exception-caught  # Plugin loader: plugin constructor may raise anything
            self._diagnostics.append(f"Failed to instantiate writer from entry point '{ep.name}': {exc}")
            return

        # Step d: Validate protocol conformance (async write method)
        if not self._validate_protocol(instance):
            self._diagnostics.append(
                f"Entry point '{ep.name}' does not conform to SBOMWriter protocol: missing async 'write' method"
            )
            return

        # Step e: Register, logging warning for duplicate formats
        if output_format in self._writers:
            self._diagnostics.append(
                f"Duplicate writer for format '{output_format.value}': overriding with entry point '{ep.name}'"
            )

        self._writers[output_format] = instance

    def _validate_protocol(self, writer: object) -> bool:
        """Validate an instance conforms to the SBOMWriter protocol.

        Checks for the presence of an async 'write' method.

        Args:
            writer: The loaded writer instance to validate.

        Returns:
            True if the object has an async 'write' method, False otherwise.
        """
        write_method = getattr(writer, "write", None)
        if write_method is None:
            return False
        return inspect.iscoroutinefunction(write_method)
