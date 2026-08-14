"""Unit tests for WriterRegistry."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from debcraft.domain.sbom.errors import UnsupportedFormatError
from debcraft.domain.sbom.values import OutputFormat
from debcraft.infrastructure.sbom_writers.registry import WriterRegistry

pytestmark = [pytest.mark.unit]


class FakeValidWriter:
    """A valid writer with an async write method."""

    async def write(self, document, output_path, context):
        """Fake write method."""
        return None


class FakeInvalidWriter:
    """An invalid writer with a synchronous write method."""

    def write(self, document, output_path, context):
        """Non-async write method."""
        return None


class FakeNoWriteMethod:
    """An invalid writer with no write method at all."""

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


class TestWriterRegistryInit:
    """Tests for WriterRegistry initialization."""

    def test_empty_registry(self):
        """Registry starts empty."""
        registry = WriterRegistry()
        assert registry.registered_formats == []
        assert registry.diagnostics == []

    def test_diagnostics_returns_copy(self):
        """Diagnostics property returns a copy."""
        registry = WriterRegistry()
        diags = registry.diagnostics
        diags.append("extra")
        assert registry.diagnostics == []


class TestLoadFromEntryPoints:
    """Tests for load_from_entry_points method."""

    @patch("debcraft.infrastructure.sbom_writers.registry.importlib.metadata.entry_points")
    def test_loads_valid_writer(self, mock_entry_points):
        """Valid entry point is loaded and registered."""
        ep = _make_entry_point("spdx_3_0", load_result=FakeValidWriter)
        mock_entry_points.return_value = [ep]

        registry = WriterRegistry()
        registry.load_from_entry_points()

        assert OutputFormat.SPDX_3_0 in registry.registered_formats
        assert isinstance(registry.get_writer(OutputFormat.SPDX_3_0), FakeValidWriter)
        assert registry.diagnostics == []

    @patch("debcraft.infrastructure.sbom_writers.registry.importlib.metadata.entry_points")
    def test_loads_multiple_writers(self, mock_entry_points):
        """Multiple valid entry points are all registered."""
        ep1 = _make_entry_point("spdx_3_0", load_result=FakeValidWriter)
        ep2 = _make_entry_point("spdx_2_3", load_result=FakeValidWriter)
        ep3 = _make_entry_point("cyclonedx", load_result=FakeValidWriter)
        mock_entry_points.return_value = [ep1, ep2, ep3]

        registry = WriterRegistry()
        registry.load_from_entry_points()

        assert OutputFormat.SPDX_3_0 in registry.registered_formats
        assert OutputFormat.SPDX_2_3 in registry.registered_formats
        assert OutputFormat.CYCLONEDX in registry.registered_formats

    @patch("debcraft.infrastructure.sbom_writers.registry.importlib.metadata.entry_points")
    def test_skips_failed_load_with_diagnostic(self, mock_entry_points):
        """Failed entry point load is skipped with diagnostic recorded."""
        ep = _make_entry_point("spdx_3_0", load_error=ImportError("module not found"))
        mock_entry_points.return_value = [ep]

        registry = WriterRegistry()
        registry.load_from_entry_points()

        assert registry.registered_formats == []
        assert len(registry.diagnostics) == 1
        assert "Failed to load entry point 'spdx_3_0'" in registry.diagnostics[0]
        assert "module not found" in registry.diagnostics[0]

    @patch("debcraft.infrastructure.sbom_writers.registry.importlib.metadata.entry_points")
    def test_skips_invalid_protocol_with_diagnostic(self, mock_entry_points):
        """Writer failing protocol validation is skipped with diagnostic."""
        ep = _make_entry_point("spdx_3_0", load_result=FakeInvalidWriter)
        mock_entry_points.return_value = [ep]

        registry = WriterRegistry()
        registry.load_from_entry_points()

        assert registry.registered_formats == []
        assert len(registry.diagnostics) == 1
        assert "does not conform to SBOMWriter protocol" in registry.diagnostics[0]

    @patch("debcraft.infrastructure.sbom_writers.registry.importlib.metadata.entry_points")
    def test_skips_no_write_method_with_diagnostic(self, mock_entry_points):
        """Writer with no write method at all is skipped."""
        ep = _make_entry_point("spdx_3_0", load_result=FakeNoWriteMethod)
        mock_entry_points.return_value = [ep]

        registry = WriterRegistry()
        registry.load_from_entry_points()

        assert registry.registered_formats == []
        assert len(registry.diagnostics) == 1
        assert "does not conform to SBOMWriter protocol" in registry.diagnostics[0]

    @patch("debcraft.infrastructure.sbom_writers.registry.importlib.metadata.entry_points")
    def test_skips_unrecognized_entry_point_name(self, mock_entry_points):
        """Entry point with name not matching OutputFormat is skipped."""
        ep = _make_entry_point("nonexistent_format", load_result=FakeValidWriter)
        mock_entry_points.return_value = [ep]

        registry = WriterRegistry()
        registry.load_from_entry_points()

        assert registry.registered_formats == []
        assert len(registry.diagnostics) == 1
        assert "does not map to a valid OutputFormat" in registry.diagnostics[0]

    @patch("debcraft.infrastructure.sbom_writers.registry.importlib.metadata.entry_points")
    def test_continues_loading_after_failure(self, mock_entry_points):
        """Failure in one entry point doesn't stop loading others."""
        ep_bad = _make_entry_point("spdx_3_0", load_error=ImportError("bad module"))
        ep_good = _make_entry_point("spdx_2_3", load_result=FakeValidWriter)
        mock_entry_points.return_value = [ep_bad, ep_good]

        registry = WriterRegistry()
        registry.load_from_entry_points()

        assert OutputFormat.SPDX_2_3 in registry.registered_formats
        assert OutputFormat.SPDX_3_0 not in registry.registered_formats
        assert len(registry.diagnostics) == 1

    @patch("debcraft.infrastructure.sbom_writers.registry.importlib.metadata.entry_points")
    def test_duplicate_format_uses_last_loaded(self, mock_entry_points):
        """Last loaded writer wins for duplicate formats, with warning."""

        class WriterA(FakeValidWriter):
            pass

        class WriterB(FakeValidWriter):
            pass

        ep1 = _make_entry_point("spdx_3_0", load_result=WriterA)
        ep2 = _make_entry_point("spdx_3_0", load_result=WriterB)
        mock_entry_points.return_value = [ep1, ep2]

        registry = WriterRegistry()
        registry.load_from_entry_points()

        assert OutputFormat.SPDX_3_0 in registry.registered_formats
        assert isinstance(registry.get_writer(OutputFormat.SPDX_3_0), WriterB)
        assert any("Duplicate writer" in d for d in registry.diagnostics)

    @patch("debcraft.infrastructure.sbom_writers.registry.importlib.metadata.entry_points")
    def test_entry_points_query_failure(self, mock_entry_points):
        """Graceful handling when entry_points() itself fails."""
        mock_entry_points.side_effect = Exception("metadata unavailable")

        registry = WriterRegistry()
        registry.load_from_entry_points()

        assert registry.registered_formats == []
        assert len(registry.diagnostics) == 1
        assert "Failed to query entry points" in registry.diagnostics[0]

    @patch("debcraft.infrastructure.sbom_writers.registry.importlib.metadata.entry_points")
    def test_instantiation_failure_skipped_with_diagnostic(self, mock_entry_points):
        """Writer class that fails instantiation is skipped."""

        class BadWriter:
            def __init__(self):
                raise RuntimeError("init failed")

        ep = _make_entry_point("spdx_3_0", load_result=BadWriter)
        mock_entry_points.return_value = [ep]

        registry = WriterRegistry()
        registry.load_from_entry_points()

        assert registry.registered_formats == []
        assert len(registry.diagnostics) == 1
        assert "Failed to instantiate writer" in registry.diagnostics[0]


