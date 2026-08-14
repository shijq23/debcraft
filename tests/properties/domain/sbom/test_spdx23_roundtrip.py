"""Property-based tests for SPDX 2.3 round-trip data preservation.

# Feature: sbom-writers, Property 7: SPDX 2.3 round-trip data preservation

**Validates: Requirements 10.2, 10.6, 10.7**

Property 7: SPDX 2.3 round-trip data preservation.
For any valid SBOMDocument (including documents with Unicode characters in
package names, None optional fields, and varying numbers of components),
when serialized by the SPDX23Writer and the resulting JSON is parsed back
into a dictionary, the dictionary SHALL contain all package names, versions,
SPDX identifiers, relationship types, and checksum values present in the
original SBOMDocument with exact string equality. Optional fields set to None
SHALL use "NOASSERTION" sentinel or be omitted per SPDX 2.3 rules, and
parsing back SHALL not introduce spurious values.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.domain.sbom.values import (
    SBOMDocument,
    SBOMPackage,
)
from debcraft.infrastructure.sbom_writers.printer import SBOMPrinter
from debcraft.infrastructure.sbom_writers.spdx23 import SPDX23Writer

from .strategies import (
    non_empty_text,
    sbom_checksums,
    sbom_creation_infos,
    sbom_external_references,
    sbom_extracted_licenses,
    sbom_relationships,
    short_text,
    urls,
)

# ---------------------------------------------------------------------------
# Strategy: documents with unique SPDX IDs per package
# ---------------------------------------------------------------------------


@st.composite
def _unique_id_packages(draw: st.DrawFn, min_size: int = 0, max_size: int = 4) -> list[SBOMPackage]:
    """Generate a list of SBOMPackage with unique SPDX IDs."""
    count = draw(st.integers(min_value=min_size, max_value=max_size))
    used_ids: set[str] = set()
    packages: list[SBOMPackage] = []
    for i in range(count):
        # Generate a unique SPDX ID by appending index suffix
        spdx_id = f"SPDXRef-Pkg-{i}"
        used_ids.add(spdx_id)
        pkg = draw(
            st.builds(
                SBOMPackage,
                spdx_id=st.just(spdx_id),
                name=non_empty_text,
                version=st.none() | short_text,
                supplier=st.none() | short_text,
                download_location=st.none() | urls,
                checksums=st.lists(sbom_checksums(), max_size=3),
                package_url=st.none() | urls,
                concluded_license=st.none() | short_text,
                declared_license=st.none() | short_text,
                copyright_text=st.none() | short_text,
                description=st.none() | short_text,
                external_references=st.lists(sbom_external_references(), max_size=2),
            )
        )
        packages.append(pkg)
    return packages


@st.composite
def sbom_documents_unique_ids(draw: st.DrawFn) -> SBOMDocument:
    """Generate valid SBOMDocument instances with unique SPDX IDs."""
    packages = draw(_unique_id_packages(min_size=0, max_size=3))

    # Root package gets a distinct ID
    root_package = draw(
        st.builds(
            SBOMPackage,
            spdx_id=st.just("SPDXRef-RootPkg"),
            name=non_empty_text,
            version=st.none() | short_text,
            supplier=st.none() | short_text,
            download_location=st.none() | urls,
            checksums=st.lists(sbom_checksums(), max_size=3),
            package_url=st.none() | urls,
            concluded_license=st.none() | short_text,
            declared_license=st.none() | short_text,
            copyright_text=st.none() | short_text,
            description=st.none() | short_text,
            external_references=st.lists(sbom_external_references(), max_size=2),
        )
    )

    return draw(
        st.builds(
            SBOMDocument,
            namespace=urls,
            name=st.text(
                alphabet=st.characters(
                    categories=("L", "M", "N", "P", "S", "Z"),
                    exclude_characters="\x00",
                ),
                min_size=1,
                max_size=255,
            ),
            creation_info=sbom_creation_infos(),
            root_package=st.just(root_package),
            packages=st.just(packages),
            relationships=st.lists(sbom_relationships(), max_size=3),
            extracted_licenses=st.lists(sbom_extracted_licenses(), max_size=2),
            comment=st.none() | short_text,
            provenance_tool=st.none() | short_text,
            provenance_timestamp=st.none()
            | st.from_regex(
                r"20[0-9]{2}-[01][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z",
                fullmatch=True,
            ),
        )
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_to_dict(document: SBOMDocument) -> dict:
    """Serialize an SBOMDocument to a Python dict via SPDX23Writer's internal method.

    We call the writer's _serialize method directly to get the SPDX 2.3 dict
    without needing filesystem I/O or a WorkflowContext.
    """
    writer = SPDX23Writer()
    diagnostics: list[str] = []
    return writer._serialize(document, diagnostics)


def _serialize_and_parse(document: SBOMDocument) -> dict:
    """Serialize an SBOMDocument to SPDX 2.3 JSON and parse it back."""
    spdx_dict = _serialize_to_dict(document)
    printer = SBOMPrinter()
    json_bytes = printer.print(spdx_dict)
    return json.loads(json_bytes.decode("utf-8"))


# ---------------------------------------------------------------------------
# Property 7: SPDX 2.3 round-trip data preservation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.spdx
class TestProperty7SPDX23RoundTrip:
    """Property 7: SPDX 2.3 round-trip data preservation.

    For any valid SBOMDocument, serialization and parse-back preserves all
    package names, versions, SPDX identifiers, relationship types, checksum
    values with exact string equality.
    """

    @given(doc=sbom_documents_unique_ids())
    def test_package_names_preserved(self, doc: SBOMDocument) -> None:
        """All package names from the original document appear in the parsed output."""
        parsed = _serialize_and_parse(doc)

        # Collect all package names from the original document
        all_packages = [doc.root_package, *list(doc.packages)]
        parsed_packages = parsed["packages"]

        # Match packages by SPDX ID (unique within the document)
        parsed_by_id = {pkg["SPDXID"]: pkg for pkg in parsed_packages}

        for pkg in all_packages:
            assert pkg.spdx_id in parsed_by_id
            assert parsed_by_id[pkg.spdx_id]["name"] == pkg.name

    @given(doc=sbom_documents_unique_ids())
    def test_package_versions_preserved(self, doc: SBOMDocument) -> None:
        """All package versions are preserved with exact string equality.

        Versions set to None use NOASSERTION sentinel in SPDX 2.3.
        """
        parsed = _serialize_and_parse(doc)

        all_packages = [doc.root_package, *list(doc.packages)]
        parsed_packages = parsed["packages"]
        parsed_by_id = {pkg["SPDXID"]: pkg for pkg in parsed_packages}

        for pkg in all_packages:
            parsed_pkg = parsed_by_id[pkg.spdx_id]
            if pkg.version is not None:
                assert parsed_pkg["versionInfo"] == pkg.version
            else:
                assert parsed_pkg["versionInfo"] == "NOASSERTION"

    @given(doc=sbom_documents_unique_ids())
    def test_spdx_identifiers_preserved(self, doc: SBOMDocument) -> None:
        """All SPDX identifiers from original packages appear in parsed output."""
        parsed = _serialize_and_parse(doc)

        all_packages = [doc.root_package, *list(doc.packages)]
        original_ids = {pkg.spdx_id for pkg in all_packages}

        parsed_ids = {pkg["SPDXID"] for pkg in parsed["packages"]}

        assert original_ids == parsed_ids

    @given(doc=sbom_documents_unique_ids())
    def test_relationship_types_preserved(self, doc: SBOMDocument) -> None:
        """All relationship types are preserved in parsed output.

        Unmapped types fall back to OTHER per SPDX 2.3 rules.
        """
        parsed = _serialize_and_parse(doc)

        # SPDX 2.3 supported types
        spdx23_types = {"DESCRIBES", "CONTAINS", "DEPENDS_ON", "BUILD_TOOL_OF", "OTHER"}

        for i, rel in enumerate(doc.relationships):
            parsed_rel = parsed["relationships"][i]

            # The writer maps unsupported types to OTHER
            expected_type = rel.relationship_type.value
            if expected_type not in spdx23_types:
                expected_type = "OTHER"

            assert parsed_rel["relationshipType"] == expected_type
            assert parsed_rel["spdxElementId"] == rel.source_id
            assert parsed_rel["relatedSpdxElement"] == rel.target_id

    @given(doc=sbom_documents_unique_ids())
    def test_checksum_values_preserved(self, doc: SBOMDocument) -> None:
        """All checksum values are preserved with exact string equality."""
        parsed = _serialize_and_parse(doc)

        all_packages = [doc.root_package, *list(doc.packages)]
        parsed_packages = parsed["packages"]
        parsed_by_id = {pkg["SPDXID"]: pkg for pkg in parsed_packages}

        for pkg in all_packages:
            parsed_pkg = parsed_by_id[pkg.spdx_id]
            if pkg.checksums:
                assert "checksums" in parsed_pkg
                parsed_checksums = parsed_pkg["checksums"]
                assert len(parsed_checksums) == len(pkg.checksums)
                for orig_cs, parsed_cs in zip(pkg.checksums, parsed_checksums, strict=False):
                    assert parsed_cs["checksumValue"] == orig_cs.value
                    assert parsed_cs["algorithm"] == orig_cs.algorithm.value
            else:
                # Empty checksums list means no checksums key in output
                assert "checksums" not in parsed_pkg

    @given(doc=sbom_documents_unique_ids())
    def test_none_optional_fields_use_noassertion(self, doc: SBOMDocument) -> None:
        """Optional fields set to None use NOASSERTION sentinel per SPDX 2.3 rules."""
        parsed = _serialize_and_parse(doc)

        all_packages = [doc.root_package, *list(doc.packages)]
        parsed_packages = parsed["packages"]
        parsed_by_id = {pkg["SPDXID"]: pkg for pkg in parsed_packages}

        for pkg in all_packages:
            parsed_pkg = parsed_by_id[pkg.spdx_id]

            # String fields that use NOASSERTION when None
            if pkg.version is None:
                assert parsed_pkg["versionInfo"] == "NOASSERTION"
            if pkg.download_location is None:
                assert parsed_pkg["downloadLocation"] == "NOASSERTION"
            if pkg.supplier is None:
                assert parsed_pkg["supplier"] == "NOASSERTION"
            if pkg.concluded_license is None:
                assert parsed_pkg["licenseConcluded"] == "NOASSERTION"
            if pkg.declared_license is None:
                assert parsed_pkg["licenseDeclared"] == "NOASSERTION"
            if pkg.copyright_text is None:
                assert parsed_pkg["copyrightText"] == "NOASSERTION"

    @given(doc=sbom_documents_unique_ids())
    def test_no_spurious_packages_introduced(self, doc: SBOMDocument) -> None:
        """Parsing back SHALL not introduce spurious packages."""
        parsed = _serialize_and_parse(doc)

        all_packages = [doc.root_package, *list(doc.packages)]
        # Number of parsed packages should equal original package count
        assert len(parsed["packages"]) == len(all_packages)

    @given(doc=sbom_documents_unique_ids())
    def test_no_spurious_relationships_introduced(self, doc: SBOMDocument) -> None:
        """Parsing back SHALL not introduce spurious relationships."""
        parsed = _serialize_and_parse(doc)

        assert len(parsed["relationships"]) == len(doc.relationships)

    @given(doc=sbom_documents_unique_ids())
    def test_unicode_package_names_preserved(self, doc: SBOMDocument) -> None:
        """Unicode characters in package names are preserved without loss.

        Validates Requirement 10.6: Unicode code points preserved without
        normalization, replacement, or loss.
        """
        parsed = _serialize_and_parse(doc)

        all_packages = [doc.root_package, *list(doc.packages)]
        parsed_packages = parsed["packages"]
        parsed_by_id = {pkg["SPDXID"]: pkg for pkg in parsed_packages}

        for pkg in all_packages:
            parsed_pkg = parsed_by_id[pkg.spdx_id]
            assert parsed_pkg["name"] == pkg.name
