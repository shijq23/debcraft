"""Integration test verifying CLI scope creates working scanner instances.

Confirms the core bug fix: scanners in the CLI path are now instances with
bound methods, not classes. The _create_di_scope() function explicitly
instantiates all 7 scanner types with no-op port adapters and registers them
via scanner_registry.register().

Requirements: 2.1 (scanner registry stores instances, not classes)
"""

from __future__ import annotations

import inspect

import pytest

from debcraft.domain.scanner.values import ArtifactType

pytestmark = [pytest.mark.integration]


class TestCliScopeScannerInstances:
    """Verify that _create_di_scope() produces a registry with working scanner instances."""

    def test_all_artifact_types_have_registered_scanners(self) -> None:
        """Every ArtifactType has a scanner registered in the CLI scope."""
        from debcraft.cli.sbom import _create_di_scope
        from debcraft.infrastructure.scanners.registry import ScannerRegistry

        scope = _create_di_scope()
        registry = scope.resolve(ScannerRegistry)

        for artifact_type in ArtifactType:
            scanner = registry.get_scanner(artifact_type)
            assert scanner is not None, f"No scanner registered for {artifact_type.value}"

    def test_scanners_are_instances_not_classes(self) -> None:
        """Scanners returned by the registry are instances, not class objects."""
        from debcraft.cli.sbom import _create_di_scope
        from debcraft.infrastructure.scanners.registry import ScannerRegistry

        scope = _create_di_scope()
        registry = scope.resolve(ScannerRegistry)

        for artifact_type in ArtifactType:
            scanner = registry.get_scanner(artifact_type)
            assert not inspect.isclass(scanner), (
                f"Scanner for {artifact_type.value} is a class, not an instance: {scanner}"
            )

    def test_scanners_have_callable_bound_scan_method(self) -> None:
        """Each scanner has a callable 'scan' method that is a bound method."""
        from debcraft.cli.sbom import _create_di_scope
        from debcraft.infrastructure.scanners.registry import ScannerRegistry

        scope = _create_di_scope()
        registry = scope.resolve(ScannerRegistry)

        for artifact_type in ArtifactType:
            scanner = registry.get_scanner(artifact_type)
            scan_method = getattr(scanner, "scan", None)

            assert scan_method is not None, f"Scanner for {artifact_type.value} has no 'scan' attribute"
            assert callable(scan_method), f"Scanner for {artifact_type.value} has a non-callable 'scan' attribute"
            # Verify it's a bound method (has __self__), not an unbound function
            assert hasattr(scan_method, "__self__"), (
                f"Scanner for {artifact_type.value} has an unbound 'scan' method — "
                "this indicates a class was stored instead of an instance"
            )
            assert scan_method.__self__ is scanner, (
                f"Scanner for {artifact_type.value} has a 'scan' method bound to a different object"
            )

    def test_scan_methods_are_coroutines(self) -> None:
        """Each scanner's scan method is an async coroutine function."""
        from debcraft.cli.sbom import _create_di_scope
        from debcraft.infrastructure.scanners.registry import ScannerRegistry

        scope = _create_di_scope()
        registry = scope.resolve(ScannerRegistry)

        for artifact_type in ArtifactType:
            scanner = registry.get_scanner(artifact_type)
            scan_method = scanner.scan
            assert inspect.iscoroutinefunction(scan_method), (
                f"Scanner for {artifact_type.value} has a non-async 'scan' method"
            )
