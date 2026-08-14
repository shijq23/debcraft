"""Property-based tests for NETINST ISO repository scanner.

# Feature: netinst-iso-repo-scanner
"""

from __future__ import annotations

import asyncio
import gzip
from unittest.mock import MagicMock

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.domain.scanner.values import Artifact, ArtifactType, IdentifiedPackage
from debcraft.infrastructure.scanners.iso import ISOScanner
from debcraft.platform.contracts.workflow import (
    CancellationToken,
    ProgressReporter,
    WorkflowContext,
)

# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------


class _MockISOReader:
    """Mock ISO reader for property-based testing."""

    def __init__(
        self,
        *,
        files: dict[str, bytes] | None = None,
        dirs: dict[str, list[str]] | None = None,
    ) -> None:
        self._files = files or {}
        self._dirs = dirs or {}

    def open(self, path: str) -> None:
        pass

    def list_dir(self, path: str) -> list[str]:
        if path in self._dirs:
            return self._dirs[path]
        raise FileNotFoundError(f"Directory not found: {path}")

    def read_file(self, path: str) -> bytes:
        if path in self._files:
            return self._files[path]
        raise FileNotFoundError(f"File not found: {path}")

    def close(self) -> None:
        pass


class _MockSquashfsReader:
    """Mock squashfs reader for property-based testing."""

    def open(self, data: bytes) -> None:
        raise OSError("No squashfs")

    def read_file(self, path: str) -> bytes:
        raise FileNotFoundError

    def list_dir(self, path: str) -> list[str]:
        raise FileNotFoundError

    def close(self) -> None:
        pass


def _make_contents_port() -> MagicMock:
    """Create a mock ContentsIndexPort."""
    port = MagicMock()
    return port


def _make_package_port() -> MagicMock:
    """Create a mock PackageLookupPort."""
    port = MagicMock()
    return port


class _MockProgressReporter(ProgressReporter):
    """Mock progress reporter that records calls."""

    def __init__(self) -> None:
        self.reports: list[tuple[float, str]] = []

    def report(self, percentage: float, message: str = "") -> None:
        self.reports.append((percentage, message))


# ---------------------------------------------------------------------------
# Property 1: Repository Detection from Root Entries
# ---------------------------------------------------------------------------

# Feature: netinst-iso-repo-scanner, Property 1: Repository Detection from Root Entries


@pytest.mark.property
@given(entries=st.lists(st.text(min_size=1, max_size=20)))
def test_repository_detection_iff_dists_present(entries: list[str]) -> None:
    """For any set of root entries, scanner detects repo structure iff 'dists' is present.

    **Validates: Requirements 1.1, 1.2**
    """
    iso_reader = _MockISOReader(dirs={"": entries})
    scanner = ISOScanner(
        iso_reader=iso_reader,
        squashfs_reader=_MockSquashfsReader(),
        contents_port=_make_contents_port(),
        package_port=_make_package_port(),
    )

    diagnostics: list[str] = []
    result = scanner._has_repository_structure(diagnostics)

    if "dists" in entries:
        assert result is True
        assert any("Repository structure detected" in d for d in diagnostics)
    else:
        assert result is False
        assert not any("Repository structure detected" in d for d in diagnostics)


# ---------------------------------------------------------------------------
# Property 4: Gzip Decompression Round-Trip
# ---------------------------------------------------------------------------

# Strategy for valid package names
_package_name = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789-+."),
    min_size=1,
    max_size=20,
).filter(lambda s: s[0].isalpha())

_version_str = st.from_regex(r"[0-9]+\.[0-9]+-[0-9]+", fullmatch=True)
_arch_str = st.sampled_from(["amd64", "i386", "arm64", "armhf", "all"])


def _build_stanza(name: str, version: str, arch: str) -> str:
    return f"Package: {name}\nVersion: {version}\nArchitecture: {arch}\n"


