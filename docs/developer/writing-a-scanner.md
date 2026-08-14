# Writing a Custom Scanner

## Introduction

This guide explains how to implement a custom artifact scanner for debcraft. A
scanner is a plugin that extracts Debian package metadata from a specific artifact
format (ISO images, Docker tarballs, disk images, etc.) and returns a structured
result that the platform transforms into an SBOM.

By the end of this guide you will understand:

- The `ArtifactScanner` protocol contract your scanner must satisfy
- How to implement the async `scan` method
- How to register your scanner as a Python entry point
- How to use `WorkflowContext` for cancellation, progress, and logging
- The value objects your scanner receives and produces

## The ArtifactScanner Protocol

All scanners implement the `ArtifactScanner` protocol defined in
`debcraft.domain.scanner.ports`:

```python
from debcraft.domain.scanner.ports import ArtifactScanner
from debcraft.domain.scanner.values import Artifact, ScanResult
from debcraft.platform.contracts.workflow import WorkflowContext


class ArtifactScanner(Protocol):
    """Protocol that all scanner implementations must satisfy."""

    async def scan(self, artifact: Artifact, context: WorkflowContext) -> ScanResult: ...
```

### Method Signature

```python
async def scan(self, artifact: Artifact, context: WorkflowContext) -> ScanResult
```

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `artifact` | `Artifact` | Describes the target to scan — its type, filesystem path, and any scanner-specific options. |
| `context` | `WorkflowContext` | Provides cancellation, progress reporting, logging, and other platform services for the duration of the scan. |

### Return Value

The method returns a `ScanResult` containing the identified packages, the scanning
strategy used, any diagnostic messages, the scan duration, and the path that was
scanned.

### Exceptions

| Exception | When raised |
|-----------|-------------|
| `ArtifactAccessError` | The artifact path does not exist or is not readable. Takes `path` and `reason` arguments. |

```python
from debcraft.domain.scanner.errors import ArtifactAccessError

# Raise when the artifact cannot be accessed
raise ArtifactAccessError(path=artifact.path, reason="File not found")
```

For format errors or missing optional dependencies, return a `ScanResult` with an
empty `packages` list and a message in `diagnostics` rather than raising an
exception. This allows the workflow to continue processing other artifacts.

## Implementing a Scanner

Here is a skeleton scanner that satisfies the protocol. It validates the artifact
path, reports progress, and returns an empty result:

```python
"""Skeleton scanner implementation for a custom artifact type."""

from __future__ import annotations

import os
import time

from debcraft.domain.scanner.errors import ArtifactAccessError
from debcraft.domain.scanner.values import (
    Artifact,
    ScanResult,
)
from debcraft.platform.contracts.workflow import WorkflowContext


class CustomScanner:
    """Scanner for a custom artifact format."""

    async def scan(self, artifact: Artifact, context: WorkflowContext) -> ScanResult:
        """Scan a custom artifact and return identified packages.

        Args:
            artifact: The artifact descriptor (type, path, options).
            context: Workflow context providing cancellation, progress, logging.

        Returns:
            ScanResult with identified packages and metadata.

        Raises:
            ArtifactAccessError: If the artifact path is inaccessible.
        """
        start = time.time()

        # 1. Validate artifact accessibility
        if not os.path.isfile(artifact.path):
            raise ArtifactAccessError(
                path=artifact.path,
                reason="File not found",
            )

        # 2. Report initial progress
        context.progress.report(0.0, "Starting scan")
        context.logger.info(f"Scanning artifact: {artifact.path}")

        # 3. Check for cancellation before expensive work
        if context.cancellation_token.is_cancelled:
            return ScanResult(
                packages=[],
                strategy="cancelled",
                diagnostics=["Scan cancelled before processing"],
                duration_seconds=time.time() - start,
                artifact_path=artifact.path,
            )

        # 4. Perform scanning logic (replace with your implementation)
        context.progress.report(50.0, "Analyzing artifact contents")
        packages = []  # Populate with IdentifiedPackage instances

        # 5. Report completion and return result
        context.progress.report(100.0, "Scan complete")

        return ScanResult(
            packages=packages,
            strategy="custom_strategy",
            diagnostics=[],
            duration_seconds=time.time() - start,
            artifact_path=artifact.path,
        )
```

## Entry-Point Registration

