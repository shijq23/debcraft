"""Unit tests for ScannerRegistry."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from debcraft.domain.scanner.errors import UnsupportedArtifactTypeError
from debcraft.domain.scanner.values import ArtifactType
from debcraft.infrastructure.scanners.registry import ScannerRegistry

pytestmark = [pytest.mark.unit]


class FakeValidScanner:
    """A valid scanner with an async scan method."""

    priority = 0

    async def scan(self, artifact, context):
        """Fake scan method."""
        return None


class FakeHighPriorityScanner:
    """A valid scanner with higher priority."""

    priority = 10

    async def scan(self, artifact, context):
        """Fake scan method."""
        return None


class FakeInvalidScanner:
    """An invalid scanner without async scan method."""

    def scan(self, artifact, context):
        """Non-async scan method."""
        return None


class FakeNoScanMethod:
    """An invalid scanner with no scan method at all."""

    pass


def _make_entry_point(name: str, load_result=None, load_error=None):
    """Create a mock entry point."""
    ep = MagicMock()
    ep.name = name
    if load_error:
        ep.load.side_effect = load_error
    else:
        ep.load.return_value = load_result
    return ep


class TestScannerRegistryInit:
    """Tests for ScannerRegistry initialization."""

    def test_empty_registry(self):
        """Registry starts empty."""
        registry = ScannerRegistry()
        assert registry.registered_types == []
        assert registry.diagnostics == []

    def test_diagnostics_returns_copy(self):
        """Diagnostics property returns a copy."""
        registry = ScannerRegistry()
        diags = registry.diagnostics
        diags.append("extra")
        assert registry.diagnostics == []


class TestLoadFromEntryPoints:
    """Tests for load_from_entry_points method."""

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_loads_valid_scanner(self, mock_entry_points):
        """Valid entry point is loaded and registered."""
        scanner = FakeValidScanner()
        ep = _make_entry_point("directory", load_result=scanner)
        mock_entry_points.return_value = [ep]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        assert ArtifactType.DIRECTORY in registry.registered_types
        assert registry.get_scanner(ArtifactType.DIRECTORY) is scanner
        assert registry.diagnostics == []

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_loads_multiple_scanners(self, mock_entry_points):
        """Multiple valid entry points are all registered."""
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

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_skips_failed_load_with_diagnostic(self, mock_entry_points):
        """Failed entry point load is skipped with diagnostic recorded."""
        ep = _make_entry_point("directory", load_error=ImportError("module not found"))
        mock_entry_points.return_value = [ep]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        assert registry.registered_types == []
        assert len(registry.diagnostics) == 1
        assert "Failed to load entry point 'directory'" in registry.diagnostics[0]
        assert "module not found" in registry.diagnostics[0]

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_skips_invalid_protocol_with_diagnostic(self, mock_entry_points):
        """Scanner failing protocol validation is skipped with diagnostic."""
        scanner = FakeInvalidScanner()
        ep = _make_entry_point("directory", load_result=scanner)
        mock_entry_points.return_value = [ep]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        assert registry.registered_types == []
        assert len(registry.diagnostics) == 1
        assert "does not conform to ArtifactScanner protocol" in registry.diagnostics[0]

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_skips_no_scan_method_with_diagnostic(self, mock_entry_points):
        """Scanner with no scan method at all is skipped."""
        scanner = FakeNoScanMethod()
        ep = _make_entry_point("directory", load_result=scanner)
        mock_entry_points.return_value = [ep]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        assert registry.registered_types == []
        assert len(registry.diagnostics) == 1
        assert "does not conform to ArtifactScanner protocol" in registry.diagnostics[0]

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_skips_invalid_artifact_type_name(self, mock_entry_points):
        """Entry point with name not matching ArtifactType is skipped."""
        scanner = FakeValidScanner()
        ep = _make_entry_point("nonexistent_type", load_result=scanner)
        mock_entry_points.return_value = [ep]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        assert registry.registered_types == []
        assert len(registry.diagnostics) == 1
        assert "does not map to a valid ArtifactType" in registry.diagnostics[0]

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_continues_loading_after_failure(self, mock_entry_points):
        """Failure in one entry point doesn't stop loading others."""
        valid_scanner = FakeValidScanner()
        ep_bad = _make_entry_point("directory", load_error=ImportError("bad module"))
        ep_good = _make_entry_point("docker", load_result=valid_scanner)
        mock_entry_points.return_value = [ep_bad, ep_good]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        assert ArtifactType.DOCKER in registry.registered_types
        assert ArtifactType.DIRECTORY not in registry.registered_types
        assert len(registry.diagnostics) == 1

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_higher_priority_wins(self, mock_entry_points):
        """Scanner with higher priority replaces lower priority."""
        low_scanner = FakeValidScanner()
        high_scanner = FakeHighPriorityScanner()
        ep_low = _make_entry_point("directory", load_result=low_scanner)
        ep_high = _make_entry_point("directory", load_result=high_scanner)
        mock_entry_points.return_value = [ep_low, ep_high]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        assert registry.get_scanner(ArtifactType.DIRECTORY) is high_scanner

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_equal_priority_lexicographic_first_wins(self, mock_entry_points):
        """Equal priority → lexicographically first entry point name wins."""
        scanner_a = FakeValidScanner()
        scanner_b = FakeValidScanner()
        # Both have priority 0 (default from FakeValidScanner)
        # "directory" entry point name for both — first one loaded wins
        # To test tiebreak, use the same ArtifactType but different ep names
        # isn't possible since name must map to ArtifactType.
        # Instead, simulate two entry points for the same type with same priority:
        ep_a = _make_entry_point("directory", load_result=scanner_a)
        ep_b = _make_entry_point("directory", load_result=scanner_b)
        # First registered has name "directory", second also "directory"
        # Same name means >= in comparison, so first should win
        mock_entry_points.return_value = [ep_a, ep_b]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        # First registered wins since second has same name (not lexicographically less)
        assert registry.get_scanner(ArtifactType.DIRECTORY) is scanner_a

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_entry_points_query_failure(self, mock_entry_points):
        """Graceful handling when entry_points() itself fails."""
        mock_entry_points.side_effect = Exception("metadata unavailable")

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        assert registry.registered_types == []
        assert len(registry.diagnostics) == 1
        assert "Failed to query entry points" in registry.diagnostics[0]

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

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_non_int_priority_defaults_to_zero(self, mock_entry_points):
        """Scanner with non-int priority attribute defaults to 0."""

        class BadPriorityScanner:
            priority = "high"

            async def scan(self, artifact, context):
                return None

        scanner = BadPriorityScanner()
        ep = _make_entry_point("directory", load_result=scanner)
        mock_entry_points.return_value = [ep]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        assert registry.get_scanner(ArtifactType.DIRECTORY) is scanner


