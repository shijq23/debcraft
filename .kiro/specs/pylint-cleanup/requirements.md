# Requirements Document

## Introduction

This feature addresses pylint findings in the debcraft codebase (current score: 9.58/10) through targeted refactoring, configuration tuning, and code quality improvements. The goal is to reach a clean pylint run (≥9.90/10) while preserving the existing hexagonal architecture, maintaining test coverage, and respecting intentional design patterns such as lazy imports and protocol/ABC ellipsis bodies.

## Glossary

- **Pylint**: Static code analysis tool for Python that checks for errors, enforces coding standards, and detects code smells.
- **EARS Pattern**: Easy Approach to Requirements Syntax — a structured pattern language for writing unambiguous requirements.
- **Scanner**: A plugin that analyzes a specific artifact type (directory, docker, oci, iso, qcow2, img, ami) and produces package metadata.
- **SBOM_Writer**: A component that serializes scan results into a specific Software Bill of Materials format (CycloneDX, SPDX 2.3, SPDX 3.0).
- **Pylint_Configuration**: The `[tool.pylint.*]` sections in `pyproject.toml` that control pylint's behavior and suppressions.
- **Scanner_Base_Mixin**: A proposed shared module providing common scanner boilerplate (cancellation checks, filesystem analysis dispatch, progress reporting).
- **Storage_Engine**: The platform component responsible for resolving filesystem paths for mirror, cache, and output directories.
- **CLI_Module**: A Typer-based command-line interface module in `debcraft/cli/`.
- **Domain_Layer**: The `debcraft/domain/` package containing pure business logic with no infrastructure dependencies.
- **Infrastructure_Layer**: The `debcraft/infrastructure/` package implementing ports defined by the domain and platform layers.
- **Refactoring**: Code restructuring that preserves external behavior while improving internal structure.
- **Cyclomatic_Complexity**: A quantitative measure of the number of linearly independent paths through a program's source code.
- **Duplicate_Code**: Blocks of code repeated across multiple locations that can be consolidated into shared abstractions.

## Requirements

### Requirement 1: Extract shared scanner boilerplate into a base mixin

**User Story:** As a maintainer, I want scanner implementations to share common infrastructure code through a mixin, so that cancellation checks, filesystem analysis calls, and progress reporting are defined once rather than duplicated across seven scanner files.

#### Acceptance Criteria

1. WHEN a scanner calls the mixin cancellation-check method, THE Scanner_Base_Mixin SHALL read the WorkflowContext.cancellation_token.is_cancelled property and, if True, raise a ScannerError subclass indicating cancellation that includes the artifact path and a diagnostic message describing the step at which cancellation occurred.
2. THE Scanner_Base_Mixin SHALL provide a method that accepts a list of file paths, a ContentsIndexPort, a PackageLookupPort, a snapshot_id integer, and a WorkflowContext, invokes analyze_filesystem, checks the cancellation token before returning, reports progress at 100.0 on completion, and returns the resulting list of IdentifiedPackage objects and diagnostics.
3. THE Scanner_Base_Mixin SHALL provide a method that accepts a percentage value from 0.0 to 100.0 and a descriptive message of at most 256 characters, and delegates to WorkflowContext.progress.report with those arguments unchanged.
4. WHEN a scanner implementation uses the Scanner_Base_Mixin, THE scanner SHALL invoke the mixin cancellation-check method instead of inlining `if context.cancellation_token.is_cancelled` guard clauses, invoke the mixin filesystem-analysis method instead of calling analyze_filesystem directly, and invoke the mixin progress method instead of calling context.progress.report directly.
5. WHEN the Scanner_Base_Mixin is applied to all seven scanner implementations (directory, docker, img, iso, oci, qcow2, ami), THE Pylint R0801 duplicate-code warning SHALL no longer be reported for the scanner modules under src/debcraft/infrastructure/scanners/ when run with min-similarity-lines set to 6 and ignore-imports set to true.

