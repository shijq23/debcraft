# Requirements Document

## Introduction

This feature addresses pylint warnings and code quality issues in the debcraft Python codebase (current score: 9.96/10). The refactoring targets three categories: complexity/size violations (too many statements, lines, arguments, local variables, branches, return statements), code duplication (repeated scan-result/cancellation patterns, repeated stanza-parsing patterns, repeated SBOM writer patterns), and minor style issues (broad exception catches, protected member access, useless imports). All changes preserve existing behavior while improving maintainability and pylint compliance.

## Glossary

- **Debcraft**: The Python application being refactored, using a `src/debcraft/` layout.
- **Pylint**: The static analysis tool reporting code quality violations.
- **Scanner**: A module in `infrastructure/scanners/` that identifies installed packages from an artifact (directory, qcow2, iso, img, ami, docker).
- **SBOM_Writer**: A module in `infrastructure/sbom_writers/` that serializes an SBOM document to a specific output format (CycloneDX, SPDX 2.3, SPDX 3.0).
- **Mirror_Engine**: The module `infrastructure/mirror/engine.py` orchestrating the repository synchronization pipeline.
- **Download_Coordinator**: The class in `infrastructure/mirror/download.py` managing concurrent HTTP downloads.
- **Stanza_Parser**: A parser that reads control-file-style stanza blocks from Packages or dpkg-status files.
- **ScanResult**: A data class carrying identified packages, scanning strategy, diagnostics, duration, and artifact path.
- **CancellationToken**: A cooperative cancellation signal checked between pipeline steps.
- **WorkflowContext**: The execution context providing DI scope, cancellation, progress, logging, and event publishing to workflows.

## Requirements

### Requirement 1: Reduce Mirror Engine Module Size

**User Story:** As a developer, I want the mirror engine split into smaller focused modules, so that each module stays within pylint's 1000-line limit and is easier to navigate.

#### Acceptance Criteria

1. WHEN the Mirror_Engine module refactoring is complete, THE `infrastructure/mirror/engine.py` file SHALL contain no more than 1000 physical lines as measured by pylint's C0302 rule (including comments and blank lines).
2. WHEN the Mirror_Engine module is split, THE Debcraft codebase SHALL preserve all existing public API signatures of the `MirrorEngine` class (the `sync_repository` method and the `SyncResult` dataclass) such that existing callers can import and invoke them from their original module path without modification.
3. WHEN the Mirror_Engine module is split, THE Debcraft codebase SHALL extract helper functions or internal classes into separate underscore-prefixed private modules within the `infrastructure/mirror/` package, where each extracted module also contains no more than 1000 physical lines.
4. WHEN the Mirror_Engine module refactoring is complete, THE existing test suite SHALL pass with no test modifications required (excluding import path changes within the `infrastructure/mirror/` package itself).

### Requirement 2: Reduce Positional Argument Counts

**User Story:** As a developer, I want functions with too many positional arguments refactored to use configuration dataclasses or keyword-only arguments, so that call sites are clearer and pylint's argument-count threshold is satisfied.

#### Acceptance Criteria

1. WHEN the `MirrorEngine.__init__` method (engine.py:71) is refactored, THE MirrorEngine.__init__ SHALL accept no more than 5 positional arguments (excluding `self`) by converting remaining parameters to keyword-only arguments or bundling them into a configuration dataclass.
2. WHEN the `_attempt_download` method (download.py:298) is refactored, THE DownloadCoordinator SHALL accept no more than 5 positional arguments (excluding `self`) for that method by converting remaining parameters to keyword-only arguments or bundling them into a configuration dataclass.
3. WHEN the `_sync_single_repository` function (mirror.py:174) is refactored, THE Debcraft codebase SHALL accept no more than 5 positional arguments for that function by grouping related parameters into a configuration dataclass or using keyword-only arguments.
4. WHEN the `_run_sbom` function (sbom.py:250) is refactored, THE Debcraft codebase SHALL accept no more than 5 positional arguments for that function by converting remaining parameters to keyword-only arguments or bundling them into a configuration dataclass.
5. WHEN the `WorkflowContext.__init__` (platform/contracts/workflow.py:114) is refactored, THE WorkflowContext SHALL accept no more than 5 positional arguments (excluding `self`) by converting remaining parameters to keyword-only arguments or bundling them into a configuration dataclass.
6. WHEN the `_upsert_repository_file` method (engine.py:850) is refactored, THE MirrorEngine SHALL accept no more than 5 positional arguments (excluding `self`) for that method by converting remaining parameters to keyword-only arguments or bundling them into a configuration dataclass.
7. WHEN positional arguments are reduced for any function in criteria 1–6, THE refactored function SHALL maintain the same public return type and observable side effects as the original implementation.
8. WHEN positional arguments are reduced for any function in criteria 1–6, THE Debcraft codebase SHALL pass all existing unit and integration tests without modification to test assertions (test call-site updates to match new signatures are permitted).
9. WHEN positional arguments are reduced, THE Debcraft codebase SHALL produce zero R0917 (too-many-positional-arguments) violations when analyzed by pylint with the default threshold of 5 positional arguments for the refactored functions.

