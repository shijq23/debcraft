"""Value objects for the artifact scanner domain layer.

Immutable dataclasses representing artifact descriptors, identified packages,
enrichment metadata, and scan results. These carry no behavior beyond field
access and are produced by scanner implementations and enrichment services.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ArtifactType(Enum):
    """Enumeration of supported artifact formats."""

    DIRECTORY = "directory"
    DOCKER = "docker"
    OCI = "oci"
    ISO = "iso"
    QCOW2 = "qcow2"
    IMG = "img"
    AMI = "ami"


class ScanningStrategy(Enum):
    """How packages were identified."""

    DPKG_METADATA = "dpkg_metadata"
    FILESYSTEM_ANALYSIS = "filesystem_analysis"


VALID_PACKAGE_STATUSES = frozenset(
    {
        "installed",
        "config-files",
        "half-installed",
        "unpacked",
        "half-configured",
        "triggers-awaited",
        "triggers-pending",
        "not-installed",
        "inferred",
    }
)


@dataclass(frozen=True)
class Artifact:
    """Describes a target to scan.

    Attributes:
        type: The artifact format type.
        path: Filesystem path to the artifact (max 4096 chars).
        options: Scanner-specific configuration (max 64 entries).
    """

    type: ArtifactType
    path: str
    options: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class IdentifiedPackage:
    """A single package found during scanning.

    Attributes:
        name: Package name (non-empty).
        version: Package version string (non-empty).
        architecture: Target architecture (non-empty, or "" if unknown).
        status: Installation status from VALID_PACKAGE_STATUSES.
    """

    name: str
    version: str
    architecture: str
    status: str


@dataclass(frozen=True)
class PackageEnrichment:
    """Metadata enrichment data for an identified package.

    Attributes:
        source_package: Source package name.
        maintainer: Package maintainer.
        homepage: Upstream homepage URL.
        depends: Runtime dependencies string.
        section: Archive section.
        priority: Package priority.
        description: Short package description.
        sha256: SHA256 of the .deb file.
        download_url: Fully-qualified download URL.
        purl: Package URL (PURL) string.
        license_expressions: List of (spdx_expression, source_algorithm) tuples.
        local_deb_path: Path to cached .deb file in local mirror, if available.
    """

    source_package: str | None = None
    maintainer: str | None = None
    homepage: str | None = None
    depends: str | None = None
    section: str | None = None
    priority: str | None = None
    description: str | None = None
    sha256: str | None = None
    download_url: str | None = None
    purl: str | None = None
    license_expressions: list[tuple[str, str]] = field(default_factory=list)
    local_deb_path: str | None = None


@dataclass(frozen=True)
class EnrichedPackage:
    """An identified package with optional enrichment metadata.

    Attributes:
        package: The base identified package.
        enrichment: Optional enrichment data from M3/M4/M5.
    """

    package: IdentifiedPackage
    enrichment: PackageEnrichment | None = None


@dataclass(frozen=True)
class ScanResult:
    """Uniform result produced by every scanner.

    Attributes:
        packages: Identified packages (zero or more).
        strategy: How packages were identified.
        diagnostics: Diagnostic/warning messages.
        duration_seconds: Scan duration (non-negative float).
        artifact_path: Path that was scanned.
        enriched_packages: Packages with enrichment (populated post-enrichment).
    """

    packages: list[IdentifiedPackage]
    strategy: str
    diagnostics: list[str]
    duration_seconds: float
    artifact_path: str
    enriched_packages: list[EnrichedPackage] = field(default_factory=list)