### Requirement 2: Consolidate duplicate _MinimalStorageEngine into a shared module

**User Story:** As a maintainer, I want the `_MinimalStorageEngine` class used by CLI modules to be defined in a single location, so that changes to storage resolution logic are made once rather than in multiple files.

#### Acceptance Criteria

1. THE CLI_Module layer SHALL provide a single `MinimalStorageEngine` class in a new module `src/debcraft/cli/_storage.py` that implements the StorageEngine abstract interface, resolves XDG-compliant config and cache paths, and supports the `config` and `mirror` storage purposes.
2. WHEN `cli/index.py` or `cli/mirror.py` need a minimal storage engine, THE CLI_Module SHALL import `MinimalStorageEngine` from `debcraft.cli._storage` instead of defining a local `_MinimalStorageEngine` class.
3. WHEN the consolidation is complete, THE Pylint duplicate code warning R0801 for `_MinimalStorageEngine` SHALL no longer be reported.
4. WHEN the shared `MinimalStorageEngine` is imported and used, THE existing CLI behavior for path resolution SHALL remain identical as verified by existing tests passing.

### Requirement 3: Extract shared SBOM output logic into a common helper

**User Story:** As a maintainer, I want SBOM writers to share output serialization logic, so that JSON/file-writing boilerplate is defined once.

#### Acceptance Criteria

1. THE Infrastructure_Layer SHALL provide a shared SBOM output helper that accepts serialized bytes and an output path, and encapsulates parent directory creation, file writing with partial-file cleanup on failure, SHA-256 computation, and file size calculation.
2. WHEN an SBOM_Writer (CycloneDX, SPDX 2.3, or SPDX 3.0) produces output, THE SBOM_Writer SHALL delegate directory creation, byte-level file writing, partial-file cleanup, and hash computation to the shared output helper instead of implementing these operations inline.
3. IF the shared output helper encounters an OS error during directory creation or file writing, THEN THE helper SHALL remove any partial file and raise an OutputPathError indicating the failing path and OS error description.
4. WHEN the consolidation is complete, THE Pylint duplicate code warning R0801 for SBOM writer output logic SHALL no longer be reported.

### Requirement 4: Extract shared progress bar setup into a CLI utility

**User Story:** As a maintainer, I want CLI modules to share progress bar initialization logic, so that Rich progress bar configuration is consistent and defined once.

#### Acceptance Criteria

1. THE CLI_Module layer SHALL provide a shared progress bar factory function that returns a Rich Progress instance configured with SpinnerColumn, TextColumn for task description, BarColumn, TextColumn for percentage, and TimeElapsedColumn, bound to the module-level Rich Console.
2. THE shared progress bar factory function SHALL accept an optional boolean parameter to disable progress output, defaulting to enabled when the parameter is not provided.
3. WHEN a CLI command in the index, mirror, or sbom modules requires a progress bar, THE CLI_Module SHALL call the shared factory function rather than constructing a Progress instance with inline column configuration.
4. WHEN the consolidation is complete, THE Pylint duplicate code checker SHALL report no R0801 warning for progress bar column configuration across CLI module files.

### Requirement 5: Extract human-readable size formatting into a shared utility

**User Story:** As a maintainer, I want a single utility function for formatting byte counts as human-readable strings, so that the logic is defined once and reused consistently.

#### Acceptance Criteria

1. THE CLI_Module layer SHALL provide a `format_bytes` utility function that accepts a non-negative integer byte count and returns a human-readable string using IEC binary units (B, KiB, MiB, GiB) with one decimal place of precision for values above 1024 bytes.
2. WHEN any CLI module (mirror, sbom, or index) needs to display a human-readable file size, THE module SHALL call the shared `format_bytes` function instead of implementing size formatting inline.
3. WHEN the consolidation is complete, THE Pylint duplicate code warning R0801 for size formatting SHALL no longer be reported.