### Requirement 3: Reduce Local Variable and Statement Counts

**User Story:** As a developer, I want functions with excessive local variables or statements decomposed into smaller helper functions, so that each function has a single clear responsibility.

#### Acceptance Criteria

1. THE `DockerScanner.scan` method (docker.py) SHALL use no more than 20 local variables as measured by Pylint rule R0914.
2. THE `_run_sbom` function (cli/sbom.py) SHALL use no more than 20 local variables as measured by Pylint rule R0914.
3. THE `SBOMWorkflow.execute` method (sbom_writers/workflow.py) SHALL contain no more than 50 statements as measured by Pylint rule R0915.
4. WHEN a function is decomposed to reduce local variables or statements, THE Debcraft codebase SHALL extract logic into private helper methods (prefixed with underscore) that are called only from the original method.
5. WHEN helper methods are extracted, THE existing test suite SHALL pass without modification to test code, confirming that the public interface and observable behavior of each refactored function remain unchanged.

### Requirement 4: Reduce Branch and Return Statement Counts

**User Story:** As a developer, I want functions with excessive branches or return statements simplified, so that control flow is easier to follow.

#### Acceptance Criteria

1. WHEN the `oci.py:114` function (`_validate_oci_artifact`) is refactored, THE Debcraft codebase SHALL contain no more than 8 return statements in that function, as measured by Pylint R0911 no longer reporting a violation.
2. WHEN the `spdx_tokenizer.py:30` function (`SPDXTokenizer.tokenize`) is refactored, THE Debcraft codebase SHALL contain no more than 12 branches in that function, as measured by Pylint R0912 no longer reporting a violation.
3. WHEN branches or returns are reduced, THE Debcraft codebase SHALL preserve equivalent logic such that all existing unit and property-based tests for the modified modules pass without modification.
4. WHEN branches or returns are reduced, THE Debcraft codebase SHALL introduce no new Pylint errors or warnings in the modified files.

### Requirement 5: Eliminate Duplicated Scan-Result and Cancellation Patterns

**User Story:** As a developer, I want repeated scan-result construction and cancellation-check patterns across scanners extracted into shared utilities, so that bug fixes and changes propagate to all scanners automatically.

#### Acceptance Criteria

1. WHEN scan-result/cancellation duplication is eliminated, THE Debcraft codebase SHALL provide shared utility methods in the ScannerMixin that construct ScanResult instances for at least the following duplicated patterns: (a) early-exit on cancellation (setting packages to an empty list, recording a cancellation diagnostic with the step name, and capturing elapsed duration), (b) package-loop iteration that checks the cancellation token between entries and appends a diagnostic stating how many of the total packages were processed before cancellation, and (c) success-result construction that records the final package list, strategy, accumulated diagnostics, elapsed duration, and artifact path.
2. WHEN scan-result/cancellation duplication is eliminated, THE scanner modules (directory, qcow2, iso, img, ami) SHALL each invoke the shared ScannerMixin methods for cancellation-exit and package-loop-with-cancellation logic rather than directly constructing ScanResult instances inline for those cases, such that no scanner module contains more than one inline ScanResult construction per scan code path that duplicates another scanner's cancellation or result-building logic.
3. WHEN the shared utility methods are invoked by any scanner module, THE returned ScanResult SHALL contain identical field values for packages, strategy, diagnostics, duration_seconds, and artifact_path as the previously inlined code would have produced given the same inputs, verified by all existing scanner unit tests passing without modification to their assertions.
4. IF the cancellation token is signalled during a package-iteration loop delegated to the shared utility, THEN THE shared utility SHALL return a ScanResult containing only the packages processed before cancellation, a diagnostic message stating the count of packages processed out of the total, and a duration_seconds value reflecting elapsed time from the provided start timestamp to the cancellation point.

### Requirement 6: Eliminate Duplicated Stanza-Parsing Patterns

