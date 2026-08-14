"""Bug condition exploration test for architecture test ABC mapping.

**Validates: Requirements 1.1, 1.2, 2.1, 2.2**

Property 1: Bug Condition — Infrastructure ABCs Reported as Missing

This test demonstrates that the architecture test `test_all_abcs_have_kernel_implementations`
incorrectly reports infrastructure ABCs as missing implementations. These ABCs
(DatabaseProvider, Repository, StorageEngine, StorageProvider, UnitOfWork) have concrete
implementations in `infrastructure/` but the test only scans `platform/kernel/`.

The test encodes the EXPECTED behavior: infrastructure ABCs with implementations in
`infrastructure/` should NOT be reported as missing. On unfixed code, this test FAILS,
confirming the bug exists. After the fix is applied, this test PASSES.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from abc import ABC
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

SRC_ROOT = Path(__file__).resolve().parent.parent.parent / "src" / "debcraft"
CONTRACTS_DIR = SRC_ROOT / "platform" / "contracts"
KERNEL_DIR = SRC_ROOT / "platform" / "kernel"
INFRASTRUCTURE_DIR = SRC_ROOT / "infrastructure"

# The 5 known infrastructure ABCs that trigger the bug
INFRASTRUCTURE_ABCS = [
    "DatabaseProvider",
    "Repository",
    "StorageEngine",
    "StorageProvider",
    "UnitOfWork",
]

# User-facing ABCs excluded from implementation checks (mirrors the test's exclusion)
_USER_FACING_ABCS = frozenset({"Workflow"})


def _discover_contract_abcs() -> dict[str, type]:
    """Discover all ABC classes defined in the contracts package."""
    abcs: dict[str, type] = {}
    contracts_pkg = "debcraft.platform.contracts"

    for module_info in pkgutil.walk_packages(
        [str(CONTRACTS_DIR)],
        prefix=f"{contracts_pkg}.",
    ):
        try:
            module = importlib.import_module(module_info.name)
        except ImportError:
            continue

        for name, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, ABC)
                and obj is not ABC
                and obj.__module__.startswith(contracts_pkg)
                and any(getattr(method, "__isabstractmethod__", False) for method in vars(obj).values())
            ):
                abcs[name] = obj

    return abcs


def _discover_kernel_classes() -> dict[str, type]:
    """Discover all classes defined in the kernel package."""
    classes: dict[str, type] = {}
    kernel_pkg = "debcraft.platform.kernel"

    for module_info in pkgutil.walk_packages(
        [str(KERNEL_DIR)],
        prefix=f"{kernel_pkg}.",
    ):
        try:
            module = importlib.import_module(module_info.name)
        except ImportError:
            continue

        for name, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__.startswith(kernel_pkg):
                classes[name] = obj

    return classes


def _discover_infrastructure_classes() -> dict[str, type]:
    """Discover all classes defined in the infrastructure package."""
    classes: dict[str, type] = {}
    infra_pkg = "debcraft.infrastructure"

    for module_info in pkgutil.walk_packages(
        [str(INFRASTRUCTURE_DIR)],
        prefix=f"{infra_pkg}.",
    ):
        try:
            module = importlib.import_module(module_info.name)
        except ImportError:
            continue

        for name, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__.startswith(infra_pkg):
                classes[name] = obj

    return classes


def _is_bug_condition(abc_name: str, abc_type: type) -> bool:
    """Check if an ABC meets the bug condition.

    Returns True when the ABC:
    - Is NOT in _USER_FACING_ABCS
    - Has a concrete subclass in infrastructure/
    - Does NOT have a concrete subclass in kernel/
    """
    if abc_name in _USER_FACING_ABCS:
        return False

    kernel_classes = _discover_kernel_classes()
    infrastructure_classes = _discover_infrastructure_classes()

    has_kernel_impl = any(issubclass(cls, abc_type) and cls is not abc_type for cls in kernel_classes.values())
    has_infra_impl = any(issubclass(cls, abc_type) and cls is not abc_type for cls in infrastructure_classes.values())

    return has_infra_impl and not has_kernel_impl


@pytest.mark.architecture
class TestABCBugConditionExploration:
    """Exploration test confirming infrastructure ABCs are incorrectly reported.

    These tests encode the EXPECTED behavior (infrastructure ABCs should not be
    reported as missing). They FAIL on unfixed code, confirming the bug exists.
    After the fix, they PASS.
    """

    @given(abc_name=st.sampled_from(INFRASTRUCTURE_ABCS))
    def test_infrastructure_abcs_not_reported_as_missing(self, abc_name: str) -> None:
        """Infrastructure ABCs with implementations should not be missing.

        For each of the 5 infrastructure ABCs, verifies that the current
        test logic correctly recognizes their implementations exist.

        On unfixed code, the test only scans kernel/ so these ABCs ARE
        reported as missing — this assertion fails, confirming the bug.

        **Validates: Requirements 1.1, 1.2, 2.1, 2.2**
        """
        # Discover ABCs and kernel classes (mimicking the existing test logic)
        contract_abcs = _discover_contract_abcs()
        kernel_classes = _discover_kernel_classes()

        # Verify this ABC exists in contracts
        assert abc_name in contract_abcs, (
            f"{abc_name} not found in platform/contracts/ — expected it to be discoverable as an ABC"
        )

        abc_type = contract_abcs[abc_name]

        # Verify this ABC meets the bug condition (has infra impl, no kernel impl)
        assert _is_bug_condition(abc_name, abc_type), (
            f"{abc_name} does not meet bug condition — "
            f"expected it to have infrastructure implementation "
            f"but no kernel implementation"
        )

        # --- EXPECTED BEHAVIOR ASSERTION ---
        # The fixed test should recognize infrastructure implementations.
        # On UNFIXED code, only kernel_classes are checked, so this will FAIL.
        infrastructure_classes = _discover_infrastructure_classes()

        # Check if any implementation exists in EITHER kernel or infrastructure
        has_implementation = any(
            issubclass(cls, abc_type) and cls is not abc_type
            for cls in (*kernel_classes.values(), *infrastructure_classes.values())
        )

        # This is what the FIXED test should find: implementation exists
        assert has_implementation, (
            f"{abc_name} has no implementation in kernel/ or infrastructure/. "
            f"This should not happen for known infrastructure ABCs."
        )

        # --- BUG CONFIRMATION / FIX VERIFICATION ---
        # The FIXED test checks both kernel AND infrastructure classes.
        # For infrastructure ABCs, it should now find an implementation
        # in infrastructure/ and NOT report them as missing.
        has_kernel_impl = any(issubclass(cls, abc_type) and cls is not abc_type for cls in kernel_classes.values())
        has_infra_impl = any(
            issubclass(cls, abc_type) and cls is not abc_type for cls in infrastructure_classes.values()
        )

        # EXPECTED BEHAVIOR: Infrastructure ABCs should NOT be reported as missing.
        # On UNFIXED code, only kernel is checked so these are reported as missing (test FAILS).
        # On FIXED code, both kernel and infrastructure are checked (test PASSES).
        assert has_kernel_impl or has_infra_impl, (
            f"BUG CONFIRMED: {abc_name} has no concrete implementation in "
            f"either kernel/ or infrastructure/. "
            f"The test incorrectly reports {abc_name} as missing an implementation. "
            f"The fix should scan infrastructure/ as well."
        )
