# Design: Scanner Registry Class-vs-Instance and Artifact Auto-Detection

## Overview

Two compounding bugs prevent `debcraft sbom` from working on non-directory artifacts without an explicit `--type` flag:

1. **Scanner Registry stores classes, not instances.** Entry points in `pyproject.toml` reference scanner classes (e.g., `DirectoryScanner`). `ScannerRegistry._load_entry_point()` calls `ep.load()` which returns the class object, then stores it directly. When the workflow later calls `scanner.scan(artifact, context)`, this is an unbound method call — `artifact` is consumed as `self`, producing a `TypeError`.

2. **No artifact type auto-detection.** `SBOMWorkflow._scan()` defaults to `ArtifactType.DIRECTORY` when `self._config.artifact_type` is `None`, rather than inferring the type from the artifact path's file extension.

## Components

### 1. `ScannerRegistry` — instance registration support

**File:** `src/debcraft/infrastructure/scanners/registry.py`

**Changes:**

- Add a public `register(artifact_type: ArtifactType, scanner: ArtifactScanner) -> None` method that stores a pre-built scanner instance, validates protocol conformance, and respects priority-based selection (same logic as entry-point loading).
- Modify `_load_entry_point()` to detect when `loaded` is a class (via `inspect.isclass(loaded)`) rather than an instance. When a class is detected, record a diagnostic warning (since classes cannot be instantiated without constructor dependencies at this layer) and skip registration. This makes the failure mode explicit rather than silently storing an unusable class.

The registry itself does NOT instantiate scanners because scanner constructors require domain ports (`ContentsIndexPort`, `PackageLookupPort`) that the registry has no access to.

### 2. CLI scope builder — instantiate scanners with no-op ports

**File:** `src/debcraft/cli/sbom.py`

**Changes:**

- Replace `scanner_registry.load_from_entry_points()` in `_create_di_scope()` with explicit scanner instantiation. Import each scanner class, construct it with appropriate no-op port adapters, and call `scanner_registry.register(artifact_type, instance)` for each.
- Add a `_NoOpContentsIndexPort` class (returns empty results) and a `_NoOpPackageLookupPort` class (returns None) as minimal adapters for CLI-mode scanning. These match the existing pattern of `_NoOpCacheAdapter` and `_NoOpDatabaseProvider` already in the file.
- Register all scanner types defined in pyproject.toml: directory, docker, oci, iso, qcow2, img, ami.

This approach keeps the CLI path self-contained and avoids complex factory/DI patterns in the registry.

### 3. Bootstrap path — instantiate scanners with DI

**File:** `src/debcraft/infrastructure/scanners/bootstrap.py`

**Changes:**

- After `registry.load_from_entry_points()` (which will now skip classes with a diagnostic), resolve the required ports from the DI container and instantiate each scanner class explicitly, then call `registry.register()` for each.
- Alternatively, switch fully to explicit instantiation (mirroring the CLI approach) if the container provides the ports at bootstrap time.

This ensures the DI path also stores instances, not classes.

### 4. Artifact type auto-detection

**File:** `src/debcraft/domain/scanner/values.py`

**Changes:**

- Add a module-level function `detect_artifact_type(path: str) -> ArtifactType` that maps file extensions to artifact types:

```python
_EXTENSION_MAP: dict[str, ArtifactType] = {
    ".iso": ArtifactType.ISO,
    ".qcow2": ArtifactType.QCOW2,
    ".img": ArtifactType.IMG,
    ".tar": ArtifactType.DOCKER,
    ".tar.gz": ArtifactType.DOCKER,
    ".tgz": ArtifactType.DOCKER,
    ".oci": ArtifactType.OCI,
    ".ami": ArtifactType.AMI,
}


def detect_artifact_type(path: str) -> ArtifactType:
    """Detect artifact type from file extension or path characteristics.

    Checks if path is a directory first, then matches against known
    extensions. Falls back to DIRECTORY for unrecognized extensions.
    """
    from pathlib import Path as _Path

    p = _Path(path)
    if p.is_dir():
        return ArtifactType.DIRECTORY

    # Handle compound extensions like .tar.gz
    suffixes = "".join(p.suffixes).lower()
    for ext, artifact_type in _EXTENSION_MAP.items():
        if suffixes.endswith(ext):
            return artifact_type

    # Single extension fallback
    ext = p.suffix.lower()
    if ext in _EXTENSION_MAP:
        return _EXTENSION_MAP[ext]

    return ArtifactType.DIRECTORY
```

### 5. Workflow scan step — use auto-detection

**File:** `src/debcraft/infrastructure/sbom_writers/workflow.py`

**Changes:**

- In `SBOMWorkflow._scan()`, replace the hardcoded `ArtifactType.DIRECTORY` fallback with a call to `detect_artifact_type(self._config.artifact_path)`:

```python
if self._config.artifact_type:
    artifact_type = ArtifactType(self._config.artifact_type)
else:
    artifact_type = detect_artifact_type(self._config.artifact_path)
```

This preserves the explicit `--type` override (requirement 3.1) while enabling auto-detection when the flag is omitted.

## Key Design Decisions

### Why not instantiate in the registry?

Scanner constructors require domain ports (`ContentsIndexPort`, `PackageLookupPort`) as dependencies. The registry is a discovery/lookup layer — it has no awareness of the DI container or port implementations. Pushing instantiation into the registry would create a circular dependency or require the registry to accept a factory/resolver, adding complexity for no clear benefit.

### Why explicit instantiation instead of a factory pattern?

The project has exactly 7 scanner types, each with the same constructor signature (`contents_port`, `package_port`). A factory or abstract-factory pattern would be over-engineering for a fixed set of types. Explicit instantiation is readable, debuggable, and matches the existing `_NoOp*` pattern in the CLI module.

### Why put auto-detection in the domain layer?

`ArtifactType` is a domain value object. The mapping from file extensions to artifact types is domain knowledge (which file extensions correspond to which artifact categories). Placing the function in `values.py` co-locates it with the enum it returns, avoids import cycles, and makes it testable without infrastructure dependencies.

### Why fallback to DIRECTORY?

Preserves backward compatibility (requirement 3.1/2.4). If a path has no recognized extension and isn't explicitly typed, DIRECTORY scanning is the safest default — it looks for dpkg metadata in a filesystem tree, which degrades gracefully when the artifact isn't actually a directory.

### Why warn-and-skip for class entry points?

Making `_load_entry_point()` explicitly skip classes (with a diagnostic) rather than silently storing them converts a runtime `TypeError` into a visible warning at startup. This is a defense-in-depth measure — the primary fix is explicit instantiation in the CLI/bootstrap paths. The diagnostic helps developers catch misconfiguration early.