Debcraft discovers scanners at runtime through Python entry points. Register your
scanner in your package's `pyproject.toml`:

```toml
[project.entry-points."debcraft.scanners"]
custom = "my_package.scanners.custom:CustomScanner"
```

The key (`custom`) becomes the artifact type name used in CLI invocations:

```bash
debcraft sbom --type custom /path/to/artifact
```

For reference, debcraft's built-in scanners are registered in its own
`pyproject.toml`:

```toml
[project.entry-points."debcraft.scanners"]
directory = "debcraft.infrastructure.scanners.directory:DirectoryScanner"
docker = "debcraft.infrastructure.scanners.docker:DockerScanner"
iso = "debcraft.infrastructure.scanners.iso:ISOScanner"
qcow2 = "debcraft.infrastructure.scanners.qcow2:QCOW2Scanner"
img = "debcraft.infrastructure.scanners.img:IMGScanner"
```

After installing your package (e.g., `pip install -e .`), debcraft will
automatically discover and load your scanner.

## Using WorkflowContext

The `WorkflowContext` provides platform services to your scanner during execution.
The most relevant attributes for scanner authors are:

### Cooperative Cancellation

Check `cancellation_token.is_cancelled` periodically during long-running operations.
When `True`, stop work and return a partial or empty result:

```python
async def scan(self, artifact: Artifact, context: WorkflowContext) -> ScanResult:
    for i, layer in enumerate(layers):
        if context.cancellation_token.is_cancelled:
            return ScanResult(
                packages=packages_so_far,
                strategy="partial",
                diagnostics=["Scan cancelled during layer processing"],
                duration_seconds=time.time() - start,
                artifact_path=artifact.path,
            )
        # Process layer...
```

The cancellation token is monotonic — once cancelled, it stays cancelled.

### Progress Reporting

Call `context.progress.report(percentage, message)` to report scan progress. The
`percentage` parameter is a float from `0.0` to `100.0`:

```python
context.progress.report(0.0, "Opening artifact")
context.progress.report(25.0, "Parsing metadata")
context.progress.report(75.0, "Resolving packages")
context.progress.report(100.0, "Scan complete")
```

Progress updates may be displayed in the CLI, logged, or published as events
depending on the execution context.

### Logging

Use `context.logger` to emit structured log entries:

```python
context.logger.info(f"Found {len(packages)} packages in {artifact.path}")
context.logger.warning("dpkg status file not found, falling back to filesystem analysis")
context.logger.error(f"Failed to read layer: {e}")
```

### WorkflowContext Attributes Reference

| Attribute | Type | Description |
|-----------|------|-------------|
| `scope` | `Scope` | Scoped dependency injection container for resolving services. |
| `cancellation_token` | `CancellationToken` | Cooperative cancellation signal (`is_cancelled` property). |
| `progress` | `ProgressReporter` | Reports progress via `report(percentage, message)`. |
| `resources` | `ResourceManager` | Manages resource acquisition and cleanup. |
| `logger` | `Logger` | Structured logger for scan-related messages. |
| `event_bus` | `EventBus` | Publishes domain events during execution. |

## Value Objects Reference

### Artifact

Describes the target to scan. Defined in `debcraft.domain.scanner.values`.

```python
@dataclass(frozen=True)
class Artifact:
    type: ArtifactType
    path: str
    options: dict[str, str] = field(default_factory=dict)
```

| Field | Type | Description |
|-------|------|-------------|
| `type` | `ArtifactType` | The artifact format (e.g., `ArtifactType.DOCKER`, `ArtifactType.ISO`). |
| `path` | `str` | Filesystem path to the artifact file (max 4096 characters). |
| `options` | `dict[str, str]` | Scanner-specific configuration (max 64 entries). Passed from CLI options. |

### ArtifactType

Enumeration of supported artifact formats:

```python
class ArtifactType(Enum):
    DIRECTORY = "directory"
    DOCKER = "docker"
    OCI = "oci"
    ISO = "iso"
    QCOW2 = "qcow2"
    IMG = "img"
    AMI = "ami"
```

### ScanResult

The uniform result every scanner must produce. Defined in
`debcraft.domain.scanner.values`.

```python
@dataclass(frozen=True)
class ScanResult:
    packages: list[IdentifiedPackage]
    strategy: str
    diagnostics: list[str]
    duration_seconds: float
    artifact_path: str
    enriched_packages: list[EnrichedPackage] = field(default_factory=list)
```

