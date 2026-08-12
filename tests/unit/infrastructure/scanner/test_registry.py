"""Unit tests for ScannerRegistry with entry point discovery.

Tests cover:
- Successful loading of a valid scanner via entry point
- ImportError during load → skipped with diagnostic
- Protocol validation failure (no async scan) → skipped with diagnostic
- Requesting unsupported type → UnsupportedArtifactTypeError with registered list
- Priority selection (higher wins, lexicographic tiebreak)

Requirements: 12.3, 12.5, 12.6, 12.7
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from debcraft.domain.scanner.errors import UnsupportedArtifactTypeError
from debcraft.domain.scanner.values import ArtifactType
from debcraft.infrastructure.scanners.registry import ScannerRegistry

pytestmark = [pytest.mark.unit]


class FakeValidScanner:
    """A valid scanner conforming to ArtifactScanner protocol."""

    priority = 0

    async def scan(self, artifact, context):
        """Async scan method satisfying the protocol."""
        return None


class FakeHighPriorityScanner:
    """A valid scanner with higher priority."""

    priority = 10

    async def scan(self, artifact, context):
        """Async scan method satisfying the protocol."""
        return None


class FakeLowPriorityScanner:
    """A valid scanner with low priority."""

    priority = 1

    async def scan(self, artifact, context):
        """Async scan method satisfying the protocol."""
        return None


class FakeInvalidScanner:
    """A scanner without an async scan method (sync only)."""

    def scan(self, artifact, context):
        """Non-async scan method — does NOT satisfy the protocol."""
        return None


class FakeNoScanMethod:
    """An object with no scan method at all."""

    pass


def _make_entry_point(name: str, load_result=None, load_error=None):
    """Create a mock entry point with the given name and load behavior."""
    ep = MagicMock()
    ep.name = name
    if load_error:
        ep.load.side_effect = load_error
    else:
        ep.load.return_value = load_result
    return ep


class TestSuccessfulLoading:
    """Requirement 12.3: Valid entry points are loaded and registered."""

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_loads_valid_scanner_via_entry_point(self, mock_entry_points):
        """A valid scanner entry point is discovered, loaded, and registered."""
        scanner = FakeValidScanner()
        ep = _make_entry_point("directory", load_result=scanner)
        mock_entry_points.return_value = [ep]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        assert ArtifactType.DIRECTORY in registry.registered_types
        assert registry.get_scanner(ArtifactType.DIRECTORY) is scanner
        assert registry.diagnostics == []

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_loads_multiple_valid_scanners(self, mock_entry_points):
        """Multiple valid entry points for different types are all registered."""
        dir_scanner = FakeValidScanner()
        docker_scanner = FakeValidScanner()
        ep1 = _make_entry_point("directory", load_result=dir_scanner)
        ep2 = _make_entry_point("docker", load_result=docker_scanner)
        mock_entry_points.return_value = [ep1, ep2]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        assert ArtifactType.DIRECTORY in registry.registered_types
        assert ArtifactType.DOCKER in registry.registered_types
        assert registry.get_scanner(ArtifactType.DIRECTORY) is dir_scanner
        assert registry.get_scanner(ArtifactType.DOCKER) is docker_scanner


class TestImportErrorHandling:
    """Requirement 12.3: ImportError during load → skipped with diagnostic."""

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_import_error_skipped_with_diagnostic(self, mock_entry_points):
        """Entry point that raises ImportError is skipped and diagnostic recorded."""
        ep = _make_entry_point("directory", load_error=ImportError("module not found"))
        mock_entry_points.return_value = [ep]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        assert registry.registered_types == []
        assert len(registry.diagnostics) == 1
        assert "Failed to load entry point 'directory'" in registry.diagnostics[0]
        assert "module not found" in registry.diagnostics[0]

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_import_error_does_not_stop_other_loading(self, mock_entry_points):
        """A failing entry point does not prevent subsequent entry points from loading."""
        valid_scanner = FakeValidScanner()
        ep_bad = _make_entry_point("directory", load_error=ImportError("bad module"))
        ep_good = _make_entry_point("docker", load_result=valid_scanner)
        mock_entry_points.return_value = [ep_bad, ep_good]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        assert ArtifactType.DOCKER in registry.registered_types
        assert ArtifactType.DIRECTORY not in registry.registered_types
        assert len(registry.diagnostics) == 1


class TestProtocolValidationFailure:
    """Requirement 12.6, 12.7: Protocol validation failure → skipped with diagnostic."""

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_sync_scan_method_rejected(self, mock_entry_points):
        """Scanner with sync (non-async) scan method fails validation."""
        scanner = FakeInvalidScanner()
        ep = _make_entry_point("directory", load_result=scanner)
        mock_entry_points.return_value = [ep]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        assert registry.registered_types == []
        assert len(registry.diagnostics) == 1
        assert "does not conform to ArtifactScanner protocol" in registry.diagnostics[0]
        assert "directory" in registry.diagnostics[0]

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_no_scan_method_rejected(self, mock_entry_points):
        """Scanner with no scan method at all fails validation."""
        scanner = FakeNoScanMethod()
        ep = _make_entry_point("directory", load_result=scanner)
        mock_entry_points.return_value = [ep]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        assert registry.registered_types == []
        assert len(registry.diagnostics) == 1
        assert "does not conform to ArtifactScanner protocol" in registry.diagnostics[0]

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_protocol_failure_does_not_stop_other_loading(self, mock_entry_points):
        """Protocol failure in one entry point doesn't stop loading others."""
        invalid_scanner = FakeInvalidScanner()
        valid_scanner = FakeValidScanner()
        ep_bad = _make_entry_point("directory", load_result=invalid_scanner)
        ep_good = _make_entry_point("docker", load_result=valid_scanner)
        mock_entry_points.return_value = [ep_bad, ep_good]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        assert ArtifactType.DOCKER in registry.registered_types
        assert ArtifactType.DIRECTORY not in registry.registered_types


