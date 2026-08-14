"""Property-based tests for cache behavior.

# Feature: package-intelligence, Property 20: Cache Store on Success, Skip on Failure
# Feature: package-intelligence, Property 21: Cache Hit Returns Cached Result Without Re-Extraction
# Feature: package-intelligence, Property 22: Cache Invalidation on Version Change

**Validates: Requirements 11.1, 11.2, 11.3, 11.5**
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.domain.package_intelligence.deb_parser import DebParser
from debcraft.domain.package_intelligence.errors import DebParseError
from debcraft.domain.package_intelligence.values import (
    DebParseResult,
    DependencyRelation,
)

if TYPE_CHECKING:
    pass


# ===========================================================================
# Hypothesis strategies for generating DebParseResult instances
# ===========================================================================

_PACKAGE_NAME_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789.+-"
_VERSION_CHARS = "0123456789.+-~:"
_ARCH_CHOICES = ["amd64", "arm64", "i386", "all", "armhf", "ppc64el"]
_PRINTABLE_ASCII = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 !@#$%^&*()-_=+[]{}|;',.<>?/~`"


@st.composite
def deb_parse_result_strategy(draw: st.DrawFn) -> DebParseResult:
    """Generate a valid DebParseResult with random data."""
    first_char = draw(st.sampled_from(list("abcdefghijklmnopqrstuvwxyz")))
    rest = draw(st.text(alphabet=_PACKAGE_NAME_CHARS, min_size=0, max_size=15))
    package_name = first_char + rest

    version = draw(st.text(alphabet=_VERSION_CHARS, min_size=1, max_size=12))
    architecture = draw(st.sampled_from(_ARCH_CHOICES))

    # Generate control fields
    # Avoid generating field names that match dependency fields (Depends, Pre-Depends,
    # Recommends, Suggests) since those require valid dependency syntax when re-parsed.
    _dependency_field_names = {"depends", "pre-depends", "recommends", "suggests"}
    num_extra_fields = draw(st.integers(min_value=0, max_value=3))
    control_fields: dict[str, str] = {
        "Package": package_name,
        "Version": version,
        "Architecture": architecture,
    }
    for _ in range(num_extra_fields):
        field_name = draw(
            st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-", min_size=1, max_size=15).filter(
                lambda name: name.lower() not in _dependency_field_names
            )
        )
        field_value = draw(st.text(alphabet=_PRINTABLE_ASCII, min_size=1, max_size=30))
        control_fields[field_name] = field_value

    # Generate dependencies
    num_deps = draw(st.integers(min_value=0, max_value=3))
    dependencies: list[DependencyRelation] = []
    for _ in range(num_deps):
        dep_first = draw(st.sampled_from(list("abcdefghijklmnopqrstuvwxyz")))
        dep_rest = draw(st.text(alphabet=_PACKAGE_NAME_CHARS, min_size=0, max_size=10))
        dep_pkg = dep_first + dep_rest
        has_ver = draw(st.booleans())
        ver_constraint = None
        if has_ver:
            ver_constraint = draw(st.sampled_from([">= 1.0", "<< 2.0", "= 1.5", ">> 0.9"]))
        dependencies.append(DependencyRelation(package=dep_pkg, version_constraint=ver_constraint))

    # Generate file listing
    num_files = draw(st.integers(min_value=0, max_value=5))
    file_listing: list[str] = []
    for _ in range(num_files):
        path = draw(st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789/.-_", min_size=1, max_size=40))
        file_listing.append(path)

    # Optional copyright text
    has_copyright = draw(st.booleans())
    copyright_text: str | None = None
    if has_copyright:
        copyright_text = draw(st.text(alphabet=_PRINTABLE_ASCII + "\n", min_size=1, max_size=100))

    return DebParseResult(
        package_name=package_name,
        version=version,
        architecture=architecture,
        control_fields=control_fields,
        dependencies=dependencies,
        file_listing=file_listing,
        copyright_text=copyright_text,
    )


@st.composite
def sha256_hex_strategy(draw: st.DrawFn) -> str:
    """Generate a valid SHA256 hex digest string."""
    raw_bytes = draw(st.binary(min_size=1, max_size=64))
    return hashlib.sha256(raw_bytes).hexdigest()


# ===========================================================================
# Cache-aware workflow helper (the coordination logic under test)
# ===========================================================================


class CacheAwareParseWorkflow:
    """Demonstrates the cache-aware workflow pattern from the application layer.

    This is the coordination logic that:
    1. Computes SHA256
    2. Checks cache with (sha256, parser_version)
    3. If cache hit → returns cached result
    4. If cache miss → parses with DebParser → stores in cache → returns result
    """

    def __init__(
        self,
        parser: DebParser,
        cache: MockCache,
        parser_version: int,
    ) -> None:
        self._parser = parser
        self._cache = cache
        self._parser_version = parser_version

    def execute(self, deb_path: str, sha256: str) -> DebParseResult:
        """Execute the cache-aware parse workflow.

        Args:
            deb_path: Path to the .deb file.
            sha256: Pre-computed SHA256 of the file.

        Returns:
            DebParseResult (from cache or fresh parse).

        Raises:
            DebParseError: If parsing fails (no cache store on failure).
        """
        # Step 2: Check cache
        cached = self._cache.get_sync(sha256, self._parser_version)
        if cached is not None:
            return cached

        # Step 3: Parse (may raise DebParseError)
        result = self._parser.parse(deb_path)

        # Step 4: Store on success
        self._cache.store_sync(sha256, self._parser_version, result)
        return result


# ===========================================================================
# Mock implementations for testing
# ===========================================================================


class MockCache:
    """Mock ParseCachePort that tracks interactions for verification."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, int], DebParseResult] = {}
        self.get_calls: list[tuple[str, int]] = []
        self.store_calls: list[tuple[str, int, DebParseResult]] = []

    def seed(self, sha256: str, parser_version: int, result: DebParseResult) -> None:
        """Pre-populate the cache with an entry."""
        self._store[(sha256, parser_version)] = result

    def get_sync(self, sha256: str, parser_version: int) -> DebParseResult | None:
        """Synchronous get for testing the workflow pattern."""
        self.get_calls.append((sha256, parser_version))
        return self._store.get((sha256, parser_version))

    def store_sync(self, sha256: str, parser_version: int, result: DebParseResult) -> None:
        """Synchronous store for testing the workflow pattern."""
        self.store_calls.append((sha256, parser_version, result))
        self._store[(sha256, parser_version)] = result


