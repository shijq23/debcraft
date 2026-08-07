# Bugfix Requirements Document

## Introduction

The architecture test `test_all_abcs_have_kernel_implementations` in `tests/architecture/test_platform_architecture.py` incorrectly fails for 5 ABCs (`DatabaseProvider`, `Repository`, `StorageEngine`, `StorageProvider`, `UnitOfWork`) because it only searches `platform/kernel/` for implementations. These ABCs are infrastructure-layer contracts whose implementations intentionally live in `infrastructure/` (e.g., `DefaultStorageEngine` in `infrastructure/storage/engine.py`, `SQLiteDatabaseProvider` in `infrastructure/database/provider.py`). The test's exclusion mechanism only accounts for user-facing ABCs (like `Workflow`) but not for infrastructure-layer ABCs.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the test discovers ABCs whose implementations reside in `infrastructure/` rather than `platform/kernel/` THEN the system reports them as "ABCs without kernel implementations" and the test fails

1.2 WHEN the `_USER_FACING_ABCS` exclusion set is checked for infrastructure ABCs (DatabaseProvider, Repository, StorageEngine, StorageProvider, UnitOfWork) THEN the system does not exclude them because the set only contains "Workflow"

### Expected Behavior (Correct)

2.1 WHEN the test discovers ABCs whose implementations reside in `infrastructure/` rather than `platform/kernel/` THEN the system SHALL recognize these as infrastructure-layer contracts and not report them as missing kernel implementations

2.2 WHEN an ABC is an infrastructure-layer contract with implementations in `infrastructure/` THEN the system SHALL exclude it from the kernel-implementation check without requiring a kernel-side implementation

### Unchanged Behavior (Regression Prevention)

3.1 WHEN an ABC has its implementation in `platform/kernel/` THEN the system SHALL CONTINUE TO validate that the kernel implementation exists and is a concrete subclass of the ABC

3.2 WHEN an ABC is a user-facing contract (e.g., Workflow) intended for plugin/user extension THEN the system SHALL CONTINUE TO exclude it from the kernel-implementation check via the existing exclusion mechanism

3.3 WHEN a new ABC is added to `platform/contracts/` without a corresponding implementation in either `kernel/` or `infrastructure/` THEN the system SHALL CONTINUE TO report it as missing an implementation (the test still catches genuinely unimplemented ABCs)
