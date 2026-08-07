# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Infrastructure ABCs Reported as Missing
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists
  - **Scoped PBT Approach**: Scope the property to the 5 concrete failing ABCs: DatabaseProvider, Repository, StorageEngine, StorageProvider, UnitOfWork
  - Write a property-based test using Hypothesis that:
    - Discovers all ABCs in `platform/contracts/` via `_discover_contract_abcs()`
    - Discovers all classes in `infrastructure/` via a new `_discover_infrastructure_classes()` helper
    - For each ABC where `isBugCondition(abc)` holds (has infrastructure implementation but no kernel implementation, and not in `_USER_FACING_ABCS`):
      - Asserts that the current test logic (`test_all_abcs_have_kernel_implementations`) does NOT find an implementation (confirming the bug)
    - Uses `@given(sampled_from([...]))` over the 5 known infrastructure ABCs
  - The test assertions should verify: for infrastructure ABCs, the fixed test should NOT report them as missing
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves the bug exists by showing infrastructure ABCs are reported as missing)
  - Document counterexamples found: e.g., "StorageEngine has DefaultStorageEngine in infrastructure/storage/engine.py but test reports it as missing"
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 2.1, 2.2_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Kernel ABCs and Exclusions Unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - Observe behavior on UNFIXED code for non-buggy inputs:
    - Observe: ABCs with kernel implementations (if any) pass the existing test
    - Observe: "Workflow" ABC is excluded via `_USER_FACING_ABCS`
    - Observe: Contract purity tests and global state tests pass independently
  - Write property-based tests using Hypothesis that:
    - For all ABCs where `isBugCondition` returns false (kernel-implemented ABCs or user-facing ABCs):
      - Verify kernel-implemented ABCs are correctly detected as having implementations
      - Verify user-facing ABCs (Workflow) are properly excluded
    - For a synthetic mock ABC with no implementation in either kernel or infrastructure:
      - Verify it would be reported as missing (genuinely unimplemented detection preserved)
    - Verify `_USER_FACING_ABCS` exclusion mechanism still functions
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3_

- [x] 3. Fix for infrastructure ABCs incorrectly reported as missing implementations

  - [x] 3.1 Implement the fix
    - Add `INFRASTRUCTURE_DIR` constant: `INFRASTRUCTURE_DIR = SRC_ROOT / "infrastructure"`
    - Add `_discover_infrastructure_classes` method to `TestABCImplementationMapping` that walks the `infrastructure/` package using `pkgutil.walk_packages` and discovers all classes (mirroring `_discover_kernel_classes`)
    - Update `test_all_abcs_have_kernel_implementations` to also call `_discover_infrastructure_classes()` and check both kernel and infrastructure classes for ABC implementations
    - Update the test assertion logic: an ABC passes if it has a concrete subclass in EITHER kernel_classes OR infrastructure_classes
    - Update docstring of `TestABCImplementationMapping` to reflect that implementations may reside in either `platform/kernel/` or `infrastructure/`
    - _Bug_Condition: isBugCondition(abc) where abc has concrete subclass in infrastructure but not kernel, and abc not in _USER_FACING_ABCS_
    - _Expected_Behavior: ABCs with infrastructure implementations are recognized and not reported as missing_
    - _Preservation: Kernel-implemented ABCs still validated, user-facing exclusions still work, genuinely missing ABCs still caught_
    - _Requirements: 1.1, 1.2, 2.1, 2.2, 3.1, 3.2, 3.3_

  - [x] 3.2 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Infrastructure ABCs Pass Test
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior (infrastructure ABCs should not be reported as missing)
    - When this test passes, it confirms the expected behavior is satisfied
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed - infrastructure ABCs now recognized)
    - _Requirements: 2.1, 2.2_

  - [x] 3.3 Verify preservation tests still pass
    - **Property 2: Preservation** - Kernel ABCs and Exclusions Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions - kernel ABCs still validated, exclusions still work, missing ABCs still detected)
    - Confirm all tests still pass after fix (no regressions)

- [x] 4. Checkpoint - Ensure all tests pass
  - Run the full architecture test suite: `pytest tests/architecture/test_platform_architecture.py -v`
  - Verify all 3 test classes pass: TestContractPurity, TestABCImplementationMapping, TestNoMutableGlobalState
  - Verify no new warnings or issues introduced
  - Ensure all tests pass, ask the user if questions arise.