class TestUnsupportedTypeError:
    """Requirement 12.5: Requesting unsupported type → error with registered list."""

    def test_raises_for_unregistered_type(self):
        """Raises UnsupportedArtifactTypeError when no scanner registered."""
        registry = ScannerRegistry()

        with pytest.raises(UnsupportedArtifactTypeError) as exc_info:
            registry.get_scanner(ArtifactType.DIRECTORY)

        assert exc_info.value.artifact_type == "directory"
        assert exc_info.value.registered == []

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_error_includes_registered_types(self, mock_entry_points):
        """Error message lists all currently registered types."""
        scanner = FakeValidScanner()
        ep = _make_entry_point("docker", load_result=scanner)
        mock_entry_points.return_value = [ep]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        with pytest.raises(UnsupportedArtifactTypeError) as exc_info:
            registry.get_scanner(ArtifactType.DIRECTORY)

        assert "docker" in exc_info.value.registered
        assert exc_info.value.artifact_type == "directory"

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_error_lists_multiple_registered_types(self, mock_entry_points):
        """Error lists all registered types when multiple are available."""
        dir_scanner = FakeValidScanner()
        docker_scanner = FakeValidScanner()
        ep1 = _make_entry_point("directory", load_result=dir_scanner)
        ep2 = _make_entry_point("docker", load_result=docker_scanner)
        mock_entry_points.return_value = [ep1, ep2]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        with pytest.raises(UnsupportedArtifactTypeError) as exc_info:
            registry.get_scanner(ArtifactType.OCI)

        assert "directory" in exc_info.value.registered
        assert "docker" in exc_info.value.registered


class TestPrioritySelection:
    """Requirement 12.7: Priority selection — higher priority wins."""

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_higher_priority_wins(self, mock_entry_points):
        """Scanner with higher priority replaces lower priority for same type."""
        low_scanner = FakeLowPriorityScanner()
        high_scanner = FakeHighPriorityScanner()
        ep_low = _make_entry_point("directory", load_result=low_scanner)
        ep_high = _make_entry_point("directory", load_result=high_scanner)
        mock_entry_points.return_value = [ep_low, ep_high]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        assert registry.get_scanner(ArtifactType.DIRECTORY) is high_scanner

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_higher_priority_wins_regardless_of_order(self, mock_entry_points):
        """Higher priority wins even when loaded first."""
        low_scanner = FakeLowPriorityScanner()
        high_scanner = FakeHighPriorityScanner()
        # High priority loaded first, low priority loaded second
        ep_high = _make_entry_point("directory", load_result=high_scanner)
        ep_low = _make_entry_point("directory", load_result=low_scanner)
        mock_entry_points.return_value = [ep_high, ep_low]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        assert registry.get_scanner(ArtifactType.DIRECTORY) is high_scanner

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_equal_priority_first_registered_wins(self, mock_entry_points):
        """Equal priority → first registered (lexicographically first name) wins."""
        scanner_a = FakeValidScanner()
        scanner_b = FakeValidScanner()
        # Both have priority 0, same entry point name "directory"
        # First registered should win since second has same name (not less)
        ep_a = _make_entry_point("directory", load_result=scanner_a)
        ep_b = _make_entry_point("directory", load_result=scanner_b)
        mock_entry_points.return_value = [ep_a, ep_b]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        assert registry.get_scanner(ArtifactType.DIRECTORY) is scanner_a

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_missing_priority_defaults_to_zero(self, mock_entry_points):
        """Scanner without priority attribute defaults to 0."""

        class NoPriorityScanner:
            async def scan(self, artifact, context):
                return None

        scanner = NoPriorityScanner()
        ep = _make_entry_point("directory", load_result=scanner)
        mock_entry_points.return_value = [ep]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        assert registry.get_scanner(ArtifactType.DIRECTORY) is scanner
