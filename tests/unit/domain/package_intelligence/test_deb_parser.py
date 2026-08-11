"""Unit tests for the DebParser domain logic."""

from __future__ import annotations

import io
import tarfile

import pytest

from debcraft.domain.package_intelligence.deb_parser import DebParser
from debcraft.domain.package_intelligence.errors import (
    DebParseError,
)
from debcraft.domain.package_intelligence.values import (
    DebParseResult,
)


def _make_tar_bytes(files: dict[str, str]) -> bytes:
    """Create a tar archive in memory with given file name -> content mapping."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tar:
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeFileReader:
    """Fake DebFileReader for testing."""

    def __init__(
        self,
        ar_header: bytes = b"!<arch>\n",
        debian_binary: bytes = b"2.0\n",
        control_tar: bytes | None = None,
        data_tar: bytes | None = None,
        missing_members: set[str] | None = None,
    ) -> None:
        self.ar_header = ar_header
        self.debian_binary = debian_binary
        self.control_tar = control_tar
        self.data_tar = data_tar
        self.missing_members = missing_members or set()

    def read_ar_member(self, deb_path: str, member_prefix: str) -> bytes:
        if member_prefix in self.missing_members:
            raise FileNotFoundError(f"Member '{member_prefix}' not found")
        if member_prefix == "":
            return self.ar_header
        if member_prefix == "debian-binary":
            return self.debian_binary
        if member_prefix == "control.tar":
            if self.control_tar is None:
                raise FileNotFoundError("control.tar not found")
            return self.control_tar
        if member_prefix == "data.tar":
            if self.data_tar is None:
                raise FileNotFoundError("data.tar not found")
            return self.data_tar
        raise FileNotFoundError(f"Unknown member: {member_prefix}")

    def compute_sha256(self, file_path: str) -> str:
        return "fake_sha256"


@pytest.mark.unit
class TestDebParserInit:
    """Tests for DebParser initialization."""

    def test_parser_version_is_one(self) -> None:
        assert DebParser.PARSER_VERSION == 1

    def test_accepts_file_reader(self) -> None:
        reader = FakeFileReader()
        parser = DebParser(reader)
        assert parser._file_reader is reader


@pytest.mark.unit
class TestArMagicValidation:
    """Tests for ar archive magic byte validation."""

    def test_valid_magic_passes(self) -> None:
        control_tar = _make_tar_bytes({"./control": "Package: test\nVersion: 1.0\nArchitecture: amd64\n"})
        data_tar = _make_tar_bytes({"./usr/bin/test": ""})
        reader = FakeFileReader(control_tar=control_tar, data_tar=data_tar)
        parser = DebParser(reader)
        # Should not raise
        result = parser.parse("/fake/test.deb")
        assert result.package_name == "test"

    def test_invalid_magic_raises_error(self) -> None:
        reader = FakeFileReader(ar_header=b"not_an_archive")
        parser = DebParser(reader)
        with pytest.raises(DebParseError, match="missing magic bytes"):
            parser.parse("/fake/test.deb")

    def test_empty_file_raises_error(self) -> None:
        reader = FakeFileReader(ar_header=b"")
        parser = DebParser(reader)
        with pytest.raises(DebParseError, match="missing magic bytes"):
            parser.parse("/fake/test.deb")


@pytest.mark.unit
class TestDebianBinaryValidation:
    """Tests for debian-binary version validation."""

    def test_version_2_0_passes(self) -> None:
        control_tar = _make_tar_bytes({"./control": "Package: test\nVersion: 1.0\nArchitecture: amd64\n"})
        data_tar = _make_tar_bytes({"./usr/bin/test": ""})
        reader = FakeFileReader(debian_binary=b"2.0\n", control_tar=control_tar, data_tar=data_tar)
        parser = DebParser(reader)
        result = parser.parse("/fake/test.deb")
        assert result.package_name == "test"

    def test_version_2_1_passes(self) -> None:
        control_tar = _make_tar_bytes({"./control": "Package: test\nVersion: 1.0\nArchitecture: amd64\n"})
        data_tar = _make_tar_bytes({"./usr/bin/test": ""})
        reader = FakeFileReader(debian_binary=b"2.1\n", control_tar=control_tar, data_tar=data_tar)
        parser = DebParser(reader)
        result = parser.parse("/fake/test.deb")
        assert result.package_name == "test"

    def test_version_3_0_raises_error(self) -> None:
        reader = FakeFileReader(debian_binary=b"3.0\n")
        parser = DebParser(reader)
        with pytest.raises(DebParseError, match="Unsupported debian-binary version"):
            parser.parse("/fake/test.deb")

    def test_version_1_0_raises_error(self) -> None:
        reader = FakeFileReader(debian_binary=b"1.0\n")
        parser = DebParser(reader)
        with pytest.raises(DebParseError, match="Unsupported debian-binary version"):
            parser.parse("/fake/test.deb")


@pytest.mark.unit
class TestControlExtraction:
    """Tests for control file extraction and parsing."""

    def test_basic_control_fields(self) -> None:
        control_text = (
            "Package: libc6\n"
            "Version: 2.40-1\n"
            "Architecture: amd64\n"
            "Maintainer: GNU Libc Maintainers <debian-glibc@lists.debian.org>\n"
            "Description: GNU C Library: Shared libraries\n"
            " Lots of details here\n"
        )
        control_tar = _make_tar_bytes({"./control": control_text})
        data_tar = _make_tar_bytes({"./usr/lib/libc.so.6": ""})
        reader = FakeFileReader(control_tar=control_tar, data_tar=data_tar)
        parser = DebParser(reader)
        result = parser.parse("/fake/libc6.deb")

        assert result.package_name == "libc6"
        assert result.version == "2.40-1"
        assert result.architecture == "amd64"
        assert result.control_fields["Maintainer"] == "GNU Libc Maintainers <debian-glibc@lists.debian.org>"
        assert "Lots of details here" in result.control_fields["Description"]

    def test_control_without_leading_dot_slash(self) -> None:
        control_text = "Package: simple\nVersion: 1.0\nArchitecture: all\n"
        control_tar = _make_tar_bytes({"control": control_text})
        data_tar = _make_tar_bytes({"./usr/bin/simple": ""})
        reader = FakeFileReader(control_tar=control_tar, data_tar=data_tar)
        parser = DebParser(reader)
        result = parser.parse("/fake/simple.deb")
        assert result.package_name == "simple"

    def test_missing_control_tar_raises_error(self) -> None:
        reader = FakeFileReader(missing_members={"control.tar"})
        parser = DebParser(reader)
        with pytest.raises(DebParseError, match=r"control\.tar"):
            parser.parse("/fake/test.deb")

    def test_multiline_description(self) -> None:
        control_text = (
            "Package: test\n"
            "Version: 1.0\n"
            "Architecture: all\n"
            "Description: Short desc\n"
            " This is a longer description\n"
            " spanning multiple lines.\n"
        )
        control_tar = _make_tar_bytes({"./control": control_text})
        data_tar = _make_tar_bytes({"./usr/bin/test": ""})
        reader = FakeFileReader(control_tar=control_tar, data_tar=data_tar)
        parser = DebParser(reader)
        result = parser.parse("/fake/test.deb")
        desc = result.control_fields["Description"]
        assert "Short desc" in desc
        assert "This is a longer description" in desc
        assert "spanning multiple lines." in desc


@pytest.mark.unit
class TestDependencyParsing:
    """Tests for dependency field parsing."""

    def test_simple_dependency(self) -> None:
        control_text = "Package: test\nVersion: 1.0\nArchitecture: amd64\nDepends: libc6 (>= 2.17)\n"
        control_tar = _make_tar_bytes({"./control": control_text})
        data_tar = _make_tar_bytes({"./usr/bin/test": ""})
        reader = FakeFileReader(control_tar=control_tar, data_tar=data_tar)
        parser = DebParser(reader)
        result = parser.parse("/fake/test.deb")

        assert len(result.dependencies) == 1
        assert result.dependencies[0].package == "libc6"
        assert result.dependencies[0].version_constraint == ">= 2.17"

    def test_multiple_dependencies(self) -> None:
        control_text = (
            "Package: test\nVersion: 1.0\nArchitecture: amd64\nDepends: libc6 (>= 2.17), libz1, libssl3 (>= 3.0)\n"
        )
        control_tar = _make_tar_bytes({"./control": control_text})
        data_tar = _make_tar_bytes({"./usr/bin/test": ""})
        reader = FakeFileReader(control_tar=control_tar, data_tar=data_tar)
        parser = DebParser(reader)
        result = parser.parse("/fake/test.deb")

        assert len(result.dependencies) == 3
        assert result.dependencies[0].package == "libc6"
        assert result.dependencies[1].package == "libz1"
        assert result.dependencies[1].version_constraint is None
        assert result.dependencies[2].package == "libssl3"

    def test_alternative_dependencies(self) -> None:
        control_text = "Package: test\nVersion: 1.0\nArchitecture: amd64\nDepends: editor | vim | nano\n"
        control_tar = _make_tar_bytes({"./control": control_text})
        data_tar = _make_tar_bytes({"./usr/bin/test": ""})
        reader = FakeFileReader(control_tar=control_tar, data_tar=data_tar)
        parser = DebParser(reader)
        result = parser.parse("/fake/test.deb")

        assert len(result.dependencies) == 1
        dep = result.dependencies[0]
        assert dep.package == "editor"
        assert len(dep.alternatives) == 2
        assert dep.alternatives[0].package == "vim"
        assert dep.alternatives[1].package == "nano"

    def test_dependency_with_arch_qualifier(self) -> None:
        control_text = "Package: test\nVersion: 1.0\nArchitecture: amd64\nDepends: libc6:any (>= 2.17)\n"
        control_tar = _make_tar_bytes({"./control": control_text})
        data_tar = _make_tar_bytes({"./usr/bin/test": ""})
        reader = FakeFileReader(control_tar=control_tar, data_tar=data_tar)
        parser = DebParser(reader)
        result = parser.parse("/fake/test.deb")

        assert len(result.dependencies) == 1
        assert result.dependencies[0].package == "libc6"

    def test_pre_depends_parsed(self) -> None:
        control_text = "Package: test\nVersion: 1.0\nArchitecture: amd64\nPre-Depends: dpkg (>= 1.19)\n"
        control_tar = _make_tar_bytes({"./control": control_text})
        data_tar = _make_tar_bytes({"./usr/bin/test": ""})
        reader = FakeFileReader(control_tar=control_tar, data_tar=data_tar)
        parser = DebParser(reader)
        result = parser.parse("/fake/test.deb")

        assert len(result.dependencies) == 1
        assert result.dependencies[0].package == "dpkg"
        assert result.dependencies[0].version_constraint == ">= 1.19"

    def test_no_dependency_fields(self) -> None:
        control_text = "Package: test\nVersion: 1.0\nArchitecture: all\n"
        control_tar = _make_tar_bytes({"./control": control_text})
        data_tar = _make_tar_bytes({"./usr/bin/test": ""})
        reader = FakeFileReader(control_tar=control_tar, data_tar=data_tar)
        parser = DebParser(reader)
        result = parser.parse("/fake/test.deb")

        assert result.dependencies == []


@pytest.mark.unit
class TestFileListing:
    """Tests for file listing extraction from data.tar."""

    def test_basic_file_listing(self) -> None:
        control_tar = _make_tar_bytes({"./control": "Package: test\nVersion: 1.0\nArchitecture: all\n"})
        data_tar = _make_tar_bytes(
            {
                "./usr/bin/hello": "#!/bin/sh\necho hello",
                "./usr/share/doc/test/README": "readme",
            }
        )
        reader = FakeFileReader(control_tar=control_tar, data_tar=data_tar)
        parser = DebParser(reader)
        result = parser.parse("/fake/test.deb")

        assert "./usr/bin/hello" in result.file_listing
        assert "./usr/share/doc/test/README" in result.file_listing

    def test_missing_data_tar_raises_error(self) -> None:
        control_tar = _make_tar_bytes({"./control": "Package: test\nVersion: 1.0\nArchitecture: all\n"})
        reader = FakeFileReader(control_tar=control_tar, missing_members={"data.tar"})
        parser = DebParser(reader)
        with pytest.raises(DebParseError, match=r"data\.tar"):
            parser.parse("/fake/test.deb")


@pytest.mark.unit
class TestCopyrightExtraction:
    """Tests for copyright file extraction from data.tar."""

    def test_copyright_found(self) -> None:
        control_tar = _make_tar_bytes({"./control": "Package: test\nVersion: 1.0\nArchitecture: all\n"})
        copyright_content = "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n"
        data_tar = _make_tar_bytes(
            {
                "./usr/bin/test": "",
                "./usr/share/doc/test/copyright": copyright_content,
            }
        )
        reader = FakeFileReader(control_tar=control_tar, data_tar=data_tar)
        parser = DebParser(reader)
        result = parser.parse("/fake/test.deb")

        assert result.copyright_text == copyright_content

    def test_copyright_not_found(self) -> None:
        control_tar = _make_tar_bytes({"./control": "Package: test\nVersion: 1.0\nArchitecture: all\n"})
        data_tar = _make_tar_bytes(
            {
                "./usr/bin/test": "",
                "./usr/share/doc/test/README": "readme",
            }
        )
        reader = FakeFileReader(control_tar=control_tar, data_tar=data_tar)
        parser = DebParser(reader)
        result = parser.parse("/fake/test.deb")

        assert result.copyright_text is None

    def test_copyright_without_leading_dot_slash(self) -> None:
        control_tar = _make_tar_bytes({"./control": "Package: test\nVersion: 1.0\nArchitecture: all\n"})
        copyright_content = "Some copyright text"
        data_tar = _make_tar_bytes(
            {
                "usr/bin/test": "",
                "usr/share/doc/test/copyright": copyright_content,
            }
        )
        reader = FakeFileReader(control_tar=control_tar, data_tar=data_tar)
        parser = DebParser(reader)
        result = parser.parse("/fake/test.deb")

        assert result.copyright_text == copyright_content


@pytest.mark.unit
class TestFullParse:
    """Integration-style tests for complete parse workflow."""

    def test_complete_parse_result(self) -> None:
        control_text = (
            "Package: mypackage\n"
            "Version: 2.1.0-1\n"
            "Architecture: amd64\n"
            "Maintainer: Test <test@example.com>\n"
            "Description: A test package\n"
            "Section: utils\n"
            "Priority: optional\n"
            "Installed-Size: 1024\n"
            "Homepage: https://example.com\n"
            "Depends: libc6 (>= 2.17), libstdc++6 (>= 4.9)\n"
            "Recommends: bash-completion\n"
        )
        copyright_text = "Copyright 2024 Test Author\nLicense: MIT\n"
        control_tar = _make_tar_bytes({"./control": control_text})
        data_tar = _make_tar_bytes(
            {
                "./usr/bin/mypackage": "binary content",
                "./usr/share/doc/mypackage/copyright": copyright_text,
                "./usr/share/doc/mypackage/changelog.gz": "compressed",
            }
        )
        reader = FakeFileReader(control_tar=control_tar, data_tar=data_tar)
        parser = DebParser(reader)
        result = parser.parse("/fake/mypackage.deb")

        assert isinstance(result, DebParseResult)
        assert result.package_name == "mypackage"
        assert result.version == "2.1.0-1"
        assert result.architecture == "amd64"
        assert result.control_fields["Maintainer"] == "Test <test@example.com>"
        assert result.control_fields["Section"] == "utils"
        assert result.control_fields["Priority"] == "optional"
        assert result.control_fields["Installed-Size"] == "1024"
        assert result.control_fields["Homepage"] == "https://example.com"
        assert len(result.dependencies) == 3  # 2 from Depends + 1 from Recommends
        assert result.copyright_text == copyright_text
        assert len(result.file_listing) == 3
