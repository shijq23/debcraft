"""Property-based tests for CycloneDX round-trip data preservation.

# Feature: sbom-writers, Property 9: CycloneDX round-trip data preservation

**Validates: Requirements 10.4, 10.6, 10.7**

Property 9: CycloneDX round-trip data preservation.
For any valid SBOMDocument (including documents with Unicode characters and
None optional fields), when serialized by the CycloneDXWriter and the resulting
JSON is parsed back into a dictionary, the dictionary SHALL contain all component
names, versions, PURLs, hash values, and dependency references (set equality)
present in the original SBOMDocument.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given

from debcraft.domain.sbom.values import (
    ChecksumAlgorithm,
    RelationshipType,
    SBOMDocument,
)
from debcraft.infrastructure.sbom_writers.cyclonedx import CycloneDXWriter
from debcraft.infrastructure.sbom_writers.printer import SBOMPrinter

from .strategies import sbom_documents

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALGORITHM_MAP = {
    ChecksumAlgorithm.SHA256: "SHA-256",
    ChecksumAlgorithm.SHA1: "SHA-1",
    ChecksumAlgorithm.MD5: "MD5",
}


def _serialize_and_parse(document: SBOMDocument) -> dict:
    """Serialize a document via CycloneDXWriter and parse back to dict."""
    writer = CycloneDXWriter()
    data = writer._serialize(document)
    printer = SBOMPrinter()
    output_bytes = printer.print(data)
    return json.loads(output_bytes.decode("utf-8"))


# ---------------------------------------------------------------------------
# Property 9: CycloneDX round-trip data preservation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.spdx
class TestProperty9CycloneDXRoundTrip:
    """Property 9: CycloneDX round-trip data preservation.

    For any valid SBOMDocument (including documents with Unicode characters and
    None optional fields), when serialized by the CycloneDXWriter and the
    resulting JSON is parsed back into a dictionary, the dictionary SHALL
    contain all component names, versions, PURLs, hash values, and dependency
    references (set equality) present in the original SBOMDocument.
    """

    @given(doc=sbom_documents())
    def test_component_names_preserved(self, doc: SBOMDocument) -> None:
        """All component names from the document are present in serialized output."""
        parsed = _serialize_and_parse(doc)

        components = parsed.get("components", [])
        serialized_names = {comp["name"] for comp in components}

        expected_names = {pkg.name for pkg in doc.packages}
        assert expected_names == serialized_names

    @given(doc=sbom_documents())
    def test_component_versions_preserved(self, doc: SBOMDocument) -> None:
        """All component versions from the document are present in serialized output."""
        parsed = _serialize_and_parse(doc)

        components = parsed.get("components", [])

        # Collect all versions from components (None versions are omitted)
        serialized_versions: set[str] = set()
        for comp in components:
            if "version" in comp:
                serialized_versions.add(comp["version"])

        expected_versions: set[str] = set()
        for pkg in doc.packages:
            if pkg.version is not None:
                expected_versions.add(pkg.version)

        assert expected_versions == serialized_versions

    @given(doc=sbom_documents())
    def test_component_purls_preserved(self, doc: SBOMDocument) -> None:
        """All PURLs from the document are present in serialized output."""
        parsed = _serialize_and_parse(doc)

        components = parsed.get("components", [])

        # Collect all PURLs from components (None purls are omitted)
        serialized_purls: set[str] = set()
        for comp in components:
            if "purl" in comp:
                serialized_purls.add(comp["purl"])

        expected_purls: set[str] = set()
        for pkg in doc.packages:
            if pkg.package_url is not None:
                expected_purls.add(pkg.package_url)

        assert expected_purls == serialized_purls

    @given(doc=sbom_documents())
    def test_hash_values_preserved(self, doc: SBOMDocument) -> None:
        """All hash values from components are present in serialized output."""
        parsed = _serialize_and_parse(doc)

        components = parsed.get("components", [])

        # Collect all hash values from serialized components
        serialized_hashes: set[tuple[str, str]] = set()
        for comp in components:
            for h in comp.get("hashes", []):
                serialized_hashes.add((h["alg"], h["content"]))

        # Expected hashes from the domain packages
        expected_hashes: set[tuple[str, str]] = set()
        for pkg in doc.packages:
            for checksum in pkg.checksums:
                alg = _ALGORITHM_MAP[checksum.algorithm]
                expected_hashes.add((alg, checksum.value))

        assert expected_hashes == serialized_hashes

    @given(doc=sbom_documents())
    def test_dependency_references_preserved(self, doc: SBOMDocument) -> None:
        """Dependency references (DEPENDS_ON) are preserved as set equality."""
        parsed = _serialize_and_parse(doc)

        writer = CycloneDXWriter()

        # Build spdx_id → bom-ref map for all packages
        id_to_bom_ref: dict[str, str] = {}
        for pkg in doc.packages:
            id_to_bom_ref[pkg.spdx_id] = writer._generate_bom_ref(pkg)
        id_to_bom_ref[doc.root_package.spdx_id] = writer._generate_bom_ref(doc.root_package)

        # Expected dependencies: source_bom_ref → set of target_bom_refs
        expected_deps: dict[str, set[str]] = {}
        for rel in doc.relationships:
            if rel.relationship_type == RelationshipType.DEPENDS_ON:
                source_ref = id_to_bom_ref.get(rel.source_id)
                target_ref = id_to_bom_ref.get(rel.target_id)
                if source_ref is not None and target_ref is not None:
                    if source_ref not in expected_deps:
                        expected_deps[source_ref] = set()
                    expected_deps[source_ref].add(target_ref)

        # Actual dependencies from serialized output
        actual_deps: dict[str, set[str]] = {}
        for dep_entry in parsed.get("dependencies", []):
            ref = dep_entry["ref"]
            depends_on = set(dep_entry.get("dependsOn", []))
            if depends_on:
                actual_deps[ref] = depends_on

        assert expected_deps == actual_deps

    @given(doc=sbom_documents())
    def test_unicode_names_preserved(self, doc: SBOMDocument) -> None:
        """Unicode characters in component names are preserved without loss."""
        parsed = _serialize_and_parse(doc)

        components = parsed.get("components", [])
        serialized_names = {comp["name"] for comp in components}

        # Every package name with unicode should round-trip exactly
        for pkg in doc.packages:
            assert pkg.name in serialized_names

    @given(doc=sbom_documents())
    def test_none_optional_fields_omitted(self, doc: SBOMDocument) -> None:
        """Optional fields set to None are omitted from the CycloneDX output."""
        parsed = _serialize_and_parse(doc)

        components = parsed.get("components", [])

        for pkg, comp in zip(doc.packages, components, strict=False):
            # version=None → "version" key absent
            if pkg.version is None:
                assert "version" not in comp
            # package_url=None → "purl" key absent
            if pkg.package_url is None:
                assert "purl" not in comp
            # concluded_license=None → "licenses" key absent
            if pkg.concluded_license is None:
                assert "licenses" not in comp
            # copyright_text=None → "copyright" key absent
            if pkg.copyright_text is None:
                assert "copyright" not in comp
