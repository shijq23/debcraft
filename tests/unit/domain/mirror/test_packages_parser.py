"""Unit tests for domain/mirror/packages_parser.py."""

import pytest

from debcraft.domain.mirror.packages_parser import PackagesParser
from debcraft.domain.mirror.values import FileEntry


@pytest.mark.unit
@pytest.mark.mirror
class TestPackagesParserParse:
    """Tests for PackagesParser.parse()."""

    def setup_method(self):
        self.parser = PackagesParser()

    def test_single_complete_stanza(self):
        content = (
            "Package: libssl3\n"
            "Version: 3.0.2-0ubuntu1\n"
            "Architecture: amd64\n"
            "Filename: pool/main/l/libssl3/libssl3_3.0.2-0ubuntu1_amd64.deb\n"
            "Size: 1234567\n"
            "SHA256: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\n"
        )
        result = self.parser.parse(content)
        assert len(result) == 1
        assert result[0] == FileEntry(
            relative_path="pool/main/l/libssl3/libssl3_3.0.2-0ubuntu1_amd64.deb",
            sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            size_bytes=1234567,
        )

    def test_multiple_stanzas(self):
        content = (
            "Package: pkg-a\n"
            "Filename: pool/main/a/pkg-a/pkg-a_1.0_amd64.deb\n"
            "Size: 100\n"
            "SHA256: aaaa000000000000000000000000000000000000000000000000000000000000\n"
            "\n"
            "Package: pkg-b\n"
            "Filename: pool/main/b/pkg-b/pkg-b_2.0_arm64.deb\n"
            "Size: 200\n"
            "SHA256: bbbb000000000000000000000000000000000000000000000000000000000000\n"
        )
        result = self.parser.parse(content)
        assert len(result) == 2
        assert result[0].relative_path == "pool/main/a/pkg-a/pkg-a_1.0_amd64.deb"
        assert result[0].size_bytes == 100
        assert result[1].relative_path == "pool/main/b/pkg-b/pkg-b_2.0_arm64.deb"
        assert result[1].size_bytes == 200

    def test_empty_content_returns_empty_list(self):
        assert self.parser.parse("") == []

    def test_whitespace_only_content_returns_empty_list(self):
        assert self.parser.parse("   \n  \n  ") == []

    def test_missing_sha256_field_skips_stanza(self):
        content = "Package: no-hash\nFilename: pool/main/n/no-hash/no-hash_1.0.deb\nSize: 500\n"
        result = self.parser.parse(content)
        assert result == []

    def test_missing_filename_field_skips_stanza(self):
        content = (
            "Package: no-file\nSize: 500\nSHA256: abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234\n"
        )
        result = self.parser.parse(content)
        assert result == []

    def test_missing_size_field_skips_stanza(self):
        content = (
            "Package: no-size\n"
            "Filename: pool/main/n/no-size/no-size_1.0.deb\n"
            "SHA256: abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234\n"
        )
        result = self.parser.parse(content)
        assert result == []

    def test_invalid_size_value_skips_stanza(self):
        content = (
            "Package: bad-size\n"
            "Filename: pool/main/b/bad-size/bad-size_1.0.deb\n"
            "Size: not-a-number\n"
            "SHA256: abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234\n"
        )
        result = self.parser.parse(content)
        assert result == []

    def test_negative_size_skips_stanza(self):
        content = (
            "Package: neg-size\n"
            "Filename: pool/main/n/neg-size/neg-size_1.0.deb\n"
            "Size: -100\n"
            "SHA256: abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234\n"
        )
        result = self.parser.parse(content)
        assert result == []

    def test_multiline_continuation_ignored(self):
        """Multi-line continuations (indented lines) should not interfere with parsing."""
        content = (
            "Package: libssl3\n"
            "Version: 3.0.2\n"
            "Description: OpenSSL shared library\n"
            " This is a multi-line\n"
            " continuation of the Description.\n"
            " It spans multiple lines.\n"
            "Filename: pool/main/l/libssl3/libssl3_3.0.2_amd64.deb\n"
            "Size: 999\n"
            "SHA256: 1111111111111111111111111111111111111111111111111111111111111111\n"
        )
        result = self.parser.parse(content)
        assert len(result) == 1
        assert result[0].relative_path == "pool/main/l/libssl3/libssl3_3.0.2_amd64.deb"
        assert result[0].size_bytes == 999

    def test_multiple_blank_lines_between_stanzas(self):
        content = (
            "Package: pkg-a\n"
            "Filename: pool/a.deb\n"
            "Size: 10\n"
            "SHA256: aaaa000000000000000000000000000000000000000000000000000000000000\n"
            "\n"
            "\n"
            "\n"
            "Package: pkg-b\n"
            "Filename: pool/b.deb\n"
            "Size: 20\n"
            "SHA256: bbbb000000000000000000000000000000000000000000000000000000000000\n"
        )
        result = self.parser.parse(content)
        assert len(result) == 2

    def test_stanza_with_valid_and_invalid_mixed(self):
        """Only stanzas with all required fields are included."""
        content = (
            "Package: valid\n"
            "Filename: pool/valid.deb\n"
            "Size: 42\n"
            "SHA256: cccc000000000000000000000000000000000000000000000000000000000000\n"
            "\n"
            "Package: invalid-missing-sha\n"
            "Filename: pool/invalid.deb\n"
            "Size: 100\n"
            "\n"
            "Package: also-valid\n"
            "Filename: pool/also-valid.deb\n"
            "Size: 77\n"
            "SHA256: dddd000000000000000000000000000000000000000000000000000000000000\n"
        )
        result = self.parser.parse(content)
        assert len(result) == 2
        assert result[0].relative_path == "pool/valid.deb"
        assert result[1].relative_path == "pool/also-valid.deb"

    def test_size_zero_is_valid(self):
        content = (
            "Package: empty-pkg\n"
            "Filename: pool/empty.deb\n"
            "Size: 0\n"
            "SHA256: 0000000000000000000000000000000000000000000000000000000000000000\n"
        )
        result = self.parser.parse(content)
        assert len(result) == 1
        assert result[0].size_bytes == 0

    def test_filename_uses_relative_path_field(self):
        """The Filename value should map to FileEntry.relative_path."""
        content = (
            "Package: test\n"
            "Filename: pool/main/t/test/test_1.0_all.deb\n"
            "Size: 512\n"
            "SHA256: abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789\n"
        )
        result = self.parser.parse(content)
        assert result[0].relative_path == "pool/main/t/test/test_1.0_all.deb"

    def test_trailing_newline_at_end_of_file(self):
        content = (
            "Package: test\n"
            "Filename: pool/test.deb\n"
            "Size: 1\n"
            "SHA256: 1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef\n"
            "\n"
        )
        result = self.parser.parse(content)
        assert len(result) == 1

    def test_no_package_field_still_parses_if_required_fields_present(self):
        """Package field is not required for extraction — only Filename, SHA256, Size."""
        content = (
            "Filename: pool/orphan.deb\n"
            "Size: 10\n"
            "SHA256: ffff000000000000000000000000000000000000000000000000000000000000\n"
        )
        result = self.parser.parse(content)
        assert len(result) == 1
        assert result[0].relative_path == "pool/orphan.deb"

    def test_large_file_count(self):
        """Parser handles 50+ stanzas correctly."""
        stanzas = []
        for i in range(60):
            sha = f"{i:064x}"
            stanzas.append(
                f"Package: pkg-{i}\n"
                f"Filename: pool/main/p/pkg-{i}/pkg-{i}_{i}.0_amd64.deb\n"
                f"Size: {1000 + i}\n"
                f"SHA256: {sha}\n"
            )
        content = "\n".join(stanzas)
        result = self.parser.parse(content)
        assert len(result) == 60
        # Spot-check first and last entries
        assert result[0].relative_path == "pool/main/p/pkg-0/pkg-0_0.0_amd64.deb"
        assert result[0].size_bytes == 1000
        assert result[59].relative_path == "pool/main/p/pkg-59/pkg-59_59.0_amd64.deb"
        assert result[59].size_bytes == 1059