class MockDebFileReaderSuccess:
    """Mock DebFileReader that returns valid .deb archive data for a successful parse."""

    def __init__(self, result: DebParseResult) -> None:
        self._result = result
        self.read_calls: list[tuple[str, str]] = []

    def read_ar_member(self, deb_path: str, member_prefix: str) -> bytes:
        """Return appropriate mock data based on the member being requested."""
        self.read_calls.append((deb_path, member_prefix))

        if member_prefix == "":
            # ar archive magic validation
            return b"!<arch>\n" + b"\x00" * 64

        if member_prefix == "debian-binary":
            return b"2.0\n"

        if member_prefix == "control.tar":
            # Build a minimal tar containing a control file
            import io
            import tarfile

            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:") as tar:
                control_text = self._build_control_text()
                info = tarfile.TarInfo(name="./control")
                info.size = len(control_text)
                tar.addfile(info, io.BytesIO(control_text))
            return buf.getvalue()

        if member_prefix == "data.tar":
            # Build a minimal tar with file listing
            import io
            import tarfile

            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:") as tar:
                for file_path in self._result.file_listing:
                    info = tarfile.TarInfo(name=file_path)
                    info.size = 0
                    tar.addfile(info)

                # Add copyright file if present
                if self._result.copyright_text is not None:
                    copyright_path = f"./usr/share/doc/{self._result.package_name}/copyright"
                    content = self._result.copyright_text.encode("utf-8")
                    info = tarfile.TarInfo(name=copyright_path)
                    info.size = len(content)
                    tar.addfile(info, io.BytesIO(content))
            return buf.getvalue()

        return b""

    def compute_sha256(self, file_path: str) -> str:
        """Return a dummy SHA256."""
        return "0" * 64

    def _build_control_text(self) -> bytes:
        """Build control file bytes from the result's control_fields."""
        lines: list[str] = []
        for field_name, value in self._result.control_fields.items():
            lines.append(f"{field_name}: {value}")
        # Add dependency fields if present
        if self._result.dependencies:
            dep_strs: list[str] = []
            for dep in self._result.dependencies:
                dep_str = dep.package
                if dep.version_constraint:
                    dep_str += f" ({dep.version_constraint})"
                dep_strs.append(dep_str)
            lines.append(f"Depends: {', '.join(dep_strs)}")
        return ("\n".join(lines) + "\n").encode("utf-8")