### Requirement 6: Decompose overly complex functions

**User Story:** As a maintainer, I want large functions to be decomposed into smaller, focused helper methods, so that each function has manageable complexity and is easier to test in isolation.

#### Acceptance Criteria

1. WHEN a function in `engine.py`, `oci.py`, `docker.py`, `qcow2.py`, `service.py`, `cli/mirror.py`, `cli/sbom.py`, or `cli/index.py` triggers pylint R0914 (too-many-locals), R0915 (too-many-statements), R0912 (too-many-branches), or R0911 (too-many-return-statements), THE Refactoring SHALL decompose the function into smaller module-private helper methods prefixed with underscore.
2. WHEN a function is decomposed, THE resulting helper methods SHALL each have at most 15 local variables, 50 statements, 12 branches, 6 return statements, and 5 parameters (excluding self/cls).
3. WHEN a function is decomposed, THE original function's public signature (name, parameters, return type, and exceptions raised) SHALL remain unchanged so that all existing callers continue to work without modification.
4. WHEN a function is decomposed, THE external behavior of the module SHALL remain unchanged as verified by existing tests passing without modification.

### Requirement 7: Replace broad exception catching with specific exception types

**User Story:** As a maintainer, I want exception handlers to catch specific exception types rather than bare `Exception`, so that unexpected errors propagate correctly and debugging is easier.

#### Acceptance Criteria

1. WHEN a `try/except` block in the infrastructure or domain layer catches `Exception` (pylint W0718), THE Refactoring SHALL replace it with one or more specific exception types appropriate to the operation (e.g., `OSError`, `ValueError`, `KeyError`, `TypeError`, `sqlalchemy.exc.SQLAlchemyError`, `aiohttp.ClientError`, `json.JSONDecodeError`).
2. IF a broad `except Exception` is genuinely needed as a last-resort safety net at a workflow boundary (top-level CLI error handler, event bus dispatch, or plugin loader), THEN THE code SHALL include an inline `# pylint: disable=broad-exception-caught` comment followed by a brief justification comment.
3. WHEN the refactoring is complete, THE Pylint warning W0718 SHALL only appear in locations with explicit inline suppressions, and the total count of remaining W0718 suppressions SHALL NOT exceed 10 across the entire codebase.

### Requirement 8: Configure pylint to suppress intentional pattern warnings

**User Story:** As a maintainer, I want pylint configured to recognize intentional coding patterns, so that warnings for deliberate design choices do not clutter the output.

#### Acceptance Criteria

1. THE `[tool.pylint."messages control"]` section in `pyproject.toml` SHALL add W2301 (unnecessary-ellipsis) to the `disable` list, because the codebase intentionally uses ellipsis in protocol and ABC method bodies.
2. THE `[tool.pylint."messages control"]` section in `pyproject.toml` SHALL add C0415 (import-outside-toplevel) to the `disable` list, because the codebase intentionally uses lazy imports for startup performance.
3. THE `[tool.pylint."messages control"]` section in `pyproject.toml` SHALL add W0622 (redefined-builtin) to the `disable` list, because the codebase intentionally uses parameter names like `format` in SBOM writer APIs.
4. WHEN `uv run pylint src/` is executed after the configuration change, THE output SHALL contain zero occurrences of W2301, C0415, or W0622.

### Requirement 9: Add inline suppressions for known false positives

**User Story:** As a maintainer, I want false-positive pylint errors suppressed with inline comments, so that the pylint run is clean and actionable.

#### Acceptance Criteria

