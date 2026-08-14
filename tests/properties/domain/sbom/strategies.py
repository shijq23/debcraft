"""Shared Hypothesis strategies for SBOM domain value objects.

# Feature: sbom-writers, Property 1: Model construction preserves valid inputs

Provides composite strategies for generating valid instances of all SBOM value
objects for use in property-based tests. Includes Unicode text (CJK, Arabic,
emoji) in string fields where patterns allow.
"""

from __future__ import annotations

from hypothesis import strategies as st

from debcraft.domain.sbom.values import (
    ChecksumAlgorithm,
    ExternalReferenceCategory,
    RelationshipType,
    SBOMChecksum,
    SBOMCreationInfo,
    SBOMDocument,
    SBOMExternalReference,
    SBOMExtractedLicense,
    SBOMPackage,
    SBOMRelationship,
)

# ---------------------------------------------------------------------------
# Base strategies for constrained string patterns
# ---------------------------------------------------------------------------

spdx_ids: st.SearchStrategy[str] = st.from_regex(r"SPDXRef-[a-zA-Z0-9.\-]{1,64}", fullmatch=True)

license_refs: st.SearchStrategy[str] = st.from_regex(r"LicenseRef-[a-zA-Z0-9.\-]{1,64}", fullmatch=True)

sha256_hashes: st.SearchStrategy[str] = st.from_regex(r"[0-9a-f]{64}", fullmatch=True)

sha1_hashes: st.SearchStrategy[str] = st.from_regex(r"[0-9a-f]{40}", fullmatch=True)

md5_hashes: st.SearchStrategy[str] = st.from_regex(r"[0-9a-f]{32}", fullmatch=True)

# Non-empty text including Unicode (CJK, Arabic, emoji) for freeform fields
_unicode_alphabet = st.characters(
    categories=("L", "M", "N", "P", "S", "Z"),
    exclude_characters="\x00",
)

non_empty_text: st.SearchStrategy[str] = st.text(alphabet=_unicode_alphabet, min_size=1, max_size=100)

short_text: st.SearchStrategy[str] = st.text(alphabet=_unicode_alphabet, min_size=1, max_size=50)

# Document name: non-empty, max 255 characters
document_names: st.SearchStrategy[str] = st.text(alphabet=_unicode_alphabet, min_size=1, max_size=255)

# Tool identifiers in "Tool: name-version" format
tool_identifiers: st.SearchStrategy[str] = st.text(
    alphabet=st.characters(categories=("L", "N", "P", "S")),
    min_size=1,
    max_size=50,
).map(lambda s: f"Tool: {s}")

# URLs (non-empty strings for external reference fields)
urls: st.SearchStrategy[str] = st.from_regex(r"https?://[a-z0-9]{1,20}\.[a-z]{2,4}(/[a-z0-9]{1,10})*", fullmatch=True)

# ---------------------------------------------------------------------------
# Composite strategies for valid value objects
# ---------------------------------------------------------------------------


def sbom_checksums() -> st.SearchStrategy[SBOMChecksum]:
    """Generate valid SBOMChecksum instances with matching algorithm/length."""
    return st.one_of(
        st.builds(
            SBOMChecksum,
            algorithm=st.just(ChecksumAlgorithm.SHA256),
            value=sha256_hashes,
        ),
        st.builds(
            SBOMChecksum,
            algorithm=st.just(ChecksumAlgorithm.SHA1),
            value=sha1_hashes,
        ),
        st.builds(
            SBOMChecksum,
            algorithm=st.just(ChecksumAlgorithm.MD5),
            value=md5_hashes,
        ),
    )


def sbom_external_references() -> st.SearchStrategy[SBOMExternalReference]:
    """Generate valid SBOMExternalReference instances."""
    return st.builds(
        SBOMExternalReference,
        category=st.sampled_from(ExternalReferenceCategory),
        url=urls,
        comment=st.none() | short_text,
    )


def sbom_extracted_licenses() -> st.SearchStrategy[SBOMExtractedLicense]:
    """Generate valid SBOMExtractedLicense instances."""
    return st.builds(
        SBOMExtractedLicense,
        license_id=license_refs,
        extracted_text=non_empty_text,
        name=st.none() | short_text,
        cross_references=st.lists(urls, max_size=3),
    )


def sbom_creation_infos() -> st.SearchStrategy[SBOMCreationInfo]:
    """Generate valid SBOMCreationInfo instances."""
    return st.builds(
        SBOMCreationInfo,
        tools=st.lists(tool_identifiers, min_size=1, max_size=3),
        created=st.from_regex(
            r"20[0-9]{2}-[01][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z",
            fullmatch=True,
        ),
        creators=st.lists(non_empty_text, min_size=1, max_size=3),
        license_list_version=st.none() | st.from_regex(r"3\.[0-9]{1,2}", fullmatch=True),
    )


def sbom_packages() -> st.SearchStrategy[SBOMPackage]:
    """Generate valid SBOMPackage instances."""
    return st.builds(
        SBOMPackage,
        spdx_id=spdx_ids,
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


def sbom_relationships() -> st.SearchStrategy[SBOMRelationship]:
    """Generate valid SBOMRelationship instances."""
    return st.builds(
        SBOMRelationship,
        source_id=spdx_ids,
        target_id=spdx_ids,
        relationship_type=st.sampled_from(RelationshipType),
    )


def sbom_documents() -> st.SearchStrategy[SBOMDocument]:
    """Generate valid SBOMDocument instances."""
    return st.builds(
        SBOMDocument,
        namespace=urls,
        name=document_names,
        creation_info=sbom_creation_infos(),
        root_package=sbom_packages(),
        packages=st.lists(sbom_packages(), max_size=3),
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
