"""Fallback package identification via filesystem path matching.

Identifies packages by matching filesystem paths against the Contents index
when dpkg metadata is unavailable. This module provides a pure async function
with no side effects beyond the port calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from debcraft.domain.scanner.ports import ContentsIndexPort, PackageLookupPort

from debcraft.domain.scanner.values import IdentifiedPackage


@dataclass(frozen=True)
class FilesystemAnalysisResult:
    """Result of filesystem-based package identification.

    Attributes:
        packages: Identified packages with status "inferred".
        diagnostics: Warnings about unresolved paths or limits.
    """

    packages: list[IdentifiedPackage] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


async def analyze_filesystem(
    file_paths: list[str],
    contents_port: ContentsIndexPort,
    package_port: PackageLookupPort,
    snapshot_id: int,
    max_paths: int = 100_000,
) -> FilesystemAnalysisResult:
    """Identify packages by matching filesystem paths against Contents index.

    Algorithm:
    1. Truncate file_paths to max_paths (record diagnostic if truncated)
    2. Batch-query ContentsIndexPort for path->package mappings
    3. Deduplicate: one IdentifiedPackage per unique package name
    4. For each unique package, query PackageLookupPort for version/arch
    5. Skip packages with no PackageInstance (record diagnostic)
    6. Set status to "inferred" for all results

    Args:
        file_paths: Observed filesystem paths in the artifact.
        contents_port: Port for Contents index lookups.
        package_port: Port for package metadata lookups.
        snapshot_id: RepositorySnapshot ID for consistent queries.
        max_paths: Maximum paths to process (default 100,000).

    Returns:
        FilesystemAnalysisResult with identified packages and diagnostics.
    """
    diagnostics: list[str] = []

    # Step 1: Truncate to max_paths if necessary
    total = len(file_paths)
    if total > max_paths:
        skipped = total - max_paths
        paths_to_process = file_paths[:max_paths]
        diagnostics.append(f"Path limit reached: processed {max_paths} of {total} paths, {skipped} skipped")
    else:
        paths_to_process = file_paths

    # Step 2: Batch-query ContentsIndexPort for path->package mappings
    path_to_package = await contents_port.find_owners(paths_to_process, snapshot_id)

    # If no contents data available, include diagnostic
    if not path_to_package:
        diagnostics.append("No contents data available: no filesystem paths matched the Contents index")
        return FilesystemAnalysisResult(packages=[], diagnostics=diagnostics)

    # Step 3: Deduplicate by package name
    unique_package_names: set[str] = set(path_to_package.values())

    # Step 4 & 5: Query PackageLookupPort for each unique package
    packages: list[IdentifiedPackage] = []
    for name in sorted(unique_package_names):
        result = await package_port.find_by_name(name, snapshot_id)
        if result is None:
            # Step 5: Skip unresolved packages with diagnostic
            diagnostics.append(f"Package '{name}' found in Contents index but no metadata available")
            continue

        version, architecture, _status = result

        # Step 6: Set status to "inferred" for all results
        packages.append(
            IdentifiedPackage(
                name=name,
                version=version,
                architecture=architecture,
                status="inferred",
            )
        )

    return FilesystemAnalysisResult(packages=packages, diagnostics=diagnostics)
