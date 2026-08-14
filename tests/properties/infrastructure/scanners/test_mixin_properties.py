"""Property-based tests for ScannerMixin scan-result construction.

**Validates: Requirements 5.3**

# Feature: pylint-refactoring, Property 2: ScanResult Construction Equivalence

For any valid combination of packages list, strategy string, diagnostics list,
start_time float, and artifact_path string, the `_build_success_result` mixin
method SHALL produce a ScanResult with: packages == input packages,
strategy == input strategy, diagnostics == input diagnostics,
duration_seconds >= 0, and artifact_path == input path.
"""

from __future__ import annotations

import time

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.domain.scanner.values import VALID_PACKAGE_STATUSES, IdentifiedPackage, ScanResult
from debcraft.infrastructure.scanners._mixin import ScannerMixin

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Valid package statuses to draw from
_PACKAGE_STATUSES = sorted(VALID_PACKAGE_STATUSES)

st_identified_package = st.builds(
    IdentifiedPackage,
    name=st.text(min_size=1, max_size=50),
    version=st.text(min_size=1, max_size=30),
    architecture=st.text(min_size=1, max_size=20),
    status=st.sampled_from(_PACKAGE_STATUSES),
)

st_packages = st.lists(st_identified_package, min_size=0, max_size=20)
st_strategy = st.text(min_size=1, max_size=50)
st_diagnostics = st.lists(st.text(max_size=100), min_size=0, max_size=10)
st_artifact_path = st.text(min_size=1, max_size=200)


# ---------------------------------------------------------------------------
# Property 2: ScanResult Construction Equivalence
# ---------------------------------------------------------------------------


@pytest.mark.property
@pytest.mark.unit
class TestProperty2ScanResultConstructionEquivalence:
    """Property 2: ScanResult Construction Equivalence.

    For any valid combination of packages list, strategy string, diagnostics
    list, start_time float, and artifact_path string, the `_build_success_result`
    mixin method SHALL produce a ScanResult with field values matching the inputs.

    **Validates: Requirements 5.3**
    """

    @given(
        packages=st_packages,
        strategy=st_strategy,
        diagnostics=st_diagnostics,
        artifact_path=st_artifact_path,
    )
    def test_build_success_result_field_equality(
        self,
        packages: list[IdentifiedPackage],
        strategy: str,
        diagnostics: list[str],
        artifact_path: str,
    ) -> None:
        """Validates Requirements 5.3.

        The _build_success_result method produces a ScanResult whose packages,
        strategy, diagnostics, and artifact_path fields exactly match the inputs,
        and whose duration_seconds is non-negative.
        """
        mixin = ScannerMixin()
        start_time = time.perf_counter()

        result = mixin._build_success_result(
            packages=packages,
            strategy=strategy,
            diagnostics=diagnostics,
            start_time=start_time,
            artifact_path=artifact_path,
        )

        # Verify it returns a ScanResult
        assert isinstance(result, ScanResult)

        # Verify field equality
        assert result.packages == packages, f"Expected packages {packages}, got {result.packages}"
        assert result.strategy == strategy, f"Expected strategy '{strategy}', got '{result.strategy}'"
        assert result.diagnostics == diagnostics, f"Expected diagnostics {diagnostics}, got {result.diagnostics}"
        assert result.artifact_path == artifact_path, (
            f"Expected artifact_path '{artifact_path}', got '{result.artifact_path}'"
        )

        # duration_seconds should be non-negative (time.perf_counter() - start_time >= 0)
        assert result.duration_seconds >= 0, f"Expected non-negative duration, got {result.duration_seconds}"