1. WHEN SQLAlchemy's `func.now()` or `func.count()` triggers pylint E1102 (not-callable), THE code SHALL include an inline `# pylint: disable=not-callable` comment on the affected line, suppressing only the `not-callable` diagnostic and no other diagnostic.
2. WHEN `license_mapper.py` triggers pylint E1128 (assignment-from-none), THE code SHALL include an inline `# pylint: disable=assignment-from-none` comment on the affected line, suppressing only the `assignment-from-none` diagnostic and no other diagnostic.
3. WHEN inline suppressions are added, THE suppression comment SHALL appear at the end of the same source line as the triggering statement, not as a standalone `# pylint: disable=` comment on a preceding line or at module level.
4. WHEN pylint is executed on the codebase with inline suppressions in place, THE pylint run SHALL produce zero E1102 violations from `func.now()` or `func.count()` calls and zero E1128 violations from `license_mapper.py`.

### Requirement 10: Fix minor style issues detected by pylint

**User Story:** As a maintainer, I want minor pylint style warnings resolved, so that the codebase follows consistent Python idioms and the pylint score improves.

#### Acceptance Criteria

1. WHEN pylint reports R1705 (unnecessary-else-after-return), THE Refactoring SHALL remove the unnecessary `else` clause.
2. WHEN pylint reports R1714 (consider-using-in), THE Refactoring SHALL replace chained equality comparisons with membership tests using `in`.
3. WHEN pylint reports R1721 (unnecessary-comprehension), THE Refactoring SHALL simplify the comprehension to a direct constructor call.
4. WHEN pylint reports W0706 (try-except-raise), THE Refactoring SHALL remove the redundant `try/except` that only re-raises.
5. WHEN pylint reports C0121 (singleton-comparison), THE Refactoring SHALL replace `== None` or `== True` with `is None` or `is True`.
6. WHEN pylint reports C2801 (unnecessary-dunder-call), THE Refactoring SHALL replace direct dunder method calls with the corresponding operator or builtin function.
7. WHEN pylint reports R1732 (consider-using-with), THE Refactoring SHALL use a context manager (`with` statement) for resource acquisition.
8. WHEN pylint reports W0246 (useless-parent-delegation), THE Refactoring SHALL remove methods that only call `super()` without adding behavior.
9. WHEN pylint reports W0611 (unused-import), THE Refactoring SHALL remove the unused import statement.
10. WHEN pylint reports W0613 (unused-argument), THE Refactoring SHALL prefix the unused argument with an underscore if the argument is part of a method signature required by an abstract base class, interface, or callback protocol; otherwise THE Refactoring SHALL remove the argument from the signature and all call sites.
11. WHEN pylint reports C0301 (line-too-long), THE Refactoring SHALL reformat the line to fit within the 120-character limit.
12. WHEN pylint reports C0413 (wrong-import-position), THE Refactoring SHALL move the import to the correct position after module docstrings and `__future__` imports.
13. WHEN any criterion in this requirement is applied, THE Refactoring SHALL preserve the existing observable behavior such that the full existing test suite continues to pass without modification.
14. WHEN all fixes in this requirement are applied, THE Refactoring SHALL result in zero pylint findings for codes R1705, R1714, R1721, W0706, C0121, C2801, R1732, W0246, W0611, W0613, C0301, and C0413 across the codebase.

### Requirement 11: Achieve target pylint score

**User Story:** As a maintainer, I want the pylint score to reach at least 9.90/10 after all changes, so that the codebase meets the project's quality threshold.

#### Acceptance Criteria

1. WHEN all refactoring and configuration changes are complete, THE debcraft codebase SHALL achieve a pylint score of 9.90 or higher out of 10.00 when running `uv run pylint src/` from the project root using the pylint configuration defined in `pyproject.toml`.
2. WHEN the pylint score is achieved, THE existing test suite SHALL pass when invoked via `uv run pytest` without modification to test assertions.
3. WHEN the pylint score is achieved, THE existing mypy check (invoked via `uv run mypy`) and ruff checks (invoked via `uv run ruff format --check .` and `uv run ruff check .`) SHALL produce zero errors.
4. IF the pylint score drops below 9.90 due to a subsequent code change, THEN THE pylint invocation SHALL exit with a non-zero status by configuring a `--fail-under=9.90` threshold in the pylint configuration.
