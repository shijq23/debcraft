"""Unit tests for domain/mirror/release_parser.py."""

import pytest

from debcraft.domain.mirror.errors import ReleaseParseError
from debcraft.domain.mirror.release_parser import ReleaseMetadata, ReleaseParser
from debcraft.domain.mirror.values import FileEntry


@pytest.mark.unit
@pytest.mark.mirror
class TestReleaseMetadata:
    """Tests for the ReleaseMetadata frozen dataclass."""

    def test_default_construction(self):
        meta = ReleaseMetadata()
        assert meta.files == []
        assert meta.date is None
        assert meta.codename is None
        assert meta.origin is None
        assert meta.label is None
        assert meta.suite is None
        assert meta.architectures is None

    def test_with_files(self):
        entries = [FileEntry(relative_path="main/binary-amd64/Packages", sha256="a" * 64, size_bytes=100)]
        meta = ReleaseMetadata(files=entries, codename="bookworm")
        assert len(meta.files) == 1
        assert meta.codename == "bookworm"

    def test_frozen(self):
        meta = ReleaseMetadata()
        with pytest.raises(AttributeError):
            meta.codename = "test"  # type: ignore[misc]


@pytest.mark.unit
@pytest.mark.mirror
class TestReleaseParser:
    """Tests for the ReleaseParser class."""

    def setup_method(self):
        self.parser = ReleaseParser()

    def test_parse_basic_release_file(self):
        content = (
            "Origin: Debian\n"
            "Codename: bookworm\n"
            "Date: Sat, 01 Jan 2024 00:00:00 UTC\n"
            "SHA256:\n"
            " e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  1234 main/binary-amd64/Packages\n"
            " a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2  5678 main/binary-amd64/Packages.gz\n"
        )
        result = self.parser.parse(content)
        assert len(result.files) == 2
        assert result.files[0].relative_path == "main/binary-amd64/Packages"
        assert result.files[0].sha256 == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert result.files[0].size_bytes == 1234
        assert result.files[1].relative_path == "main/binary-amd64/Packages.gz"
        assert result.files[1].size_bytes == 5678
        assert result.codename == "bookworm"
        assert result.origin == "Debian"
        assert result.date == "Sat, 01 Jan 2024 00:00:00 UTC"

    def test_parse_sha256sums_header(self):
        content = "SHA256Sums:\n e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  1234 main/Packages\n"
        result = self.parser.parse(content)
        assert len(result.files) == 1
        assert result.files[0].relative_path == "main/Packages"

    def test_parse_multiple_spaces_between_fields(self):
        content = (
            "SHA256:\n"
            " e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855    1234   main/binary-amd64/Packages\n"
        )
        result = self.parser.parse(content)
        assert len(result.files) == 1
        assert result.files[0].size_bytes == 1234
        assert result.files[0].relative_path == "main/binary-amd64/Packages"

    def test_parse_section_ends_at_non_indented_line(self):
        content = (
            "SHA256:\n"
            " e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  1234 main/Packages\n"
            "MD5Sum:\n"
            " d41d8cd98f00b204e9800998ecf8427e  1234 main/Packages\n"
        )
        result = self.parser.parse(content)
        assert len(result.files) == 1
        assert result.files[0].sha256 == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_parse_section_ends_at_eof(self):
        content = (
            "SHA256:\n"
            " e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  1234 main/Packages\n"
            " a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2  5678 main/Sources\n"
        )
        result = self.parser.parse(content)
        assert len(result.files) == 2

    def test_parse_metadata_fields(self):
        content = (
            "Origin: TestOrg\n"
            "Label: TestLabel\n"
            "Suite: stable\n"
            "Codename: bookworm\n"
            "Date: Mon, 15 Jan 2024 12:00:00 UTC\n"
            "Architectures: amd64 arm64\n"
            "SHA256:\n"
            " e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  100 main/Packages\n"
        )
        result = self.parser.parse(content)
        assert result.origin == "TestOrg"
        assert result.label == "TestLabel"
        assert result.suite == "stable"
        assert result.codename == "bookworm"
        assert result.date == "Mon, 15 Jan 2024 12:00:00 UTC"
        assert result.architectures == "amd64 arm64"

    def test_parse_zero_size_file(self):
        content = "SHA256:\n e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  0 main/empty\n"
        result = self.parser.parse(content)
        assert result.files[0].size_bytes == 0

    def test_raises_on_empty_content(self):
        with pytest.raises(ReleaseParseError, match="empty"):
            self.parser.parse("")

    def test_raises_on_whitespace_only_content(self):
        with pytest.raises(ReleaseParseError, match="empty"):
            self.parser.parse("   \n  \n  ")

    def test_raises_on_missing_sha256_section(self):
        content = "Origin: Debian\nCodename: bookworm\nMD5Sum:\n d41d8cd98f00b204e9800998ecf8427e  1234 main/Packages\n"
        with pytest.raises(ReleaseParseError, match="No SHA256"):
            self.parser.parse(content)

    def test_raises_on_sha256_section_with_no_entries(self):
        content = "SHA256:\nMD5Sum:\n d41d8cd98f00b204e9800998ecf8427e  1234 main/Packages\n"
        with pytest.raises(ReleaseParseError, match="no valid entries"):
            self.parser.parse(content)

    def test_raises_on_malformed_entry_too_few_fields(self):
        content = "SHA256:\n e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  1234\n"
        with pytest.raises(ReleaseParseError, match="expected 3 fields"):
            self.parser.parse(content)

    def test_raises_on_malformed_entry_too_many_fields(self):
        content = (
            "SHA256:\n e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  1234 main/Packages extra\n"
        )
        with pytest.raises(ReleaseParseError, match="expected 3 fields"):
            self.parser.parse(content)

    def test_raises_on_invalid_hash_length(self):
        content = "SHA256:\n abcdef  1234 main/Packages\n"
        with pytest.raises(ReleaseParseError, match="expected 64 hex characters"):
            self.parser.parse(content)

    def test_raises_on_non_hex_hash(self):
        content = "SHA256:\n zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz  1234 main/Packages\n"
        with pytest.raises(ReleaseParseError, match="non-hex characters"):
            self.parser.parse(content)

    def test_raises_on_non_integer_size(self):
        content = "SHA256:\n e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  abc main/Packages\n"
        with pytest.raises(ReleaseParseError, match="not a valid integer"):
            self.parser.parse(content)

    def test_raises_on_negative_size(self):
        content = "SHA256:\n e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  -1 main/Packages\n"
        with pytest.raises(ReleaseParseError, match="cannot be negative"):
            self.parser.parse(content)

    def test_url_included_in_error(self):
        with pytest.raises(ReleaseParseError) as exc_info:
            self.parser.parse("", url="https://example.com/Release")
        assert "https://example.com/Release" in str(exc_info.value)

    def test_parse_real_world_like_content(self):
        content = (
            "Origin: eLxr\n"
            "Label: eLxr\n"
            "Suite: elxr3\n"
            "Codename: elxr3\n"
            "Date: Sat, 01 Jun 2024 00:00:00 UTC\n"
            "Architectures: amd64 arm64\n"
            "Components: main\n"
            "SHA256:\n"
            " 4a8e0c0fd6e5c3b2a1d0e9f8c7b6a5d4e3f2c1b0a9d8e7f6c5b4a3d2e1f0c9b8  523456 main/binary-amd64/Packages\n"
            " 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b  87654 main/binary-amd64/Packages.gz\n"
            " 9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f0e9d8c7b6a5f4e3d2c1b0a9f8e  412345 main/binary-arm64/Packages\n"
            " 0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b  76543 main/binary-arm64/Packages.gz\n"
            " 5f4e3d2c1b0a9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f0e9d8c7b6a5f4e  198765 main/source/Sources\n"
            " 2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c  45678 main/source/Sources.gz\n"
        )
        result = self.parser.parse(content, url="https://mirror.elxr.dev/elxr/dists/elxr3/Release")
        assert len(result.files) == 6
        assert result.origin == "eLxr"
        assert result.suite == "elxr3"
        assert result.codename == "elxr3"
        assert result.files[0].relative_path == "main/binary-amd64/Packages"
        assert result.files[0].size_bytes == 523456
        assert result.files[4].relative_path == "main/source/Sources"

    def test_skips_blank_indented_lines(self):
        content = (
            "SHA256:\n"
            " e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  1234 main/Packages\n"
            "   \n"
            " a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2  5678 main/Sources\n"
        )
        result = self.parser.parse(content)
        assert len(result.files) == 2
