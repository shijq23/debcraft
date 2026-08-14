"""Self-contained example: scan the test fixture IMG with IMGScanner."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

from debcraft.domain.scanner.ports import GuestfsInspector  # noqa: F401
from debcraft.domain.scanner.values import Artifact, ArtifactType
from debcraft.infrastructure.scanners.img import IMGScanner
from debcraft.platform.contracts.workflow import (
    CancellationToken,
    ProgressReporter,
    WorkflowContext,
)

# ---------------------------------------------------------------------------
# Step 1: File-existence check
# ---------------------------------------------------------------------------

IMG_PATH = "fixtures/images/test.img"

if not os.path.isfile(IMG_PATH):
    print(
        f"Error: '{IMG_PATH}' not found.\nRun fixtures/build-img.sh to generate the fixture image.",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Step 2: Implement a stub GuestfsInspector using debugfs
# ---------------------------------------------------------------------------


class DebugfsGuestfsInspector:
    """GuestfsInspector stub backed by debugfs (e2fsprogs).

    Reads files from an ext4 image without mounting, simulating what
    libguestfs would do. Only supports the operations needed for the
    dpkg status extraction path.
    """

    def __init__(self) -> None:  # noqa: D107
        self._image_path: str | None = None

    def open_image(self, path: str, readonly: bool = True) -> None:
        """Open a disk image for inspection."""
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Image not found: {path}")
        self._image_path = path

    def inspect_os(self) -> list[str]:
        """Return a synthetic root device path.

        In a real guestfs implementation this would inspect the partition
        table. For our ext4 fixture (no partition table, bare filesystem)
        we return a single synthetic device.
        """
        return ["/dev/sda"]

    def mount_readonly(self, device: str, mountpoint: str) -> None:
        """No-op mount — debugfs reads directly from the image file."""
        pass

    def read_file(self, path: str) -> bytes:
        """Read a file from the ext4 image using debugfs.

        Uses 'debugfs -R "cat <path>" <image>' to extract file contents
        without mounting.
        """
        if self._image_path is None:
            raise RuntimeError("Image not opened")
        result = subprocess.run(  # noqa: S603
            ["debugfs", "-R", f"cat {path}", self._image_path],  # noqa: S607
            capture_output=True,
        )
        if result.returncode != 0 or not result.stdout:
            raise FileNotFoundError(f"{path} not found in image")
        return result.stdout

    def ls(self, directory: str) -> list[str]:
        """List directory contents from the ext4 image using debugfs."""
        if self._image_path is None:
            raise RuntimeError("Image not opened")
        result = subprocess.run(  # noqa: S603
            ["debugfs", "-R", f"ls {directory}", self._image_path],  # noqa: S607
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise FileNotFoundError(f"{directory} not found in image")
        entries = []
        for line in result.stdout.strip().split("\n"):
            # debugfs ls output has inode numbers followed by names
            parts = line.split()
            if parts:
                name = parts[-1]
                if name not in (".", ".."):
                    entries.append(name)
        return entries

    def close(self) -> None:
        """Release resources."""
        self._image_path = None


# ---------------------------------------------------------------------------
# Step 3: Implement stub ContentsIndexPort and PackageLookupPort
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
# Step 4: Build a minimal WorkflowContext stub
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
# Step 5: Construct Artifact and invoke the scanner
# ---------------------------------------------------------------------------


async def main() -> None:  # noqa: D103
    # Instantiate all dependencies
    guestfs = DebugfsGuestfsInspector()
    contents_port = StubContentsIndex()
    package_port = StubPackageLookup()

    # Build the scanner with its dependency ports
    scanner = IMGScanner(guestfs, contents_port, package_port)

    # Construct the artifact pointing at our fixture IMG
    artifact = Artifact(type=ArtifactType.IMG, path=IMG_PATH)

    # Create a minimal workflow context (no-op progress, uncancelled token)
    context = build_context()

    # Run the scan — this reads the ext4 image and parses var/lib/dpkg/status
    result = await scanner.scan(artifact, context)

    # Print each identified package
    for pkg in result.packages:
        print(f"{pkg.name} {pkg.version} {pkg.architecture}")


# Entry point — run the async scan with asyncio
if __name__ == "__main__":
    asyncio.run(main())
