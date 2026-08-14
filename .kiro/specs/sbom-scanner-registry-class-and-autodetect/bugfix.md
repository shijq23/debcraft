# Bugfix Requirements Document

## Introduction

Running `debcraft sbom` on an ISO file without specifying `--type` fails with `TypeError: DirectoryScanner.scan() missing 1 required positional argument: 'context'`. Two compounding bugs cause this failure: (1) the scanner registry stores entry-point-loaded classes instead of instances, so `scanner.scan(artifact, context)` becomes an unbound method call, and (2) the SBOM workflow defaults to `ArtifactType.DIRECTORY` when `--type` is omitted instead of auto-detecting the type from the file extension.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the scanner registry loads entry points via `load_from_entry_points()` THEN the system stores the scanner CLASS object (not an instance) in the registry, causing subsequent calls to `scanner.scan(artifact, context)` to fail with a TypeError because `artifact` is consumed as `self`

1.2 WHEN `debcraft sbom` is invoked on an `.iso` file without `--type` THEN the system defaults `artifact_type` to `ArtifactType.DIRECTORY` instead of detecting the correct type from the file extension

1.3 WHEN `debcraft sbom` is invoked on any non-directory artifact (e.g., `.qcow2`, `.img`) without `--type` THEN the system defaults to `ArtifactType.DIRECTORY` instead of detecting the correct type

### Expected Behavior (Correct)

2.1 WHEN the scanner registry loads entry points via `load_from_entry_points()` THEN the system SHALL store scanner instances (or use a factory pattern) so that `scanner.scan(artifact, context)` is a bound method call that executes correctly

2.2 WHEN `debcraft sbom` is invoked on an `.iso` file without `--type` THEN the system SHALL auto-detect `ArtifactType.ISO` from the file extension

2.3 WHEN `debcraft sbom` is invoked on any artifact file with a recognized extension (`.iso`, `.qcow2`, `.img`) without `--type` THEN the system SHALL auto-detect the appropriate `ArtifactType` from the file extension

2.4 WHEN `debcraft sbom` is invoked on a path without a recognized extension and without `--type` THEN the system SHALL default to `ArtifactType.DIRECTORY` (preserving the existing fallback)

### Unchanged Behavior (Regression Prevention)

3.1 WHEN `debcraft sbom` is invoked with an explicit `--type` argument THEN the system SHALL CONTINUE TO use the specified type regardless of file extension

3.2 WHEN a scanner instance is manually registered via `register()` or test mocks THEN the system SHALL CONTINUE TO store and return that instance correctly

3.3 WHEN entry points fail to load (ImportError, missing module) THEN the system SHALL CONTINUE TO record diagnostics and skip the failed entry point without crashing

3.4 WHEN `get_scanner()` is called for an unregistered artifact type THEN the system SHALL CONTINUE TO raise `UnsupportedArtifactTypeError`

3.5 WHEN priority-based selection is used between multiple scanners for the same type THEN the system SHALL CONTINUE TO select the highest-priority scanner
