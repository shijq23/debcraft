# Architecture Test ABC Mapping Fix — Bugfix Design

## Overview

The `test_all_abcs_have_kernel_implementations` architecture test incorrectly reports 5 ABCs as missing kernel implementations. These ABCs (`DatabaseProvider`, `Repository`, `StorageEngine`, `StorageProvider`, `UnitOfWork`) are infrastructure-layer contracts whose implementations intentionally reside in `infrastructure/`, not `platform/kernel/`. The fix expands the test's implementation discovery to also scan the `infrastructure/` directory, so that ABCs with infrastructure-layer implementations are correctly recognized as having concrete subclasses.

## Glossary

- **Bug_Condition (C)**: The condition that triggers the false test failure — when an ABC has its implementation in `infrastructure/` rather than `platform/kernel/`, and the test only scans the kernel directory
- **Property (P)**: The desired behavior — ABCs with implementations in either `kernel/` or `infrastructure/` should pass the architecture test
- **Preservation**: Existing test behavior for kernel-implemented ABCs, user-facing ABC exclusions, and detection of genuinely unimplemented ABCs must remain unchanged
- **`_discover_kernel_classes`**: The method in `TestABCImplementationMapping` that currently scans only `platform/kernel/` for implementation classes
- **`_USER_FACING_ABCS`**: The frozenset exclusion mechanism for ABCs intentionally left without kernel/infrastructure implementations (currently contains only "Workflow")
- **Infrastructure ABCs**: ABCs in `platform/contracts/` whose concrete implementations live in `infrastructure/` by architectural design (DatabaseProvider, Repository, StorageEngine, StorageProvider, UnitOfWork)

## Bug Details

### Bug Condition

The bug manifests when the architecture test `test_all_abcs_have_kernel_implementations` discovers ABCs defined in `platform/contracts/` whose concrete implementations reside in `infrastructure/` rather than `platform/kernel/`. The `_discover_kernel_classes` method only walks the `platform/kernel/` package, so infrastructure implementations are never found. The `_USER_FACING_ABCS` exclusion set only contains "Workflow", leaving infrastructure ABCs unexcluded.

**Formal Specification:**
```
FUNCTION isBugCondition(abc)
  INPUT: abc of type ABCClass discovered in platform/contracts/
  OUTPUT: boolean

  RETURN abc.name NOT IN _USER_FACING_ABCS
         AND hasConcreteSubclass(abc, infrastructure_classes)
         AND NOT hasConcreteSubclass(abc, kernel_classes)
END FUNCTION
```

### Examples

- `StorageEngine` ABC defined in `platform/contracts/storage.py` → `DefaultStorageEngine` in `infrastructure/storage/engine.py` → test reports "StorageEngine" as missing (BUG)
- `DatabaseProvider` ABC defined in `platform/contracts/persistence.py` → `SqliteDatabaseProvider` in `infrastructure/database/provider.py` → test reports "DatabaseProvider" as missing (BUG)
- `UnitOfWork` ABC defined in `platform/contracts/persistence.py` → `SqliteUnitOfWork` in `infrastructure/database/unit_of_work.py` → test reports "UnitOfWork" as missing (BUG)
- `StorageProvider` ABC defined in `platform/contracts/storage.py` → `LocalStorageProvider` in `infrastructure/storage/providers.py` → test reports "StorageProvider" as missing (BUG)
- `Repository` ABC defined in `platform/contracts/persistence.py` → `SqlAlchemyRepository[T]` in `infrastructure/repositories/base.py` → test reports "Repository" as missing (BUG)
- `Workflow` ABC in `platform/contracts/` → excluded via `_USER_FACING_ABCS` → test passes (CORRECT, unchanged)
- A hypothetical new ABC with no implementation anywhere → test reports it as missing (CORRECT, unchanged)

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- ABCs with implementations in `platform/kernel/` must continue to be validated against their kernel implementations
- The `_USER_FACING_ABCS` exclusion mechanism must continue to work for ABCs intentionally left without any implementation (like "Workflow")
- Genuinely unimplemented ABCs (no implementation in either kernel or infrastructure) must still be caught and reported as failures
- The contract purity tests (`TestContractPurity`) must remain unaffected
- The mutable global state test (`TestNoMutableGlobalState`) must remain unaffected

