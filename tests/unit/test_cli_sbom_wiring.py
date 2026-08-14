"""Unit tests for CLI scanner registry wiring with production readers.

Tests that `_create_scanner_registry()` wires production ISO and squashfs
reader implementations, and raises descriptive ImportError when dependencies
are missing.

Requirements: 7.1, 7.2, 7.4
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest


@pytest.mark.unit
class TestScannerRegistryWiring:
    """Tests that the scanner registry uses production reader types."""

    def test_scanner_registry_uses_production_iso_reader(self):
        """The ISO scanner is wired with PyCdlibISOReader, not a no-op stub."""
        from debcraft.cli.sbom import _create_scanner_registry
        from debcraft.domain.scanner.values import ArtifactType
        from debcraft.infrastructure.scanners.iso_reader_pycdlib import PyCdlibISOReader

        registry = _create_scanner_registry()
        iso_scanner = registry.get_scanner(ArtifactType.ISO)
        assert isinstance(iso_scanner._iso_reader, PyCdlibISOReader)

    def test_scanner_registry_uses_production_squashfs_reader(self):
        """The ISO scanner is wired with PySquashfsImageReader, not a no-op stub."""
        from debcraft.cli.sbom import _create_scanner_registry
        from debcraft.domain.scanner.values import ArtifactType
        from debcraft.infrastructure.scanners.squashfs_reader_pysquashfsimage import PySquashfsImageReader

        registry = _create_scanner_registry()
        iso_scanner = registry.get_scanner(ArtifactType.ISO)
        assert isinstance(iso_scanner._squashfs_reader, PySquashfsImageReader)

    def test_missing_pycdlib_raises_importerror(self):
        """Missing pycdlib dependency raises ImportError with descriptive message."""
        from debcraft.cli import sbom as sbom_module

        # We need to simulate import failure within _create_scanner_registry.
        # The function does a deferred import of iso_reader_pycdlib which imports pycdlib.
        # We patch sys.modules to make the iso_reader_pycdlib module raise ImportError.
        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def mock_import(name, *args, **kwargs):
            if name == "debcraft.infrastructure.scanners.iso_reader_pycdlib":
                exc = ImportError("No module named 'pycdlib'")
                exc.name = "pycdlib"
                raise exc
            return original_import(name, *args, **kwargs)

        # Remove cached modules so the import is re-attempted
        modules_to_remove = [
            key
            for key in sys.modules
            if key.startswith("debcraft.infrastructure.scanners.iso_reader_pycdlib")
            or key.startswith("debcraft.infrastructure.scanners.squashfs_reader_pysquashfsimage")
        ]
        saved_modules = {key: sys.modules.pop(key) for key in modules_to_remove}

        try:
            with patch("builtins.__import__", side_effect=mock_import), pytest.raises(ImportError, match="pycdlib"):
                sbom_module._create_scanner_registry()
        finally:
            # Restore cached modules
            sys.modules.update(saved_modules)
