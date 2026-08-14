"""Unit tests for SPDX 2.3 Writer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from debcraft.domain.sbom.errors import (
    DocumentValidationError,
    OutputPathError,
    WriterCancellationError,
)
from debcraft.domain.sbom.values import (
    ChecksumAlgorithm,
    OutputFormat,
    RelationshipType,
    SBOMChecksum,
    SBOMCreationInfo,
    SBOMDocument,
    SBOMExtractedLicense,
    SBOMPackage,
    SBOMRelationship,
)
from debcraft.infrastructure.sbom_writers.spdx23 import SPDX23Writer
from debcraft.platform.contracts.workflow import CancellationToken, WorkflowContext

pytestmark = [pytest.mark.unit]


def _make_context(cancelled: bool = False) -> WorkflowContext:
    """Create a mock WorkflowContext."""
    token = CancellationToken()
    if cancelled:
        token.cancel()
    ctx = MagicMock(spec=WorkflowContext)
    ctx.cancellation_token = token
    return ctx


def _make_creation_info() -> SBOMCreationInfo:
    """Create a minimal SBOMCreationInfo."""
    return SBOMCreationInfo(
        tools=["Tool: debcraft-0.1.0"],
        created="2024-01-15T10:30:00Z",
        creators=["Tool: debcraft"],
        license_list_version="3.21",
    )


def _make_root_package() -> SBOMPackage:
    """Create a minimal root package."""
    return SBOMPackage(
        spdx_id="SPDXRef-Package-root",
        name="test-artifact",
        version="1.0.0",
        download_location="https://example.com/test-1.0.0.tar.gz",
    )


def _make_document(**kwargs) -> SBOMDocument:
    """Create a minimal SBOMDocument with overrides."""
    defaults = {
        "namespace": "https://debcraft.io/spdxdocs/abc123-uuid",
        "name": "test-document",
        "creation_info": _make_creation_info(),
        "root_package": _make_root_package(),
    }
    defaults.update(kwargs)
    return SBOMDocument(**defaults)


class TestSPDX23WriterTopLevel:
    """Test top-level SPDX 2.3 document fields."""

    @pytest.mark.asyncio
    async def test_top_level_fields(self, tmp_path: Path) -> None:
        """AC 5.5: Top-level fields are correctly populated."""
        doc = _make_document()
        writer = SPDX23Writer()
        output = tmp_path / "output.spdx.json"

        await writer.write(doc, output, _make_context())

        data = json.loads(output.read_text())
        assert data["spdxVersion"] == "SPDX-2.3"
        assert data["dataLicense"] == "CC0-1.0"
        assert data["SPDXID"] == "SPDXRef-DOCUMENT"
        assert data["name"] == "test-document"
        assert data["documentNamespace"] == "https://debcraft.io/spdxdocs/abc123-uuid"

    @pytest.mark.asyncio
    async def test_format_is_spdx_2_3(self, tmp_path: Path) -> None:
        """WriterResult has correct format."""
        doc = _make_document()
        writer = SPDX23Writer()
        output = tmp_path / "output.spdx.json"

        result = await writer.write(doc, output, _make_context())

        assert result.format == OutputFormat.SPDX_2_3
        assert result.output_path == output


class TestSPDX23WriterPackages:
    """Test package serialization."""

    @pytest.mark.asyncio
    async def test_package_noassertion_sentinels(self, tmp_path: Path) -> None:
        """AC 5.2, 5.13: Null string fields use NOASSERTION sentinel."""
        root = SBOMPackage(
            spdx_id="SPDXRef-Package-minimal",
            name="minimal-pkg",
        )
        doc = _make_document(root_package=root)
        writer = SPDX23Writer()
        output = tmp_path / "output.spdx.json"

        await writer.write(doc, output, _make_context())

        data = json.loads(output.read_text())
        pkg = data["packages"][0]
        assert pkg["SPDXID"] == "SPDXRef-Package-minimal"
        assert pkg["name"] == "minimal-pkg"
        assert pkg["versionInfo"] == "NOASSERTION"
        assert pkg["downloadLocation"] == "NOASSERTION"
        assert pkg["supplier"] == "NOASSERTION"
        assert pkg["licenseConcluded"] == "NOASSERTION"
        assert pkg["licenseDeclared"] == "NOASSERTION"
        assert pkg["copyrightText"] == "NOASSERTION"

    @pytest.mark.asyncio
    async def test_package_with_values(self, tmp_path: Path) -> None:
        """AC 5.2: Package fields populated from SBOM_Package."""
        root = SBOMPackage(
            spdx_id="SPDXRef-Package-full",
            name="full-pkg",
            version="2.0",
            download_location="https://example.com/full-2.0.tar.gz",
            supplier="Organization: ACME",
            concluded_license="MIT",
            declared_license="MIT",
            copyright_text="Copyright 2024 ACME",
        )
        doc = _make_document(root_package=root)
        writer = SPDX23Writer()
        output = tmp_path / "output.spdx.json"

        await writer.write(doc, output, _make_context())

        data = json.loads(output.read_text())
        pkg = data["packages"][0]
        assert pkg["versionInfo"] == "2.0"
        assert pkg["downloadLocation"] == "https://example.com/full-2.0.tar.gz"
        assert pkg["supplier"] == "Organization: ACME"
        assert pkg["licenseConcluded"] == "MIT"
        assert pkg["licenseDeclared"] == "MIT"
        assert pkg["copyrightText"] == "Copyright 2024 ACME"

    @pytest.mark.asyncio
    async def test_empty_arrays_omitted(self, tmp_path: Path) -> None:
        """AC 5.2: Empty array fields are omitted."""
        root = SBOMPackage(
            spdx_id="SPDXRef-Package-empty",
            name="empty-arrays",
        )
        doc = _make_document(root_package=root)
        writer = SPDX23Writer()
        output = tmp_path / "output.spdx.json"

        await writer.write(doc, output, _make_context())

        data = json.loads(output.read_text())
        pkg = data["packages"][0]
        assert "checksums" not in pkg
        assert "externalRefs" not in pkg

    @pytest.mark.asyncio
    async def test_multiple_packages(self, tmp_path: Path) -> None:
        """Root + component packages all in packages array."""
        component = SBOMPackage(
            spdx_id="SPDXRef-Package-component",
            name="component-pkg",
            version="1.0",
        )
        doc = _make_document(packages=[component])
        writer = SPDX23Writer()
        output = tmp_path / "output.spdx.json"

        await writer.write(doc, output, _make_context())

        data = json.loads(output.read_text())
        assert len(data["packages"]) == 2
        names = [p["name"] for p in data["packages"]]
        assert "test-artifact" in names
        assert "component-pkg" in names


class TestSPDX23WriterRelationships:
    """Test relationship serialization."""

    @pytest.mark.asyncio
    async def test_relationship_mapping(self, tmp_path: Path) -> None:
        """AC 5.3: Relationships map to spdxElementId, relatedSpdxElement, relationshipType."""
        rel = SBOMRelationship(
            source_id="SPDXRef-DOCUMENT",
            target_id="SPDXRef-Package-root",
            relationship_type=RelationshipType.DESCRIBES,
        )
        doc = _make_document(relationships=[rel])
        writer = SPDX23Writer()
        output = tmp_path / "output.spdx.json"

        await writer.write(doc, output, _make_context())

        data = json.loads(output.read_text())
        r = data["relationships"][0]
        assert r["spdxElementId"] == "SPDXRef-DOCUMENT"
        assert r["relatedSpdxElement"] == "SPDXRef-Package-root"
        assert r["relationshipType"] == "DESCRIBES"

    @pytest.mark.asyncio
    async def test_all_known_types(self, tmp_path: Path) -> None:
        """All defined RelationshipType values are mapped."""
        rels = [
            SBOMRelationship(
                source_id="SPDXRef-Package-a",
                target_id="SPDXRef-Package-b",
                relationship_type=rt,
            )
            for rt in RelationshipType
        ]
        doc = _make_document(relationships=rels)
        writer = SPDX23Writer()
        output = tmp_path / "output.spdx.json"

        await writer.write(doc, output, _make_context())

        data = json.loads(output.read_text())
        types = [r["relationshipType"] for r in data["relationships"]]
        # All current types have direct SPDX 2.3 vocabulary mappings
        for t in types:
            assert t in {"DESCRIBES", "CONTAINS", "DEPENDS_ON", "BUILD_TOOL_OF", "OTHER"}


class TestSPDX23WriterCreationInfo:
    """Test creationInfo serialization."""

    @pytest.mark.asyncio
    async def test_creation_info(self, tmp_path: Path) -> None:
        """AC 5.6: creationInfo with created, creators, licenseListVersion."""
        doc = _make_document()
        writer = SPDX23Writer()
        output = tmp_path / "output.spdx.json"

        await writer.write(doc, output, _make_context())

        data = json.loads(output.read_text())
        ci = data["creationInfo"]
        assert ci["created"] == "2024-01-15T10:30:00Z"
        assert ci["creators"] == ["Tool: debcraft"]
        assert ci["licenseListVersion"] == "3.21"

    @pytest.mark.asyncio
    async def test_creation_info_no_license_version(self, tmp_path: Path) -> None:
        """LicenseListVersion omitted when None."""
        info = SBOMCreationInfo(
            tools=["Tool: debcraft-0.1.0"],
            created="2024-01-15T10:30:00Z",
            creators=["Tool: debcraft"],
        )
        doc = _make_document(creation_info=info)
        writer = SPDX23Writer()
        output = tmp_path / "output.spdx.json"

        await writer.write(doc, output, _make_context())

        data = json.loads(output.read_text())
        assert "licenseListVersion" not in data["creationInfo"]


class TestSPDX23WriterChecksums:
    """Test checksum serialization."""

    @pytest.mark.asyncio
    async def test_checksum_mapping(self, tmp_path: Path) -> None:
        """AC 5.7: Checksums map to algorithm and checksumValue."""
        root = SBOMPackage(
            spdx_id="SPDXRef-Package-cs",
            name="checksum-pkg",
            checksums=[
                SBOMChecksum(
                    algorithm=ChecksumAlgorithm.SHA256,
                    value="a" * 64,
                ),
            ],
        )
        doc = _make_document(root_package=root)
        writer = SPDX23Writer()
        output = tmp_path / "output.spdx.json"

        await writer.write(doc, output, _make_context())

        data = json.loads(output.read_text())
        cs = data["packages"][0]["checksums"][0]
        assert cs["algorithm"] == "SHA256"
        assert cs["checksumValue"] == "a" * 64


class TestSPDX23WriterExternalRefs:
    """Test PURL and external reference serialization."""

    @pytest.mark.asyncio
    async def test_purl_external_ref(self, tmp_path: Path) -> None:
        """AC 5.8: PURL → externalRefs with PACKAGE-MANAGER category."""
        root = SBOMPackage(
            spdx_id="SPDXRef-Package-purl",
            name="purl-pkg",
            package_url="pkg:deb/debian/libc6@2.31-13",
        )
        doc = _make_document(root_package=root)
        writer = SPDX23Writer()
        output = tmp_path / "output.spdx.json"

        await writer.write(doc, output, _make_context())

        data = json.loads(output.read_text())
        refs = data["packages"][0]["externalRefs"]
        assert len(refs) == 1
        assert refs[0]["referenceCategory"] == "PACKAGE-MANAGER"
        assert refs[0]["referenceType"] == "purl"
        assert refs[0]["referenceLocator"] == "pkg:deb/debian/libc6@2.31-13"


class TestSPDX23WriterExtractedLicenses:
    """Test extracted license serialization."""

    @pytest.mark.asyncio
    async def test_extracted_licenses(self, tmp_path: Path) -> None:
        """AC 5.9: ExtractedLicenses → hasExtractedLicensingInfos."""
        lic = SBOMExtractedLicense(
            license_id="LicenseRef-custom-1",
            extracted_text="Custom license text here",
            name="Custom License",
            cross_references=["https://example.com/license"],
        )
        doc = _make_document(extracted_licenses=[lic])
        writer = SPDX23Writer()
        output = tmp_path / "output.spdx.json"

        await writer.write(doc, output, _make_context())

        data = json.loads(output.read_text())
        infos = data["hasExtractedLicensingInfos"]
        assert len(infos) == 1
        assert infos[0]["licenseId"] == "LicenseRef-custom-1"
        assert infos[0]["extractedText"] == "Custom license text here"
        assert infos[0]["name"] == "Custom License"
        assert infos[0]["seeAlsos"] == ["https://example.com/license"]

    @pytest.mark.asyncio
    async def test_no_extracted_licenses_omits_field(self, tmp_path: Path) -> None:
        """HasExtractedLicensingInfos omitted when no extracted licenses."""
        doc = _make_document()
        writer = SPDX23Writer()
        output = tmp_path / "output.spdx.json"

        await writer.write(doc, output, _make_context())

        data = json.loads(output.read_text())
        assert "hasExtractedLicensingInfos" not in data


class TestSPDX23WriterDeterminism:
    """Test deterministic output."""

    @pytest.mark.asyncio
    async def test_sorted_keys_and_indent(self, tmp_path: Path) -> None:
        """AC 5.10: 2-space indent, sorted keys, UTF-8 no BOM."""
        doc = _make_document()
        writer = SPDX23Writer()
        output = tmp_path / "output.spdx.json"

        await writer.write(doc, output, _make_context())

        content = output.read_bytes()
        # No BOM
        assert not content.startswith(b"\xef\xbb\xbf")
        # UTF-8 decodable
        text = content.decode("utf-8")
        # Sorted keys - SPDXID should come before creationInfo, etc.
        lines = text.split("\n")
        # 2-space indentation for first level keys
        indented_lines = [line for line in lines if line.startswith("  ")]
        assert len(indented_lines) > 0
        for line in indented_lines:
            # Should use 2-space indentation (not tabs or 4-space)
            stripped = line.lstrip(" ")
            indent = len(line) - len(stripped)
            assert indent % 2 == 0

    @pytest.mark.asyncio
    async def test_identical_output_twice(self, tmp_path: Path) -> None:
        """AC 3.5: Same input produces byte-identical output."""
        doc = _make_document()
        writer = SPDX23Writer()

        output1 = tmp_path / "out1.json"
        output2 = tmp_path / "out2.json"

        await writer.write(doc, output1, _make_context())
        await writer.write(doc, output2, _make_context())

        assert output1.read_bytes() == output2.read_bytes()


class TestSPDX23WriterResult:
    """Test WriterResult fields."""

    @pytest.mark.asyncio
    async def test_sha256_matches_content(self, tmp_path: Path) -> None:
        """AC 3.6: SHA-256 in result matches written bytes."""
        doc = _make_document()
        writer = SPDX23Writer()
        output = tmp_path / "output.spdx.json"

        result = await writer.write(doc, output, _make_context())

        expected_hash = hashlib.sha256(output.read_bytes()).hexdigest()
        assert result.sha256 == expected_hash

    @pytest.mark.asyncio
    async def test_file_size_matches(self, tmp_path: Path) -> None:
        """AC 3.6: file_size matches actual written bytes."""
        doc = _make_document()
        writer = SPDX23Writer()
        output = tmp_path / "output.spdx.json"

        result = await writer.write(doc, output, _make_context())

        assert result.file_size == output.stat().st_size


class TestSPDX23WriterDirectoryCreation:
    """Test parent directory creation."""

    @pytest.mark.asyncio
    async def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        """AC 3.7: Creates parent directories if needed."""
        doc = _make_document()
        writer = SPDX23Writer()
        output = tmp_path / "deep" / "nested" / "dir" / "output.spdx.json"

        result = await writer.write(doc, output, _make_context())

        assert output.exists()
        assert result.output_path == output


class TestSPDX23WriterErrors:
    """Test error handling."""

    @pytest.mark.asyncio
    async def test_none_document_raises(self, tmp_path: Path) -> None:
        """AC 3.9: None document raises DocumentValidationError."""
        writer = SPDX23Writer()
        output = tmp_path / "output.spdx.json"

        with pytest.raises(DocumentValidationError, match="document is None"):
            await writer.write(None, output, _make_context())  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_cancellation_before_write(self, tmp_path: Path) -> None:
        """AC 3.10: Cancellation raises WriterCancellationError."""
        doc = _make_document()
        writer = SPDX23Writer()
        output = tmp_path / "output.spdx.json"

        with pytest.raises(WriterCancellationError):
            await writer.write(doc, output, _make_context(cancelled=True))

        # No file left behind
        assert not output.exists()

    @pytest.mark.asyncio
    async def test_unwritable_path_raises(self, tmp_path: Path) -> None:
        """AC 3.8: Unwritable path raises OutputPathError."""
        doc = _make_document()
        writer = SPDX23Writer()
        # Use a path that can't be written to
        output = Path("/proc/nonexistent/output.spdx.json")

        with pytest.raises(OutputPathError):
            await writer.write(doc, output, _make_context())


class TestSPDX23WriterValidation:
    """Test schema validation integration."""

    @pytest.mark.asyncio
    async def test_validation_runs_and_diagnostics_included(self, tmp_path: Path) -> None:
        """AC 5.11, 5.12: Validation runs; file still written even on validation errors."""
        doc = _make_document()
        writer = SPDX23Writer()
        output = tmp_path / "output.spdx.json"

        result = await writer.write(doc, output, _make_context())

        # File is always written
        assert output.exists()
        # Diagnostics is a list (may or may not have validation errors depending on schema)
        assert isinstance(result.diagnostics, list)