class MockDebFileReaderFailure:
    """Mock DebFileReader that causes parse failure (invalid ar magic)."""

    def __init__(self) -> None:
        self.read_calls: list[tuple[str, str]] = []

    def read_ar_member(self, deb_path: str, member_prefix: str) -> bytes:
        """Return invalid bytes to trigger DebParseError."""
        self.read_calls.append((deb_path, member_prefix))
        # Return bytes that don't start with ar magic
        return b"NOT_AN_AR_ARCHIVE"

    def compute_sha256(self, file_path: str) -> str:
        """Return a dummy SHA256."""
        return "0" * 64


# ===========================================================================
# Property 20: Cache Store on Success, Skip on Failure
# ===========================================================================


@pytest.mark.unit
@pytest.mark.package
class TestProperty20CacheStoreOnSuccessSkipOnFailure:
    """Property 20: Cache Store on Success, Skip on Failure.

    For any .deb file that parses successfully, the cache SHALL receive a
    store call with the correct SHA256 and parser version. For any .deb file
    that fails to parse, the cache SHALL NOT receive a store call.

    **Validates: Requirements 11.1, 11.5**
    """

    @given(
        parse_result=deb_parse_result_strategy(),
        sha256=sha256_hex_strategy(),
    )
    def test_store_called_on_successful_parse(self, parse_result: DebParseResult, sha256: str) -> None:
        """Cache store is called with correct SHA256 and parser version on success."""
        reader = MockDebFileReaderSuccess(parse_result)
        parser = DebParser(file_reader=reader)
        cache = MockCache()

        workflow = CacheAwareParseWorkflow(
            parser=parser,
            cache=cache,
            parser_version=DebParser.PARSER_VERSION,
        )

        workflow.execute("/fake/path.deb", sha256)

        # Verify store was called exactly once
        assert len(cache.store_calls) == 1, f"Expected exactly 1 store call, got {len(cache.store_calls)}"

        # Verify store was called with correct sha256 and parser version
        stored_sha, stored_version, _stored_result = cache.store_calls[0]
        assert stored_sha == sha256, f"Expected SHA256 '{sha256}', got '{stored_sha}'"
        assert stored_version == DebParser.PARSER_VERSION, (
            f"Expected parser version {DebParser.PARSER_VERSION}, got {stored_version}"
        )

    @given(sha256=sha256_hex_strategy())
    def test_store_not_called_on_failed_parse(self, sha256: str) -> None:
        """Cache store is NOT called when parsing fails."""
        reader = MockDebFileReaderFailure()
        parser = DebParser(file_reader=reader)
        cache = MockCache()

        workflow = CacheAwareParseWorkflow(
            parser=parser,
            cache=cache,
            parser_version=DebParser.PARSER_VERSION,
        )

        with pytest.raises(DebParseError):
            workflow.execute("/fake/path.deb", sha256)

        # Verify store was never called
        assert len(cache.store_calls) == 0, (
            f"Expected 0 store calls on failure, got {len(cache.store_calls)}.\nStore calls: {cache.store_calls}"
        )


# ===========================================================================
# Property 21: Cache Hit Returns Cached Result Without Re-Extraction
# ===========================================================================


