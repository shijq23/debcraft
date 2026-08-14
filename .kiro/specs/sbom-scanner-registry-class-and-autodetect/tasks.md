# Implementation Plan: Scanner Registry Class-vs-Instance and Artifact Auto-Detection

## Overview

Fix two compounding bugs in the debcraft SBOM subsystem:
1. Scanner Registry stores classes instead of instances (causes TypeError on scan)
2. No artifact type auto-detection (defaults to DIRECTORY for all files)

## Tasks

- [x] 1. Write bug condition exploration property test [bug-exploration]
  - [x] 1.1 Create a property test that demonstrates the bug: `load_from_entry_points()` stores classes (not instances) and calling `scan()` on them fails with TypeError
  - [x] 1.2 Create a property test that demonstrates auto-detection bug: workflow defaults to DIRECTORY regardless of file extension
- [x] 2. Add `register()` method to ScannerRegistry [registry-register]
  - [x] 2.1 Add `register(artifact_type: ArtifactType, scanner: ArtifactScanner) -> None` method to `ScannerRegistry` that stores a pre-built instance with protocol validation
  - [x] 2.2 Modify `_load_entry_point()` to detect when `loaded` is a class (via `inspect.isclass`) and skip with a diagnostic warning instead of storing the class
  - [x] 2.3 Add unit tests for the new `register()` method and the class-detection behavior
- [x] 3. Implement artifact type auto-detection [auto-detection]
  - [x] 3.1 Add `detect_artifact_type(path: str) -> ArtifactType` function to `src/debcraft/domain/scanner/values.py` with extension-to-type mapping
  - [x] 3.2 Update `SBOMWorkflow._scan()` in `workflow.py` to use `detect_artifact_type()` instead of hardcoded DIRECTORY fallback
  - [x] 3.3 Add unit tests for `detect_artifact_type()` covering all extensions, directories, compound extensions, and fallback
- [x] 4. Fix CLI scope builder to instantiate scanners [cli-fix]
  - [x] 4.1 Add `_NoOpContentsIndexPort` and `_NoOpPackageLookupPort` no-op adapter classes to `sbom.py`
  - [x] 4.2 Replace `scanner_registry.load_from_entry_points()` in `_create_di_scope()` with explicit scanner instantiation using no-op ports and `registry.register()` calls
  - [x] 4.3 Add integration test verifying CLI scope creates working scanner instances
- [x] 5. Fix bootstrap path for DI container [bootstrap-fix]
  - [x] 5.1 Update `scanner_bootstrap()` to instantiate scanners with DI-resolved ports and register instances via `registry.register()`
  - [x] 5.2 Verify bootstrap path still works with existing DI container tests

## Task Dependency Graph

```json
{
  "waves": [
    {"tasks": [1, 2, 3]},
    {"tasks": [4, 5]}
  ]
}
```

## Notes

- Task 1 is a bug exploration test and should be written first to confirm both bugs are reproducible
- Tasks 2 and 3 can proceed in parallel as they address independent concerns
- Tasks 4 and 5 both depend on Task 2's `register()` method being available
- The CLI path (Task 4) uses no-op adapters while the bootstrap path (Task 5) uses DI-resolved ports
