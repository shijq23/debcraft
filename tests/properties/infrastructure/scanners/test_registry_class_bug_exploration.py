"""Bug condition exploration test for scanner registry class-vs-instance bug.

**Validates: Requirements 1.1**

Property 1: Bug Condition — Entry points return classes, not instances

This test demonstrates that `load_from_entry_points()` stores the CLASS
object returned by `ep.load()` directly in the registry. When the workflow
later calls `scanner.scan(artifact, context)`, it's an unbound method call —
`artifact` is consumed as `self`, producing a TypeError.

The existing unit tests mask this bug by passing pre-built INSTANCES as the
mocked entry point load result. In production, entry points point to classes
(e.g., `debcraft.infrastructure.scanners.directory:DirectoryScanner`), so
`ep.load()` returns the class, not an instance.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.domain.scanner.values import ArtifactType
from debcraft.infrastructure.scanners.registry import ScannerRegistry

pytestmark = [pytest.mark.unit]


# --- Scanner CLASS (not instance) mimicking production entry points ---


class FakeScannerClass:
    """A scanner class with the correct async scan signature.

    In production, entry points reference classes like DirectoryScanner.
    ep.load() returns this CLASS, not an instance of it.
    """

    priority = 0

    async def scan(self, artifact, context):
        """Scan method that requires self to be a proper instance."""
        # In a real scanner, self would hold references to ports/dependencies.
        # This method should only work when called on an INSTANCE.
        return {"scanned": True, "artifact_path": artifact.path}


# --- Strategies ---

_ARTIFACT_TYPES_WITH_ENTRY_POINT_NAMES = [
    ("directory", ArtifactType.DIRECTORY),
    ("docker", ArtifactType.DOCKER),
    ("iso", ArtifactType.ISO),
]


def _make_entry_point(name: str, load_result):
    """Create a mock entry point that returns load_result from ep.load()."""
    ep = MagicMock()
    ep.name = name
    ep.load.return_value = load_result
    return ep


@pytest.mark.unit
@pytest.mark.xfail(reason="Exploration test: documents the old buggy logic (fixed via inspect.isclass guard)")
class TestRegistryClassBugExploration:
    """Demonstrate that the registry stores classes and scan() fails with TypeError.

    The bug: entry points return CLASS objects, not instances.
    The registry stores whatever ep.load() returns.
    When workflow calls scanner.scan(artifact, context), if scanner is a CLASS,
    Python interprets it as an unbound method call where artifact becomes self.
    This produces: TypeError: scan() missing 1 required positional argument: 'context'
    """

    @given(
        entry_point_idx=st.integers(min_value=0, max_value=2),
    )
    def test_class_stored_by_registry_produces_type_error_on_scan(self, entry_point_idx: int) -> None:
        """For any scanner class loaded from entry points, calling scan() fails.

        When the registry stores a CLASS (not an instance), calling
        scanner.scan(artifact, context) is equivalent to:
            ScannerClass.scan(artifact, context)
        which means artifact is treated as self, and context becomes artifact,
        leaving the real context argument missing → TypeError.

        This test ASSERTS correct behavior (scan succeeds). It is EXPECTED TO
        FAIL on the unfixed code, confirming the bug exists.

        **Validates: Requirements 1.1**
        """
        ep_name, artifact_type = _ARTIFACT_TYPES_WITH_ENTRY_POINT_NAMES[entry_point_idx]

        # Simulate production: ep.load() returns the CLASS, not an instance
        scanner_class = FakeScannerClass

        with patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points") as mock_eps:
            ep = _make_entry_point(ep_name, load_result=scanner_class)
            mock_eps.return_value = [ep]

            registry = ScannerRegistry()
            registry.load_from_entry_points()

        # The registry should have registered something for this type
        assert artifact_type in registry.registered_types

        # Get the "scanner" from registry
        scanner = registry.get_scanner(artifact_type)

        # Create mock artifact and context
        artifact = MagicMock()
        artifact.path = "/some/artifact/path"
        context = MagicMock()

        # Call scan — this SHOULD work if the registry stored an instance.
        # On the buggy code, scanner IS the class itself, so this call becomes:
        #   FakeScannerClass.scan(artifact, context)
        # which Python interprets as an unbound call where artifact=self,
        # producing: TypeError: scan() missing 1 required positional argument
        result = asyncio.get_event_loop().run_until_complete(scanner.scan(artifact, context))

        # Assert the scan produced a valid result (bound method call succeeded)
        assert result is not None
        assert result["scanned"] is True
        assert result["artifact_path"] == "/some/artifact/path"
