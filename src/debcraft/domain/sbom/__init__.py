"""SBOM domain layer - format-independent internal SBOM model.

This module defines the internal SBOM model as frozen dataclass value objects.
The model uses SPDX 3.0 concepts (relationships, elements, packages) but imports
no SPDX or CycloneDX libraries. Format-specific mapping is handled by
infrastructure writers.
"""

from debcraft.domain.sbom.values import (
    ChecksumAlgorithm,
    ExternalReferenceCategory,
    OutputFormat,
    RelationshipType,
    SBOMChecksum,
    SBOMCreationInfo,
    SBOMDocument,
    SBOMExternalReference,
    SBOMExtractedLicense,
    SBOMPackage,
    SBOMRelationship,
    WriterResult,
)

__all__ = [
    "ChecksumAlgorithm",
    "ExternalReferenceCategory",
    "OutputFormat",
    "RelationshipType",
    "SBOMChecksum",
    "SBOMCreationInfo",
    "SBOMDocument",
    "SBOMExternalReference",
    "SBOMExtractedLicense",
    "SBOMPackage",
    "SBOMRelationship",
    "WriterResult",
]