**User Story:** As a developer, I want repeated stanza-parsing logic between `packages_parser` and `sources_parser` (and between `mirror/packages_parser` and `scanner/dpkg_parser`) unified, so that parsing changes are made in one place.

#### Acceptance Criteria

1. WHEN stanza-parsing duplication is eliminated, THE Debcraft codebase SHALL provide a shared stanza-parsing utility that reads key-value blocks separated by blank lines.
2. WHEN the shared stanza-parsing utility is introduced, THE `packages_parser`, `sources_parser`, `mirror/packages_parser`, and `scanner/dpkg_parser` modules SHALL delegate to the shared utility for stanza boundary detection and field extraction.
3. WHEN stanza parsing is unified, THE Debcraft codebase SHALL produce identical parse results for all previously supported inputs.

### Requirement 7: Eliminate Duplicated SBOM Writer Patterns

**User Story:** As a developer, I want repeated cancellation-check and write-to-disk patterns between `cyclonedx.py` and `spdx23.py` writers extracted into a shared base or utility, so that writer logic is consistent and maintained in one place.

#### Acceptance Criteria

1. WHEN SBOM writer duplication is eliminated, THE Debcraft codebase SHALL provide a shared write-with-cancellation utility or base class method that accepts output bytes, an output path, a cancellation token, an OutputFormat value, and a diagnostics list, and that performs the following sequence: pre-write cancellation check raising WriterCancellationError if cancelled, disk write via write_sbom_output returning sha256 and file_size, post-write cancellation check that unlinks the written file and raises WriterCancellationError if cancelled, and construction of a WriterResult containing output_path, format, sha256, file_size, and diagnostics.
2. WHEN the shared write utility is introduced, THE `cyclonedx.py` and `spdx23.py` writers SHALL each invoke the shared utility exactly once per write call to perform the cancellation-check, disk-write, post-write-cancellation-check-with-cleanup, and WriterResult construction sequence, rather than implementing these steps inline.
3. WHEN writer patterns are unified, THE SBOM writer modules SHALL produce byte-identical WriterResult field values (output_path, format, sha256, file_size, diagnostics) and the same cancellation behavior (raising WriterCancellationError with the output_path and unlinking the file on post-write cancellation) as the original inline implementations, given the same inputs.
4. IF the shared utility or base class method is called with a cancellation token that is already cancelled before the disk write, THEN THE shared utility SHALL raise WriterCancellationError with the output_path without writing any file to disk.
5. WHEN the refactoring is complete, THE Pylint R0801 duplicate-code warning for the cancellation/write/cleanup sequence between `cyclonedx.py` and `spdx23.py` SHALL no longer be reported.

### Requirement 8: Fix Minor Style Issues

**User Story:** As a developer, I want remaining minor style violations resolved, so that the pylint score reaches 10/10 and the codebase follows consistent conventions.

#### Acceptance Criteria

1. WHEN the broad-exception-caught warning at `sbom_writers/workflow.py:381` is fixed, THE handler SHALL either catch a specific exception type narrower than `Exception` or use a `# pylint: disable=broad-exception-caught` suppression comment with an inline justification (maximum 120 characters) explaining why catching `Exception` is intentional at that boundary.
2. WHEN the broad-exception-caught warning at `platform/kernel/workflow.py:302` is fixed, THE handler SHALL either catch a specific exception type narrower than `Exception` or use a `# pylint: disable=broad-exception-caught` suppression comment with an inline justification (maximum 120 characters) explaining why catching `Exception` is intentional at that boundary.
3. WHEN the protected-member-access warning at `engine.py:596` is fixed, THE access to `_config` SHALL be replaced with a public method or property on the owning class, and no W0212 pylint warning SHALL remain on that line.
4. WHEN the protected-member-access warnings at `container.py:224,227` are fixed, THE accesses to `_registrations` SHALL be replaced with a public method or property on the parent container class, and no W0212 pylint warnings SHALL remain on those lines.
5. WHEN the useless-import-alias (C0414) and unused-import (W0611) warnings at `mirror/errors.py:10` are fixed, THE `ReleaseParseError` import SHALL use one of the following patterns: (a) a direct import without identity alias combined with an explicit `__all__` list that includes `ReleaseParseError`, or (b) removal of the import if it is not consumed by any downstream module. No C0414 or W0611 pylint warnings SHALL remain on that line.
6. WHEN all fixes in criteria 1–5 are applied, THE project SHALL produce a pylint score of 10.00/10 with zero warnings of codes W0718, W0212, C0414, or W0611 when pylint is run against the `src/debcraft` package with the project's existing pylint configuration.
