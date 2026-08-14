"""Property-based tests for _infer_file_type bug condition and preservation.

# Feature: indexer-file-type-classification
# Property 1: Bug Condition - Filename-Based Classification
# Property 2: Preservation - Legitimate Metadata File Classification

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.domain.indexer.service import _infer_file_type

# ===========================================================================
# Strategies for Bug Condition Exploration
# ===========================================================================

# Keywords that trigger misclassification when they appear in directory names
_KEYWORDS = ["packages", "sources", "release", "resources"]

# File extensions for binary/source packages (non-metadata files)
_BINARY_EXTENSIONS = [".deb", ".udeb", ".dsc"]

# Architecture variants
_ARCHITECTURES = st.sampled_from(["amd64", "arm64", "i386", "all", "armhf"])

# Version strings
_VERSIONS = st.sampled_from(["1.0", "2.3.4-1", "11", "0.99", "3.0.1-2", "1.2.3"])

# Simple package name parts (no keywords)
_SAFE_NAME_PARTS = st.sampled_from(["lxde", "core", "python3", "lib", "foo", "bar", "minimal", "gtk"])


@st.composite
def _package_name_containing_keyword(draw: st.DrawFn) -> str:
    """Generate a package directory name that contains a metadata keyword as a substring.

    Examples: lxde-metapackages, testresources, lsb-release-minimal, importlib-resources
    """
    keyword = draw(st.sampled_from(_KEYWORDS))
    prefix = draw(_SAFE_NAME_PARTS)
    suffix = draw(_SAFE_NAME_PARTS)

    # Various patterns for embedding the keyword in a package name
    pattern = draw(
        st.sampled_from(
            [
                f"{prefix}-meta{keyword}",  # e.g., lxde-metapackages
                f"{prefix}-{keyword}-{suffix}",  # e.g., lsb-release-minimal
                f"test{keyword}",  # e.g., testresources
                f"{prefix}-{keyword}",  # e.g., importlib-resources
                f"{keyword}-{suffix}",  # e.g., packages-gtk
                f"{prefix}{keyword}",  # e.g., libpackages
            ]
        )
    )
    return pattern


@st.composite
def _deb_filename(draw: st.DrawFn) -> str:
    """Generate a .deb/.udeb/.dsc filename that does NOT start with a metadata keyword."""
    pkg_name = draw(_SAFE_NAME_PARTS)
    version = draw(_VERSIONS)
    arch = draw(_ARCHITECTURES)
    ext = draw(st.sampled_from(_BINARY_EXTENSIONS))
    return f"{pkg_name}_{version}_{arch}{ext}"


@st.composite
def _url_with_keyword_in_directory(draw: st.DrawFn) -> str:
    """Generate a URL of the form pool/main/<letter>/<package-name-containing-keyword>/<pkg>_<version>_<arch>.deb.

    The directory name contains a metadata keyword as a substring, but the filename
    is a binary package file (.deb, .udeb, .dsc) that should be classified as "unknown".
    """
    dir_name = draw(_package_name_containing_keyword())
    filename = draw(_deb_filename())
    letter = dir_name[0] if dir_name else "a"

    return f"pool/main/{letter}/{dir_name}/{filename}"


# ===========================================================================
# Property 1: Bug Condition - Filename-Based Classification
# ===========================================================================

# Feature: indexer-file-type-classification, Property 1: Bug Condition


@pytest.mark.unit
class TestProperty1BugConditionFileTypeClassification:
    """Property 1: Bug Condition - Filename-Based Classification.

    For any URL where the full path contains a metadata keyword ("packages",
    "sources", "release", "resources") in a directory name but the filename
    is a binary package file (.deb, .udeb, .dsc), the function `_infer_file_type`
    SHALL return "unknown".

    On UNFIXED code, this test is EXPECTED TO FAIL — failure confirms the bug exists.

    **Validates: Requirements 1.1, 1.2, 1.3, 1.4**
    """

    @given(url=_url_with_keyword_in_directory())
    def test_bug_condition_binary_files_in_keyword_directories_are_unknown(self, url: str) -> None:
        """Binary package files in directories containing metadata keywords should be 'unknown'.

        The current (buggy) implementation does substring matching on the full URL,
        so it misclassifies these as metadata types instead of "unknown".
        """
        result = _infer_file_type(url)

        assert result == "unknown", (
            f"Bug confirmed: _infer_file_type({url!r}) returned {result!r} "
            f"instead of 'unknown'. The function is matching a metadata keyword "
            f"from the directory path instead of the filename."
        )


# ===========================================================================
# Strategies for Preservation Property Tests
# ===========================================================================

# Path prefixes for legitimate metadata files
_PATH_PREFIXES = st.sampled_from(
    [
        "dists/bookworm/main/binary-amd64/",
        "dists/bookworm/main/binary-i386/",
        "dists/bookworm/main/binary-arm64/",
        "dists/jammy/universe/binary-amd64/",
        "dists/jammy/universe/source/",
        "dists/stable/main/binary-armhf/",
        "dists/trixie/contrib/binary-amd64/",
        "http://mirror.example.com/debian/dists/stable/main/binary-arm64/",
        "http://archive.ubuntu.com/ubuntu/dists/jammy/main/binary-amd64/",
        "dists/bookworm/main/",
        "dists/bookworm/",
        "",
    ]
)

# Compression extensions (including no compression)
_COMPRESSION_EXTENSIONS = st.sampled_from(["", ".gz", ".xz", ".bz2"])


@st.composite
def _packages_file_url(draw: st.DrawFn) -> str:
    """Generate a legitimate Packages metadata file URL."""
    prefix = draw(_PATH_PREFIXES)
    ext = draw(_COMPRESSION_EXTENSIONS)
    return f"{prefix}Packages{ext}"


@st.composite
def _sources_file_url(draw: st.DrawFn) -> str:
    """Generate a legitimate Sources metadata file URL."""
    prefix = draw(_PATH_PREFIXES)
    ext = draw(_COMPRESSION_EXTENSIONS)
    return f"{prefix}Sources{ext}"


@st.composite
def _contents_file_url(draw: st.DrawFn) -> str:
    """Generate a legitimate Contents metadata file URL."""
    prefix = draw(_PATH_PREFIXES)
    arch = draw(st.sampled_from(["amd64", "i386", "arm64", "armhf", "all", "source"]))
    ext = draw(_COMPRESSION_EXTENSIONS)
    return f"{prefix}Contents-{arch}{ext}"


@st.composite
def _release_file_url(draw: st.DrawFn) -> str:
    """Generate a legitimate Release or InRelease metadata file URL."""
    prefix = draw(_PATH_PREFIXES)
    filename = draw(st.sampled_from(["Release", "InRelease"]))
    return f"{prefix}{filename}"


# ===========================================================================
# Property 2: Preservation - Legitimate Metadata File Classification
# ===========================================================================

# Feature: indexer-file-type-classification, Property 2: Preservation


@pytest.mark.unit
class TestProperty2PreservationMetadataClassification:
    """Property 2: Preservation - Legitimate Metadata File Classification.

    For any URL whose filename (last path segment) matches a known metadata
    pattern, `_infer_file_type` SHALL return the correct classification.
    These tests PASS on the unfixed code and MUST CONTINUE to pass after the fix.

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**
    """

    @given(url=_packages_file_url())
    def test_preservation_packages_files_classified_correctly(self, url: str) -> None:
        """Legitimate Packages files (with various paths/compressions) are classified as 'packages'.

        **Validates: Requirements 3.1**
        """
        result = _infer_file_type(url)
        assert result == "packages", (
            f"Preservation failure: _infer_file_type({url!r}) returned {result!r} "
            f"instead of 'packages'. Legitimate Packages files must be classified correctly."
        )

    @given(url=_sources_file_url())
    def test_preservation_sources_files_classified_correctly(self, url: str) -> None:
        """Legitimate Sources files (with various paths/compressions) are classified as 'sources'.

        **Validates: Requirements 3.2**
        """
        result = _infer_file_type(url)
        assert result == "sources", (
            f"Preservation failure: _infer_file_type({url!r}) returned {result!r} "
            f"instead of 'sources'. Legitimate Sources files must be classified correctly."
        )

    @given(url=_contents_file_url())
    def test_preservation_contents_files_classified_correctly(self, url: str) -> None:
        """Legitimate Contents files (with various paths/compressions) are classified as 'contents'.

        **Validates: Requirements 3.3**
        """
        result = _infer_file_type(url)
        assert result == "contents", (
            f"Preservation failure: _infer_file_type({url!r}) returned {result!r} "
            f"instead of 'contents'. Legitimate Contents files must be classified correctly."
        )

    @given(url=_release_file_url())
    def test_preservation_release_files_classified_correctly(self, url: str) -> None:
        """Legitimate Release/InRelease files are classified as 'release'.

        **Validates: Requirements 3.4**
        """
        result = _infer_file_type(url)
        assert result == "release", (
            f"Preservation failure: _infer_file_type({url!r}) returned {result!r} "
            f"instead of 'release'. Legitimate Release/InRelease files must be classified correctly."
        )

    def test_preservation_function_signature_unchanged(self) -> None:
        """The function accepts a single string argument and returns a string.

        **Validates: Requirements 3.6**
        """
        # Verify the function works with a string argument and returns a string
        result = _infer_file_type("dists/bookworm/main/binary-amd64/Packages.gz")
        assert isinstance(result, str)
        assert result in {"packages", "sources", "contents", "release", "unknown"}

    def test_preservation_unrecognized_filenames_are_unknown(self) -> None:
        """Files with unrecognized filenames continue to be classified as 'unknown'.

        **Validates: Requirements 3.5**
        """
        # These paths should NOT contain any metadata keywords at all
        unknown_urls = [
            "dists/bookworm/main/binary-amd64/other-file.txt",
            "some/random/path/data.bin",
            "archive/file.tar.gz",
        ]
        for url in unknown_urls:
            result = _infer_file_type(url)
            assert result == "unknown", (
                f"Preservation failure: _infer_file_type({url!r}) returned {result!r} "
                f"instead of 'unknown'. Unrecognized filenames must remain 'unknown'."
            )
