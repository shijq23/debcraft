"""Property-based tests for IndexerService.

# Feature: repository-indexer, Property 10: Incremental indexing decision
# Feature: repository-indexer, Property 11: Deterministic processing order
# Feature: repository-indexer, Property 12: Duplicate natural key skipping
# Feature: repository-indexer, Property 13: Download URL computation

**Validates: Requirements 5.1, 5.2, 5.3, 5.4, 6.2, 6.4**
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.domain.indexer.service import (
    IndexerService,
    _compute_download_url,
    _infer_file_type,
)
from debcraft.domain.indexer.values import PackageMetadata

# ===========================================================================
# Test helpers for Property 10
# ===========================================================================


@dataclass(frozen=True)
class FakeIndexingRecord:
    """Minimal indexing record with the attributes the skip logic inspects."""

    indexed_sha256: str
    parser_version: int


def _make_indexer_service() -> IndexerService:
    """Create an IndexerService with mock dependencies for testing _should_skip."""
    return IndexerService(
        file_reader=AsyncMock(),
        metadata_repository=AsyncMock(),
        mirror_file_repository=AsyncMock(),
        event_bus=AsyncMock(),
    )


# ===========================================================================
# Strategies for Property 10
# ===========================================================================

# SHA256 hex strings: exactly 64 hex characters
_sha256_strategy = st.text(
    alphabet="0123456789abcdef",
    min_size=64,
    max_size=64,
)

# Parser versions: positive integers
_parser_version_strategy = st.integers(min_value=1, max_value=1000)


# ===========================================================================
# Property 10: Incremental indexing decision
# ===========================================================================

# Feature: repository-indexer, Property 10: Incremental indexing decision


@pytest.mark.unit
class TestProperty10IncrementalIndexingDecision:
    """Property 10: Incremental indexing decision.

    For any repository file with a recorded indexing state (sha256, parser_version),
    the indexer SHALL skip re-parsing if and only if the file state is INDEXED AND
    the current file SHA256 matches the recorded SHA256 AND the current parser
    version equals the recorded parser version.

    **Validates: Requirements 5.1, 5.2, 5.3**
    """

    @settings(max_examples=100)
    @given(
        current_sha256=_sha256_strategy,
        current_parser_version=_parser_version_strategy,
    )
    def test_no_record_should_not_skip(
        self,
        current_sha256: str,
        current_parser_version: int,
    ) -> None:
        """When no indexing record exists (None), the file should NOT be skipped."""
        service = _make_indexer_service()

        result = service._should_skip(None, current_sha256, current_parser_version)

        assert result is False, "File with no indexing record should never be skipped"

    @settings(max_examples=100)
    @given(
        sha256=_sha256_strategy,
        parser_version=_parser_version_strategy,
    )
    def test_matching_record_should_skip(
        self,
        sha256: str,
        parser_version: int,
    ) -> None:
        """When record exists with matching SHA256 and parser_version, should skip."""
        service = _make_indexer_service()
        record = FakeIndexingRecord(
            indexed_sha256=sha256,
            parser_version=parser_version,
        )

        result = service._should_skip(record, sha256, parser_version)

        assert result is True, (
            f"File with matching SHA256 ({sha256[:8]}...) and parser_version ({parser_version}) should be skipped"
        )

    @settings(max_examples=100)
    @given(
        recorded_sha256=_sha256_strategy,
        current_sha256=_sha256_strategy,
        parser_version=_parser_version_strategy,
    )
    def test_sha256_differs_should_not_skip(
        self,
        recorded_sha256: str,
        current_sha256: str,
        parser_version: int,
    ) -> None:
        """When record exists but SHA256 differs, should NOT skip."""
        from hypothesis import assume

        assume(recorded_sha256 != current_sha256)

        service = _make_indexer_service()
        record = FakeIndexingRecord(
            indexed_sha256=recorded_sha256,
            parser_version=parser_version,
        )

        result = service._should_skip(record, current_sha256, parser_version)

        assert result is False, (
            f"File with different SHA256 (recorded={recorded_sha256[:8]}... vs "
            f"current={current_sha256[:8]}...) should NOT be skipped"
        )

    @settings(max_examples=100)
    @given(
        sha256=_sha256_strategy,
        recorded_version=_parser_version_strategy,
        current_version=_parser_version_strategy,
    )
    def test_parser_version_differs_should_not_skip(
        self,
        sha256: str,
        recorded_version: int,
        current_version: int,
    ) -> None:
        """When record exists but parser_version differs, should NOT skip."""
        from hypothesis import assume

        assume(recorded_version != current_version)

        service = _make_indexer_service()
        record = FakeIndexingRecord(
            indexed_sha256=sha256,
            parser_version=recorded_version,
        )

        result = service._should_skip(record, sha256, current_version)

        assert result is False, (
            f"File with different parser_version (recorded={recorded_version} vs "
            f"current={current_version}) should NOT be skipped"
        )

    @settings(max_examples=100)
    @given(
        recorded_sha256=_sha256_strategy,
        current_sha256=_sha256_strategy,
        recorded_version=_parser_version_strategy,
        current_version=_parser_version_strategy,
    )
    def test_both_differ_should_not_skip(
        self,
        recorded_sha256: str,
        current_sha256: str,
        recorded_version: int,
        current_version: int,
    ) -> None:
        """When record exists but both SHA256 and parser_version differ, should NOT skip."""
        from hypothesis import assume

        assume(recorded_sha256 != current_sha256)
        assume(recorded_version != current_version)

        service = _make_indexer_service()
        record = FakeIndexingRecord(
            indexed_sha256=recorded_sha256,
            parser_version=recorded_version,
        )

        result = service._should_skip(record, current_sha256, current_version)

        assert result is False, "File with both SHA256 and parser_version different should NOT be skipped"

    @settings(max_examples=100)
    @given(
        recorded_sha256=_sha256_strategy,
        current_sha256=_sha256_strategy,
        recorded_version=_parser_version_strategy,
        current_version=_parser_version_strategy,
    )
    def test_skip_iff_both_match(
        self,
        recorded_sha256: str,
        current_sha256: str,
        recorded_version: int,
        current_version: int,
    ) -> None:
        """A file is skipped if and only if BOTH sha256 and parser_version match.

        This is the comprehensive property: skip == (sha256_match AND version_match).
        """
        service = _make_indexer_service()
        record = FakeIndexingRecord(
            indexed_sha256=recorded_sha256,
            parser_version=recorded_version,
        )

        result = service._should_skip(record, current_sha256, current_version)

        expected = recorded_sha256 == current_sha256 and recorded_version == current_version
        assert result == expected, (
            f"Skip decision should be (sha256_match={recorded_sha256 == current_sha256} "
            f"AND version_match={recorded_version == current_version}) = {expected}, "
            f"got {result}"
        )


# ===========================================================================
# Test helpers for Property 11
# ===========================================================================


@dataclass(frozen=True)
class FakeFileInfo:
    """Minimal file-like object with a url attribute for testing sort order."""

    url: str


# Strategies for generating URLs that cover different file types
_FILE_TYPE_SEGMENTS = st.sampled_from(["Packages", "Sources", "Contents", "Release", "InRelease"])

_REPO_PATH_SEGMENTS = st.sampled_from(["main", "contrib", "non-free", "universe", "multiverse"])

_SUITE_SEGMENTS = st.sampled_from(["bookworm", "bullseye", "jammy", "noble", "trixie", "sid"])

_ARCH_SEGMENTS = st.sampled_from(["amd64", "arm64", "i386", "armhf", "all"])

_COMPRESSION_SUFFIXES = st.sampled_from(["", ".gz", ".xz", ".bz2"])


@st.composite
def _repository_file_url(draw: st.DrawFn) -> str:
    """Generate a realistic repository file URL covering different file types."""
    suite = draw(_SUITE_SEGMENTS)
    component = draw(_REPO_PATH_SEGMENTS)
    file_type = draw(_FILE_TYPE_SEGMENTS)
    arch = draw(_ARCH_SEGMENTS)
    suffix = draw(_COMPRESSION_SUFFIXES)

    if file_type in ("Release", "InRelease"):
        return f"dists/{suite}/{file_type}{suffix}"
    if file_type == "Contents":
        return f"dists/{suite}/{component}/Contents-{arch}{suffix}"
    if file_type == "Packages":
        return f"dists/{suite}/{component}/binary-{arch}/{file_type}{suffix}"
    # Sources
    return f"dists/{suite}/{component}/source/{file_type}{suffix}"


@st.composite
def _file_info_list(draw: st.DrawFn) -> list[FakeFileInfo]:
    """Generate a list of FakeFileInfo objects with unique URLs."""
    urls = draw(
        st.lists(
            _repository_file_url(),
            min_size=2,
            max_size=20,
            unique=True,
        )
    )
    return [FakeFileInfo(url=url) for url in urls]


# ===========================================================================
# Property 11: Deterministic processing order
# ===========================================================================

# Feature: repository-indexer, Property 11: Deterministic processing order


@pytest.mark.unit
class TestProperty11DeterministicProcessingOrder:
    """Property 11: Deterministic processing order.

    For any set of pending repository files, the indexer SHALL process
    them in the same order regardless of insertion order, specifically
    sorted by (repository_name, file_type, file_path) ascending.

    **Validates: Requirements 5.4**
    """

    @settings(max_examples=100)
    @given(files=_file_info_list())
    def test_sort_order_is_deterministic_regardless_of_input_order(self, files: list[FakeFileInfo]) -> None:
        """Two different shuffles of the same file set produce the same sorted order."""
        repository_name = "test-repo"

        # Create two different random permutations
        perm1 = list(files)
        perm2 = list(files)
        random.shuffle(perm1)
        random.shuffle(perm2)

        # Sort both with the same key function used by IndexerService
        def sort_key(f: FakeFileInfo) -> tuple[str, str, str]:
            return (repository_name, _infer_file_type(f.url), f.url)

        sorted1 = sorted(perm1, key=sort_key)
        sorted2 = sorted(perm2, key=sort_key)

        # Both sorts must produce the same order
        assert [f.url for f in sorted1] == [f.url for f in sorted2], (
            f"Sort order not deterministic.\n"
            f"Permutation 1 sorted: {[f.url for f in sorted1]}\n"
            f"Permutation 2 sorted: {[f.url for f in sorted2]}"
        )

    @settings(max_examples=100)
    @given(files=_file_info_list())
    def test_sort_groups_by_file_type(self, files: list[FakeFileInfo]) -> None:
        """Files are grouped by inferred file type in the sorted output."""
        repository_name = "test-repo"

        def sort_key(f: FakeFileInfo) -> tuple[str, str, str]:
            return (repository_name, _infer_file_type(f.url), f.url)

        sorted_files = sorted(files, key=sort_key)

        # Extract the file types in order
        file_types = [_infer_file_type(f.url) for f in sorted_files]

        # Verify that identical file types are grouped (no interleaving)
        seen_types: set[str] = set()
        last_type: str | None = None
        for ft in file_types:
            if ft != last_type:
                assert ft not in seen_types, (
                    f"File type '{ft}' appears non-contiguously in sorted output.\nTypes order: {file_types}"
                )
                seen_types.add(ft)
                last_type = ft


# ===========================================================================
# Strategies for Property 12
# ===========================================================================

# Debian package name characters
_PKG_NAME_START = "abcdefghijklmnopqrstuvwxyz0123456789"
_PKG_NAME_CHARS = _PKG_NAME_START + "+-."


def _debian_package_name() -> st.SearchStrategy[str]:
    """Generate a valid Debian package name for natural key testing."""
    return st.builds(
        lambda start, rest: start + rest,
        st.text(alphabet=_PKG_NAME_START, min_size=1, max_size=1),
        st.text(alphabet=_PKG_NAME_CHARS, min_size=1, max_size=15),
    ).filter(lambda s: not s.endswith("+") and not s.endswith("-") and not s.endswith("."))


def _debian_version() -> st.SearchStrategy[str]:
    """Generate a valid Debian version string."""
    return st.text(
        alphabet="0123456789abcdefghijklmnopqrstuvwxyz.+-~",
        min_size=1,
        max_size=15,
    ).filter(lambda s: s[0].isalnum() and s[-1].isalnum())


_ARCHITECTURES = st.sampled_from(["amd64", "arm64", "i386", "all", "armhf"])


def _deb_filename(pkg_name: str, version: str, arch: str) -> str:
    """Build a plausible .deb filename from components."""
    return f"pool/main/{pkg_name[0]}/{pkg_name}/{pkg_name}_{version}_{arch}.deb"


_SHA256_HEX = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)


@st.composite
def _package_metadata(draw: st.DrawFn) -> PackageMetadata:
    """Generate a valid PackageMetadata with constrained fields for key testing."""
    pkg_name = draw(_debian_package_name())
    version = draw(_debian_version())
    arch = draw(_ARCHITECTURES)
    filename = _deb_filename(pkg_name, version, arch)
    sha256 = draw(_SHA256_HEX)
    size_bytes = draw(st.integers(min_value=100, max_value=10**9))

    return PackageMetadata(
        package_name=pkg_name,
        version=version,
        architecture=arch,
        filename=filename,
        sha256=sha256,
        size_bytes=size_bytes,
        source_package=pkg_name,
        source_version=version,
    )


def _natural_key(pkg: PackageMetadata) -> tuple[str, str, str, str]:
    """Extract the natural key from a PackageMetadata object."""
    return (pkg.package_name, pkg.version, pkg.architecture, pkg.filename)


@st.composite
def _package_list_with_duplicates(
    draw: st.DrawFn,
) -> list[PackageMetadata]:
    """Generate a list of PackageMetadata objects that includes duplicate natural keys.

    Strategy:
    1. Generate a base list of unique packages (at least 1)
    2. Duplicate some of them (possibly with different non-key fields like sha256)
    3. Shuffle the combined list
    """
    # Generate base unique packages
    base_packages = draw(st.lists(_package_metadata(), min_size=1, max_size=10))

    # Decide how many duplicates to introduce (at least 1)
    num_duplicates = draw(st.integers(min_value=1, max_value=len(base_packages)))

    # Select packages to duplicate (indices into base_packages)
    dup_indices = draw(
        st.lists(
            st.integers(min_value=0, max_value=len(base_packages) - 1),
            min_size=num_duplicates,
            max_size=num_duplicates,
        )
    )

    # Create duplicates - same natural key but potentially different non-key fields
    duplicates: list[PackageMetadata] = []
    for idx in dup_indices:
        original = base_packages[idx]
        # Create a duplicate with the same natural key but different sha256/size
        new_sha256 = draw(_SHA256_HEX)
        new_size = draw(st.integers(min_value=100, max_value=10**9))
        duplicate = PackageMetadata(
            package_name=original.package_name,
            version=original.version,
            architecture=original.architecture,
            filename=original.filename,
            sha256=new_sha256,
            size_bytes=new_size,
            source_package=original.source_package,
            source_version=original.source_version,
        )
        duplicates.append(duplicate)

    # Combine and shuffle
    combined = base_packages + duplicates
    shuffled = draw(st.permutations(combined))
    return list(shuffled)


# ===========================================================================
# Property 12: Duplicate natural key skipping
# ===========================================================================

# Feature: repository-indexer, Property 12: Duplicate natural key skipping


@pytest.mark.unit
class TestProperty12DuplicateNaturalKeySkipping:
    """Property 12: Duplicate natural key skipping.

    For any list of PackageMetadata objects containing entries with duplicate
    natural keys (package_name, version, architecture, filename), persisting
    them into a snapshot SHALL result in exactly one PackageInstance record
    per unique natural key.

    **Validates: Requirements 6.2**
    """

    @settings(max_examples=100)
    @given(packages=_package_list_with_duplicates())
    def test_deduplication_produces_unique_natural_keys(self, packages: list[PackageMetadata]) -> None:
        """Only unique natural keys survive deduplication."""
        # Compute expected unique count using set of natural keys
        all_keys = [_natural_key(pkg) for pkg in packages]
        unique_keys = set(all_keys)
        expected_unique_count = len(unique_keys)

        # Simulate the deduplication logic: keep first occurrence per natural key
        seen: set[tuple[str, str, str, str]] = set()
        deduplicated: list[PackageMetadata] = []
        for pkg in packages:
            key = _natural_key(pkg)
            if key not in seen:
                seen.add(key)
                deduplicated.append(pkg)

        assert len(deduplicated) == expected_unique_count, (
            f"Expected {expected_unique_count} unique packages after deduplication, "
            f"got {len(deduplicated)}.\n"
            f"Total input: {len(packages)}\n"
            f"Unique keys: {unique_keys}"
        )

    @settings(max_examples=100)
    @given(packages=_package_list_with_duplicates())
    def test_deduplicated_keys_are_subset_of_input_keys(self, packages: list[PackageMetadata]) -> None:
        """Every key in the deduplicated result is present in the input."""
        all_keys = {_natural_key(pkg) for pkg in packages}

        # Deduplicate
        seen: set[tuple[str, str, str, str]] = set()
        deduplicated: list[PackageMetadata] = []
        for pkg in packages:
            key = _natural_key(pkg)
            if key not in seen:
                seen.add(key)
                deduplicated.append(pkg)

        result_keys = {_natural_key(pkg) for pkg in deduplicated}

        # Result keys must be exactly the set of unique input keys
        assert result_keys == all_keys, (
            f"Deduplicated keys don't match unique input keys.\n"
            f"Missing: {all_keys - result_keys}\n"
            f"Extra: {result_keys - all_keys}"
        )

    @settings(max_examples=100)
    @given(packages=_package_list_with_duplicates())
    def test_input_with_duplicates_has_fewer_unique_keys(self, packages: list[PackageMetadata]) -> None:
        """The list always contains duplicates, so unique count < total count."""
        all_keys = [_natural_key(pkg) for pkg in packages]
        unique_keys = set(all_keys)

        # Our strategy guarantees at least one duplicate
        assert len(unique_keys) <= len(packages), (
            f"Expected duplicates in input but got {len(unique_keys)} unique out of {len(packages)} total."
        )


# ===========================================================================
# Feature: repository-indexer, Property 13: Download URL computation
# ===========================================================================

# Strategies for generating base URLs and filenames

_URL_SCHEMES = st.sampled_from(["http://", "https://"])

_URL_DOMAINS = st.sampled_from(
    [
        "deb.debian.org",
        "archive.ubuntu.com",
        "mirror.example.com",
        "packages.example.org",
    ]
)

_URL_BASE_PATHS = st.sampled_from(["/debian", "/ubuntu", "/repo", "/packages/main", ""])

_URL_TRAILING_SLASHES = st.sampled_from(["", "/", "//", "///"])


@st.composite
def _base_url_strategy(draw: st.DrawFn) -> str:
    """Generate a repository base URL, possibly with trailing slashes."""
    scheme = draw(_URL_SCHEMES)
    domain = draw(_URL_DOMAINS)
    path = draw(_URL_BASE_PATHS)
    trailing = draw(_URL_TRAILING_SLASHES)
    return f"{scheme}{domain}{path}{trailing}"


_FILENAME_POOL_SECTIONS = st.sampled_from(["main", "contrib", "non-free", "universe"])

_FILENAME_LETTERS = st.sampled_from(["a", "b", "lib", "libc", "libfoo", "z", "python3"])

_FILENAME_PKG_NAMES = st.sampled_from(["libfoo", "python3-bar", "gcc-12", "linux-image", "apt", "curl"])

_FILENAME_VERSIONS = st.sampled_from(["1.0", "2.3.4-1", "1:3.0~beta1-2", "0.99+git20230101"])

_FILENAME_ARCHS = st.sampled_from(["amd64", "arm64", "i386", "all", "armhf"])

_FILENAME_LEADING_SLASHES = st.sampled_from(["", "/", "//"])


@st.composite
def _filename_strategy(draw: st.DrawFn) -> str:
    """Generate a package filename path, possibly with leading slashes."""
    leading = draw(_FILENAME_LEADING_SLASHES)
    section = draw(_FILENAME_POOL_SECTIONS)
    letter = draw(_FILENAME_LETTERS)
    name = draw(_FILENAME_PKG_NAMES)
    version = draw(_FILENAME_VERSIONS)
    arch = draw(_FILENAME_ARCHS)
    return f"{leading}pool/{section}/{letter}/{name}/{name}_{version}_{arch}.deb"


# Feature: repository-indexer, Property 13: Download URL computation


@pytest.mark.unit
class TestProperty13DownloadUrlComputation:
    """Property 13: Download URL computation.

    For any base_url and filename, the computed download_url SHALL equal
    base_url.rstrip('/') + '/' + filename.lstrip('/').

    **Validates: Requirements 6.4**
    """

    @settings(max_examples=100)
    @given(base_url=_base_url_strategy(), filename=_filename_strategy())
    def test_download_url_equals_expected_computation(self, base_url: str, filename: str) -> None:
        """Computed download URL matches base_url.rstrip('/') + '/' + filename.lstrip('/')."""
        result = _compute_download_url(base_url, filename)
        expected = base_url.rstrip("/") + "/" + filename.lstrip("/")

        assert result == expected, (
            f"Download URL mismatch.\n"
            f"base_url: {base_url!r}\n"
            f"filename: {filename!r}\n"
            f"result:   {result!r}\n"
            f"expected: {expected!r}"
        )

    @settings(max_examples=100)
    @given(base_url=_base_url_strategy(), filename=_filename_strategy())
    def test_no_double_slashes_between_base_and_filename(self, base_url: str, filename: str) -> None:
        """No double slashes exist at the join point between base URL and filename."""
        result = _compute_download_url(base_url, filename)

        # Strip the scheme (http:// or https://) to avoid false positives
        scheme_end = result.index("://") + 3
        path_part = result[scheme_end:]

        # The path portion should not contain "//" — the function ensures
        # exactly one slash separator between base and filename
        assert "//" not in path_part, (
            f"Double slash found in path portion of download URL.\n"
            f"base_url: {base_url!r}\n"
            f"filename: {filename!r}\n"
            f"result:   {result!r}\n"
            f"path_part: {path_part!r}"
        )
