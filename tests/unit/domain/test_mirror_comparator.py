"""Unit tests for the mirror file comparator module.

Tests the FileComparator.compute_sync_decisions() and
generate_index_paths() functions.
"""

from __future__ import annotations

import pytest

from debcraft.domain.mirror.comparator import FileComparator, generate_index_paths
from debcraft.domain.mirror.values import FileEntry


@pytest.mark.unit
@pytest.mark.mirror
class TestFileComparatorSyncDecisions:
    """Tests for FileComparator.compute_sync_decisions()."""

    def setup_method(self) -> None:
        self.comparator = FileComparator()

    def test_skip_when_checksum_matches(self) -> None:
        """Returns skip decision when local SHA256 matches remote."""
        entry = FileEntry(
            relative_path="pool/main/a/apt/apt_2.6.1_amd64.deb",
            sha256="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            size_bytes=1024,
        )
        local_checksums = {
            "pool/main/a/apt/apt_2.6.1_amd64.deb": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        }

        decisions = self.comparator.compute_sync_decisions([entry], local_checksums)

        assert len(decisions) == 1
        assert decisions[0].action == "skip"
        assert decisions[0].reason == "checksum matches"
        assert decisions[0].file_entry is entry

    def test_download_when_file_absent(self) -> None:
        """Returns download decision when local file does not exist."""
        entry = FileEntry(
            relative_path="pool/main/a/apt/apt_2.6.1_amd64.deb",
            sha256="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            size_bytes=1024,
        )
        local_checksums: dict[str, str] = {}

        decisions = self.comparator.compute_sync_decisions([entry], local_checksums)

        assert len(decisions) == 1
        assert decisions[0].action == "download"
        assert decisions[0].reason == "file not cached"
        assert decisions[0].file_entry is entry

    def test_download_when_checksum_differs(self) -> None:
        """Returns download decision when local SHA256 differs from remote."""
        entry = FileEntry(
            relative_path="dists/stable/main/binary-amd64/Packages.gz",
            sha256="aaaa0000bbbb1111cccc2222dddd3333eeee4444ffff5555aaaa0000bbbb1111",
            size_bytes=2048,
        )
        local_checksums = {
            "dists/stable/main/binary-amd64/Packages.gz": (
                "1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff"
            ),
        }

        decisions = self.comparator.compute_sync_decisions([entry], local_checksums)

        assert len(decisions) == 1
        assert decisions[0].action == "download"
        assert decisions[0].reason == "checksum differs"
        assert decisions[0].file_entry is entry

    def test_empty_remote_entries_returns_empty_list(self) -> None:
        """Returns empty list when no remote entries are provided."""
        decisions = self.comparator.compute_sync_decisions([], {"some/path": "abc123"})
        assert decisions == []

    def test_multiple_entries_mixed_decisions(self) -> None:
        """Handles multiple entries with a mix of skip and download decisions."""
        entries = [
            FileEntry(relative_path="file_a", sha256="hash_a", size_bytes=100),
            FileEntry(relative_path="file_b", sha256="hash_b", size_bytes=200),
            FileEntry(relative_path="file_c", sha256="hash_c_new", size_bytes=300),
        ]
        local_checksums = {
            "file_a": "hash_a",  # matches → skip
            # file_b absent             → download
            "file_c": "hash_c_old",  # differs → download
        }

        decisions = self.comparator.compute_sync_decisions(entries, local_checksums)

        assert len(decisions) == 3
        assert decisions[0].action == "skip"
        assert decisions[1].action == "download"
        assert decisions[1].reason == "file not cached"
        assert decisions[2].action == "download"
        assert decisions[2].reason == "checksum differs"

    def test_one_decision_per_remote_entry(self) -> None:
        """Produces exactly one decision per remote entry."""
        entries = [FileEntry(relative_path=f"file_{i}", sha256=f"hash_{i}", size_bytes=i * 100) for i in range(5)]
        local_checksums = {f"file_{i}": f"hash_{i}" for i in range(3)}

        decisions = self.comparator.compute_sync_decisions(entries, local_checksums)
        assert len(decisions) == len(entries)


@pytest.mark.unit
@pytest.mark.mirror
class TestGenerateIndexPaths:
    """Tests for generate_index_paths()."""

    def test_single_component_single_architecture(self) -> None:
        """Single component and architecture produces one path."""
        result = generate_index_paths(["main"], ["amd64"])
        assert result == ["main/binary-amd64/Packages.gz"]

    def test_multiple_components_single_architecture(self) -> None:
        """Multiple components x single architecture."""
        result = generate_index_paths(["main", "contrib"], ["amd64"])
        assert result == [
            "main/binary-amd64/Packages.gz",
            "contrib/binary-amd64/Packages.gz",
        ]

    def test_single_component_multiple_architectures(self) -> None:
        """Single component x multiple architectures."""
        result = generate_index_paths(["main"], ["amd64", "arm64"])
        assert result == [
            "main/binary-amd64/Packages.gz",
            "main/binary-arm64/Packages.gz",
        ]

    def test_cartesian_product_count(self) -> None:
        """Result count is components x architectures."""
        components = ["main", "contrib", "non-free"]
        architectures = ["amd64", "arm64", "i386"]
        result = generate_index_paths(components, architectures)
        assert len(result) == len(components) * len(architectures)

    def test_all_pairs_unique(self) -> None:
        """All generated paths are unique."""
        components = ["main", "contrib"]
        architectures = ["amd64", "arm64", "i386"]
        result = generate_index_paths(components, architectures)
        assert len(result) == len(set(result))

    def test_empty_components_returns_empty(self) -> None:
        """Empty components list produces no paths."""
        result = generate_index_paths([], ["amd64"])
        assert result == []

    def test_empty_architectures_returns_empty(self) -> None:
        """Empty architectures list produces no paths."""
        result = generate_index_paths(["main"], [])
        assert result == []

    def test_path_format(self) -> None:
        """Generated paths follow the expected format."""
        result = generate_index_paths(["universe"], ["riscv64"])
        assert result == ["universe/binary-riscv64/Packages.gz"]
