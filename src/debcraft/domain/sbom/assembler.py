"""Model Assembler - transforms scan results into an SBOMDocument.

The ModelAssembler is a pure domain service that converts ScanResult and
EnrichedPackage values into the format-independent SBOMDocument internal model.
It has no I/O dependencies except reading its own package version via
importlib.metadata.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

from debcraft.domain.sbom.values import (
    ChecksumAlgorithm,
    ExternalReferenceCategory,
    RelationshipType,
    SBOMChecksum,
    SBOMCreationInfo,
    SBOMDocument,
    SBOMExternalReference,
    SBOMPackage,
    SBOMRelationship,
)

if TYPE_CHECKING:
    from debcraft.domain.scanner.values import EnrichedPackage, ScanResult

# Pattern for characters allowed in SPDX IDs (alphanumeric, hyphen, dot)
_SPDX_SAFE_CHAR = re.compile(r"[^a-zA-Z0-9.\-]")


class ModelAssembler:
    """Transforms scan results into an SBOMDocument.

    The assembler maps enriched packages to SBOM packages, generates
    relationships (DESCRIBES and DEPENDS_ON), assigns unique SPDX IDs,
    and populates creation metadata.
    """

    def assemble(
        self,
        scan_result: ScanResult,
        enriched_packages: list[EnrichedPackage],
    ) -> SBOMDocument:
        """Transform scan results into an SBOMDocument.

        Args:
            scan_result: The scan result containing artifact path information.
            enriched_packages: The enriched packages to include as components.

        Returns:
            A fully constructed SBOMDocument with all packages, relationships,
            and metadata populated.
        """
        # Get debcraft version for creation info
        debcraft_version = self._get_debcraft_version()

        # Generate creation info (AC 2.8)
        creation_info = self._build_creation_info(debcraft_version)

        # Generate namespace (AC 2.10)
        namespace = self._generate_namespace(scan_result.artifact_path)

        # Build root package
        root_spdx_id = "SPDXRef-Package-root"
        root_package = SBOMPackage(
            spdx_id=root_spdx_id,
            name=scan_result.artifact_path,
        )

        # Handle zero-package case (AC 2.9)
        if not enriched_packages:
            return SBOMDocument(
                namespace=namespace,
                name=scan_result.artifact_path,
                creation_info=creation_info,
                root_package=root_package,
                packages=[],
                relationships=[],
                comment="No packages were identified during scanning.",
                provenance_tool=f"debcraft-{debcraft_version}",
                provenance_timestamp=creation_info.created,
            )

        # Build component packages with unique SPDX IDs (AC 2.1, 2.7)
        packages = self._build_packages(enriched_packages)

        # Build a name→spdx_id lookup for dependency resolution
        name_to_spdx_id: dict[str, str] = {}
        for pkg in packages:
            name_to_spdx_id[pkg.name] = pkg.spdx_id

        # Generate relationships
        relationships: list[SBOMRelationship] = []

        # DESCRIBES relationships from root to each component (AC 2.5)
        for pkg in packages:
            relationships.append(
                SBOMRelationship(
                    source_id=root_spdx_id,
                    target_id=pkg.spdx_id,
                    relationship_type=RelationshipType.DESCRIBES,
                )
            )

        # DEPENDS_ON relationships from dependency parsing (AC 2.6)
        for enriched_pkg, sbom_pkg in zip(enriched_packages, packages, strict=False):
            depends_rels = self._build_depends_on_relationships(enriched_pkg, sbom_pkg.spdx_id, name_to_spdx_id)
            relationships.extend(depends_rels)

        return SBOMDocument(
            namespace=namespace,
            name=scan_result.artifact_path,
            creation_info=creation_info,
            root_package=root_package,
            packages=packages,
            relationships=relationships,
            provenance_tool=f"debcraft-{debcraft_version}",
            provenance_timestamp=creation_info.created,
        )

    def _get_debcraft_version(self) -> str:
        """Read the debcraft package version from importlib.metadata."""
        try:
            return version("debcraft")
        except PackageNotFoundError:
            return "unknown"

    def _build_creation_info(self, debcraft_version: str) -> SBOMCreationInfo:
        """Build SBOMCreationInfo with debcraft tool info (AC 2.8)."""
        now = datetime.now(tz=UTC)
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        return SBOMCreationInfo(
            tools=[f"Tool: debcraft-{debcraft_version}"],
            created=timestamp,
            creators=["Tool: debcraft"],
        )

    def _generate_namespace(self, artifact_path: str) -> str:
        """Generate document namespace (AC 2.10).

        Format: https://debcraft.io/spdxdocs/<first-16-hex-of-sha256(artifact_path)>-<uuid4>
        """
        path_hash = hashlib.sha256(artifact_path.encode()).hexdigest()[:16]
        unique_id = str(uuid.uuid4())
        return f"https://debcraft.io/spdxdocs/{path_hash}-{unique_id}"

    def _sanitize_for_spdx_id(self, value: str) -> str:
        """Replace non-alphanumeric characters (except hyphen and dot) with hyphens."""
        return _SPDX_SAFE_CHAR.sub("-", value)

    def _build_packages(self, enriched_packages: list[EnrichedPackage]) -> list[SBOMPackage]:
        """Build SBOMPackage list with unique SPDX IDs (AC 2.1, 2.7).

        Handles duplicate sanitized identifiers by appending sequential suffixes.
        """
        # First pass: generate base SPDX IDs and count collisions
        base_ids: list[str] = []
        for ep in enriched_packages:
            sanitized_name = self._sanitize_for_spdx_id(ep.package.name)
            sanitized_version = self._sanitize_for_spdx_id(ep.package.version)
            base_id = f"SPDXRef-Package-{sanitized_name}-{sanitized_version}"
            base_ids.append(base_id)

        # Second pass: resolve collisions with sequential suffix
        id_counts: dict[str, int] = {}
        final_ids: list[str] = []
        for base_id in base_ids:
            if base_id not in id_counts:
                id_counts[base_id] = 1
                final_ids.append(base_id)
            else:
                id_counts[base_id] += 1
                final_ids.append(f"{base_id}-{id_counts[base_id]}")

        # If there were duplicates, the first occurrence keeps its base name
        # and subsequent ones get -2, -3, etc.
        # But we need to also handle the case where the first occurrence
        # should remain unsuffixed. The current logic already does that:
        # first occurrence → base_id, second → base_id-2, third → base_id-3

        # Build the packages
        packages: list[SBOMPackage] = []
        for ep, spdx_id in zip(enriched_packages, final_ids, strict=False):
            pkg = self._build_single_package(ep, spdx_id)
            packages.append(pkg)

        return packages

    def _build_single_package(self, enriched_pkg: EnrichedPackage, spdx_id: str) -> SBOMPackage:
        """Build a single SBOMPackage from an EnrichedPackage (AC 2.1-2.4)."""
        pkg = enriched_pkg.package
        enrichment = enriched_pkg.enrichment

        # AC 2.1: Map name, version, architecture → description
        description: str | None = None
        if pkg.architecture:
            description = f"Architecture: {pkg.architecture}"

        # Enrichment-derived fields
        package_url: str | None = None
        checksums: list[SBOMChecksum] = []
        concluded_license: str | None = None
        declared_license: str | None = None
        external_references: list[SBOMExternalReference] = []

        if enrichment is not None:
            # AC 2.2: Non-null purl → package_url + PACKAGE_MANAGER external ref
            if enrichment.purl is not None:
                package_url = enrichment.purl
                external_references.append(
                    SBOMExternalReference(
                        category=ExternalReferenceCategory.PACKAGE_MANAGER,
                        url=enrichment.purl,
                    )
                )

            # AC 2.3: Non-null sha256 → SBOMChecksum with SHA256
            if enrichment.sha256 is not None:
                checksums.append(
                    SBOMChecksum(
                        algorithm=ChecksumAlgorithm.SHA256,
                        value=enrichment.sha256,
                    )
                )

            # AC 2.4: license_expressions → concluded_license and declared_license
            if enrichment.license_expressions:
                first_expression = enrichment.license_expressions[0][0]
                concluded_license = first_expression
                declared_license = first_expression

        return SBOMPackage(
            spdx_id=spdx_id,
            name=pkg.name,
            version=pkg.version,
            description=description,
            package_url=package_url,
            checksums=checksums,
            concluded_license=concluded_license,
            declared_license=declared_license,
            external_references=external_references,
        )

    def _build_depends_on_relationships(
        self,
        enriched_pkg: EnrichedPackage,
        source_spdx_id: str,
        name_to_spdx_id: dict[str, str],
    ) -> list[SBOMRelationship]:
        """Parse depends string and generate DEPENDS_ON relationships (AC 2.6).

        Parses the depends field as comma-separated dependency specifications.
        Extracts the package name (portion before version constraint in parens)
        and generates DEPENDS_ON relationships for matching packages.
        """
        relationships: list[SBOMRelationship] = []

        if enriched_pkg.enrichment is None or enriched_pkg.enrichment.depends is None:
            return relationships

        depends_str = enriched_pkg.enrichment.depends
        # Split on comma, each entry may look like "libfoo (>= 1.0)" or "libbar"
        dep_specs = [dep.strip() for dep in depends_str.split(",")]

        for dep_spec in dep_specs:
            if not dep_spec:
                continue
            # Extract package name: everything before the first '(' or whitespace
            # that precedes a '('
            dep_name = dep_spec.split("(")[0].strip()
            # Also handle pipe alternatives like "libfoo | libbar"
            # Take just the first alternative
            dep_name = dep_name.split("|")[0].strip()

            if dep_name and dep_name in name_to_spdx_id:
                target_spdx_id = name_to_spdx_id[dep_name]
                relationships.append(
                    SBOMRelationship(
                        source_id=source_spdx_id,
                        target_id=target_spdx_id,
                        relationship_type=RelationshipType.DEPENDS_ON,
                    )
                )

        return relationships
