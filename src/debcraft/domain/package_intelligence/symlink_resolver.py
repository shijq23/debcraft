"""Resolves copyright symlinks using Contents file-ownership data."""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING

from debcraft.domain.package_intelligence.values import SymlinkResolutionResult

if TYPE_CHECKING:
    from debcraft.domain.package_intelligence.ports import ContentsLookupPort


class SymlinkResolver:
    """Resolves copyright symlinks using Contents file-ownership data."""

    MAX_RESOLUTION_DEPTH: int = 10

    def __init__(self, contents_lookup: ContentsLookupPort) -> None:
        """Initialize the resolver with a contents lookup port."""
        self._contents_lookup = contents_lookup

    def resolve(self, symlink_target: str, source_dir: str) -> SymlinkResolutionResult:
        """Resolve a symlink target to the owning package's copyright.

        Raises no exceptions; returns failure result on unresolvable links.
        """
        visited: set[str] = set()
        current_target = symlink_target
        current_source_dir = source_dir

        for _hop in range(self.MAX_RESOLUTION_DEPTH):
            # Resolve the path: relative paths join with source_dir, absolute used directly
            if posixpath.isabs(current_target):
                resolved_path = current_target
            else:
                resolved_path = posixpath.normpath(posixpath.join(current_source_dir, current_target))

            # Cycle detection
            if resolved_path in visited:
                return SymlinkResolutionResult(
                    resolved=False,
                    failure_reason=f"Circular symlink chain detected at: {resolved_path}",
                )
            visited.add(resolved_path)

            # Look up the owner of the resolved path
            owner = self._contents_lookup.find_owner(resolved_path)
            if owner is None:
                return SymlinkResolutionResult(
                    resolved=False,
                    failure_reason=f"Cannot resolve path: no package owns {resolved_path}",
                )

            # Found an owner — retrieve the copyright content
            copyright_content = self._contents_lookup.get_copyright_content(owner)
            if copyright_content is None:
                return SymlinkResolutionResult(
                    resolved=False,
                    failure_reason=(f"Package {owner} owns {resolved_path} but has no copyright content"),
                )

            # Check if the copyright content is itself a symlink target
            # (another path to follow in the chain)
            if _is_symlink_path(copyright_content):
                # Follow the chain: use the resolved path's directory as the
                # new source directory for relative path resolution
                current_source_dir = posixpath.dirname(resolved_path)
                current_target = copyright_content
                continue

            # Successfully resolved to actual copyright content
            return SymlinkResolutionResult(
                resolved=True,
                target_path=resolved_path,
                owning_package=owner,
                copyright_content=copyright_content,
            )

        # Exceeded maximum resolution depth
        return SymlinkResolutionResult(
            resolved=False,
            failure_reason=(f"Maximum resolution depth ({self.MAX_RESOLUTION_DEPTH}) exceeded"),
        )


def _is_symlink_path(content: str) -> bool:
    """Heuristic to detect if copyright content is actually a symlink path.

    Real copyright files are multi-line documents. A symlink target is typically
    a single-line path (absolute or relative).
    """
    stripped = content.strip()
    if not stripped or "\n" in stripped:
        return False
    # Looks like a path: starts with / or ../ or contains path separators
    # that suggest it's a symlink target rather than copyright text
    return stripped.startswith("/") or stripped.startswith("../") or stripped.startswith("./")
