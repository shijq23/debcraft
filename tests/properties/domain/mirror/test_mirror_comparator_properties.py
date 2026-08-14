"""Property-based tests for file comparator and index path generation.

**Validates: Requirements 1.3, 2.1, 2.2, 2.3, 3.1, 3.2**

Property 3: Matching checksums produce skip decisions.
For any FileEntry and local cache state where the locally stored SHA256
for that file's relative path equals the FileEntry's sha256, the
FileComparator SHALL produce a SyncDecision with action="skip".

Property 4: Mismatched or absent checksums produce download decisions.
For any FileEntry and local cache state where either no local file exists
for that relative path, or the local SHA256 differs from the FileEntry's
sha256, the FileComparator SHALL produce a SyncDecision with action="download".

Property 5: Component × architecture Cartesian product path generation.
For any non-empty list of components and non-empty list of architectures,
the generated index paths SHALL contain exactly len(components) * len(architectures)
entries, and each entry SHALL correspond to a unique (component, architecture)
pair formatted as "{component}/binary-{architecture}/Packages.gz".
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.domain.mirror.comparator import FileComparator, generate_index_paths
from debcraft.domain.mirror.values import FileEntry

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Valid SHA256 hex strings (64 hex characters)
_sha256_strategy = st.text(
    alphabet="0123456789abcdef",
    min_size=64,
    max_size=64,
)

# Relative paths: non-empty strings with path-like characters
_relative_path_strategy = st.text(
    st.characters(whitelist_categories=("L", "N"), whitelist_characters="/-_."),
    min_size=1,
    max_size=100,
).filter(lambda s: len(s.strip()) > 0)

# File sizes
_size_strategy = st.integers(min_value=0, max_value=10**9)


def _file_entry_strategy() -> st.SearchStrategy[FileEntry]:
    """Generate a valid FileEntry with random sha256, path, and size."""
    return st.builds(
        FileEntry,
        relative_path=_relative_path_strategy,
        sha256=_sha256_strategy,
        size_bytes=_size_strategy,
    )


# Non-empty strings for components and architectures
_component_strategy = st.text(
    st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_."),
    min_size=1,
    max_size=30,
).filter(lambda s: len(s.strip()) > 0)

_architecture_strategy = st.text(
    st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_."),
    min_size=1,
    max_size=30,
).filter(lambda s: len(s.strip()) > 0)


# ---------------------------------------------------------------------------
# Property 3: Matching checksums produce skip decisions
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty3MatchingChecksumsSkip:
    """Property 3: Matching checksums produce skip decisions.

    For any FileEntry and local cache state where the locally stored SHA256
    for that file's relative path equals the FileEntry's sha256, the
    FileComparator SHALL produce a SyncDecision with action="skip".
    """

    @given(entry=_file_entry_strategy())
    def test_matching_sha256_produces_skip(self, entry: FileEntry) -> None:
        """**Validates: Requirements 1.3**.

        When local checksums contain the same sha256 for a file's path,
        the comparator produces a skip decision.
        """
        comparator = FileComparator()
        local_checksums = {entry.relative_path: entry.sha256}

        decisions = comparator.compute_sync_decisions([entry], local_checksums)

        assert len(decisions) == 1
        assert decisions[0].action == "skip"
        assert decisions[0].file_entry == entry

    @given(
        entry=_file_entry_strategy(),
        extra_paths=st.dictionaries(
            keys=_relative_path_strategy,
            values=_sha256_strategy,
            min_size=0,
            max_size=5,
        ),
    )
    def test_matching_sha256_skip_with_other_entries_present(
        self, entry: FileEntry, extra_paths: dict[str, str]
    ) -> None:
        """**Validates: Requirements 2.2, 3.2**.

        A matching checksum still produces skip even when other
        unrelated entries exist in the local checksums map.
        """
        comparator = FileComparator()
        # Ensure the entry's path maps to its own sha256
        local_checksums = {**extra_paths, entry.relative_path: entry.sha256}

        decisions = comparator.compute_sync_decisions([entry], local_checksums)

        assert len(decisions) == 1
        assert decisions[0].action == "skip"


# ---------------------------------------------------------------------------
# Property 4: Mismatched or absent checksums produce download decisions
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty4MismatchedOrAbsentChecksumsDownload:
    """Property 4: Mismatched or absent checksums produce download decisions.

    For any FileEntry and local cache state where either no local file exists
    for that relative path, or the local SHA256 differs from the FileEntry's
    sha256, the FileComparator SHALL produce a SyncDecision with action="download".
    """

    @given(entry=_file_entry_strategy())
    def test_absent_path_produces_download(self, entry: FileEntry) -> None:
        """**Validates: Requirements 2.1**.

        When the local checksums dict has no entry for the file's path,
        the comparator produces a download decision.
        """
        comparator = FileComparator()
        local_checksums: dict[str, str] = {}

        decisions = comparator.compute_sync_decisions([entry], local_checksums)

        assert len(decisions) == 1
        assert decisions[0].action == "download"
        assert decisions[0].file_entry == entry

    @given(
        entry=_file_entry_strategy(),
        different_sha256=_sha256_strategy,
    )
    def test_mismatched_sha256_produces_download(self, entry: FileEntry, different_sha256: str) -> None:
        """**Validates: Requirements 3.1**.

        When the local SHA256 differs from the remote FileEntry's sha256,
        the comparator produces a download decision.
        """
        from hypothesis import assume

        # Ensure the generated sha256 actually differs
        assume(different_sha256 != entry.sha256)

        comparator = FileComparator()
        local_checksums = {entry.relative_path: different_sha256}

        decisions = comparator.compute_sync_decisions([entry], local_checksums)

        assert len(decisions) == 1
        assert decisions[0].action == "download"
        assert decisions[0].file_entry == entry

    @given(
        entry=_file_entry_strategy(),
        other_paths=st.dictionaries(
            keys=_relative_path_strategy,
            values=_sha256_strategy,
            min_size=1,
            max_size=5,
        ),
    )
    def test_absent_path_with_other_entries_produces_download(
        self, entry: FileEntry, other_paths: dict[str, str]
    ) -> None:
        """**Validates: Requirements 2.1**.

        Even when other paths exist in local checksums, if the specific
        file's path is absent, the comparator produces a download decision.
        """
        from hypothesis import assume

        # Ensure the entry's path isn't accidentally in other_paths
        assume(entry.relative_path not in other_paths)

        comparator = FileComparator()

        decisions = comparator.compute_sync_decisions([entry], other_paths)

        assert len(decisions) == 1
        assert decisions[0].action == "download"


# ---------------------------------------------------------------------------
# Property 5: Component × architecture Cartesian product path generation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty5CartesianProductPathGeneration:
    """Property 5: Component × architecture Cartesian product path generation.

    For any non-empty list of components and non-empty list of architectures,
    the generated index paths SHALL contain exactly len(components) * len(architectures)
    entries, and each entry SHALL correspond to a unique (component, architecture)
    pair formatted as "{component}/binary-{architecture}/Packages.gz".
    """

    @given(
        components=st.lists(_component_strategy, min_size=1, max_size=10),
        architectures=st.lists(_architecture_strategy, min_size=1, max_size=10),
    )
    def test_path_count_equals_cartesian_product_size(self, components: list[str], architectures: list[str]) -> None:
        """**Validates: Requirements 2.3**.

        The number of generated paths equals len(components) * len(architectures).
        """
        paths = generate_index_paths(components, architectures)

        expected_count = len(components) * len(architectures)
        assert len(paths) == expected_count

    @given(
        components=st.lists(_component_strategy, min_size=1, max_size=10, unique=True),
        architectures=st.lists(_architecture_strategy, min_size=1, max_size=10, unique=True),
    )
    def test_all_paths_are_unique(self, components: list[str], architectures: list[str]) -> None:
        """**Validates: Requirements 2.3**.

        When components and architectures are individually unique,
        all generated paths are unique.
        """
        paths = generate_index_paths(components, architectures)

        assert len(paths) == len(set(paths))

    @given(
        components=st.lists(_component_strategy, min_size=1, max_size=10, unique=True),
        architectures=st.lists(_architecture_strategy, min_size=1, max_size=10, unique=True),
    )
    def test_each_path_follows_expected_format(self, components: list[str], architectures: list[str]) -> None:
        """**Validates: Requirements 2.3**.

        Each generated path matches the format
        "{component}/binary-{architecture}/Packages.gz".
        """
        paths = generate_index_paths(components, architectures)

        for path in paths:
            assert path.endswith("/Packages.gz"), f"Path '{path}' does not end with /Packages.gz"
            # Extract component and architecture from the path
            # Format: {component}/binary-{architecture}/Packages.gz
            parts = path.split("/")
            assert len(parts) >= 3, f"Path '{path}' has fewer than 3 segments"
            assert parts[-1] == "Packages.gz"
            arch_segment = parts[-2]
            assert arch_segment.startswith("binary-"), f"Segment '{arch_segment}' does not start with 'binary-'"

    @given(
        components=st.lists(_component_strategy, min_size=1, max_size=10, unique=True),
        architectures=st.lists(_architecture_strategy, min_size=1, max_size=10, unique=True),
    )
    def test_every_component_architecture_pair_represented(
        self, components: list[str], architectures: list[str]
    ) -> None:
        """**Validates: Requirements 2.3**.

        Every (component, architecture) pair from the input lists has
        a corresponding path in the output.
        """
        paths = generate_index_paths(components, architectures)

        expected_paths = {f"{comp}/binary-{arch}/Packages.gz" for comp in components for arch in architectures}

        assert set(paths) == expected_paths