class TestGetScanner:
    """Tests for get_scanner method."""

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_returns_registered_scanner(self, mock_entry_points):
        """Returns scanner when type is registered."""
        scanner = FakeValidScanner()
        ep = _make_entry_point("directory", load_result=scanner)
        mock_entry_points.return_value = [ep]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        result = registry.get_scanner(ArtifactType.DIRECTORY)
        assert result is scanner

    def test_raises_for_unregistered_type(self):
        """Raises UnsupportedArtifactTypeError for missing type."""
        registry = ScannerRegistry()

        with pytest.raises(UnsupportedArtifactTypeError) as exc_info:
            registry.get_scanner(ArtifactType.DIRECTORY)

        assert exc_info.value.artifact_type == "directory"
        assert exc_info.value.registered == []

    @patch("debcraft.infrastructure.scanners.registry.importlib.metadata.entry_points")
    def test_raises_with_registered_list(self, mock_entry_points):
        """Error includes list of registered types."""
        scanner = FakeValidScanner()
        ep = _make_entry_point("docker", load_result=scanner)
        mock_entry_points.return_value = [ep]

        registry = ScannerRegistry()
        registry.load_from_entry_points()

        with pytest.raises(UnsupportedArtifactTypeError) as exc_info:
            registry.get_scanner(ArtifactType.DIRECTORY)

        assert "docker" in exc_info.value.registered


class TestValidateProtocol:
    """Tests for _validate_protocol method."""

    def test_valid_async_scan(self):
        """Accepts class with async scan method."""
        registry = ScannerRegistry()
        assert registry._validate_protocol(FakeValidScanner()) is True

    def test_sync_scan_rejected(self):
        """Rejects class with synchronous scan method."""
        registry = ScannerRegistry()
        assert registry._validate_protocol(FakeInvalidScanner()) is False

    def test_no_scan_method_rejected(self):
        """Rejects class with no scan method."""
        registry = ScannerRegistry()
        assert registry._validate_protocol(FakeNoScanMethod()) is False

    def test_scan_as_non_callable_rejected(self):
        """Rejects class where scan is not callable."""

        class ScanAsAttribute:
            scan = "not a method"

        registry = ScannerRegistry()
        assert registry._validate_protocol(ScanAsAttribute()) is False
