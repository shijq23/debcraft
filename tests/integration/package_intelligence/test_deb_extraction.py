"""Integration tests for the .deb extraction pipeline.

Exercises: LocalDebFileReader → DebParser end-to-end with real .deb fixture files.
Verifies control field extraction, file listing, and copyright text extraction
without mocks.

Requirements: 1.1, 1.2, 1.3, 1.10
"""

from __future__ import annotations

import gzip
import io
import tarfile
from pathlib import Path

import pytest

from debcraft.domain.package_intelligence.deb_parser import DebParser
from debcraft.domain.package_intelligence.errors import DebParseError
from debcraft.infrastructure.package_intelligence.file_reader import LocalDebFileReader


def _build_ar_archive(members: list[tuple[str, bytes]]) -> bytes:
    """Build a minimal ar archive with the given members.

    Args:
        members: List of (name, content) tuples.

    Returns:
        Raw bytes of a valid ar archive.
    """
    buf = bytearray(b"!<arch>\n")

    for name, content in members:
        padded_name = f"{name + '/':16s}"
        header = (
            f"{padded_name}"
            f"{'0':12s}"  # timestamp
            f"{'0':6s}"  # owner
            f"{'0':6s}"  # group
            f"{'100644':8s}"  # mode
            f"{len(content):<10d}"  # size
            "`\n"  # magic
        )
        buf.extend(header.encode("ascii"))
        buf.extend(content)
        # Pad to even boundary
        if len(content) % 2 != 0:
            buf.extend(b"\n")

    return bytes(buf)


def _build_control_tar(fields: dict[str, str]) -> bytes:
    """Build a control.tar containing a control file with the given fields.

    Args:
        fields: Dictionary of control file field names to values.

    Returns:
        Raw tar bytes (uncompressed) containing the control file.
    """
    control_text = ""
    for field_name, value in fields.items():
        control_text += f"{field_name}: {value}\n"

    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w:") as tar:
        control_bytes = control_text.encode("utf-8")
        info = tarfile.TarInfo(name="./control")
        info.size = len(control_bytes)
        tar.addfile(info, io.BytesIO(control_bytes))
    return tar_buf.getvalue()


def _build_data_tar(
    file_paths: list[str],
    copyright_content: str | None = None,
    package_name: str | None = None,
) -> bytes:
    """Build a data.tar with the given file listing and optional copyright.

    Args:
        file_paths: List of paths to include as entries in the tar.
        copyright_content: If provided, creates a copyright file at
            `./usr/share/doc/<package_name>/copyright`.
        package_name: Required if copyright_content is provided.

    Returns:
        Raw tar bytes (uncompressed).
    """
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w:") as tar:
        for fpath in file_paths:
            info = tarfile.TarInfo(name=fpath)
            info.size = 0
            tar.addfile(info)

        if copyright_content is not None and package_name is not None:
            copyright_path = f"./usr/share/doc/{package_name}/copyright"
            content_bytes = copyright_content.encode("utf-8")
            info = tarfile.TarInfo(name=copyright_path)
            info.size = len(content_bytes)
            tar.addfile(info, io.BytesIO(content_bytes))

    return tar_buf.getvalue()


def _build_deb_file(
    control_fields: dict[str, str],
    data_file_paths: list[str],
    copyright_content: str | None = None,
    compress_control: bool = False,
    compress_data: bool = False,
) -> bytes:
    """Build a complete .deb archive with all required members.

    Args:
        control_fields: Fields for the control file.
        data_file_paths: File paths to include in data.tar.
        copyright_content: Optional copyright text to include.
        compress_control: If True, compress control.tar with gzip.
        compress_data: If True, compress data.tar with gzip.

    Returns:
        Raw bytes of a valid .deb archive.
    """
    package_name = control_fields.get("Package", "")

    control_tar = _build_control_tar(control_fields)
    data_tar = _build_data_tar(data_file_paths, copyright_content, package_name)

    if compress_control:
        control_member_name = "control.tar.gz"
        control_tar = gzip.compress(control_tar)
    else:
        control_member_name = "control.tar"

    if compress_data:
        data_member_name = "data.tar.gz"
        data_tar = gzip.compress(data_tar)
    else:
        data_member_name = "data.tar"

    return _build_ar_archive(
        [
            ("debian-binary", b"2.0\n"),
            (control_member_name, control_tar),
            (data_member_name, data_tar),
        ]
    )


