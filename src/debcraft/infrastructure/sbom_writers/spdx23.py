"""SPDX 2.3 JSON writer implementation.

Serializes the internal SBOMDocument model into SPDX 2.3 JSON format,
validates against the official schema, and writes to disk with deterministic
output for reproducibility.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from debcraft.domain.sbom.errors import (
    DocumentValidationError,
    SchemaUnavailableError,
    WriterCancellationError,
)
from debcraft.domain.sbom.validator import SchemaValidator
from debcraft.domain.sbom.values import (
    OutputFormat,
    SBOMChecksum,
    SBOMCreationInfo,
    SBOMDocument,
    SBOMExternalReference,
    SBOMExtractedLicense,
    SBOMPackage,
    SBOMRelationship,
    WriterResult,
)
from debcraft.infrastructure.sbom_writers._write_utils import write_with_cancellation
from debcraft.infrastructure.sbom_writers.printer import SBOMPrinter

if TYPE_CHECKING:
    from pathlib import Path

    from debcraft.platform.contracts.workflow import WorkflowContext

#: SPDX 2.3 relationship types that have a direct vocabulary mapping.
_SPDX23_RELATIONSHIP_TYPES: set[str] = {
    "DESCRIBES",
    "CONTAINS",
    "DEPENDS_ON",
    "BUILD_TOOL_OF",
    "OTHER",
}


class SPDX23Writer:
    """Serializes SBOMDocument into SPDX 2.3 JSON format.

    Implements the SBOMWriter protocol. Produces deterministic output
    using SBOMPrinter and validates against the bundled SPDX 2.3 schema.
    """

    def __init__(self) -> None:
        """Initialize SPDX23Writer with printer and validator."""
        self._printer = SBOMPrinter()
        self._validator = SchemaValidator()

    async def write(self, document: SBOMDocument, output_path: Path, context: WorkflowContext) -> WriterResult:
        """Serialize an SBOM document to SPDX 2.3 JSON format.

        Args:
            document: The internal SBOM document to serialize.
            output_path: Filesystem path where the output file will be written.
            context: Workflow context providing cancellation, progress, logging.

        Returns:
            WriterResult with output path, format, SHA-256 hash, file size,
            and any validation diagnostics.

        Raises:
            DocumentValidationError: If document is None or has no root package.
            OutputPathError: If the output path is not writable.
            WriterCancellationError: If cancellation is requested during write.
        """
        # Validate document
        if document is None:
            raise DocumentValidationError("document is None")
        if not hasattr(document, "root_package") or document.root_package is None:
            raise DocumentValidationError("document has no root package")

        # Check cancellation before serialization
        if context.cancellation_token.is_cancelled:
            raise WriterCancellationError(output_path)

        # Serialize document to Python dict
        diagnostics: list[str] = []
        spdx_dict = self._serialize(document, diagnostics)

        # Check cancellation before printing
        if context.cancellation_token.is_cancelled:
            raise WriterCancellationError(output_path)

        # Format as deterministic JSON bytes
        output_bytes = self._printer.print(spdx_dict)

        # Validate against SPDX 2.3 schema
        json_string = output_bytes.decode("utf-8")
        try:
            validation_errors = self._validator.validate(json_string, OutputFormat.SPDX_2_3)
            diagnostics.extend(validation_errors)
        except SchemaUnavailableError:
            diagnostics.append("Schema validation skipped: SPDX 2.3 schema unavailable")

        # Write to disk with cancellation checks
        return await write_with_cancellation(
            output_bytes=output_bytes,
            output_path=output_path,
            cancellation_token=context.cancellation_token,
            output_format=OutputFormat.SPDX_2_3,
            diagnostics=diagnostics,
        )

    def _serialize(self, document: SBOMDocument, diagnostics: list[str]) -> dict[str, Any]:
        """Convert SBOMDocument to SPDX 2.3 JSON structure.

        Args:
            document: The SBOM document to serialize.
            diagnostics: List to append diagnostic messages to.

        Returns:
            Python dict representing the SPDX 2.3 JSON structure.
        """
        result: dict[str, Any] = {
            "spdxVersion": "SPDX-2.3",
            "dataLicense": "CC0-1.0",
            "SPDXID": "SPDXRef-DOCUMENT",
            "name": document.name,
            "documentNamespace": document.namespace,
            "creationInfo": self._serialize_creation_info(document.creation_info),
        }

        # Serialize packages (root + components)
        all_packages = [document.root_package, *list(document.packages)]
        result["packages"] = [self._serialize_package(pkg) for pkg in all_packages]

        # Serialize relationships
        result["relationships"] = [self._serialize_relationship(rel, diagnostics) for rel in document.relationships]

        # Serialize extracted licenses if present
        if document.extracted_licenses:
            result["hasExtractedLicensingInfos"] = [
                self._serialize_extracted_license(lic) for lic in document.extracted_licenses
            ]

        return result

    def _serialize_creation_info(self, info: SBOMCreationInfo) -> dict[str, Any]:
        """Serialize SBOMCreationInfo to SPDX 2.3 creationInfo object.

        Args:
            info: Creation info to serialize.

        Returns:
            Dict with created, creators, and optionally licenseListVersion.
        """
        creation_info: dict[str, Any] = {
            "created": info.created,
            "creators": list(info.creators),
        }
        if info.license_list_version is not None:
            creation_info["licenseListVersion"] = info.license_list_version
        return creation_info

    def _serialize_package(self, package: SBOMPackage) -> dict[str, Any]:
        """Serialize SBOMPackage to SPDX 2.3 package entry.

        Uses NOASSERTION sentinel for null string fields and omits
        empty array fields.

        Args:
            package: The package to serialize.

        Returns:
            Dict representing an SPDX 2.3 package entry.
        """
        pkg: dict[str, Any] = {
            "SPDXID": package.spdx_id,
            "name": package.name,
            "versionInfo": package.version if package.version is not None else "NOASSERTION",
            "downloadLocation": (package.download_location if package.download_location is not None else "NOASSERTION"),
            "supplier": package.supplier if package.supplier is not None else "NOASSERTION",
            "licenseConcluded": (package.concluded_license if package.concluded_license is not None else "NOASSERTION"),
            "licenseDeclared": (package.declared_license if package.declared_license is not None else "NOASSERTION"),
            "copyrightText": (package.copyright_text if package.copyright_text is not None else "NOASSERTION"),
        }

        # Checksums - omit if empty
        if package.checksums:
            pkg["checksums"] = [self._serialize_checksum(cs) for cs in package.checksums]

        # External references - include PURL and other references
        external_refs = self._build_external_refs(package)
        if external_refs:
            pkg["externalRefs"] = external_refs

        return pkg

    def _serialize_checksum(self, checksum: SBOMChecksum) -> dict[str, str]:
        """Serialize SBOMChecksum to SPDX 2.3 checksum object.

        Args:
            checksum: The checksum to serialize.

        Returns:
            Dict with algorithm and checksumValue.
        """
        return {
            "algorithm": checksum.algorithm.value,
            "checksumValue": checksum.value,
        }

    def _build_external_refs(self, package: SBOMPackage) -> list[dict[str, str]]:
        """Build externalRefs array from package PURL and external references.

        Args:
            package: The package whose external refs to build.

        Returns:
            List of externalRefs dicts. Empty list if no refs.
        """
        refs: list[dict[str, str]] = []

        # Add PURL as an external reference
        if package.package_url is not None:
            refs.append(
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": package.package_url,
                }
            )

        # Add other external references
        for ext_ref in package.external_references:
            ref_entry = self._serialize_external_reference(ext_ref)
            refs.append(ref_entry)

        return refs

    def _serialize_external_reference(self, ref: SBOMExternalReference) -> dict[str, str]:
        """Serialize SBOMExternalReference to SPDX 2.3 externalRefs entry.

        Args:
            ref: The external reference to serialize.

        Returns:
            Dict with referenceCategory, referenceType, and referenceLocator.
        """
        result: dict[str, str] = {
            "referenceCategory": ref.category.value.replace("_", "-"),
            "referenceType": "url",
            "referenceLocator": ref.url,
        }
        if ref.comment is not None:
            result["comment"] = ref.comment
        return result

    def _serialize_relationship(self, rel: SBOMRelationship, diagnostics: list[str]) -> dict[str, str]:
        """Serialize SBOMRelationship to SPDX 2.3 relationship entry.

        Falls back to OTHER for unmapped relationship types.

        Args:
            rel: The relationship to serialize.
            diagnostics: List to append diagnostic messages for unmapped types.

        Returns:
            Dict with spdxElementId, relatedSpdxElement, and relationshipType.
        """
        type_value = rel.relationship_type.value
        if type_value not in _SPDX23_RELATIONSHIP_TYPES:
            diagnostics.append(f"Unmapped relationship type '{type_value}' mapped to OTHER")
            type_value = "OTHER"

        return {
            "spdxElementId": rel.source_id,
            "relatedSpdxElement": rel.target_id,
            "relationshipType": type_value,
        }

    def _serialize_extracted_license(self, lic: SBOMExtractedLicense) -> dict[str, Any]:
        """Serialize SBOMExtractedLicense to hasExtractedLicensingInfos entry.

        Args:
            lic: The extracted license to serialize.

        Returns:
            Dict with licenseId, extractedText, and optionally name and seeAlsos.
        """
        entry: dict[str, Any] = {
            "licenseId": lic.license_id,
            "extractedText": lic.extracted_text,
        }
        if lic.name is not None:
            entry["name"] = lic.name
        if lic.cross_references:
            entry["seeAlsos"] = list(lic.cross_references)
        return entry
