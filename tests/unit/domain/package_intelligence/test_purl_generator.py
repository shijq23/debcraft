"""Unit tests for PURL generator.

Tests cover format correctness, percent-encoding of special characters,
default distro, arch=all handling, and PURLGenerationError for missing fields.
"""

from __future__ import annotations

import pytest

from debcraft.domain.package_intelligence.errors import PURLGenerationError
from debcraft.domain.package_intelligence.purl_generator import generate_purl


@pytest.mark.unit
class TestPURLGeneratorValidInputs:
    """Verify PURL generation for valid inputs."""

    def test_basic_purl_format(self):
        """Standard package produces correct PURL format."""
        result = generate_purl("libc6", "2.40-1", "amd64", "debian")
        assert result == "pkg:deb/debian/libc6@2.40-1?arch=amd64"

    def test_default_distro_when_none(self):
        """Distro defaults to 'debian' when None."""
        result = generate_purl("libc6", "2.40-1", "amd64", None)
        assert result == "pkg:deb/debian/libc6@2.40-1?arch=amd64"

    def test_default_distro_when_empty(self):
        """Distro defaults to 'debian' when empty string."""
        result = generate_purl("libc6", "2.40-1", "amd64", "")
        assert result == "pkg:deb/debian/libc6@2.40-1?arch=amd64"

    def test_default_distro_when_whitespace(self):
        """Distro defaults to 'debian' when whitespace-only."""
        result = generate_purl("libc6", "2.40-1", "amd64", "   ")
        assert result == "pkg:deb/debian/libc6@2.40-1?arch=amd64"

    def test_distro_lowercased(self):
        """Distro value is lowercased."""
        result = generate_purl("libc6", "2.40-1", "amd64", "Ubuntu")
        assert result == "pkg:deb/ubuntu/libc6@2.40-1?arch=amd64"

    def test_distro_mixed_case(self):
        """Distro with mixed case is lowercased."""
        result = generate_purl("libc6", "2.40-1", "amd64", "DEBIAN")
        assert result == "pkg:deb/debian/libc6@2.40-1?arch=amd64"

    def test_arch_all(self):
        """Architecture 'all' is included as ?arch=all."""
        result = generate_purl("python3-all", "3.12.0-1", "all", "debian")
        assert result == "pkg:deb/debian/python3-all@3.12.0-1?arch=all"

    def test_arch_arm64(self):
        """ARM64 architecture is preserved."""
        result = generate_purl("libc6", "2.40-1", "arm64", "debian")
        assert result == "pkg:deb/debian/libc6@2.40-1?arch=arm64"


@pytest.mark.unit
class TestPURLGeneratorPercentEncoding:
    """Verify percent-encoding of special PURL characters."""

    def test_colon_in_version_epoch(self):
        """Colon in epoch version is percent-encoded."""
        result = generate_purl("libc6", "1:2.40-1", "amd64", "debian")
        assert result == "pkg:deb/debian/libc6@1%3A2.40-1?arch=amd64"

    def test_plus_in_version(self):
        """Plus in version string is percent-encoded."""
        result = generate_purl("libfoo", "2.0+dfsg-1", "amd64", "debian")
        assert result == "pkg:deb/debian/libfoo@2.0%2Bdfsg-1?arch=amd64"

    def test_at_sign_in_version(self):
        """At sign in version is percent-encoded."""
        result = generate_purl("pkg", "1.0@beta", "amd64", "debian")
        assert result == "pkg:deb/debian/pkg@1.0%40beta?arch=amd64"

    def test_question_mark_in_version(self):
        """Question mark in version is percent-encoded."""
        result = generate_purl("pkg", "1.0?rc1", "amd64", "debian")
        assert result == "pkg:deb/debian/pkg@1.0%3Frc1?arch=amd64"

    def test_hash_in_version(self):
        """Hash in version is percent-encoded."""
        result = generate_purl("pkg", "1.0#1", "amd64", "debian")
        assert result == "pkg:deb/debian/pkg@1.0%231?arch=amd64"

    def test_multiple_special_chars_in_version(self):
        """Multiple special characters are all percent-encoded."""
        result = generate_purl("libc6", "1:2.0+dfsg-1", "amd64", "debian")
        assert result == "pkg:deb/debian/libc6@1%3A2.0%2Bdfsg-1?arch=amd64"

    def test_plus_in_package_name(self):
        """Plus in package name is percent-encoded."""
        result = generate_purl("g++", "12.3-1", "amd64", "debian")
        assert result == "pkg:deb/debian/g%2B%2B@12.3-1?arch=amd64"

    def test_colon_in_package_name(self):
        """Colon in package name is percent-encoded."""
        result = generate_purl("libc6:amd64", "2.40-1", "amd64", "debian")
        assert result == "pkg:deb/debian/libc6%3Aamd64@2.40-1?arch=amd64"


@pytest.mark.unit
class TestPURLGeneratorMissingFields:
    """Verify PURLGenerationError for missing required fields."""

    def test_none_package_name(self):
        """None package_name raises PURLGenerationError."""
        with pytest.raises(PURLGenerationError) as exc_info:
            generate_purl(None, "1.0", "amd64", "debian")  # type: ignore[arg-type]
        assert exc_info.value.missing_field == "package_name"

    def test_empty_package_name(self):
        """Empty package_name raises PURLGenerationError."""
        with pytest.raises(PURLGenerationError) as exc_info:
            generate_purl("", "1.0", "amd64", "debian")
        assert exc_info.value.missing_field == "package_name"

    def test_whitespace_package_name(self):
        """Whitespace-only package_name raises PURLGenerationError."""
        with pytest.raises(PURLGenerationError) as exc_info:
            generate_purl("   ", "1.0", "amd64", "debian")
        assert exc_info.value.missing_field == "package_name"

    def test_none_version(self):
        """None version raises PURLGenerationError."""
        with pytest.raises(PURLGenerationError) as exc_info:
            generate_purl("libc6", None, "amd64", "debian")  # type: ignore[arg-type]
        assert exc_info.value.missing_field == "version"

    def test_empty_version(self):
        """Empty version raises PURLGenerationError."""
        with pytest.raises(PURLGenerationError) as exc_info:
            generate_purl("libc6", "", "amd64", "debian")
        assert exc_info.value.missing_field == "version"

    def test_whitespace_version(self):
        """Whitespace-only version raises PURLGenerationError."""
        with pytest.raises(PURLGenerationError) as exc_info:
            generate_purl("libc6", "  \t", "amd64", "debian")
        assert exc_info.value.missing_field == "version"

    def test_none_architecture(self):
        """None architecture raises PURLGenerationError."""
        with pytest.raises(PURLGenerationError) as exc_info:
            generate_purl("libc6", "1.0", None, "debian")  # type: ignore[arg-type]
        assert exc_info.value.missing_field == "architecture"

    def test_empty_architecture(self):
        """Empty architecture raises PURLGenerationError."""
        with pytest.raises(PURLGenerationError) as exc_info:
            generate_purl("libc6", "1.0", "", "debian")
        assert exc_info.value.missing_field == "architecture"

    def test_whitespace_architecture(self):
        """Whitespace-only architecture raises PURLGenerationError."""
        with pytest.raises(PURLGenerationError) as exc_info:
            generate_purl("libc6", "1.0", "   ", "debian")
        assert exc_info.value.missing_field == "architecture"
