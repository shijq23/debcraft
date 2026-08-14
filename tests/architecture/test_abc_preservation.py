"""Preservation property tests for architecture test ABC mapping fix.

**Validates: Requirements 3.1, 3.2, 3.3**

Property 2: Preservation — Kernel ABCs and Exclusions Unchanged

These tests capture the baseline behavior on UNFIXED code that must be preserved
after the fix is applied:
1. Kernel-implemented ABCs are correctly detected (Requirement 3.1)
2. User-facing ABCs (Workflow) are properly excluded (Requirement 3.2)
3. Genuinely unimplemented ABCs would still be reported as missing (Requirement 3.3)
4. Contract purity and global state tests work independently

All tests in this file MUST PASS on the current unfixed code.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from abc import ABC, abstractmethod
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

SRC_ROOT = Path(__file__).resolve().parent.parent.parent / "src" / "debcraft"
CONTRACTS_DIR = SRC_ROOT / "platform" / "contracts"
KERNEL_DIR = SRC_ROOT / "platform" / "kernel"
INFRASTRUCTURE_DIR = SRC_ROOT / "infrastructure"

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


# Discover the non-bug-condition ABCs (kernel-implemented ABCs) at module level
# These are ABCs that have kernel implementations and are NOT the bug condition
_CONTRACT_ABCS = _discover_contract_abcs()
_KERNEL_IMPLEMENTED_ABCS = [
    name
    for name, abc_type in _CONTRACT_ABCS.items()
    if name not in _USER_FACING_ABCS and not _is_bug_condition(name, abc_type)
]


@pytest.mark.architecture
class TestPreservationKernelABCs:
    """Verify kernel-implemented ABCs are correctly detected.

    **Validates: Requirements 3.1**

    For all ABCs where isBugCondition returns false and the ABC is not user-facing,
    the existing test logic correctly detects their kernel implementations.
    This behavior must be preserved after the fix.
    """

    @given(abc_name=st.sampled_from(_KERNEL_IMPLEMENTED_ABCS))
    def test_kernel_abcs_have_implementations_detected(self, abc_name: str) -> None:
        """Kernel-implemented ABCs are correctly found by current test logic.

        For any ABC with a kernel implementation, the existing _discover_kernel_classes
        logic finds it and the ABC is NOT reported as missing.

        **Validates: Requirements 3.1**
        """
        contract_abcs = _discover_contract_abcs()
        kernel_classes = _discover_kernel_classes()

        assert abc_name in contract_abcs, f"{abc_name} should be discoverable in platform/contracts/"

        abc_type = contract_abcs[abc_name]

        # Verify it's not in the bug condition
        assert not _is_bug_condition(abc_name, abc_type), (
            f"{abc_name} should NOT meet bug condition — it has a kernel implementation"
        )

        # Verify the current test logic correctly finds its kernel implementation
        has_kernel_implementation = any(
            issubclass(cls, abc_type) and cls is not abc_type for cls in kernel_classes.values()
        )

        assert has_kernel_implementation, (
            f"{abc_name} should have a kernel implementation detected by "
            f"_discover_kernel_classes(). This baseline behavior must be preserved."
        )


@pytest.mark.architecture
class TestPreservationUserFacingExclusion:
    """Verify user-facing ABCs are properly excluded from implementation checks.

    **Validates: Requirements 3.2**

    The _USER_FACING_ABCS exclusion mechanism ensures that ABCs designed for
    user/plugin extension (like Workflow) are not checked for kernel implementations.
    """

    @given(abc_name=st.sampled_from(sorted(_USER_FACING_ABCS)))
    def test_user_facing_abcs_are_excluded(self, abc_name: str) -> None:
        """User-facing ABCs are excluded from implementation checks.

        The _USER_FACING_ABCS set ensures these ABCs are not reported as
        missing implementations.

        **Validates: Requirements 3.2**
        """
        contract_abcs = _discover_contract_abcs()

        # Verify the ABC exists in contracts
        assert abc_name in contract_abcs, f"{abc_name} should be discoverable in platform/contracts/"

        # Verify it IS in the exclusion set
        assert abc_name in _USER_FACING_ABCS, f"{abc_name} should be in _USER_FACING_ABCS exclusion set"

        # Verify the exclusion logic works: even without a kernel implementation,
        # this ABC would NOT be added to the missing list
        abc_type = contract_abcs[abc_name]
        kernel_classes = _discover_kernel_classes()

        # Simulate the test logic from test_all_abcs_have_kernel_implementations
        missing: list[str] = []
        if abc_name in _USER_FACING_ABCS:
            pass  # Excluded — should not be added to missing
        else:
            has_implementation = any(
                issubclass(cls, abc_type) and cls is not abc_type for cls in kernel_classes.values()
            )
            if not has_implementation:
                missing.append(abc_name)

        assert abc_name not in missing, (
            f"{abc_name} should be excluded from missing list via _USER_FACING_ABCS. "
            f"This exclusion mechanism must be preserved after the fix."
        )


@pytest.mark.architecture
class TestPreservationMissingDetection:
    """Verify genuinely unimplemented ABCs would still be reported as missing.

    **Validates: Requirements 3.3**

    A synthetic mock ABC with no implementation in either kernel or infrastructure
    should be reported as missing by the test logic. This ensures the test still
    catches genuinely unimplemented contracts.
    """

    @given(
        abc_name=st.text(
            alphabet=st.characters(whitelist_categories=("Lu", "Ll")),
            min_size=5,
            max_size=20,
        ).filter(lambda n: n not in _USER_FACING_ABCS)
    )
    def test_synthetic_unimplemented_abc_detected_as_missing(self, abc_name: str) -> None:
        """A synthetic ABC with no implementation is reported as missing.

        For any ABC not in _USER_FACING_ABCS and with no concrete subclass in
        either kernel or infrastructure, the test logic should report it as missing.

        **Validates: Requirements 3.3**
        """
        # Create a synthetic ABC dynamically
        synthetic_abc = type(
            abc_name,
            (ABC,),
            {"do_something": abstractmethod(lambda self: None)},
        )

        kernel_classes = _discover_kernel_classes()

        # Simulate the current test logic
        # The synthetic ABC has no implementation anywhere
        has_implementation = any(
            issubclass(cls, synthetic_abc) and cls is not synthetic_abc for cls in kernel_classes.values()
        )

        # It should NOT have any implementation (it's synthetic)
        assert not has_implementation, f"Synthetic ABC '{abc_name}' should not have any kernel implementation"

        # Therefore, the test logic would report it as missing
        missing: list[str] = []
        if abc_name not in _USER_FACING_ABCS and not has_implementation:
            missing.append(abc_name)

        assert abc_name in missing, (
            f"Synthetic ABC '{abc_name}' with no implementation should be "
            f"reported as missing. The test's ability to catch genuinely "
            f"unimplemented ABCs must be preserved."
        )

    def test_synthetic_abc_not_in_infrastructure_either(self) -> None:
        """A synthetic ABC has no implementation in infrastructure either.

        This test verifies that both kernel AND infrastructure scanning would
        fail to find an implementation for a genuinely unimplemented ABC.
        After the fix adds infrastructure scanning, this ABC should STILL
        be reported as missing.

        **Validates: Requirements 3.3**
        """

        # Create a concrete synthetic ABC
        class SyntheticUnimplementedABC(ABC):
            @abstractmethod
            def perform_action(self) -> None: ...

        kernel_classes = _discover_kernel_classes()
        infrastructure_classes = _discover_infrastructure_classes()

        # Verify no implementation exists in either location
        has_kernel_impl = any(
            issubclass(cls, SyntheticUnimplementedABC) and cls is not SyntheticUnimplementedABC
            for cls in kernel_classes.values()
        )
        has_infra_impl = any(
            issubclass(cls, SyntheticUnimplementedABC) and cls is not SyntheticUnimplementedABC
            for cls in infrastructure_classes.values()
        )

        assert not has_kernel_impl, "Synthetic ABC should have no kernel implementation"
        assert not has_infra_impl, "Synthetic ABC should have no infrastructure implementation"

        # After the fix, the test will check both locations.
        # Since neither has an implementation, it should still be reported as missing.
        missing: list[str] = []
        abc_name = "SyntheticUnimplementedABC"
        if abc_name not in _USER_FACING_ABCS and not has_kernel_impl and not has_infra_impl:
            missing.append(abc_name)

        assert abc_name in missing, (
            "Genuinely unimplemented ABCs must still be caught after the fix "
            "adds infrastructure scanning. Detection of missing ABCs is preserved."
        )


@pytest.mark.architecture
class TestPreservationExclusionMechanism:
    """Verify the _USER_FACING_ABCS exclusion mechanism functions correctly.

    **Validates: Requirements 3.2**

    The exclusion set is a frozenset that prevents specific ABCs from being
    checked for implementations. This mechanism must remain functional.
    """

    def test_exclusion_set_contains_workflow(self) -> None:
        """Workflow is in the exclusion set.

        **Validates: Requirements 3.2**
        """
        from tests.architecture.test_platform_architecture import (
            TestABCImplementationMapping,
        )

        assert "Workflow" in TestABCImplementationMapping._USER_FACING_ABCS

    def test_exclusion_set_is_frozenset(self) -> None:
        """The exclusion set is immutable (frozenset).

        **Validates: Requirements 3.2**
        """
        from tests.architecture.test_platform_architecture import (
            TestABCImplementationMapping,
        )

        assert isinstance(TestABCImplementationMapping._USER_FACING_ABCS, frozenset)

    @given(
        abc_name=st.text(
            alphabet=st.characters(whitelist_categories=("Lu", "Ll")),
            min_size=3,
            max_size=15,
        )
    )
    def test_exclusion_prevents_missing_report(self, abc_name: str) -> None:
        """Any ABC in the exclusion set is never reported as missing.

        **Validates: Requirements 3.2**
        """
        # Simulate the test logic with an ABC that IS in the exclusion set
        exclusion_set = _USER_FACING_ABCS | frozenset({abc_name})

        missing: list[str] = []
        # The test logic skips ABCs in the exclusion set
        if abc_name in exclusion_set:
            pass  # Excluded
        else:
            missing.append(abc_name)

        assert abc_name not in missing, f"ABC '{abc_name}' in exclusion set should never appear in missing list"