class TestGetWriter:
    """Tests for get_writer method."""

    @patch("debcraft.infrastructure.sbom_writers.registry.importlib.metadata.entry_points")
    def test_returns_registered_writer(self, mock_entry_points):
        """Returns writer when format is registered."""
        ep = _make_entry_point("spdx_3_0", load_result=FakeValidWriter)
        mock_entry_points.return_value = [ep]

        registry = WriterRegistry()
        registry.load_from_entry_points()

        result = registry.get_writer(OutputFormat.SPDX_3_0)
        assert isinstance(result, FakeValidWriter)

    def test_raises_for_unregistered_format(self):
        """Raises UnsupportedFormatError for missing format."""
        registry = WriterRegistry()

        with pytest.raises(UnsupportedFormatError) as exc_info:
            registry.get_writer(OutputFormat.SPDX_3_0)

        assert exc_info.value.format_name == "spdx_3_0"
        assert exc_info.value.registered == []

    @patch("debcraft.infrastructure.sbom_writers.registry.importlib.metadata.entry_points")
    def test_raises_with_registered_formats_list(self, mock_entry_points):
        """Error includes list of registered formats."""
        ep = _make_entry_point("spdx_2_3", load_result=FakeValidWriter)
        mock_entry_points.return_value = [ep]

        registry = WriterRegistry()
        registry.load_from_entry_points()

        with pytest.raises(UnsupportedFormatError) as exc_info:
            registry.get_writer(OutputFormat.SPDX_3_0)

        assert "spdx_2_3" in exc_info.value.registered


class TestValidateProtocol:
    """Tests for _validate_protocol method."""

    def test_valid_async_write(self):
        """Accepts instance with async write method."""
        registry = WriterRegistry()
        assert registry._validate_protocol(FakeValidWriter()) is True

    def test_sync_write_rejected(self):
        """Rejects instance with synchronous write method."""
        registry = WriterRegistry()
        assert registry._validate_protocol(FakeInvalidWriter()) is False

    def test_no_write_method_rejected(self):
        """Rejects instance with no write method."""
        registry = WriterRegistry()
        assert registry._validate_protocol(FakeNoWriteMethod()) is False

    def test_write_as_non_callable_rejected(self):
        """Rejects instance where write is not callable."""

        class WriteAsAttribute:
            write = "not a method"

        registry = WriterRegistry()
        assert registry._validate_protocol(WriteAsAttribute()) is False
