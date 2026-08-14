"""Self-contained example: scan the test fixture tarball with DockerScanner."""

from __future__ import annotations

import asyncio
import os
import sys

from debcraft.domain.scanner.values import Artifact, ArtifactType
from debcraft.infrastructure.scanners.docker import DockerScanner
from debcraft.platform.contracts.workflow import (
    CancellationToken,
    ProgressReporter,
    WorkflowContext,
)

# ---------------------------------------------------------------------------
# Step 1: File-existence check
# ---------------------------------------------------------------------------

DOCKER_TAR_PATH = "fixtures/images/test.tar"

if not os.path.isfile(DOCKER_TAR_PATH):
    print(
        f"Error: '{DOCKER_TAR_PATH}' not found.\nRun fixtures/build-docker.sh to generate the fixture tarball.",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Step 2: Implement stub ContentsIndexPort and PackageLookupPort
# ---------------------------------------------------------------------------


class StubContentsIndex:
    """Stub ContentsIndexPort — returns empty mapping (never reached here)."""

    async def find_owners(self, file_paths: list[str], snapshot_id: int) -> dict[str, str]:  # noqa: D102
        return {}


class StubPackageLookup:
    """Stub PackageLookupPort — returns None (never reached here)."""

    async def find_by_name(self, package_name: str, snapshot_id: int) -> tuple[str, str, str] | None:  # noqa: D102
        return None


# ---------------------------------------------------------------------------
# Step 3: Build a minimal WorkflowContext stub
# ---------------------------------------------------------------------------


class NoOpProgress(ProgressReporter):
    """No-op progress reporter — prints nothing."""

    def report(self, percentage: float, message: str = "") -> None:  # noqa: D102
        pass


class _Stub:
    """Generic stub satisfying any attribute access (scope, resources, etc.)."""

    def __getattr__(self, name: str) -> _Stub:
        return self


def build_context() -> WorkflowContext:
    """Construct a WorkflowContext with no-op/stub services."""
    stub = _Stub()
    return WorkflowContext(
        scope=stub,  # type: ignore[arg-type]
        cancellation_token=CancellationToken(),
        progress_reporter=NoOpProgress(),
        resource_manager=stub,  # type: ignore[arg-type]
        logger=stub,  # type: ignore[arg-type]
        event_bus=stub,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Step 4: Construct Artifact and invoke the scanner
# ---------------------------------------------------------------------------


async def main() -> None:  # noqa: D103
    # Instantiate the port stubs
    contents_port = StubContentsIndex()
    package_port = StubPackageLookup()

    # Build the scanner with its dependency ports
    scanner = DockerScanner(contents_port, package_port)

    # Construct the artifact pointing at our fixture tarball
    artifact = Artifact(type=ArtifactType.DOCKER, path=DOCKER_TAR_PATH)

    # Create a minimal workflow context (no-op progress, uncancelled token)
    context = build_context()

    # Run the scan — this reads manifest.json, merges layers, parses dpkg/status
    result = await scanner.scan(artifact, context)

    # Print each identified package
    for pkg in result.packages:
        print(f"{pkg.name} {pkg.version} {pkg.architecture}")


# Entry point — run the async scan with asyncio
if __name__ == "__main__":
    asyncio.run(main())