@pytest.mark.property
@given(
    packages=st.lists(
        st.tuples(_package_name, _version_str, _arch_str),
        min_size=1,
        max_size=10,
    )
)
def test_gzip_decompression_round_trip(packages: list[tuple[str, str, str]]) -> None:
    """Gzip-compressing then scanner-decompressing produces the same packages.

    # Feature: netinst-iso-repo-scanner, Property 4: Gzip Decompression Round-Trip

    **Validates: Requirements 3.2**
    """
    content = "\n".join(_build_stanza(n, v, a) for n, v, a in packages)
    compressed = gzip.compress(content.encode("utf-8"))

    # Parse from compressed (.gz) path
    iso_reader_gz = _MockISOReader(files={"test/Packages.gz": compressed})
    scanner_gz = ISOScanner(
        iso_reader=iso_reader_gz,
        squashfs_reader=_MockSquashfsReader(),
        contents_port=_make_contents_port(),
        package_port=_make_package_port(),
    )
    diag_gz: list[str] = []
    result_gz = scanner_gz._parse_packages_file("test/Packages.gz", diag_gz)

    # Parse from uncompressed path
    iso_reader_plain = _MockISOReader(files={"test/Packages": content.encode("utf-8")})
    scanner_plain = ISOScanner(
        iso_reader=iso_reader_plain,
        squashfs_reader=_MockSquashfsReader(),
        contents_port=_make_contents_port(),
        package_port=_make_package_port(),
    )
    diag_plain: list[str] = []
    result_plain = scanner_plain._parse_packages_file("test/Packages", diag_plain)

    # Both should produce the same packages
    assert len(result_gz) == len(result_plain)
    for pkg_gz, pkg_plain in zip(result_gz, result_plain, strict=False):
        assert pkg_gz.name == pkg_plain.name
        assert pkg_gz.version == pkg_plain.version
        assert pkg_gz.architecture == pkg_plain.architecture
        assert pkg_gz.status == pkg_plain.status == "installed"


# ---------------------------------------------------------------------------
# Property 2: Packages File Discovery Respects Naming Patterns
# ---------------------------------------------------------------------------

# Feature: netinst-iso-repo-scanner, Property 2: Packages File Discovery Respects Naming Patterns

# Strategy for component names (no spaces, reasonable length, not metadata entries)
_component_names = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz-"),
    min_size=1,
    max_size=15,
).filter(lambda s: s not in ("Release", "InRelease") and not s.startswith("-") and not s.endswith("-"))

# Strategy for architecture names
_arch_names = st.sampled_from(["amd64", "i386", "arm64", "armhf", "all"])


@pytest.mark.property
@given(
    codename=st.text(alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz"), min_size=1, max_size=10),
    components=st.lists(_component_names, min_size=1, max_size=3, unique=True),
    arches=st.lists(_arch_names, min_size=1, max_size=3, unique=True),
    include_metadata=st.booleans(),
)
def test_discovery_respects_naming_patterns(
    codename: str,
    components: list[str],
    arches: list[str],
    include_metadata: bool,
) -> None:
    """Packages found only in binary-<arch>/ dirs; metadata entries excluded from components.

    **Validates: Requirements 2.2, 2.3**
    """
    # Build directory tree
    dirs: dict[str, list[str]] = {"": ["dists"], "dists": [codename]}

    codename_entries = list(components)
    if include_metadata:
        codename_entries.extend(["Release", "InRelease"])
    dirs[f"dists/{codename}"] = codename_entries

    # Add arch directories for each component
    expected_paths: list[str] = []
    for comp in components:
        comp_path = f"dists/{codename}/{comp}"
        binary_dirs = [f"binary-{arch}" for arch in arches]
        # Also add non-binary entries that should be ignored
        dirs[comp_path] = [*binary_dirs, "source", "debian-installer"]

        for arch in arches:
            arch_path = f"{comp_path}/binary-{arch}"
            dirs[arch_path] = ["Packages"]
            expected_paths.append(f"{arch_path}/Packages")

    iso_reader = _MockISOReader(dirs=dirs)
    scanner = ISOScanner(
        iso_reader=iso_reader,
        squashfs_reader=_MockSquashfsReader(),
        contents_port=_make_contents_port(),
        package_port=_make_package_port(),
    )

    diagnostics: list[str] = []
    result = scanner._discover_packages_files(diagnostics)

    # All discovered paths must be in binary-<arch>/ directories
    for path in result:
        parts = path.split("/")
        # Find the binary-<arch> part
        binary_part = [p for p in parts if p.startswith("binary-")]
        assert len(binary_part) == 1, f"Path {path} should have exactly one binary-<arch> component"

    # All expected paths should be found
    assert set(result) == set(expected_paths)

    # Metadata entries should never appear as component paths
    for path in result:
        assert "/Release/" not in path
        assert "/InRelease/" not in path


# ---------------------------------------------------------------------------
# Property 5: Status-less Stanzas Produce Installed Packages
# ---------------------------------------------------------------------------
# Feature: netinst-iso-repo-scanner, Property 5: Status-less Stanzas Produce Installed Packages

_pkg_name_strategy = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789-+."),
    min_size=1,
    max_size=20,
).filter(lambda s: s[0].isalpha())