**Scope:**
All inputs that do NOT involve ABCs with infrastructure-only implementations should be completely unaffected by this fix. This includes:
- ABCs with kernel implementations (validated as before)
- User-facing ABCs excluded via `_USER_FACING_ABCS` (still excluded)
- ABCs with no implementation anywhere (still reported as missing)
- Other architecture test classes (contract purity, global state checks)

## Hypothesized Root Cause

Based on the bug description, the most likely issue is:

1. **Insufficient Implementation Discovery Scope**: The `_discover_kernel_classes` method only walks `platform/kernel/` using `pkgutil.walk_packages`. It never imports or inspects modules under `infrastructure/`, so classes like `DefaultStorageEngine`, `SqliteDatabaseProvider`, `SqliteUnitOfWork`, `LocalStorageProvider`, and `SqlAlchemyRepository` are never found as potential implementations.

2. **Incomplete Exclusion Set**: The `_USER_FACING_ABCS` frozenset only contains "Workflow". The 5 infrastructure ABCs are not excluded because the original design assumed all non-user-facing ABCs would have kernel implementations. The architecture evolved to place persistence and storage implementations in `infrastructure/` without updating the test.

3. **Architectural Assumption Mismatch**: The test was written under the assumption that every contract would have a kernel-layer implementation. As the project grew, infrastructure-layer implementations became the correct architectural pattern for persistence/storage concerns, but the test was never updated to reflect this.

## Correctness Properties

Property 1: Bug Condition - Infrastructure ABCs Pass Test

_For any_ ABC defined in `platform/contracts/` where a concrete subclass exists in the `infrastructure/` package (isBugCondition returns true), the fixed test SHALL find that implementation and NOT report the ABC as missing an implementation.

**Validates: Requirements 2.1, 2.2**

Property 2: Preservation - Genuinely Missing ABCs Still Detected

_For any_ ABC defined in `platform/contracts/` where NO concrete subclass exists in either `platform/kernel/` or `infrastructure/` AND the ABC is not in `_USER_FACING_ABCS`, the fixed test SHALL continue to report it as missing an implementation, preserving the test's ability to catch genuinely unimplemented contracts.

**Validates: Requirements 3.1, 3.2, 3.3**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `tests/architecture/test_platform_architecture.py`

**Class**: `TestABCImplementationMapping`

**Specific Changes**:

1. **Add `INFRASTRUCTURE_DIR` constant**: Define a module-level constant pointing to `src/debcraft/infrastructure/`, analogous to the existing `KERNEL_DIR`.
   ```python
   INFRASTRUCTURE_DIR = SRC_ROOT / "infrastructure"
   ```

2. **Add `_discover_infrastructure_classes` method**: Create a new method that walks the `infrastructure/` package and discovers all classes, mirroring `_discover_kernel_classes`:
   ```python
   def _discover_infrastructure_classes(self) -> dict[str, type]:
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
   ```

3. **Update `test_all_abcs_have_kernel_implementations`**: Modify the test to also check infrastructure classes when determining whether an ABC has an implementation:
   ```python
   def test_all_abcs_have_kernel_implementations(self):
       contract_abcs = self._discover_contract_abcs()
       kernel_classes = self._discover_kernel_classes()
       infrastructure_classes = self._discover_infrastructure_classes()

       missing: list[str] = []
       for abc_name, abc_type in contract_abcs.items():
           if abc_name in self._USER_FACING_ABCS:
               continue
           has_kernel_impl = any(issubclass(cls, abc_type) and cls is not abc_type for cls in kernel_classes.values())
           has_infra_impl = any(
               issubclass(cls, abc_type) and cls is not abc_type for cls in infrastructure_classes.values()
           )
           if not has_kernel_impl and not has_infra_impl:
               missing.append(abc_name)

       assert missing == [], ...
   ```

