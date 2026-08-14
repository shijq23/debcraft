"""Property-based tests for SPDX 3.0 round-trip data preservation.

# Feature: sbom-writers, Property 8: SPDX 3.0 round-trip data preservation

**Validates: Requirements 10.3, 10.6, 10.7**

Property 8: SPDX 3.0 round-trip data preservation.
For any valid SBOMDocument (including documents with Unicode characters and
None optional fields), when serialized by the SPDX3Writer and the resulting
JSON is parsed back into a dictionary, the dictionary SHALL contain all
package names, versions, element identifiers, relationship types, and hash
values present in the original SBOMDocument with exact string equality.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings

from debcraft.domain.sbom.values import (
    RelationshipType,
    SBOMDocument,
)
from debcraft.infrastructure.sbom_writers.printer import SBOMPrinter
from debcraft.infrastructure.sbom_writers.spdx3 import SPDX3Writer

from .strategies import sbom_documents

# SPDX 3.0 relationship type vocabulary (maps domain types that have SPDX 3.0 equivalents)
_SPDX3_RELATIONSHIP_TYPE_VOCABULARY = {
    RelationshipType.DESCRIBES: "https://spdx.org/rdf/3.0.1/terms/RelationshipType/describes",
    RelationshipType.CONTAINS: "https://spdx.org/rdf/3.0.1/terms/RelationshipType/contains",
    RelationshipType.DEPENDS_ON: "https://spdx.org/rdf/3.0.1/terms/RelationshipType/dependsOn",
    RelationshipType.BUILD_TOOL_OF: "https://spdx.org/rdf/3.0.1/terms/RelationshipType/buildToolOf",
}

# SPDX 3.0 NoAssertion value
_NO_ASSERTION = "https://spdx.org/rdf/3.0.1/terms/Core/NoAssertionElement"


@pytest.mark.unit
@pytest.mark.spdx
class TestProperty8SPDX3RoundTrip:
    """Property 8: SPDX 3.0 round-trip data preservation.

    For any valid SBOMDocument, when serialized by the SPDX3Writer and the
    resulting JSON is parsed back into a dictionary, the dictionary SHALL
    contain all package names, versions, element identifiers, relationship
    types, and hash values present in the original SBOMDocument with exact
    string equality.
    """

    @settings(max_examples=100)
    @given(doc=sbom_documents())
    def test_package_names_preserved(self, doc: SBOMDocument) -> None:
        """All package names from the original document appear in the serialized output."""
        spdx_dict = self._serialize_document(doc)
        parsed = self._roundtrip(spdx_dict)

        # Collect all package names from original document
        expected_names = {doc.root_package.name}
        for pkg in doc.packages:
            expected_names.add(pkg.name)

        # Collect package names from parsed output
        elements = parsed.get("element", [])
        actual_names = {el["name"] for el in elements if el.get("@type") == "software_Package"}

        assert expected_names <= actual_names, f"Missing package names: {expected_names - actual_names}"

    @settings(max_examples=100)
    @given(doc=sbom_documents())
    def test_package_versions_preserved(self, doc: SBOMDocument) -> None:
        """All package versions from the original document appear in the serialized output."""
        spdx_dict = self._serialize_document(doc)
        parsed = self._roundtrip(spdx_dict)

        # Collect all expected version values from original document
        all_packages = [doc.root_package, *doc.packages]
        expected_versions = {}
        for pkg in all_packages:
            if pkg.version:
                expected_versions[pkg.spdx_id] = pkg.version
            else:
                expected_versions[pkg.spdx_id] = _NO_ASSERTION

        # Collect version values from parsed output, indexed by spdxId
        elements = parsed.get("element", [])
        actual_versions = {
            el["spdxId"]: el.get("software_packageVersion") for el in elements if el.get("@type") == "software_Package"
        }

        for spdx_id, expected_version in expected_versions.items():
            assert spdx_id in actual_versions, f"Package {spdx_id} not found in output"
            assert actual_versions[spdx_id] == expected_version, (
                f"Version mismatch for {spdx_id}: expected {expected_version!r}, got {actual_versions[spdx_id]!r}"
            )

    @settings(max_examples=100)
    @given(doc=sbom_documents())
    def test_element_identifiers_preserved(self, doc: SBOMDocument) -> None:
        """All element identifiers (spdxId) from packages appear in the serialized output."""
        spdx_dict = self._serialize_document(doc)
        parsed = self._roundtrip(spdx_dict)

        # Collect all expected spdx IDs from original document
        expected_ids = {doc.root_package.spdx_id}
        for pkg in doc.packages:
            expected_ids.add(pkg.spdx_id)

        # Collect spdxId values from parsed output (packages only)
        elements = parsed.get("element", [])
        actual_ids = {el["spdxId"] for el in elements if el.get("@type") == "software_Package"}

        assert expected_ids <= actual_ids, f"Missing element identifiers: {expected_ids - actual_ids}"

    @settings(max_examples=100)
    @given(doc=sbom_documents())
    def test_relationship_types_preserved(self, doc: SBOMDocument) -> None:
        """All mapped relationship types from the original document appear in the output."""
        spdx_dict = self._serialize_document(doc)
        parsed = self._roundtrip(spdx_dict)

        # Collect expected relationship types (only those with SPDX 3.0 mappings)
        expected_rel_types = []
        for rel in doc.relationships:
            mapped = _SPDX3_RELATIONSHIP_TYPE_VOCABULARY.get(rel.relationship_type)
            if mapped is not None:
                expected_rel_types.append(mapped)

        # Collect actual relationship types from parsed output
        elements = parsed.get("element", [])
        actual_rel_types = [el["relationshipType"] for el in elements if el.get("@type") == "Relationship"]

        # Each expected relationship type should appear in actual
        for expected in expected_rel_types:
            assert expected in actual_rel_types, (
                f"Relationship type {expected!r} not found in output. Actual: {actual_rel_types}"
            )

    @settings(max_examples=100)
    @given(doc=sbom_documents())
    def test_hash_values_preserved(self, doc: SBOMDocument) -> None:
        """All hash values from the original document appear in the serialized output."""
        spdx_dict = self._serialize_document(doc)
        parsed = self._roundtrip(spdx_dict)

        # Collect all expected hash values from the original document
        all_packages = [doc.root_package, *doc.packages]
        expected_hashes = set()
        for pkg in all_packages:
            for checksum in pkg.checksums:
                expected_hashes.add(checksum.value)

        # Collect actual hash values from parsed output
        elements = parsed.get("element", [])
        actual_hashes = set()
        for el in elements:
            if el.get("@type") == "software_Package":
                for hash_entry in el.get("verifiedUsing", []):
                    if hash_entry.get("@type") == "Hash":
                        actual_hashes.add(hash_entry["hashValue"])

        assert expected_hashes <= actual_hashes, f"Missing hash values: {expected_hashes - actual_hashes}"

    @settings(max_examples=100)
    @given(doc=sbom_documents())
    def test_unicode_preservation(self, doc: SBOMDocument) -> None:
        """Unicode characters in package names are preserved through serialization.

        Validates Requirement 10.6: Unicode code points preserved without
        normalization, replacement, or loss.
        """
        spdx_dict = self._serialize_document(doc)
        parsed = self._roundtrip(spdx_dict)

        # Verify Unicode package names survive round-trip exactly
        elements = parsed.get("element", [])
        package_elements = [el for el in elements if el.get("@type") == "software_Package"]
        actual_names = {el["name"] for el in package_elements}

        # Check root package name
        assert doc.root_package.name in actual_names, f"Root package name {doc.root_package.name!r} not preserved"

        # Check component package names
        for pkg in doc.packages:
            assert pkg.name in actual_names, f"Package name {pkg.name!r} not preserved"

    @settings(max_examples=100)
    @given(doc=sbom_documents())
    def test_none_optional_fields_handling(self, doc: SBOMDocument) -> None:
        """Optional fields set to None use NoAssertion sentinel or are omitted.

        Validates Requirement 10.7: None optional fields represented per
        format-specific rules without introducing spurious values.
        """
        spdx_dict = self._serialize_document(doc)
        parsed = self._roundtrip(spdx_dict)

        elements = parsed.get("element", [])
        all_packages = [doc.root_package, *doc.packages]

        # Build a set of spdx_ids that appear more than once to skip ambiguous matches
        from collections import Counter

        spdx_id_counts = Counter(pkg.spdx_id for pkg in all_packages)

        for pkg in all_packages:
            # Skip packages with duplicate spdx_ids (ambiguous matching)
            if spdx_id_counts[pkg.spdx_id] > 1:
                continue

            # Find the matching element in parsed output
            matching = [
                el for el in elements if el.get("@type") == "software_Package" and el.get("spdxId") == pkg.spdx_id
            ]
            if not matching:
                continue
            el = matching[0]

            # version=None → NoAssertion sentinel
            if pkg.version is None:
                assert el.get("software_packageVersion") == _NO_ASSERTION

            # download_location=None → NoAssertion sentinel
            if pkg.download_location is None:
                assert el.get("software_downloadLocation") == _NO_ASSERTION

            # concluded_license=None → field should be absent (no spurious value)
            if pkg.concluded_license is None:
                assert "declaredLicense" not in el

            # description=None → field should be absent
            if pkg.description is None:
                assert "description" not in el

    # --- Helper methods ---

    def _serialize_document(self, doc: SBOMDocument) -> dict:
        """Serialize an SBOMDocument using the SPDX3Writer's internal serializer."""
        writer = SPDX3Writer()
        return writer._serialize(doc)

    def _roundtrip(self, spdx_dict: dict) -> dict:
        """Print dict to JSON bytes and parse back to verify round-trip."""
        printer = SBOMPrinter()
        json_bytes = printer.print(spdx_dict)
        return json.loads(json_bytes.decode("utf-8"))