_version_strategy = st.from_regex(r"[0-9]+\.[0-9]+(\.[0-9]+)?(-[0-9]+)?", fullmatch=True)
_arch_strategy = st.sampled_from(["amd64", "i386", "arm64", "armhf", "all"])
_section_strategy = st.sampled_from(["libs", "utils", "debian-installer", "admin", "net", "base"])


@pytest.mark.property
@given(
    name=_pkg_name_strategy,
    version=_version_strategy,
    arch=_arch_strategy,
    section=_section_strategy,
    has_section=st.booleans(),
    has_description=st.booleans(),
)
def test_statusless_stanzas_produce_installed_packages(
    name: str,
    version: str,
    arch: str,
    section: str,
    has_section: bool,
    has_description: bool,
) -> None:
    """Stanzas with Package+Version but no Status produce IdentifiedPackage(status='installed').

    # Feature: netinst-iso-repo-scanner, Property 5: Status-less Stanzas Produce Installed Packages

    **Validates: Requirements 3.4, 8.1, 8.3**
    """
    lines = [f"Package: {name}", f"Version: {version}", f"Architecture: {arch}"]
    if has_section:
        lines.append(f"Section: {section}")
    if has_description:
        lines.append("Description: A test package")
    # Explicitly NO Status field
    content = "\n".join(lines) + "\n"

    iso_reader = _MockISOReader(files={"test/Packages": content.encode("utf-8")})
    scanner = ISOScanner(
        iso_reader=iso_reader,
        squashfs_reader=_MockSquashfsReader(),
        contents_port=_make_contents_port(),
        package_port=_make_package_port(),
    )

    diagnostics: list[str] = []
    result = scanner._parse_packages_file("test/Packages", diagnostics)

    assert len(result) == 1
    pkg = result[0]
    assert pkg.name == name
    assert pkg.version == version
    assert pkg.architecture == arch
    assert pkg.status == "installed"
    # No diagnostics about missing fields
    assert not any("missing field" in d for d in diagnostics)


# ---------------------------------------------------------------------------
# Feature: netinst-iso-repo-scanner, Property 3: Packages.gz Preference
# ---------------------------------------------------------------------------


