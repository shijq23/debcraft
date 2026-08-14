"""Value objects for the SBOM domain layer.

Frozen dataclass value objects representing the format-independent internal SBOM
model. These are pure domain objects with no imports from infrastructure, SPDX
libraries, or CycloneDX libraries. All validation is performed at construction
time via __post_init__.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class RelationshipType(Enum):
    """Types of relationships between SBOM elements."""

    DESCRIBES = "DESCRIBES"
    CONTAINS = "CONTAINS"
    DEPENDS_ON = "DEPENDS_ON"
    BUILD_TOOL_OF = "BUILD_TOOL_OF"
    OTHER = "OTHER"


class ChecksumAlgorithm(Enum):
    """Supported checksum algorithms."""

    SHA256 = "SHA256"
    SHA1 = "SHA1"
    MD5 = "MD5"


class ExternalReferenceCategory(Enum):
    """Categories of external references."""

    PACKAGE_MANAGER = "PACKAGE_MANAGER"
    SECURITY_ADVISORY = "SECURITY_ADVISORY"
    OTHER = "OTHER"


class OutputFormat(Enum):
    """Supported SBOM output formats."""

    SPDX_3_0 = "spdx_3_0"
    SPDX_2_3 = "spdx_2_3"
    CYCLONEDX = "cyclonedx"


_SPDX_ID_PATTERN = re.compile(r"^SPDXRef-[a-zA-Z0-9.\-]+$")
_LICENSE_REF_PATTERN = re.compile(r"^LicenseRef-[a-zA-Z0-9.\-]+$")
_HEX_PATTERN = re.compile(r"^[0-9a-f]+$")

_HASH_LENGTHS: dict[ChecksumAlgorithm, int] = {
    ChecksumAlgorithm.SHA256: 64,
    ChecksumAlgorithm.SHA1: 40,
    ChecksumAlgorithm.MD5: 32,
}


@dataclass(frozen=True)
class SBOMChecksum:
    """A checksum with algorithm and hex value.

    Attributes:
        algorithm: The hash algorithm used.
        value: Lowercase hexadecimal hash string.
    """

    algorithm: ChecksumAlgorithm
    value: str

    def __post_init__(self) -> None:  # noqa: D105
        expected_length = _HASH_LENGTHS[self.algorithm]
        if len(self.value) != expected_length:
            raise ValueError(
                f"SBOMChecksum.value: expected {expected_length} characters for "
                f"{self.algorithm.value}, got {len(self.value)}"
            )
        if not _HEX_PATTERN.match(self.value):
            raise ValueError(
                f"SBOMChecksum.value: must be lowercase hexadecimal, got '{self.value[:20]}...'"
                if len(self.value) > 20
                else f"SBOMChecksum.value: must be lowercase hexadecimal, got '{self.value}'"
            )


@dataclass(frozen=True)
class SBOMExternalReference:
    """A reference to an external resource.

    Attributes:
        category: The type of external reference.
        url: The URL of the external resource (non-empty).
        comment: Optional descriptive comment.
    """

    category: ExternalReferenceCategory
    url: str
    comment: str | None = None

    def __post_init__(self) -> None:  # noqa: D105
        if not self.url:
            raise ValueError("SBOMExternalReference.url: must be non-empty")


@dataclass(frozen=True)
class SBOMExtractedLicense:
    """A license not on the SPDX license list.

    Attributes:
        license_id: Local identifier matching LicenseRef-[a-zA-Z0-9.-]+.
        extracted_text: The full license text (non-empty).
        name: Optional human-readable license name.
        cross_references: URLs where the license can be found.
    """

    license_id: str
    extracted_text: str
    name: str | None = None
    cross_references: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:  # noqa: D105
        if not _LICENSE_REF_PATTERN.match(self.license_id):
            raise ValueError(
                f"SBOMExtractedLicense.license_id: must match pattern "
                f"'LicenseRef-[a-zA-Z0-9.-]+', got '{self.license_id}'"
            )
        if not self.extracted_text:
            raise ValueError("SBOMExtractedLicense.extracted_text: must be non-empty")


@dataclass(frozen=True)
class SBOMCreationInfo:
    """Document creation metadata.

    Attributes:
        tools: Tool identifiers in "Tool: name-version" format (at least one).
        created: ISO 8601 UTC creation timestamp.
        creators: Creator identifiers (non-empty strings).
        license_list_version: Optional SPDX license list version.
    """

    tools: list[str]
    created: str
    creators: list[str]
    license_list_version: str | None = None

    def __post_init__(self) -> None:  # noqa: D105
        if not self.tools:
            raise ValueError("SBOMCreationInfo.tools: must contain at least one entry")
        for i, tool in enumerate(self.tools):
            if not tool.startswith("Tool: "):
                raise ValueError(f"SBOMCreationInfo.tools[{i}]: must be in 'Tool: name-version' format, got '{tool}'")
        if not self.creators:
            raise ValueError("SBOMCreationInfo.creators: must contain at least one entry")
        for i, creator in enumerate(self.creators):
            if not creator:
                raise ValueError(f"SBOMCreationInfo.creators[{i}]: must be non-empty")


@dataclass(frozen=True)
class SBOMPackage:
    """A software package within the SBOM.

    Attributes:
        spdx_id: Unique identifier matching SPDXRef-[a-zA-Z0-9.-]+.
        name: Package name (non-empty).
        version: Package version string (optional).
        supplier: Package supplier (optional).
        download_location: Download URL (optional).
        checksums: List of package checksums.
        package_url: Package URL / PURL (optional).
        concluded_license: Concluded license expression (optional).
        declared_license: Declared license expression (optional).
        copyright_text: Copyright text (optional).
        description: Package description (optional).
        external_references: External references for this package.
    """

    spdx_id: str
    name: str
    version: str | None = None
    supplier: str | None = None
    download_location: str | None = None
    checksums: list[SBOMChecksum] = field(default_factory=list)
    package_url: str | None = None
    concluded_license: str | None = None
    declared_license: str | None = None
    copyright_text: str | None = None
    description: str | None = None
    external_references: list[SBOMExternalReference] = field(default_factory=list)

    def __post_init__(self) -> None:  # noqa: D105
        if not _SPDX_ID_PATTERN.match(self.spdx_id):
            raise ValueError(f"SBOMPackage.spdx_id: must match pattern 'SPDXRef-[a-zA-Z0-9.-]+', got '{self.spdx_id}'")
        if not self.name:
            raise ValueError("SBOMPackage.name: must be non-empty")


@dataclass(frozen=True)
class SBOMRelationship:
    """A typed directional relationship between two SBOM elements.

    Attributes:
        source_id: Source element SPDX identifier.
        target_id: Target element SPDX identifier.
        relationship_type: The type of relationship.
    """

    source_id: str
    target_id: str
    relationship_type: RelationshipType

    def __post_init__(self) -> None:  # noqa: D105
        if not _SPDX_ID_PATTERN.match(self.source_id):
            raise ValueError(
                f"SBOMRelationship.source_id: must match pattern 'SPDXRef-[a-zA-Z0-9.-]+', got '{self.source_id}'"
            )
        if not _SPDX_ID_PATTERN.match(self.target_id):
            raise ValueError(
                f"SBOMRelationship.target_id: must match pattern 'SPDXRef-[a-zA-Z0-9.-]+', got '{self.target_id}'"
            )


@dataclass(frozen=True)
class SBOMDocument:
    """A complete SBOM document.

    Attributes:
        namespace: Document namespace (non-empty).
        name: Document name (non-empty, max 255 characters).
        creation_info: Document creation metadata.
        root_package: The root package element.
        packages: Component packages (zero or more).
        relationships: Relationships between elements.
        extracted_licenses: Licenses not on the SPDX list.
        comment: Optional document comment.
        provenance_tool: Tool version string that created the model.
        provenance_timestamp: UTC timestamp of creation (ISO 8601).
    """

    namespace: str
    name: str
    creation_info: SBOMCreationInfo
    root_package: SBOMPackage
    packages: list[SBOMPackage] = field(default_factory=list)
    relationships: list[SBOMRelationship] = field(default_factory=list)
    extracted_licenses: list[SBOMExtractedLicense] = field(default_factory=list)
    comment: str | None = None
    provenance_tool: str | None = None
    provenance_timestamp: str | None = None

    def __post_init__(self) -> None:  # noqa: D105
        if not self.namespace:
            raise ValueError("SBOMDocument.namespace: must be non-empty")
        if not self.name:
            raise ValueError("SBOMDocument.name: must be non-empty")
        if len(self.name) > 255:
            raise ValueError(f"SBOMDocument.name: must be at most 255 characters, got {len(self.name)}")


@dataclass(frozen=True)
class WriterResult:
    """Result produced by an SBOM writer after writing a document.

    Attributes:
        output_path: Path to the written file.
        format: The output format used.
        sha256: SHA-256 hash of the written file (64-char hex).
        file_size: Size of the written file in bytes (non-negative).
        diagnostics: Validation diagnostics (max 1000 entries).
    """

    output_path: Path
    format: OutputFormat
    sha256: str
    file_size: int
    diagnostics: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:  # noqa: D105
        if len(self.sha256) != 64:
            raise ValueError(f"WriterResult.sha256: must be 64 characters, got {len(self.sha256)}")
        if not _HEX_PATTERN.match(self.sha256):
            raise ValueError("WriterResult.sha256: must be lowercase hexadecimal")
        if self.file_size < 0:
            raise ValueError(f"WriterResult.file_size: must be non-negative, got {self.file_size}")
        if len(self.diagnostics) > 1000:
            raise ValueError(f"WriterResult.diagnostics: must have at most 1000 entries, got {len(self.diagnostics)}")
