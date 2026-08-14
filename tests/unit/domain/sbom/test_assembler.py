"""Unit tests for ModelAssembler.

Tests cover all acceptance criteria for Requirement 2 (Model Assembler):
AC 2.1-2.10.
"""

from __future__ import annotations

import re

import pytest

from debcraft.domain.sbom.assembler import ModelAssembler
from debcraft.domain.sbom.values import (
    ChecksumAlgorithm,
    ExternalReferenceCategory,
    RelationshipType,
)
from debcraft.domain.scanner.values import (
    EnrichedPackage,
    IdentifiedPackage,
    PackageEnrichment,
    ScanResult,
)

pytestmark = [pytest.mark.unit]


@pytest.fixture
def assembler() -> ModelAssembler:
    return ModelAssembler()


@pytest.fixture
def basic_scan_result() -> ScanResult:
    return ScanResult(
        packages=[],
        strategy="dpkg-status",
        diagnostics=[],
        duration_seconds=1.0,
        artifact_path="/path/to/artifact.deb",
    )


@pytest.fixture
def basic_enriched_package() -> EnrichedPackage:
    return EnrichedPackage(
        package=IdentifiedPackage(
            name="libfoo",
            version="1.2.3",
            architecture="amd64",
            status="installed",
        ),
        enrichment=PackageEnrichment(
            purl="pkg:deb/debian/libfoo@1.2.3?arch=amd64",
            sha256="a" * 64,
            license_expressions=[("MIT", "scanner")],
            depends="libbar (>= 2.0), libbaz",
        ),
    )