@pytest.mark.property
@given(
    codename=st.text(alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz"), min_size=1, max_size=10),
    component=st.text(alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz-"), min_size=1, max_size=10).filter(
        lambda s: s not in ("Release", "InRelease") and not s.startswith("-") and not s.endswith("-")
    ),
    arch=st.sampled_from(["amd64", "i386", "arm64", "armhf", "all"]),
    has_both=st.booleans(),
)
def test_packages_gz_preferred_when_both_exist(
    codename: str,
    component: str,
    arch: str,
    has_both: bool,
) -> None:
    """When both Packages and Packages.gz exist, only Packages.gz is used.

    # Feature: netinst-iso-repo-scanner, Property 3: Packages.gz Preference

    **Validates: Requirements 2.4**
    """
    arch_path = f"dists/{codename}/{component}/binary-{arch}"

    arch_contents = ["Packages", "Packages.gz"] if has_both else ["Packages.gz"]

    dirs = {
        "": ["dists"],
        "dists": [codename],
        f"dists/{codename}": [component],
        f"dists/{codename}/{component}": [f"binary-{arch}"],
        arch_path: arch_contents,
    }

    iso_reader = _MockISOReader(dirs=dirs)
    scanner = ISOScanner(
        iso_reader=iso_reader,
        squashfs_reader=_MockSquashfsReader(),
        contents_port=_make_contents_port(),
        package_port=_make_package_port(),
    )
    iso_reader.opened = True

    diagnostics: list[str] = []
    result = scanner._discover_packages_files(diagnostics)

    # Only Packages.gz should be in the result
    assert len(result) == 1
    assert result[0] == f"{arch_path}/Packages.gz"
    # Plain "Packages" should never be chosen when .gz is available
    assert not any(p.endswith("/Packages") and not p.endswith("/Packages.gz") for p in result)


@pytest.mark.property
@given(
    codename=st.text(alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz"), min_size=1, max_size=10),
    component=st.text(alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz-"), min_size=1, max_size=10).filter(
        lambda s: s not in ("Release", "InRelease") and not s.startswith("-") and not s.endswith("-")
    ),
    arch=st.sampled_from(["amd64", "i386", "arm64", "armhf", "all"]),
)
def test_plain_packages_used_when_no_gz(
    codename: str,
    component: str,
    arch: str,
) -> None:
    """When only Packages exists (no .gz), it is used.

    # Feature: netinst-iso-repo-scanner, Property 3: Packages.gz Preference

    **Validates: Requirements 2.4**
    """
    arch_path = f"dists/{codename}/{component}/binary-{arch}"
    dirs = {
        "": ["dists"],
        "dists": [codename],
        f"dists/{codename}": [component],
        f"dists/{codename}/{component}": [f"binary-{arch}"],
        arch_path: ["Packages"],
    }
    iso_reader = _MockISOReader(dirs=dirs)
    scanner = ISOScanner(
        iso_reader=iso_reader,
        squashfs_reader=_MockSquashfsReader(),
        contents_port=_make_contents_port(),
        package_port=_make_package_port(),
    )
    iso_reader.opened = True
    diagnostics: list[str] = []
    result = scanner._discover_packages_files(diagnostics)
    assert len(result) == 1
    assert result[0] == f"{arch_path}/Packages"


# ---------------------------------------------------------------------------
# Property 6: Missing Required Fields Produce Diagnostics
# ---------------------------------------------------------------------------

# Feature: netinst-iso-repo-scanner, Property 6: Missing Required Fields Produce Diagnostics


@pytest.mark.property
@given(
    version=st.from_regex(r"[0-9]+\.[0-9]+-[0-9]+", fullmatch=True),
    arch=st.sampled_from(["amd64", "i386", "arm64"]),
    missing_field=st.sampled_from(["Package", "Version"]),
)
def test_missing_required_fields_produce_diagnostics(
    version: str,
    arch: str,
    missing_field: str,
) -> None:
    """Stanzas missing Package or Version are skipped with a diagnostic.

    **Validates: Requirements 8.2**
    """
    if missing_field == "Package":
        # Stanza with Version but no Package
        content = f"Version: {version}\nArchitecture: {arch}\n"
    else:
        # Stanza with Package but no Version
        content = f"Package: testpkg\nArchitecture: {arch}\n"

    iso_reader = _MockISOReader(files={"test/Packages": content.encode("utf-8")})
    scanner = ISOScanner(
        iso_reader=iso_reader,
        squashfs_reader=_MockSquashfsReader(),
        contents_port=_make_contents_port(),
        package_port=_make_package_port(),
    )

    diagnostics: list[str] = []
    result = scanner._parse_packages_file("test/Packages", diagnostics)

    # Stanza should be skipped
    assert len(result) == 0
    # Should have a diagnostic identifying the missing field
    assert any(f"missing field: {missing_field}" in d for d in diagnostics)


# ---------------------------------------------------------------------------
# Property 8: Partial Failure Resilience
# ---------------------------------------------------------------------------

# Feature: netinst-iso-repo-scanner, Property 8: Partial Failure Resilience


@pytest.mark.property
@given(
    num_valid=st.integers(min_value=1, max_value=5),
    num_failing=st.integers(min_value=1, max_value=5),
)
def test_partial_failure_resilience(num_valid: int, num_failing: int) -> None:
    """Failed files produce diagnostics; successful files still contribute packages.

    **Validates: Requirements 2.5, 3.5, 4.3**
    """
    files: dict[str, bytes] = {}
    all_paths: list[str] = []

    # Add valid files
    for i in range(num_valid):
        content = f"Package: pkg{i}\nVersion: 1.0-1\nArchitecture: amd64\n"
        path = f"valid{i}/Packages"
        files[path] = content.encode("utf-8")
        all_paths.append(path)

    # Add failing files (invalid gzip for .gz files)
    for i in range(num_failing):
        path = f"failing{i}/Packages.gz"
        files[path] = b"not valid gzip data"  # Will cause decompression failure
        all_paths.append(path)

    iso_reader = _MockISOReader(files=files)
    scanner = ISOScanner(
        iso_reader=iso_reader,
        squashfs_reader=_MockSquashfsReader(),
        contents_port=_make_contents_port(),
        package_port=_make_package_port(),
    )

    diagnostics: list[str] = []
    all_packages = []
    for path in all_paths:
        result = scanner._parse_packages_file(path, diagnostics)
        all_packages.extend(result)

    # Should have packages from all valid files
    assert len(all_packages) == num_valid

    # Should have a diagnostic for each failed file
    failure_diagnostics = [d for d in diagnostics if "Failed to decompress" in d]
    assert len(failure_diagnostics) == num_failing


# ---------------------------------------------------------------------------
# Property 7: Deduplication Retains First Occurrence
# ---------------------------------------------------------------------------

# Feature: netinst-iso-repo-scanner, Property 7: Deduplication Retains First Occurrence


@pytest.mark.property
@given(
    packages=st.lists(
        st.tuples(
            st.text(alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz"), min_size=1, max_size=10),
            st.text(alphabet=st.sampled_from("0123456789.+-"), min_size=1, max_size=10),
            st.sampled_from(["amd64", "i386", "arm64", "all"]),
        ),
        min_size=1,
        max_size=30,
    )
)
def test_deduplication_retains_first_occurrence(packages: list[tuple[str, str, str]]) -> None:
    """Only first occurrence of each (name, version, arch) is retained, order preserved.

    **Validates: Requirements 4.1, 4.2**
    """
    # Build IdentifiedPackage list
    pkg_list = [IdentifiedPackage(name=n, version=v, architecture=a, status="installed") for n, v, a in packages]

    iso_reader = _MockISOReader()
    scanner = ISOScanner(
        iso_reader=iso_reader,
        squashfs_reader=_MockSquashfsReader(),
        contents_port=_make_contents_port(),
        package_port=_make_package_port(),
    )

    diagnostics: list[str] = []
    result = scanner._deduplicate_packages(pkg_list, 1, diagnostics)

    # All results should be unique by (name, version, architecture)
    keys_seen = set()
    for pkg in result:
        key = (pkg.name, pkg.version, pkg.architecture)
        assert key not in keys_seen, f"Duplicate found: {key}"
        keys_seen.add(key)

    # Each result should be the FIRST occurrence from the input
    first_occurrences: dict[tuple[str, str, str], IdentifiedPackage] = {}
    for pkg in pkg_list:
        key = (pkg.name, pkg.version, pkg.architecture)
        if key not in first_occurrences:
            first_occurrences[key] = pkg

    # Result should match first occurrences in order
    expected = list(first_occurrences.values())
    assert result == expected

    # Order should be preserved (relative order of first occurrences)
    input_first_indices = []
    seen_keys: set[tuple[str, str, str]] = set()
    for i, pkg in enumerate(pkg_list):
        key = (pkg.name, pkg.version, pkg.architecture)
        if key not in seen_keys:
            seen_keys.add(key)
            input_first_indices.append(i)

    # The result length should match unique key count
    assert len(result) == len(set(packages))


# ---------------------------------------------------------------------------
# Property 9: Cancellation Produces Partial Results
# ---------------------------------------------------------------------------

# Feature: netinst-iso-repo-scanner, Property 9: Cancellation Produces Partial Results


class _MockProgressReporter(ProgressReporter):
    """Mock progress reporter that records calls."""

    def __init__(self) -> None:
        self.reports: list[tuple[float, str]] = []

    def report(self, percentage: float, message: str = "") -> None:
        self.reports.append((percentage, message))


class _CancellingISOReader(_MockISOReader):
    """ISO reader that cancels a token after N read_file calls."""

    def __init__(
        self,
        *,
        token: CancellationToken,
        cancel_after_reads: int = 1,
        files: dict[str, bytes] | None = None,
        dirs: dict[str, list[str]] | None = None,
    ) -> None:
        super().__init__(files=files, dirs=dirs)
        self._token = token
        self._cancel_after = cancel_after_reads
        self._read_count = 0

    def read_file(self, path: str) -> bytes:
        result = super().read_file(path)
        self._read_count += 1
        if self._read_count >= self._cancel_after:
            self._token.cancel()
        return result


@pytest.mark.property
@given(
    total_files=st.integers(min_value=2, max_value=5),
    cancel_after=st.integers(min_value=1, max_value=4),
)
def test_cancellation_produces_partial_results(total_files: int, cancel_after: int) -> None:
    """Scanner returns packages parsed before cancellation point plus diagnostic.

    **Validates: Requirements 6.1, 6.2**
    """
    import asyncio

    # Ensure cancel_after < total_files
    cancel_after = min(cancel_after, total_files - 1)

    # Build repo structure with N binary-<arch> directories, each with a Packages.gz
    arches = [f"arch{i}" for i in range(total_files)]
    dirs: dict[str, list[str]] = {
        "": ["dists"],
        "dists": ["stable"],
        "dists/stable": ["main"],
        "dists/stable/main": [f"binary-{arch}" for arch in arches],
    }
    files: dict[str, bytes] = {}

    for i, arch in enumerate(arches):
        arch_dir = f"dists/stable/main/binary-{arch}"
        dirs[arch_dir] = ["Packages.gz"]
        content = f"Package: pkg{i}\nVersion: 1.0-{i}\nArchitecture: {arch}\n"
        files[f"{arch_dir}/Packages.gz"] = gzip.compress(content.encode())

    token = CancellationToken()
    iso_reader = _CancellingISOReader(
        token=token,
        cancel_after_reads=cancel_after,
        dirs=dirs,
        files=files,
    )

    ctx = MagicMock(spec=WorkflowContext)
    ctx.cancellation_token = token
    ctx.progress = _MockProgressReporter()

    scanner = ISOScanner(
        iso_reader=iso_reader,
        squashfs_reader=_MockSquashfsReader(),
        contents_port=_make_contents_port(),
        package_port=_make_package_port(),
    )

    artifact = Artifact(type=ArtifactType.ISO, path="/test.iso", options={"snapshot_id": "0"})
    result = asyncio.run(scanner.scan(artifact, ctx))

    # Should have packages only up to the cancellation point
    assert len(result.packages) <= cancel_after
    assert len(result.packages) < total_files
    # Should have cancellation diagnostic
    assert any("cancel" in d.lower() for d in result.diagnostics)
    # Strategy should be DPKG_METADATA
    assert result.strategy == "dpkg_metadata"


# ---------------------------------------------------------------------------
# Property 10: Scan Result Invariants
# ---------------------------------------------------------------------------

# Feature: netinst-iso-repo-scanner, Property 10: Scan Result Invariants


@pytest.mark.property
@given(
    num_components=st.integers(min_value=1, max_value=4),
    num_arches=st.integers(min_value=1, max_value=3),
)
def test_scan_result_invariants(num_components: int, num_arches: int) -> None:
    """Duration >= 0 and diagnostics order matches recording order.

    **Validates: Requirements 5.2, 5.3**
    """
    # Build a repo with variable structure
    components = [f"comp{i}" for i in range(num_components)]
    arches = [f"arch{i}" for i in range(num_arches)]

    dirs: dict[str, list[str]] = {
        "": ["dists"],
        "dists": ["stable"],
        "dists/stable": components,
    }
    files: dict[str, bytes] = {}

    for comp in components:
        comp_path = f"dists/stable/{comp}"
        dirs[comp_path] = [f"binary-{arch}" for arch in arches]
        for arch in arches:
            arch_path = f"{comp_path}/binary-{arch}"
            dirs[arch_path] = ["Packages"]
            content = f"Package: {comp}-{arch}\nVersion: 1.0-1\nArchitecture: {arch}\n"
            files[f"{arch_path}/Packages"] = content.encode()

    iso_reader = _MockISOReader(dirs=dirs, files=files)

    ctx = MagicMock(spec=WorkflowContext)
    token = CancellationToken()
    ctx.cancellation_token = token
    ctx.progress = _MockProgressReporter()

    scanner = ISOScanner(
        iso_reader=iso_reader,
        squashfs_reader=_MockSquashfsReader(),
        contents_port=_make_contents_port(),
        package_port=_make_package_port(),
    )

    artifact = Artifact(type=ArtifactType.ISO, path="/test.iso", options={"snapshot_id": "0"})
    result = asyncio.run(scanner.scan(artifact, ctx))

    # Invariant 1: duration_seconds >= 0
    assert result.duration_seconds >= 0

    # Invariant 2: diagnostics is a list (ordered)
    assert isinstance(result.diagnostics, list)

    # Invariant 3: First diagnostic should be the repo detection message
    # (since we have dists/)
    assert len(result.diagnostics) >= 1
    assert "Repository structure detected" in result.diagnostics[0]

    # Invariant 4: Summary diagnostic should be present (last before any trailing)
    assert any("Repository scan:" in d for d in result.diagnostics)


# ---------------------------------------------------------------------------
# Property 11: Successful Repository Scan Short-Circuits Fallback
# ---------------------------------------------------------------------------

# Feature: netinst-iso-repo-scanner, Property 11: Successful Repository Scan Short-Circuits Fallback


@pytest.mark.property
@given(
    num_packages=st.integers(min_value=1, max_value=5),
    codename=st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz"),
        min_size=1,
        max_size=10,
    ),
)
def test_successful_repo_scan_short_circuits_fallback(num_packages: int, codename: str) -> None:
    """ISO with repo producing ≥1 package returns without calling rootfs/filesystem fallback.

    **Validates: Requirements 7.2**
    """
    # Build repo content with N packages
    stanzas = []
    for i in range(num_packages):
        stanzas.append(f"Package: repopkg{i}\nVersion: 1.0-{i}\nArchitecture: amd64\n")
    repo_content = "\n".join(stanzas)

    # Also add dpkg status with different packages (to prove it's NOT used)
    dpkg_content = "Package: dpkgonly\nVersion: 2.0-1\nArchitecture: amd64\nStatus: install ok installed\n"

    dirs = {
        "": ["dists", "var"],
        "dists": [codename],
        f"dists/{codename}": ["main"],
        f"dists/{codename}/main": ["binary-amd64"],
        f"dists/{codename}/main/binary-amd64": ["Packages"],
        "var": ["lib"],
        "var/lib": ["dpkg"],
        "var/lib/dpkg": ["status"],
    }
    files = {
        f"dists/{codename}/main/binary-amd64/Packages": repo_content.encode(),
        "var/lib/dpkg/status": dpkg_content.encode(),
    }

    iso_reader = _MockISOReader(dirs=dirs, files=files)

    ctx = MagicMock(spec=WorkflowContext)
    token = CancellationToken()
    ctx.cancellation_token = token
    ctx.progress = _MockProgressReporter()

    scanner = ISOScanner(
        iso_reader=iso_reader,
        squashfs_reader=_MockSquashfsReader(),
        contents_port=_make_contents_port(),
        package_port=_make_package_port(),
    )

    artifact = Artifact(type=ArtifactType.ISO, path="/test.iso", options={"snapshot_id": "0"})
    result = asyncio.run(scanner.scan(artifact, ctx))

    # Should have strategy DPKG_METADATA
    assert result.strategy == "dpkg_metadata"
    # Should have packages from the repo (repopkgN)
    assert len(result.packages) == num_packages
    assert all(p.name.startswith("repopkg") for p in result.packages)
    # Should NOT have the dpkg-only package (proves fallback was short-circuited)
    assert not any(p.name == "dpkgonly" for p in result.packages)
    # All should have status "installed"
    assert all(p.status == "installed" for p in result.packages)
