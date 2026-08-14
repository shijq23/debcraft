"""CycloneDX 1.5 JSON writer for SBOM documents.

Serializes an internal SBOMDocument into CycloneDX 1.5 JSON format,
validates against the bundled schema, and writes deterministic output.
"""

from __future__ import annotations

import hashlib
import uuid
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
    SBOMPackage,
    WriterResult,
)
from debcraft.infrastructure.sbom_writers._write_utils import write_with_cancellation
from debcraft.infrastructure.sbom_writers.printer import SBOMPrinter

if TYPE_CHECKING:
    from pathlib import Path

    from debcraft.platform.contracts.workflow import WorkflowContext

#: Fixed namespace UUID for generating deterministic serial numbers via UUID v5.
#: Uses the DNS namespace as specified in RFC 4122.
_UUID5_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

#: Mapping from ChecksumAlgorithm to CycloneDX hash algorithm strings.
_ALGORITHM_MAP: dict[ChecksumAlgorithm, str] = {
    ChecksumAlgorithm.SHA256: "SHA-256",
    ChecksumAlgorithm.SHA1: "SHA-1",
    ChecksumAlgorithm.MD5: "MD5",
}


class CycloneDXWriter:
    """Serializes SBOMDocument into CycloneDX 1.5 JSON format.

    Produces deterministic output with sorted keys, 2-space indentation,
    and UTF-8 encoding without BOM. Validates output against the
    CycloneDX 1.5 JSON schema.
    """

    def __init__(self) -> None:
        """Initialize writer with printer and validator."""
        self._printer = SBOMPrinter()
        self._validator = SchemaValidator()

    async def write(
        self,
        document: SBOMDocument,
        output_path: Path,
        context: WorkflowContext,
    ) -> WriterResult:
        """Serialize an SBOM document to CycloneDX 1.5 JSON.

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
        if document.root_package is None:
            raise DocumentValidationError("document has no root package")

        # Check cancellation before starting
        if context.cancellation_token.is_cancelled:
            raise WriterCancellationError(output_path)

        # Serialize to dict
        data = self._serialize(document)

        # Format to deterministic JSON bytes
        output_bytes = self._printer.print(data)

        # Validate against schema
        json_string = output_bytes.decode("utf-8")
        diagnostics = self._validator.validate(json_string, OutputFormat.CYCLONEDX)

        # Write to disk with cancellation checks
        return await write_with_cancellation(
            output_bytes=output_bytes,
            output_path=output_path,
            cancellation_token=context.cancellation_token,
            output_format=OutputFormat.CYCLONEDX,
            diagnostics=diagnostics,
        )

    def _serialize(self, document: SBOMDocument) -> dict[str, Any]:
        """Convert an SBOMDocument to a CycloneDX 1.5 JSON-compatible dict.

        Args:
            document: The SBOM document to serialize.

        Returns:
            A dictionary representing the CycloneDX 1.5 JSON structure.
        """
        result: dict[str, Any] = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "version": 1,
            "serialNumber": self._generate_serial_number(document.namespace),
        }

        # Metadata
        result["metadata"] = self._build_metadata(document)

        # Components
        components = [self._build_component(pkg) for pkg in document.packages]
        result["components"] = components

        # Dependencies
        result["dependencies"] = self._build_dependencies(document)

        return result

    def _generate_serial_number(self, namespace: str) -> str:
        """Generate a deterministic URN UUID serial number from namespace.

        Uses UUID v5 with the DNS namespace and the document namespace string
        to produce a reproducible serial number.

        Args:
            namespace: The document namespace string.

        Returns:
            A URN-formatted UUID string (e.g., "urn:uuid:...").
        """
        generated = uuid.uuid5(_UUID5_NAMESPACE, namespace)
        return f"urn:uuid:{generated}"

    def _generate_bom_ref(self, package: SBOMPackage) -> str:
        """Generate a deterministic bom-ref from name, version, and purl.

        Creates a stable identifier by hashing the concatenation of
        name, version, and purl fields.

        Args:
            package: The SBOM package to generate a bom-ref for.

        Returns:
            A deterministic bom-ref string.
        """
        parts = [
            package.name,
            package.version or "",
            package.package_url or "",
        ]
        key = "|".join(parts)
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:36]

    def _build_metadata(self, document: SBOMDocument) -> dict[str, Any]:
        """Build the metadata object from creation info.

        Args:
            document: The SBOM document containing creation info.

        Returns:
            A dictionary representing the CycloneDX metadata structure.
        """
        metadata: dict[str, Any] = {}

        creation_info: SBOMCreationInfo = document.creation_info

        # Timestamp
        metadata["timestamp"] = creation_info.created

        # Tools
        tools = self._build_tools(creation_info)
        if tools:
            metadata["tools"] = tools

        # Component (root package as subject of BOM)
        metadata["component"] = self._build_component(document.root_package)

        return metadata

    def _build_tools(self, creation_info: SBOMCreationInfo) -> list[dict[str, str]]:
        """Parse tool identifiers into CycloneDX tool objects.

        Tool identifiers are in "Tool: name-version" format. The name and
        version are parsed by splitting on the last hyphen after "Tool: ".

        Args:
            creation_info: The creation info containing tool identifiers.

        Returns:
            A list of tool objects with name and version fields.
        """
        tools: list[dict[str, str]] = []
        for tool_str in creation_info.tools:
            # Remove "Tool: " prefix
            name_version = tool_str[6:] if tool_str.startswith("Tool: ") else tool_str

            # Split on last hyphen to separate name from version
            last_hyphen = name_version.rfind("-")
            if last_hyphen > 0:
                name = name_version[:last_hyphen]
                version = name_version[last_hyphen + 1 :]
                tools.append({"name": name, "version": version})
            else:
                # No hyphen found; use entire string as name, no version
                tools.append({"name": name_version, "version": ""})

        return tools

    def _build_component(self, package: SBOMPackage) -> dict[str, Any]:
        """Build a CycloneDX component entry from an SBOMPackage.

        Optional fields that are None are omitted from the output.

        Args:
            package: The SBOM package to convert.

        Returns:
            A dictionary representing a CycloneDX component.
        """
        component: dict[str, Any] = {
            "type": "library",
            "name": package.name,
            "bom-ref": self._generate_bom_ref(package),
        }

        if package.version is not None:
            component["version"] = package.version

        if package.package_url is not None:
            component["purl"] = package.package_url

        if package.checksums:
            component["hashes"] = self._build_hashes(package.checksums)

        if package.concluded_license is not None:
            component["licenses"] = [{"expression": package.concluded_license}]

        if package.copyright_text is not None:
            component["copyright"] = package.copyright_text

        return component

    def _build_hashes(self, checksums: list[SBOMChecksum]) -> list[dict[str, str]]:
        """Convert SBOM checksums to CycloneDX hash entries.

        Args:
            checksums: List of SBOM checksum value objects.

        Returns:
            A list of hash objects with alg and content fields.
        """
        hashes: list[dict[str, str]] = []
        for checksum in checksums:
            alg = _ALGORITHM_MAP.get(checksum.algorithm)
            if alg is not None:
                hashes.append(
                    {
                        "alg": alg,
                        "content": checksum.value,
                    }
                )
        return hashes

    def _build_dependencies(self, document: SBOMDocument) -> list[dict[str, Any]]:
        """Build the dependencies array from DEPENDS_ON relationships.

        Maps DEPENDS_ON relationships to CycloneDX dependency objects using
        bom-ref values for component references.

        Args:
            document: The SBOM document containing relationships and packages.

        Returns:
            A list of dependency objects with ref and dependsOn fields.
        """
        # Build a mapping from spdx_id → bom-ref for all packages
        id_to_bom_ref: dict[str, str] = {}
        for pkg in document.packages:
            id_to_bom_ref[pkg.spdx_id] = self._generate_bom_ref(pkg)

        # Also include root package
        id_to_bom_ref[document.root_package.spdx_id] = self._generate_bom_ref(document.root_package)

        # Collect DEPENDS_ON relationships grouped by source
        deps_by_source: dict[str, list[str]] = {}
        for rel in document.relationships:
            if rel.relationship_type == RelationshipType.DEPENDS_ON:
                source_ref = id_to_bom_ref.get(rel.source_id)
                target_ref = id_to_bom_ref.get(rel.target_id)
                if source_ref is not None and target_ref is not None:
                    if source_ref not in deps_by_source:
                        deps_by_source[source_ref] = []
                    deps_by_source[source_ref].append(target_ref)

        # Build dependency array
        dependencies: list[dict[str, Any]] = []
        for ref, depends_on in sorted(deps_by_source.items()):
            dependencies.append(
                {
                    "ref": ref,
                    "dependsOn": sorted(depends_on),
                }
            )

        return dependencies