class TestFieldMapping:
    """AC 2.1: Map EnrichedPackage → SBOMPackage fields."""

    def test_name_mapped_from_identified_package(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        ep = EnrichedPackage(
            package=IdentifiedPackage(
                name="mypackage",
                version="3.0",
                architecture="arm64",
                status="installed",
            ),
        )
        doc = assembler.assemble(basic_scan_result, [ep])
        assert doc.packages[0].name == "mypackage"

    def test_version_mapped_from_identified_package(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        ep = EnrichedPackage(
            package=IdentifiedPackage(
                name="mypackage",
                version="3.0",
                architecture="arm64",
                status="installed",
            ),
        )
        doc = assembler.assemble(basic_scan_result, [ep])
        assert doc.packages[0].version == "3.0"

    def test_architecture_mapped_to_description(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        ep = EnrichedPackage(
            package=IdentifiedPackage(
                name="mypackage",
                version="3.0",
                architecture="arm64",
                status="installed",
            ),
        )
        doc = assembler.assemble(basic_scan_result, [ep])
        assert doc.packages[0].description == "Architecture: arm64"


class TestPurlMapping:
    """AC 2.2: Non-null purl → package_url + PACKAGE_MANAGER external reference."""

    def test_purl_sets_package_url(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        ep = EnrichedPackage(
            package=IdentifiedPackage(name="foo", version="1.0", architecture="amd64", status="installed"),
            enrichment=PackageEnrichment(
                purl="pkg:deb/debian/foo@1.0",
            ),
        )
        doc = assembler.assemble(basic_scan_result, [ep])
        assert doc.packages[0].package_url == "pkg:deb/debian/foo@1.0"

    def test_purl_adds_external_reference(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        ep = EnrichedPackage(
            package=IdentifiedPackage(name="foo", version="1.0", architecture="amd64", status="installed"),
            enrichment=PackageEnrichment(
                purl="pkg:deb/debian/foo@1.0",
            ),
        )
        doc = assembler.assemble(basic_scan_result, [ep])
        refs = doc.packages[0].external_references
        assert len(refs) == 1
        assert refs[0].category == ExternalReferenceCategory.PACKAGE_MANAGER
        assert refs[0].url == "pkg:deb/debian/foo@1.0"

    def test_null_purl_no_package_url(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        ep = EnrichedPackage(
            package=IdentifiedPackage(name="foo", version="1.0", architecture="amd64", status="installed"),
            enrichment=PackageEnrichment(),
        )
        doc = assembler.assemble(basic_scan_result, [ep])
        assert doc.packages[0].package_url is None
        assert doc.packages[0].external_references == []


class TestSha256Mapping:
    """AC 2.3: Non-null sha256 → SBOMChecksum with SHA256 algorithm."""

    def test_sha256_adds_checksum(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        sha = "b" * 64
        ep = EnrichedPackage(
            package=IdentifiedPackage(name="foo", version="1.0", architecture="amd64", status="installed"),
            enrichment=PackageEnrichment(sha256=sha),
        )
        doc = assembler.assemble(basic_scan_result, [ep])
        checksums = doc.packages[0].checksums
        assert len(checksums) == 1
        assert checksums[0].algorithm == ChecksumAlgorithm.SHA256
        assert checksums[0].value == sha

    def test_null_sha256_no_checksum(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        ep = EnrichedPackage(
            package=IdentifiedPackage(name="foo", version="1.0", architecture="amd64", status="installed"),
            enrichment=PackageEnrichment(),
        )
        doc = assembler.assemble(basic_scan_result, [ep])
        assert doc.packages[0].checksums == []


class TestLicenseMapping:
    """AC 2.4: license_expressions → concluded_license and declared_license."""

    def test_license_expressions_set_both_fields(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        ep = EnrichedPackage(
            package=IdentifiedPackage(name="foo", version="1.0", architecture="amd64", status="installed"),
            enrichment=PackageEnrichment(
                license_expressions=[("Apache-2.0", "scanner"), ("MIT", "heuristic")],
            ),
        )
        doc = assembler.assemble(basic_scan_result, [ep])
        assert doc.packages[0].concluded_license == "Apache-2.0"
        assert doc.packages[0].declared_license == "Apache-2.0"

    def test_empty_license_expressions_leaves_none(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        ep = EnrichedPackage(
            package=IdentifiedPackage(name="foo", version="1.0", architecture="amd64", status="installed"),
            enrichment=PackageEnrichment(license_expressions=[]),
        )
        doc = assembler.assemble(basic_scan_result, [ep])
        assert doc.packages[0].concluded_license is None
        assert doc.packages[0].declared_license is None


class TestDescribesRelationships:
    """AC 2.5: DESCRIBES relationships from root to each component."""

    def test_describes_relationship_for_each_package(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        packages = [
            EnrichedPackage(
                package=IdentifiedPackage(name=f"pkg{i}", version="1.0", architecture="amd64", status="installed"),
            )
            for i in range(3)
        ]
        doc = assembler.assemble(basic_scan_result, packages)
        describes_rels = [r for r in doc.relationships if r.relationship_type == RelationshipType.DESCRIBES]
        assert len(describes_rels) == 3
        for rel in describes_rels:
            assert rel.source_id == doc.root_package.spdx_id

    def test_describes_targets_match_package_ids(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        packages = [
            EnrichedPackage(
                package=IdentifiedPackage(name=f"pkg{i}", version="1.0", architecture="amd64", status="installed"),
            )
            for i in range(2)
        ]
        doc = assembler.assemble(basic_scan_result, packages)
        describes_rels = [r for r in doc.relationships if r.relationship_type == RelationshipType.DESCRIBES]
        target_ids = {r.target_id for r in describes_rels}
        package_ids = {p.spdx_id for p in doc.packages}
        assert target_ids == package_ids


class TestDependsOnRelationships:
    """AC 2.6: Parse depends and generate DEPENDS_ON for matching packages."""

    def test_depends_on_generated_for_matching_packages(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        pkg_a = EnrichedPackage(
            package=IdentifiedPackage(name="libfoo", version="1.0", architecture="amd64", status="installed"),
            enrichment=PackageEnrichment(depends="libbar (>= 2.0)"),
        )
        pkg_b = EnrichedPackage(
            package=IdentifiedPackage(name="libbar", version="2.0", architecture="amd64", status="installed"),
        )
        doc = assembler.assemble(basic_scan_result, [pkg_a, pkg_b])
        depends_rels = [r for r in doc.relationships if r.relationship_type == RelationshipType.DEPENDS_ON]
        assert len(depends_rels) == 1
        # libfoo depends on libbar
        foo_pkg = next(p for p in doc.packages if p.name == "libfoo")
        bar_pkg = next(p for p in doc.packages if p.name == "libbar")
        assert depends_rels[0].source_id == foo_pkg.spdx_id
        assert depends_rels[0].target_id == bar_pkg.spdx_id

    def test_depends_on_ignores_non_matching_packages(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        pkg_a = EnrichedPackage(
            package=IdentifiedPackage(name="libfoo", version="1.0", architecture="amd64", status="installed"),
            enrichment=PackageEnrichment(depends="libunknown (>= 1.0)"),
        )
        doc = assembler.assemble(basic_scan_result, [pkg_a])
        depends_rels = [r for r in doc.relationships if r.relationship_type == RelationshipType.DEPENDS_ON]
        assert len(depends_rels) == 0

    def test_depends_on_multiple_dependencies(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        pkg_a = EnrichedPackage(
            package=IdentifiedPackage(name="app", version="1.0", architecture="amd64", status="installed"),
            enrichment=PackageEnrichment(depends="libfoo (>= 1.0), libbar"),
        )
        pkg_b = EnrichedPackage(
            package=IdentifiedPackage(name="libfoo", version="1.0", architecture="amd64", status="installed"),
        )
        pkg_c = EnrichedPackage(
            package=IdentifiedPackage(name="libbar", version="2.0", architecture="amd64", status="installed"),
        )
        doc = assembler.assemble(basic_scan_result, [pkg_a, pkg_b, pkg_c])
        depends_rels = [r for r in doc.relationships if r.relationship_type == RelationshipType.DEPENDS_ON]
        assert len(depends_rels) == 2


class TestSpdxIdGeneration:
    """AC 2.7: Unique SPDX IDs with collision suffix."""

    def test_spdx_id_format(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        ep = EnrichedPackage(
            package=IdentifiedPackage(name="libfoo", version="1.2.3", architecture="amd64", status="installed"),
        )
        doc = assembler.assemble(basic_scan_result, [ep])
        assert doc.packages[0].spdx_id == "SPDXRef-Package-libfoo-1.2.3"

    def test_spdx_id_sanitizes_special_chars(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        ep = EnrichedPackage(
            package=IdentifiedPackage(
                name="lib_foo+bar",
                version="1:2.3~4",
                architecture="amd64",
                status="installed",
            ),
        )
        doc = assembler.assemble(basic_scan_result, [ep])
        # _ + : ~ should all be replaced with hyphens
        spdx_id = doc.packages[0].spdx_id
        assert re.match(r"^SPDXRef-[a-zA-Z0-9.\-]+$", spdx_id)
        assert spdx_id == "SPDXRef-Package-lib-foo-bar-1-2.3-4"

    def test_spdx_id_collision_suffix(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        """Duplicate packages get sequential suffix starting at 2."""
        packages = [
            EnrichedPackage(
                package=IdentifiedPackage(name="libfoo", version="1.0", architecture="amd64", status="installed"),
            ),
            EnrichedPackage(
                package=IdentifiedPackage(name="libfoo", version="1.0", architecture="i386", status="installed"),
            ),
            EnrichedPackage(
                package=IdentifiedPackage(name="libfoo", version="1.0", architecture="arm64", status="installed"),
            ),
        ]
        doc = assembler.assemble(basic_scan_result, packages)
        ids = [p.spdx_id for p in doc.packages]
        assert ids[0] == "SPDXRef-Package-libfoo-1.0"
        assert ids[1] == "SPDXRef-Package-libfoo-1.0-2"
        assert ids[2] == "SPDXRef-Package-libfoo-1.0-3"

    def test_all_spdx_ids_unique(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        packages = [
            EnrichedPackage(
                package=IdentifiedPackage(name="libfoo", version="1.0", architecture="amd64", status="installed"),
            ),
            EnrichedPackage(
                package=IdentifiedPackage(name="libfoo", version="1.0", architecture="i386", status="installed"),
            ),
        ]
        doc = assembler.assemble(basic_scan_result, packages)
        ids = [p.spdx_id for p in doc.packages]
        assert len(ids) == len(set(ids))


class TestCreationInfo:
    """AC 2.8: SBOMCreationInfo with debcraft version."""

    def test_creation_info_tool_format(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        ep = EnrichedPackage(
            package=IdentifiedPackage(name="foo", version="1.0", architecture="amd64", status="installed"),
        )
        doc = assembler.assemble(basic_scan_result, [ep])
        assert len(doc.creation_info.tools) == 1
        assert doc.creation_info.tools[0].startswith("Tool: debcraft-")

    def test_creation_info_creator(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        ep = EnrichedPackage(
            package=IdentifiedPackage(name="foo", version="1.0", architecture="amd64", status="installed"),
        )
        doc = assembler.assemble(basic_scan_result, [ep])
        assert doc.creation_info.creators == ["Tool: debcraft"]

    def test_creation_info_timestamp_format(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        ep = EnrichedPackage(
            package=IdentifiedPackage(name="foo", version="1.0", architecture="amd64", status="installed"),
        )
        doc = assembler.assemble(basic_scan_result, [ep])
        # ISO 8601 UTC format
        assert re.match(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
            doc.creation_info.created,
        )


class TestZeroPackageCase:
    """AC 2.9: Zero packages → empty components + comment."""

    def test_zero_packages_empty_components(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        doc = assembler.assemble(basic_scan_result, [])
        assert doc.packages == []

    def test_zero_packages_has_comment(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        doc = assembler.assemble(basic_scan_result, [])
        assert doc.comment is not None
        assert "no packages" in doc.comment.lower()

    def test_zero_packages_no_relationships(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        doc = assembler.assemble(basic_scan_result, [])
        assert doc.relationships == []


class TestNamespace:
    """AC 2.10: Namespace format with artifact path hash + UUID4."""

    def test_namespace_format(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        ep = EnrichedPackage(
            package=IdentifiedPackage(name="foo", version="1.0", architecture="amd64", status="installed"),
        )
        doc = assembler.assemble(basic_scan_result, [ep])
        # Format: https://debcraft.io/spdxdocs/<16-hex>-<uuid4>
        pattern = (
            r"^https://debcraft\.io/spdxdocs/"
            r"[0-9a-f]{16}-"
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        )
        assert re.match(pattern, doc.namespace)

    def test_namespace_uses_artifact_path_hash(
        self,
        assembler: ModelAssembler,
    ):
        """The 16-hex portion is derived from sha256 of the artifact path."""
        import hashlib

        artifact_path = "/my/custom/path.deb"
        scan_result = ScanResult(
            packages=[],
            strategy="test",
            diagnostics=[],
            duration_seconds=0.5,
            artifact_path=artifact_path,
        )
        ep = EnrichedPackage(
            package=IdentifiedPackage(name="foo", version="1.0", architecture="amd64", status="installed"),
        )
        doc = assembler.assemble(scan_result, [ep])
        expected_hash = hashlib.sha256(artifact_path.encode()).hexdigest()[:16]
        assert f"/{expected_hash}-" in doc.namespace

    def test_namespace_unique_across_calls(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        ep = EnrichedPackage(
            package=IdentifiedPackage(name="foo", version="1.0", architecture="amd64", status="installed"),
        )
        doc1 = assembler.assemble(basic_scan_result, [ep])
        doc2 = assembler.assemble(basic_scan_result, [ep])
        assert doc1.namespace != doc2.namespace


class TestNoEnrichment:
    """Packages without enrichment should still be assembled correctly."""

    def test_no_enrichment_minimal_package(self, assembler: ModelAssembler, basic_scan_result: ScanResult):
        ep = EnrichedPackage(
            package=IdentifiedPackage(name="bare", version="0.1", architecture="all", status="installed"),
            enrichment=None,
        )
        doc = assembler.assemble(basic_scan_result, [ep])
        pkg = doc.packages[0]
        assert pkg.name == "bare"
        assert pkg.version == "0.1"
        assert pkg.description == "Architecture: all"
        assert pkg.package_url is None
        assert pkg.checksums == []
        assert pkg.concluded_license is None
        assert pkg.declared_license is None
        assert pkg.external_references == []
