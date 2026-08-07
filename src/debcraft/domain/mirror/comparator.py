"""File comparison logic for incremental repository synchronization.

Determines which remote files need downloading by comparing
SHA256 checksums against the local cache state.
"""

from __future__ import annotations

from itertools import product

from debcraft.domain.mirror.values import FileEntry, SyncDecision


class FileComparator:
    """Determines which files need downloading based on SHA256 comparison."""

    def compute_sync_decisions(
        self,
        remote_entries: list[FileEntry],
        local_checksums: dict[str, str],  # relative_path → sha256
    ) -> list[SyncDecision]:
        """Compare remote metadata against local state.

        For each remote entry, decides whether to download or skip
        based on whether the local cache has a matching SHA256 checksum.

        Args:
            remote_entries: Files listed in remote repository metadata.
            local_checksums: Mapping of relative paths to their local SHA256 digests.

        Returns:
            A list of SyncDecision objects, one per remote entry.
        """
        decisions: list[SyncDecision] = []

        for entry in remote_entries:
            local_sha256 = local_checksums.get(entry.relative_path)

            if local_sha256 is None:
                decisions.append(
                    SyncDecision(
                        file_entry=entry,
                        action="download",
                        reason="file not cached",
                    )
                )
            elif local_sha256 == entry.sha256:
                decisions.append(
                    SyncDecision(
                        file_entry=entry,
                        action="skip",
                        reason="checksum matches",
                    )
                )
            else:
                decisions.append(
                    SyncDecision(
                        file_entry=entry,
                        action="download",
                        reason="checksum differs",
                    )
                )

        return decisions


def generate_index_paths(
    components: list[str],
    architectures: list[str],
) -> list[str]:
    """Generate index file paths for all component x architecture combinations.

    Produces the Cartesian product of components and architectures,
    formatted as Debian repository index paths.

    Args:
        components: Repository components (e.g., ["main", "contrib"]).
        architectures: Target architectures (e.g., ["amd64", "arm64"]).

    Returns:
        List of paths like "{component}/binary-{architecture}/Packages.gz",
        one for each unique (component, architecture) pair.
    """
    return [
        f"{component}/binary-{architecture}/Packages.gz"
        for component, architecture in product(components, architectures)
    ]