@pytest.mark.integration
class TestDebExtractionPipeline:
    """End-to-end integration tests for .deb parsing with real file reader."""

    def test_control_fields_extracted_correctly(self, tmp_path: Path):
        """Control fields are extracted from a real .deb file end-to-end.

        Validates: Requirements 1.1, 1.10
        """
        control_fields = {
            "Package": "hello-world",
            "Version": "1.0.0-1",
            "Architecture": "amd64",
            "Maintainer": "Test User <test@example.com>",
            "Description": "A test package",
            "Section": "utils",
            "Priority": "optional",
            "Installed-Size": "42",
        }

        deb_bytes = _build_deb_file(
            control_fields=control_fields,
            data_file_paths=["./usr/bin/hello"],
        )
        deb_file = tmp_path / "hello-world_1.0.0-1_amd64.deb"
        deb_file.write_bytes(deb_bytes)

        reader = LocalDebFileReader()
        parser = DebParser(file_reader=reader)
        result = parser.parse(str(deb_file))

        assert result.package_name == "hello-world"
        assert result.version == "1.0.0-1"
        assert result.architecture == "amd64"
        assert result.control_fields["Maintainer"] == "Test User <test@example.com>"
        assert result.control_fields["Description"] == "A test package"
        assert result.control_fields["Section"] == "utils"
        assert result.control_fields["Priority"] == "optional"
        assert result.control_fields["Installed-Size"] == "42"

    def test_file_listing_extracted_from_data_tar(self, tmp_path: Path):
        """File listing is extracted from data.tar end-to-end.

        Validates: Requirements 1.2
        """
        data_files = [
            "./usr/bin/hello",
            "./usr/lib/libhello.so.1",
            "./usr/share/man/man1/hello.1.gz",
        ]

        deb_bytes = _build_deb_file(
            control_fields={
                "Package": "libhello",
                "Version": "2.3.4",
                "Architecture": "arm64",
            },
            data_file_paths=data_files,
        )
        deb_file = tmp_path / "libhello_2.3.4_arm64.deb"
        deb_file.write_bytes(deb_bytes)

        reader = LocalDebFileReader()
        parser = DebParser(file_reader=reader)
        result = parser.parse(str(deb_file))

        assert "./usr/bin/hello" in result.file_listing
        assert "./usr/lib/libhello.so.1" in result.file_listing
        assert "./usr/share/man/man1/hello.1.gz" in result.file_listing

    def test_copyright_text_extracted_when_present(self, tmp_path: Path):
        """Copyright text is extracted from data.tar when present.

        Validates: Requirements 1.3
        """
        copyright_text = (
            "Format: https://www.debian.org/doc/packaging-manuals/"
            "copyright-format/1.0/\n"
            "Upstream-Name: hello\n"
            "\n"
            "Files: *\n"
            "Copyright: 2024 Test Author\n"
            "License: MIT\n"
        )

        deb_bytes = _build_deb_file(
            control_fields={
                "Package": "hello",
                "Version": "1.0",
                "Architecture": "all",
            },
            data_file_paths=["./usr/bin/hello"],
            copyright_content=copyright_text,
        )
        deb_file = tmp_path / "hello_1.0_all.deb"
        deb_file.write_bytes(deb_bytes)

        reader = LocalDebFileReader()
        parser = DebParser(file_reader=reader)
        result = parser.parse(str(deb_file))

        assert result.copyright_text is not None
        assert "MIT" in result.copyright_text
        assert "2024 Test Author" in result.copyright_text

    def test_copyright_is_none_when_absent(self, tmp_path: Path):
        """Copyright text is None when no copyright file is in the archive.

        Validates: Requirements 1.3 (absence case)
        """
        deb_bytes = _build_deb_file(
            control_fields={
                "Package": "minimal-pkg",
                "Version": "0.1",
                "Architecture": "amd64",
            },
            data_file_paths=["./usr/bin/minimal"],
        )
        deb_file = tmp_path / "minimal-pkg_0.1_amd64.deb"
        deb_file.write_bytes(deb_bytes)

        reader = LocalDebFileReader()
        parser = DebParser(file_reader=reader)
        result = parser.parse(str(deb_file))

        assert result.copyright_text is None

    def test_invalid_archive_raises_deb_parse_error(self, tmp_path: Path):
        """Invalid archives raise DebParseError.

        Validates: Requirements 1.1 (error case)
        """
        deb_file = tmp_path / "invalid.deb"
        deb_file.write_bytes(b"this is not a valid deb archive")

        reader = LocalDebFileReader()
        parser = DebParser(file_reader=reader)

        with pytest.raises(DebParseError):
            parser.parse(str(deb_file))

    def test_compressed_control_and_data(self, tmp_path: Path):
        """Handles gzip-compressed control.tar.gz and data.tar.gz.

        Validates: Requirements 1.1, 1.2
        """
        control_fields = {
            "Package": "compressed-pkg",
            "Version": "3.0",
            "Architecture": "amd64",
            "Depends": "libc6 (>= 2.17), libm6",
        }
        data_files = [
            "./usr/bin/compressed",
            "./etc/compressed.conf",
        ]

        deb_bytes = _build_deb_file(
            control_fields=control_fields,
            data_file_paths=data_files,
            compress_control=True,
            compress_data=True,
        )
        deb_file = tmp_path / "compressed-pkg_3.0_amd64.deb"
        deb_file.write_bytes(deb_bytes)

        reader = LocalDebFileReader()
        parser = DebParser(file_reader=reader)
        result = parser.parse(str(deb_file))

        assert result.package_name == "compressed-pkg"
        assert result.version == "3.0"
        assert "./usr/bin/compressed" in result.file_listing
        assert "./etc/compressed.conf" in result.file_listing

    def test_dependencies_parsed_correctly(self, tmp_path: Path):
        """Dependencies are parsed into structured relations end-to-end.

        Validates: Requirements 1.10
        """
        deb_bytes = _build_deb_file(
            control_fields={
                "Package": "dep-test",
                "Version": "1.0",
                "Architecture": "amd64",
                "Depends": "libc6 (>= 2.17), libssl3 | libssl1.1",
                "Recommends": "bash-completion",
            },
            data_file_paths=["./usr/bin/dep-test"],
        )
        deb_file = tmp_path / "dep-test_1.0_amd64.deb"
        deb_file.write_bytes(deb_bytes)

        reader = LocalDebFileReader()
        parser = DebParser(file_reader=reader)
        result = parser.parse(str(deb_file))

        # Should have parsed dependencies
        assert len(result.dependencies) >= 3

        # Find the libc6 dependency
        libc_deps = [d for d in result.dependencies if d.package == "libc6"]
        assert len(libc_deps) == 1
        assert libc_deps[0].version_constraint == ">= 2.17"

        # Find the alternative dependency (libssl3 | libssl1.1)
        ssl_deps = [d for d in result.dependencies if d.package == "libssl3"]
        assert len(ssl_deps) == 1
        assert len(ssl_deps[0].alternatives) == 1
        assert ssl_deps[0].alternatives[0].package == "libssl1.1"

    def test_missing_control_tar_raises_error(self, tmp_path: Path):
        """Missing control.tar raises DebParseError.

        Validates: Requirements 1.1 (error case)
        """
        # Build an ar archive with only debian-binary and data.tar
        data_tar = _build_data_tar(["./usr/bin/test"])
        deb_bytes = _build_ar_archive(
            [
                ("debian-binary", b"2.0\n"),
                ("data.tar", data_tar),
            ]
        )
        deb_file = tmp_path / "no-control.deb"
        deb_file.write_bytes(deb_bytes)

        reader = LocalDebFileReader()
        parser = DebParser(file_reader=reader)

        with pytest.raises(DebParseError, match=r"control\.tar"):
            parser.parse(str(deb_file))

    def test_unsupported_version_raises_error(self, tmp_path: Path):
        """Unsupported debian-binary version raises DebParseError.

        Validates: Requirements 1.1 (error case)
        """
        control_tar = _build_control_tar(
            {
                "Package": "bad-version",
                "Version": "1.0",
                "Architecture": "amd64",
            }
        )
        data_tar = _build_data_tar(["./usr/bin/test"])
        deb_bytes = _build_ar_archive(
            [
                ("debian-binary", b"3.0\n"),
                ("control.tar", control_tar),
                ("data.tar", data_tar),
            ]
        )
        deb_file = tmp_path / "bad-version.deb"
        deb_file.write_bytes(deb_bytes)

        reader = LocalDebFileReader()
        parser = DebParser(file_reader=reader)

        with pytest.raises(DebParseError, match="version"):
            parser.parse(str(deb_file))
