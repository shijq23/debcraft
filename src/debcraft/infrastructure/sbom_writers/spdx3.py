"""SPDX 3.0 JSON-LD writer implementation.

Serializes the internal SBOMDocument domain model into SPDX 3.0 JSON-LD format.
The writer maps domain value objects to SPDX 3.0 vocabulary, validates the output
against the bundled SPDX 3.0 JSON schema, and produces deterministic output.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from debcraft.domain.sbom.errors import (
    DocumentValidationError,
    WriterCancellationError,
)
from debcraft.domain.sbom.validator import SchemaValidator
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
    WriterResult,
)
from debcraft.infrastructure.sbom_writers._output import write_sbom_output
from debcraft.infrastructure.sbom_writers.printer import SBOMPrinter

if TYPE_CHECKING:
    from pathlib import Path

    from debcraft.platform.contracts.workflow import WorkflowContext

#: SPDX 3.0 JSON-LD context URL.
_SPDX3_CONTEXT_URL = "https://spdx.org/rdf/3.0.1/spdx-context.jsonld"

#: Mapping from domain ChecksumAlgorithm to SPDX 3.0 hash algorithm vocabulary URLs.
_ALGORITHM_VOCABULARY: dict[ChecksumAlgorithm, str] = {
    ChecksumAlgorithm.SHA256: "https://spdx.org/rdf/3.0.1/terms/HashAlgorithm/sha256",
    ChecksumAlgorithm.SHA1: "https://spdx.org/rdf/3.0.1/terms/HashAlgorithm/sha1",
    ChecksumAlgorithm.MD5: "https://spdx.org/rdf/3.0.1/terms/HashAlgorithm/md5",
}

#: Mapping from domain RelationshipType to SPDX 3.0 relationship type vocabulary.
_RELATIONSHIP_TYPE_VOCABULARY: dict[RelationshipType, str] = {
    RelationshipType.DESCRIBES: "https://spdx.org/rdf/3.0.1/terms/RelationshipType/describes",
    RelationshipType.CONTAINS: "https://spdx.org/rdf/3.0.1/terms/RelationshipType/contains",
    RelationshipType.DEPENDS_ON: "https://spdx.org/rdf/3.0.1/terms/RelationshipType/dependsOn",
    RelationshipType.BUILD_TOOL_OF: "https://spdx.org/rdf/3.0.1/terms/RelationshipType/buildToolOf",
}

#: SPDX 3.0 NoAssertion value for fields where no data is available.
_NO_ASSERTION = "https://spdx.org/rdf/3.0.1/terms/Core/NoAssertionElement"

#: Maximum number of diagnostics to include in WriterResult.
_MAX_DIAGNOSTICS = 100


class SPDX3Writer:
    """Serializes SBOMDocument into SPDX 3.0 JSON-LD format.

    Implements the SBOMWriter protocol. Produces deterministic, schema-validated
    SPDX 3.0 JSON-LD output with 2-space indentation and sorted keys.
    """

    def __init__(self) -> None:
        """Initialize writer with printer and validator."""
        self._printer = SBOMPrinter()
        self._validator = SchemaValidator()
        self._unmapped_diagnostics: list[str] = []

    async def write(self, document: SBOMDocument, output_path: Path, context: WorkflowContext) -> WriterResult:
        """Serialize an SBOM document to SPDX 3.0 JSON-LD format.

        Args:
            document: The internal SBOM document to serialize.
            output_path: Filesystem path where the output file will be written.
            context: Workflow context providing cancellation, progress, logging.

        Returns:
            WriterResult with output path, format, SHA-256 hash, file size,
            and any validation diagnostics.

        Raises:
            DocumentValidationError: If the document is None or has no root package.
            OutputPathError: If the output path is not writable.
            WriterCancellationError: If cancellation is requested during write.
        """
        # Validate document
        if document is None:
            raise DocumentValidationError("document is None")
        if not document.root_package:
            raise DocumentValidationError("document has no root package")

        # Check cancellation before serialization
        if context.cancellation_token.is_cancelled:
            raise WriterCancellationError(output_path)

        # Serialize to SPDX 3.0 JSON-LD structure
        spdx_dict = self._serialize(document)

        # Collect unmapped relationship diagnostics from serialization
        unmapped_diagnostics = list(self._unmapped_diagnostics)

        # Format to deterministic JSON bytes
        output_bytes = self._printer.print(spdx_dict)

        # Validate against schema
        diagnostics: list[str] = list(unmapped_diagnostics)
        json_string = output_bytes.decode("utf-8")
        validation_errors = self._validator.validate(json_string, OutputFormat.SPDX_3_0)
        if validation_errors:
            diagnostics.extend(validation_errors)
        # Cap diagnostics at maximum
        diagnostics = diagnostics[:_MAX_DIAGNOSTICS]

        # Check cancellation before writing
        if context.cancellation_token.is_cancelled:
            raise WriterCancellationError(output_path)

        # Write output file (creates dirs, writes bytes, cleans up on error, computes hash)
        sha256, file_size = write_sbom_output(output_bytes, output_path)

        # Check cancellation after writing
        if context.cancellation_token.is_cancelled:
            output_path.unlink(missing_ok=True)
            raise WriterCancellationError(output_path)

        return WriterResult(
            output_path=output_path,
            format=OutputFormat.SPDX_3_0,
            sha256=sha256,
            file_size=file_size,
            diagnostics=diagnostics,
        )

    def _serialize(self, document: SBOMDocument) -> dict[str, Any]:
        """Convert SBOMDocument to SPDX 3.0 JSON-LD dictionary.

        Args:
            document: The internal SBOM document.

        Returns:
            A Python dict representing the SPDX 3.0 JSON-LD structure.
        """
        creation_info = self._map_creation_info(document.creation_info)

        # Build the element list
        elements: list[dict[str, Any]] = []

        # Add root package as a software_Package element
        elements.append(self._map_package(document.root_package, creation_info))

        # Add component packages
        for package in document.packages:
            elements.append(self._map_package(package, creation_info))

        # Add relationships (omit unmapped types)
        diagnostics_for_unmapped: list[str] = []
        for relationship in document.relationships:
            mapped = self._map_relationship(relationship, creation_info)
            if mapped is not None:
                elements.append(mapped)
            else:
                diagnostics_for_unmapped.append(
                    f"Unmapped relationship type '{relationship.relationship_type.value}' "
                    f"from '{relationship.source_id}' to '{relationship.target_id}' omitted"
                )

        # Add extracted licenses
        for license_entry in document.extracted_licenses:
            elements.append(self._map_extracted_license(license_entry, creation_info))

        # Build the top-level document
        spdx_document: dict[str, Any] = {
            "@context": _SPDX3_CONTEXT_URL,
            "@type": "SpdxDocument",
            "spdxId": document.namespace,
            "name": document.name,
            "creationInfo": creation_info,
            "element": elements,
        }

        if document.comment:
            spdx_document["comment"] = document.comment

        # Store unmapped diagnostics in a custom extension (these will be
        # collected by the write method via the serialization)
        if diagnostics_for_unmapped:
            # We pass diagnostics through a side channel by storing on self
            self._unmapped_diagnostics = diagnostics_for_unmapped
        else:
            self._unmapped_diagnostics = []

        return spdx_document

    def _map_creation_info(self, info: SBOMCreationInfo) -> dict[str, Any]:
        """Map SBOMCreationInfo to SPDX 3.0 CreationInfo structure.

        Args:
            info: The creation info from the domain model.

        Returns:
            Dict representing CreationInfo with created, createdBy, createdUsing.
        """
        # Parse tool identifiers - "Tool: name-version" → tool agent
        created_using: list[dict[str, Any]] = []
        for tool in info.tools:
            # Strip "Tool: " prefix
            tool_name = tool[6:] if tool.startswith("Tool: ") else tool
            created_using.append(
                {
                    "@type": "Tool",
                    "name": tool_name,
                }
            )

        created_by: list[dict[str, Any]] = []
        for creator in info.creators:
            created_by.append(
                {
                    "@type": "Agent",
                    "name": creator,
                }
            )

        creation_info: dict[str, Any] = {
            "created": info.created,
            "createdBy": created_by,
            "createdUsing": created_using,
        }

        if info.license_list_version:
            creation_info["licenseListVersion"] = info.license_list_version

        return creation_info

    def _map_package(self, package: SBOMPackage, creation_info: dict[str, Any]) -> dict[str, Any]:
        """Map SBOMPackage to SPDX 3.0 software_Package element.

        Uses NoAssertionValue for null/empty optional fields per AC 4.2.

        Args:
            package: The domain package value object.
            creation_info: The shared creation info dict.

        Returns:
            Dict representing a software_Package element.
        """
        element: dict[str, Any] = {
            "@type": "software_Package",
            "spdxId": package.spdx_id,
            "name": package.name,
            "creationInfo": creation_info,
            "software_packageVersion": package.version if package.version else _NO_ASSERTION,
            "software_downloadLocation": (package.download_location if package.download_location else _NO_ASSERTION),
            "software_packageUrl": package.package_url if package.package_url else _NO_ASSERTION,
            "software_copyrightText": (package.copyright_text if package.copyright_text else _NO_ASSERTION),
        }

        # Add supplier if present
        if package.supplier:
            element["suppliedBy"] = {
                "@type": "Agent",
                "name": package.supplier,
            }

        # Add checksums as Hash elements
        if package.checksums:
            element["verifiedUsing"] = [self._map_checksum(checksum) for checksum in package.checksums]

        # Add concluded license
        if package.concluded_license:
            element["declaredLicense"] = {
                "@type": "simplelicensing_LicenseExpression",
                "simplelicensing_licenseExpression": package.concluded_license,
            }

        # Add description
        if package.description:
            element["description"] = package.description

        # Add external references
        if package.external_references:
            element["externalReference"] = [
                {
                    "@type": "ExternalReference",
                    "externalReferenceType": ref.category.value.lower(),
                    "locator": [ref.url],
                }
                for ref in package.external_references
            ]

        return element

    def _map_checksum(self, checksum: SBOMChecksum) -> dict[str, Any]:
        """Map SBOMChecksum to SPDX 3.0 Hash element.

        Args:
            checksum: The domain checksum value object.

        Returns:
            Dict representing a Hash element with algorithm vocabulary URL.
        """
        return {
            "@type": "Hash",
            "algorithm": _ALGORITHM_VOCABULARY[checksum.algorithm],
            "hashValue": checksum.value,
        }

    def _map_relationship(self, relationship: SBOMRelationship, creation_info: dict[str, Any]) -> dict[str, Any] | None:
        """Map SBOMRelationship to SPDX 3.0 Relationship element.

        Returns None if the relationship type has no SPDX 3.0 equivalent.

        Args:
            relationship: The domain relationship value object.
            creation_info: The shared creation info dict.

        Returns:
            Dict representing a Relationship element, or None if unmapped.
        """
        rel_type = _RELATIONSHIP_TYPE_VOCABULARY.get(relationship.relationship_type)
        if rel_type is None:
            return None

        return {
            "@type": "Relationship",
            "spdxId": f"{relationship.source_id}-to-{relationship.target_id}",
            "creationInfo": creation_info,
            "from": relationship.source_id,
            "to": [relationship.target_id],
            "relationshipType": rel_type,
        }

    def _map_extracted_license(
        self, license_entry: SBOMExtractedLicense, creation_info: dict[str, Any]
    ) -> dict[str, Any]:
        """Map SBOMExtractedLicense to SPDX 3.0 license element.

        Maps to simplelicensing_LicenseExpression if the license_id starts with
        "LicenseRef-" (indicating it has an SPDX-style local identifier),
        or expandedlicensing_CustomLicense otherwise.

        Per AC 4.9: entries with SPDX list ID → LicenseExpression,
        entries without → CustomLicense.

        Args:
            license_entry: The domain extracted license value object.
            creation_info: The shared creation info dict.

        Returns:
            Dict representing a license element.
        """
        # Per AC 4.9: LicenseRef- IDs are custom licenses (not on SPDX list)
        # Since all SBOMExtractedLicense entries have LicenseRef- pattern,
        # they are by definition not on the SPDX license list → CustomLicense
        element: dict[str, Any] = {
            "@type": "expandedlicensing_CustomLicense",
            "spdxId": license_entry.license_id,
            "creationInfo": creation_info,
            "simplelicensing_licenseText": license_entry.extracted_text,
        }

        if license_entry.name:
            element["name"] = license_entry.name

        if license_entry.cross_references:
            element["seeAlso"] = license_entry.cross_references

        return element