@pytest.mark.unit
@pytest.mark.package
class TestProperty21CacheHitReturnsCachedResultWithoutReExtraction:
    """Property 21: Cache Hit Returns Cached Result Without Re-Extraction.

    For any SHA256 that exists in the cache with a parser version matching
    the current version, the workflow SHALL return the cached result without
    invoking the file reader for extraction.

    **Validates: Requirements 11.2**
    """

    @given(
        parse_result=deb_parse_result_strategy(),
        sha256=sha256_hex_strategy(),
    )
    def test_cache_hit_returns_cached_result(self, parse_result: DebParseResult, sha256: str) -> None:
        """When cache contains matching entry, that result is returned directly."""
        reader = MockDebFileReaderSuccess(parse_result)
        parser = DebParser(file_reader=reader)
        cache = MockCache()

        # Seed the cache with the result
        cache.seed(sha256, DebParser.PARSER_VERSION, parse_result)

        workflow = CacheAwareParseWorkflow(
            parser=parser,
            cache=cache,
            parser_version=DebParser.PARSER_VERSION,
        )

        result = workflow.execute("/fake/path.deb", sha256)

        # Verify the cached result was returned
        assert result is parse_result, "Expected the exact cached result object to be returned"

    @given(
        parse_result=deb_parse_result_strategy(),
        sha256=sha256_hex_strategy(),
    )
    def test_cache_hit_does_not_invoke_file_reader(self, parse_result: DebParseResult, sha256: str) -> None:
        """When cache hit occurs, the file reader's extraction methods are NOT called."""
        reader = MockDebFileReaderSuccess(parse_result)
        parser = DebParser(file_reader=reader)
        cache = MockCache()

        # Seed the cache
        cache.seed(sha256, DebParser.PARSER_VERSION, parse_result)

        workflow = CacheAwareParseWorkflow(
            parser=parser,
            cache=cache,
            parser_version=DebParser.PARSER_VERSION,
        )

        workflow.execute("/fake/path.deb", sha256)

        # Verify the file reader was never called (no extraction happened)
        assert len(reader.read_calls) == 0, (
            f"Expected 0 file reader calls on cache hit, got {len(reader.read_calls)}.\nCalls: {reader.read_calls}"
        )


# ===========================================================================
# Property 22: Cache Invalidation on Version Change
# ===========================================================================


@pytest.mark.unit
@pytest.mark.package
class TestProperty22CacheInvalidationOnVersionChange:
    """Property 22: Cache Invalidation on Version Change.

    For any cache entry whose stored parser version differs from the current
    parser version, the workflow SHALL ignore the cached entry and re-parse
    the file.

    **Validates: Requirements 11.3**
    """

    @given(
        parse_result=deb_parse_result_strategy(),
        sha256=sha256_hex_strategy(),
        old_version=st.integers(min_value=0, max_value=100).filter(lambda v: v != DebParser.PARSER_VERSION),
    )
    def test_mismatched_version_triggers_reparse(
        self, parse_result: DebParseResult, sha256: str, old_version: int
    ) -> None:
        """Cache entry with different parser version is ignored, triggering re-parse."""
        reader = MockDebFileReaderSuccess(parse_result)
        parser = DebParser(file_reader=reader)
        cache = MockCache()

        # Seed the cache with an OLD parser version (different from current)
        cache.seed(sha256, old_version, parse_result)

        workflow = CacheAwareParseWorkflow(
            parser=parser,
            cache=cache,
            parser_version=DebParser.PARSER_VERSION,
        )

        _result = workflow.execute("/fake/path.deb", sha256)

        # Verify the file reader WAS called (re-parsing occurred)
        assert len(reader.read_calls) > 0, (
            f"Expected file reader to be called for re-parse, but got 0 calls.\n"
            f"Old version: {old_version}, current: {DebParser.PARSER_VERSION}"
        )

    @given(
        parse_result=deb_parse_result_strategy(),
        sha256=sha256_hex_strategy(),
        old_version=st.integers(min_value=0, max_value=100).filter(lambda v: v != DebParser.PARSER_VERSION),
    )
    def test_mismatched_version_stores_new_result(
        self, parse_result: DebParseResult, sha256: str, old_version: int
    ) -> None:
        """After re-parsing due to version mismatch, new result is stored in cache."""
        reader = MockDebFileReaderSuccess(parse_result)
        parser = DebParser(file_reader=reader)
        cache = MockCache()

        # Seed the cache with an OLD parser version
        cache.seed(sha256, old_version, parse_result)

        workflow = CacheAwareParseWorkflow(
            parser=parser,
            cache=cache,
            parser_version=DebParser.PARSER_VERSION,
        )

        workflow.execute("/fake/path.deb", sha256)

        # Verify a new store call was made with the CURRENT parser version
        assert len(cache.store_calls) == 1, (
            f"Expected exactly 1 store call after re-parse, got {len(cache.store_calls)}"
        )
        stored_sha, stored_version, _stored_result = cache.store_calls[0]
        assert stored_sha == sha256
        assert stored_version == DebParser.PARSER_VERSION, (
            f"Expected new entry stored with version {DebParser.PARSER_VERSION}, got {stored_version}"
        )