4. **Optionally rename the test**: Consider renaming from `test_all_abcs_have_kernel_implementations` to `test_all_abcs_have_implementations` to reflect the broader scope. This is a minor naming improvement and not strictly required.

5. **Update class docstring**: Update `TestABCImplementationMapping` docstring to reflect that implementations may be in either kernel or infrastructure.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Run the existing `test_all_abcs_have_kernel_implementations` test and observe which ABCs are reported as missing. Cross-reference with infrastructure directory to confirm implementations exist there.

**Test Cases**:
1. **Run existing test**: Execute `pytest tests/architecture/test_platform_architecture.py::TestABCImplementationMapping` and observe failure listing 5 ABCs (will fail on unfixed code)
2. **Verify infrastructure implementations exist**: Import and inspect `DefaultStorageEngine`, `SqliteDatabaseProvider`, `SqliteUnitOfWork`, `LocalStorageProvider`, `SqlAlchemyRepository` to confirm they are concrete subclasses (will confirm root cause)
3. **Check that kernel ABCs pass**: Verify that ABCs with kernel implementations (if any) already pass the test (confirms preservation baseline)
4. **Check exclusion mechanism**: Verify "Workflow" is properly excluded (confirms existing mechanism works)

**Expected Counterexamples**:
- Test output lists: DatabaseProvider, Repository, StorageEngine, StorageProvider, UnitOfWork as "ABCs without kernel implementations"
- Root cause confirmed: implementations exist in `infrastructure/` but the test never looks there

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL abc WHERE isBugCondition(abc) DO
  result := test_all_abcs_have_implementations_fixed(abc)
  ASSERT abc NOT IN missing_list
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL abc WHERE NOT isBugCondition(abc) DO
  ASSERT test_fixed(abc) = test_original(abc)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It can generate synthetic ABC/implementation configurations to verify the test logic
- It catches edge cases like ABCs that inherit from other ABCs
- It provides strong guarantees that the detection logic works for all non-buggy inputs

**Test Plan**: Observe behavior on UNFIXED code first for kernel-implemented ABCs and user-facing exclusions, then write property-based tests capturing that behavior.

**Test Cases**:
1. **Kernel ABC Preservation**: Verify that ABCs with kernel implementations continue to pass after the fix
2. **User-Facing Exclusion Preservation**: Verify "Workflow" and any future user-facing ABCs remain excluded
3. **Missing ABC Detection Preservation**: Verify that a genuinely unimplemented ABC would still be caught by the fixed test
4. **Contract Purity Preservation**: Verify other architecture tests (import checks, global state) remain unaffected

### Unit Tests

- Test that `_discover_infrastructure_classes` correctly finds classes in the infrastructure package
- Test that the 5 previously-failing ABCs now pass with infrastructure scanning
- Test that a mock ABC with no implementation in either location is still reported as missing
- Test that the `_USER_FACING_ABCS` exclusion continues to work

### Property-Based Tests

- Generate random sets of ABCs and implementations distributed across kernel/infrastructure, verify the test correctly identifies which have implementations
- Generate configurations where some ABCs have kernel implementations, some have infrastructure implementations, and some have neither — verify correct classification
- Test preservation by running the logic with infrastructure scanning disabled vs enabled and verifying non-infrastructure ABCs produce identical results

### Integration Tests

- Run the full `test_all_abcs_have_kernel_implementations` test after the fix and verify it passes
- Run all architecture tests together to verify no regressions in contract purity or global state checks
- Add a new ABC without implementation and verify the test catches it (end-to-end detection validation)
