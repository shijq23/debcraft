"""Unit tests for download location resolver.

Tests cover specific URL patterns, edge cases with trailing/leading slashes,
and NOASSERTION fallback for missing inputs.
"""

from __future__ import annotations

import pytest

from debcraft.domain.package_intelligence.download_location import (
    resolve_download_location,
)


@pytest.mark.unit
class TestDownloadLocationValidInputs:
    """Verify URL construction for valid base_url and filename combinations."""

    def test_basic_join(self):
        """Base URL without trailing slash joined with filename."""
        result = resolve_download_location(
            "https://deb.debian.org/debian",
            "pool/main/g/glibc/libc6_2.40_amd64.deb",
        )
        assert result == "https://deb.debian.org/debian/pool/main/g/glibc/libc6_2.40_amd64.deb"

    def test_trailing_slash_on_base_url(self):
        """Base URL with trailing slash does not produce double-slash."""
        result = resolve_download_location(
            "https://deb.debian.org/debian/",
            "pool/main/g/glibc/libc6_2.40_amd64.deb",
        )
        assert result == "https://deb.debian.org/debian/pool/main/g/glibc/libc6_2.40_amd64.deb"

    def test_leading_slash_on_filename(self):
        """Filename with leading slash does not produce double-slash."""
        result = resolve_download_location(
            "https://deb.debian.org/debian",
            "/pool/main/g/glibc/libc6_2.40_amd64.deb",
        )
        assert result == "https://deb.debian.org/debian/pool/main/g/glibc/libc6_2.40_amd64.deb"

    def test_both_trailing_and_leading_slashes(self):
        """Both trailing slash on base and leading slash on filename."""
        result = resolve_download_location(
            "https://deb.debian.org/debian/",
            "/pool/main/g/glibc/libc6_2.40_amd64.deb",
        )
        assert result == "https://deb.debian.org/debian/pool/main/g/glibc/libc6_2.40_amd64.deb"

    def test_multiple_trailing_slashes(self):
        """Multiple trailing slashes on base URL are all stripped."""
        result = resolve_download_location(
            "https://repo.example.com///",
            "packages/foo.deb",
        )
        assert result == "https://repo.example.com/packages/foo.deb"

    def test_multiple_leading_slashes_on_filename(self):
        """Multiple leading slashes on filename are all stripped."""
        result = resolve_download_location(
            "https://repo.example.com",
            "///packages/foo.deb",
        )
        assert result == "https://repo.example.com/packages/foo.deb"


@pytest.mark.unit
class TestDownloadLocationNoassertion:
    """Verify NOASSERTION returned for missing/empty/whitespace inputs."""

    def test_none_base_url(self):
        """None base_url returns NOASSERTION."""
        assert resolve_download_location(None, "pool/main/foo.deb") == "NOASSERTION"

    def test_empty_base_url(self):
        """Empty base_url returns NOASSERTION."""
        assert resolve_download_location("", "pool/main/foo.deb") == "NOASSERTION"

    def test_whitespace_base_url(self):
        """Whitespace-only base_url returns NOASSERTION."""
        assert resolve_download_location("   ", "pool/main/foo.deb") == "NOASSERTION"

    def test_none_filename(self):
        """None filename returns NOASSERTION."""
        assert resolve_download_location("https://example.com", None) == "NOASSERTION"

    def test_empty_filename(self):
        """Empty filename returns NOASSERTION."""
        assert resolve_download_location("https://example.com", "") == "NOASSERTION"

    def test_whitespace_filename(self):
        """Whitespace-only filename returns NOASSERTION."""
        assert resolve_download_location("https://example.com", "   ") == "NOASSERTION"

    def test_both_none(self):
        """Both inputs None returns NOASSERTION."""
        assert resolve_download_location(None, None) == "NOASSERTION"

    def test_both_empty(self):
        """Both inputs empty returns NOASSERTION."""
        assert resolve_download_location("", "") == "NOASSERTION"

    def test_both_whitespace(self):
        """Both inputs whitespace returns NOASSERTION."""
        assert resolve_download_location("  ", "\t") == "NOASSERTION"