| Field | Type | Description |
|-------|------|-------------|
| `packages` | `list[IdentifiedPackage]` | Zero or more packages identified during the scan. |
| `strategy` | `str` | How packages were identified (e.g., `"dpkg_metadata"`, `"filesystem_analysis"`). |
| `diagnostics` | `list[str]` | Warning or informational messages (e.g., missing dependencies, fallback decisions). |
| `duration_seconds` | `float` | Wall-clock scan duration (non-negative). |
| `artifact_path` | `str` | The path that was scanned (echoed from `artifact.path`). |
| `enriched_packages` | `list[EnrichedPackage]` | Packages with enrichment metadata (populated post-scan by the platform). |

### IdentifiedPackage

A single package found during scanning:

```python
@dataclass(frozen=True)
class IdentifiedPackage:
    name: str
    version: str
    architecture: str
    status: str
```

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Package name (non-empty). |
| `version` | `str` | Package version string (non-empty). |
| `architecture` | `str` | Target architecture (e.g., `"amd64"`, `"all"`). Empty string if unknown. |
| `status` | `str` | Installation status (e.g., `"installed"`, `"config-files"`). |

## Error Handling

Scanners distinguish between two categories of errors:

### Fatal Errors — ArtifactAccessError

Raise `ArtifactAccessError` when the artifact cannot be accessed at all (file does
not exist, permission denied, etc.). This exception propagates to the caller and
halts the scan:

```python
from debcraft.domain.scanner.errors import ArtifactAccessError

if not os.path.isfile(artifact.path):
    raise ArtifactAccessError(
        path=artifact.path,
        reason="File not found",
    )
```

The exception stores `path` and `reason` attributes for programmatic access.

### Graceful Degradation — Diagnostics

For non-fatal issues (missing optional dependencies, unrecognized file formats,
corrupt layers), return a `ScanResult` with an empty or partial `packages` list and
describe the problem in `diagnostics`:

```python
# Optional dependency not available — degrade gracefully
if not guestfs_available:
    return ScanResult(
        packages=[],
        strategy="unavailable",
        diagnostics=["python3-guestfs is not installed; cannot inspect disk image"],
        duration_seconds=time.time() - start,
        artifact_path=artifact.path,
    )
```

This pattern allows the scan workflow to continue processing other artifacts and
provides actionable feedback to the user.

## Testing Your Scanner

Write tests that exercise your scanner against fixture artifacts. Use the patterns
established in the debcraft test suite:

```python
"""Tests for the custom scanner."""

import pytest

from debcraft.domain.scanner.values import Artifact, ArtifactType
from debcraft.platform.contracts.workflow import (
    CancellationToken,
    ProgressReporter,
    WorkflowContext,
)

from my_package.scanners.custom import CustomScanner


class NoOpProgress(ProgressReporter):
    """No-op progress reporter for testing."""

    def report(self, percentage: float, message: str = "") -> None:
        pass


class _Stub:
    """Generic stub satisfying any attribute access."""

    def __getattr__(self, name: str) -> "_Stub":
        return self


def build_test_context() -> WorkflowContext:
    """Build a minimal WorkflowContext for tests."""
    stub = _Stub()
    return WorkflowContext(
        scope=stub,  # type: ignore[arg-type]
        cancellation_token=CancellationToken(),
        progress_reporter=NoOpProgress(),
        resource_manager=stub,  # type: ignore[arg-type]
        logger=stub,  # type: ignore[arg-type]
        event_bus=stub,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_scan_returns_packages():
    scanner = CustomScanner()
    artifact = Artifact(type=ArtifactType.DOCKER, path="fixtures/images/test.tar")
    context = build_test_context()

    result = await scanner.scan(artifact, context)

    assert result.artifact_path == artifact.path
    assert isinstance(result.packages, list)


@pytest.mark.asyncio
async def test_scan_raises_on_missing_file():
    scanner = CustomScanner()
    artifact = Artifact(type=ArtifactType.DOCKER, path="/nonexistent/path")
    context = build_test_context()

    with pytest.raises(Exception):  # ArtifactAccessError
        await scanner.scan(artifact, context)
```

Generate test fixtures using scripts under `fixtures/` rather than committing binary
files. See `fixtures/build-iso.sh` for an example of an idempotent fixture generator
that requires only standard system tools.
