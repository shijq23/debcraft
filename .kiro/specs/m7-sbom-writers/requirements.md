# Requirements Document

## Introduction

SBOM Writers (Milestone 7) implements the SBOM generation subsystem of DebCraft that serializes the internal domain model into standard Software Bill of Materials output formats. The subsystem defines a format-independent internal SBOM model (based on SPDX 3.0 concepts but not dependent on SPDX per AC-04), then provides writer implementations that convert this internal model into SPDX 3.0 JSON, SPDX 2.3 JSON, and CycloneDX JSON. Each writer validates its output against the respective specification schema. Writers are stateless plugins discovered via the standard entry point mechanism, and the SBOM workflow orchestrates scanning, enrichment, and writing into a complete pipeline.

## Glossary

- **SBOM_Model**: The format-independent internal domain model representing a Software Bill of Materials document. Based on SPDX 3.0 concepts but decoupled from any specific output format per AC-04.
- **SBOM_Document**: An internal value object representing a complete SBOM, containing document metadata, the creating tool identity, the root package element, component packages, relationships between elements, extracted licensing information, and provenance data.
- **SBOM_Element**: A base value object representing any identifiable entity in the SBOM (packages, files, relationships). Each element carries a unique identifier (SPDX ID format) and optional annotations.
- **SBOM_Package**: A value object representing a software package within the SBOM, containing name, version, supplier, download location, checksums, package URL (PURL), concluded license, declared license, copyright text, and external references.
- **SBOM_Relationship**: A value object representing a typed directional relationship between two SBOM elements (e.g., CONTAINS, DEPENDS_ON, DESCRIBED_BY).
- **SBOM_ExtractedLicense**: A value object representing a license not on the SPDX license list, containing a local identifier, extracted text, and cross-reference URLs.
- **SBOM_CreationInfo**: A value object containing document creation metadata: tool name and version, creation timestamp, creator identity, and document namespace.
- **SBOM_Checksum**: A value object holding an algorithm identifier and hash value for content verification.
- **SBOM_ExternalReference**: A value object representing a reference to an external resource (URL, category, and optional comment).
- **SBOM_Writer**: The protocol interface that all writer implementations conform to, defining the `write(document, output_path, context) -> WriterResult` method signature.
- **Writer_Result**: A value object produced by every writer containing the output file path, the output format identifier, the SHA-256 hash of the written file, the file size in bytes, and validation diagnostics.
- **SPDX3_Writer**: The writer implementation that serializes the SBOM_Model into SPDX 3.0 JSON-LD format.
- **SPDX23_Writer**: The writer implementation that serializes the SBOM_Model into SPDX 2.3 JSON format.
- **CycloneDX_Writer**: The writer implementation that serializes the SBOM_Model into CycloneDX 1.5 JSON format.
- **Schema_Validator**: The domain service that validates serialized SBOM output against the respective specification JSON schema.
- **Writer_Registry**: The plugin registry that discovers and manages available writer implementations via importlib.metadata entry points.
- **SBOM_Workflow**: The built-in workflow that orchestrates scanning, metadata enrichment, license detection, internal model assembly, and SBOM writing into a complete pipeline.
- **Model_Assembler**: The domain service that transforms a ScanResult with EnrichedPackage values into an SBOM_Document internal model instance.
- **SBOM_Serializer**: The component within each writer responsible for converting the internal SBOM_Document value object into the format-specific JSON structure. Each writer contains exactly one serializer.
- **SBOM_Printer**: The component that formats the final JSON output with consistent indentation and encoding for human readability and deterministic output.
- **WorkflowContext**: The platform context object providing scoped dependency injection, cooperative cancellation, progress reporting, resource management, logging, and event publishing.
- **Enriched_Package**: A value object combining an IdentifiedPackage with optional PackageEnrichment metadata from M3/M4/M5.
- **Output_Format**: An enumeration of supported SBOM output formats: SPDX_3_0, SPDX_2_3, CYCLONEDX.

## Requirements

### Requirement 1: Internal SBOM Domain Model

**User Story:** As a platform developer, I want a format-independent internal SBOM model, so that the domain layer remains decoupled from any specific SBOM specification and writers can convert to multiple formats from a single source of truth.

#### Acceptance Criteria

1. THE SBOM_Model SHALL be defined as a set of frozen dataclass value objects in the domain layer (`debcraft.domain.sbom`) with no imports from infrastructure, SPDX libraries, or CycloneDX libraries.
2. THE SBOM_Document SHALL contain: a document namespace (non-empty string), creation information (SBOM_CreationInfo), a name (non-empty string, maximum 255 characters), a root package element (SBOM_Package), a list of component packages (zero or more SBOM_Package entries), a list of relationships (zero or more SBOM_Relationship entries), a list of extracted licenses (zero or more SBOM_ExtractedLicense entries), a document comment (optional string), and a provenance field recording the tool version string that created the model and the UTC timestamp of creation as an ISO 8601 datetime string.
3. THE SBOM_Package SHALL contain: a unique SPDX identifier string conforming to the pattern `SPDXRef-[a-zA-Z0-9.-]+` (unique within the containing SBOM_Document), a name (non-empty string), a version (string, optional), a supplier (string, optional), a download location (string, optional), a list of checksums (zero or more SBOM_Checksum entries), a package URL string (optional), a concluded license expression (optional string in SPDX expression syntax), a declared license expression (optional string in SPDX expression syntax), a copyright text (optional string), a description (optional string), and a list of external references (zero or more SBOM_ExternalReference entries).
4. THE SBOM_Relationship SHALL contain: a source element SPDX identifier (non-empty string conforming to `SPDXRef-[a-zA-Z0-9.-]+`), a target element SPDX identifier (non-empty string conforming to `SPDXRef-[a-zA-Z0-9.-]+`), and a relationship type selected from a defined enumeration including at minimum DESCRIBES, CONTAINS, DEPENDS_ON, BUILD_TOOL_OF, and OTHER.
5. THE SBOM_CreationInfo SHALL contain: a list of tool identifiers (each a non-empty string in "Tool: name-version" format, at least one entry), a creation timestamp (ISO 8601 UTC datetime string), a list of creator identifiers (each a non-empty string), and a license list version (optional string).
6. THE SBOM_Checksum SHALL contain: an algorithm identifier selected from a defined enumeration including at minimum SHA256, SHA1, and MD5, and a hash value (non-empty lowercase hexadecimal string whose length corresponds to the algorithm: 64 characters for SHA256, 40 characters for SHA1, 32 characters for MD5).
7. THE SBOM_ExternalReference SHALL contain: a category selected from a defined enumeration including at minimum PACKAGE_MANAGER, SECURITY_ADVISORY, and OTHER, a URL (non-empty string), and a comment (optional string).
8. THE SBOM_ExtractedLicense SHALL contain: a license identifier (non-empty string matching the pattern `LicenseRef-[a-zA-Z0-9.-]+`), extracted text (non-empty string), a name (optional string), and a list of cross-reference URLs (zero or more strings).
9. IF a frozen dataclass value object is constructed with a field value that violates a stated constraint (empty string for a required non-empty field, SPDX identifier not matching the required pattern, or hash value length not matching the specified algorithm length), THEN THE SBOM_Model SHALL raise a ValueError at construction time indicating which field failed validation and the constraint that was violated.

### Requirement 2: Model Assembler

**User Story:** As a workflow developer, I want a service that transforms scan results into the internal SBOM model, so that the writing step receives a well-formed domain object regardless of how packages were discovered.

#### Acceptance Criteria

1. WHEN a ScanResult containing one or more Enriched_Package entries is provided, THE Model_Assembler SHALL produce an SBOM_Document with one SBOM_Package per Enriched_Package, populating the SBOM_Package name from IdentifiedPackage.name, version from IdentifiedPackage.version, and description from IdentifiedPackage.architecture (prefixed with "Architecture: ").
2. WHEN an Enriched_Package has PackageEnrichment with a non-null purl field, THE Model_Assembler SHALL set the SBOM_Package package_url to the enrichment purl value and SHALL add a PACKAGE_MANAGER external reference with the purl as URL.
3. WHEN an Enriched_Package has PackageEnrichment with a non-null sha256 field, THE Model_Assembler SHALL add an SBOM_Checksum with algorithm SHA256 and the enrichment sha256 value to the SBOM_Package checksums list.
4. WHEN an Enriched_Package has PackageEnrichment with one or more license_expressions entries, THE Model_Assembler SHALL set the SBOM_Package concluded_license to the SPDX expression string (first tuple element) of the first license_expressions entry and SHALL set declared_license to the same value.
5. THE Model_Assembler SHALL generate a DESCRIBES relationship from the root document package to each component SBOM_Package.
6. WHEN an Enriched_Package has PackageEnrichment with a non-null depends field, THE Model_Assembler SHALL parse the depends string as a comma-separated list of dependency specifications, extract each dependency package name (the portion before any version constraint in parentheses), and generate a DEPENDS_ON relationship from that SBOM_Package to each dependency whose extracted name matches an SBOM_Package name within the same SBOM_Document.
7. THE Model_Assembler SHALL generate unique SPDX identifiers for each SBOM_Package in the format `SPDXRef-Package-<sanitized_name>-<sanitized_version>` where sanitized values replace non-alphanumeric characters (except hyphen and dot) with hyphens. IF two or more Enriched_Packages produce the same sanitized identifier, THEN THE Model_Assembler SHALL append a hyphen and a sequential integer suffix (starting at 2) to each duplicate to ensure uniqueness.
8. THE Model_Assembler SHALL populate SBOM_CreationInfo with tool identifier "Tool: debcraft-<version>" where version is read from the debcraft package metadata, the current UTC timestamp in ISO 8601 format, and creator "Tool: debcraft".
9. IF a ScanResult contains zero Enriched_Package entries, THEN THE Model_Assembler SHALL produce an SBOM_Document with an empty component packages list and a document comment noting that no packages were identified.
10. THE Model_Assembler SHALL generate a document namespace in the format `https://debcraft.io/spdxdocs/<artifact_path_hash>-<uuid>` where artifact_path_hash is the first 16 hexadecimal characters of the SHA-256 hash of the ScanResult artifact_path string and uuid is a randomly generated UUID4, ensuring global uniqueness.

### Requirement 3: SBOM Writer Protocol Interface

**User Story:** As a platform developer, I want a common writer protocol interface, so that all SBOM writers are interchangeable and new output formats can be added without modifying existing code.

#### Acceptance Criteria

1. THE SBOM_Writer SHALL define an async method with signature `write(self, document: SBOM_Document, output_path: Path, context: WorkflowContext) -> Writer_Result` as its sole public contract.
2. THE SBOM_Writer SHALL be defined as a Python Protocol class in the domain layer, allowing structural subtyping without requiring inheritance.
3. THE Writer_Result type SHALL be a frozen dataclass containing: the output file path (Path), the format identifier (Output_Format enum value), the SHA-256 hash of the written file (64-character hexadecimal string), the file size in bytes (non-negative integer matching the actual byte count of the written file), and a list of validation diagnostics (zero or more strings, maximum 1000 entries).
4. THE Output_Format enumeration SHALL define members SPDX_3_0, SPDX_2_3, and CYCLONEDX, and SHALL be extensible by adding new members without modifying existing writer implementations.
5. THE SBOM_Writer protocol SHALL be stateless such that calling `write` multiple times with the same SBOM_Document (by value equality) and output path produces byte-identical output files.
6. WHEN a writer produces output, THE writer SHALL compute the SHA-256 hash of the written bytes and include the hash in the Writer_Result, and SHALL set the file_size field to the exact byte count of the written file.
7. IF the output_path parent directory does not exist, THEN THE SBOM_Writer SHALL create the necessary parent directories before writing.
8. IF the output_path is not writable (permission denied or filesystem full), THEN THE SBOM_Writer SHALL raise a domain-specific error indicating the path and failure reason without leaving partial output files.
9. IF the document parameter is None or contains no root package element, THEN THE SBOM_Writer SHALL raise a domain-specific error indicating the validation failure without writing any file.
10. IF the WorkflowContext cancellation token is set during a write operation, THEN THE SBOM_Writer SHALL abort the operation, remove any partial output file at the output_path, and raise a domain-specific cancellation error.

### Requirement 4: SPDX 3.0 JSON-LD Writer

**User Story:** As a compliance officer, I want to generate SPDX 3.0 JSON-LD output, so that I can produce SBOMs in the latest SPDX standard for regulatory compliance.

#### Acceptance Criteria

1. WHEN an SBOM_Document is provided, THE SPDX3_Writer SHALL serialize the document into SPDX 3.0 JSON-LD format that passes validation against the SPDX 3.0 JSON schema.
2. THE SPDX3_Writer SHALL map each SBOM_Package to an SPDX 3.0 `software_Package` element with `name`, `software_packageVersion`, `software_downloadLocation`, `software_packageUrl`, and `software_copyrightText` fields populated from the corresponding SBOM_Package fields, substituting the SPDX 3.0 `NoAssertionValue` for any source field that is null or empty.
3. THE SPDX3_Writer SHALL map each SBOM_Relationship to an SPDX 3.0 `Relationship` element with `from`, `to`, and `relationshipType` fields using SPDX 3.0 relationship type vocabulary.
4. IF an SBOM_Relationship contains a relationship type that has no equivalent in the SPDX 3.0 relationship type vocabulary, THEN THE SPDX3_Writer SHALL omit that relationship from the output and SHALL record a warning message in the Writer_Result diagnostics list identifying the unmapped type.
5. THE SPDX3_Writer SHALL map SBOM_CreationInfo to the SPDX 3.0 `CreationInfo` structure with `created`, `createdBy`, and `createdUsing` fields.
6. THE SPDX3_Writer SHALL include the `@context` field referencing the SPDX 3.0 JSON-LD context URL and set the `@type` to "SpdxDocument".
7. THE SPDX3_Writer SHALL map SBOM_Checksum entries to SPDX 3.0 `Hash` elements with `algorithm` and `hashValue` fields using SPDX 3.0 hash algorithm vocabulary (e.g., "sha256" maps to "https://spdx.org/rdf/3.0.1/terms/HashAlgorithm/sha256").
8. THE SPDX3_Writer SHALL format the output JSON with 2-space indentation, sorted keys within each object, and UTF-8 encoding without BOM.
9. WHEN the SBOM_Document contains SBOM_ExtractedLicense entries, THE SPDX3_Writer SHALL map each entry that has a non-empty `licenseId` matching an SPDX License List identifier to a `simplelicensing_LicenseExpression` element, and SHALL map each entry that has no matching SPDX License List identifier to an `expandedlicensing_CustomLicense` element with the `extractedText` field populated.
10. THE SPDX3_Writer SHALL validate the serialized output against the SPDX 3.0 JSON schema within 30 seconds before writing to disk.
11. IF schema validation fails, THEN THE SPDX3_Writer SHALL still write the output file but SHALL include all validation error messages (up to a maximum of 100 entries) in the Writer_Result diagnostics list.

### Requirement 5: SPDX 2.3 JSON Writer

**User Story:** As a compliance officer, I want to generate SPDX 2.3 JSON output, so that I can produce SBOMs compatible with tools and processes that have not yet adopted SPDX 3.0.

#### Acceptance Criteria

1. WHEN an SBOM_Document is provided, THE SPDX23_Writer SHALL serialize the document into valid SPDX 2.3 JSON format conforming to the SPDX 2.3 specification structure.
2. THE SPDX23_Writer SHALL map each SBOM_Package to an SPDX 2.3 `packages` array entry with `SPDXID`, `name`, `versionInfo`, `downloadLocation`, `supplier`, `checksums`, `licenseConcluded`, `licenseDeclared`, `copyrightText`, and `externalRefs` fields populated from the corresponding SBOM_Package fields; for any optional field that has no value set in the SBOM_Package, THE SPDX23_Writer SHALL use the SPDX 2.3 "NOASSERTION" sentinel value for string fields and omit the field for array fields that are empty.
3. THE SPDX23_Writer SHALL map each SBOM_Relationship to an SPDX 2.3 `relationships` array entry with `spdxElementId`, `relatedSpdxElement`, and `relationshipType` fields using SPDX 2.3 relationship type vocabulary (e.g., DESCRIBES, CONTAINS, DEPENDS_ON).
4. IF an SBOM_Relationship has a relationship type that does not map to any SPDX 2.3 relationship type vocabulary term, THEN THE SPDX23_Writer SHALL map it to "OTHER" and SHALL include a diagnostic message in the Writer_Result diagnostics list indicating the original unmapped type.
5. THE SPDX23_Writer SHALL populate the top-level `spdxVersion` field with "SPDX-2.3", the `dataLicense` field with "CC0-1.0", the `SPDXID` field with "SPDXRef-DOCUMENT", the `name` field from the SBOM_Document name, and the `documentNamespace` field from the SBOM_Document namespace.
6. THE SPDX23_Writer SHALL populate the `creationInfo` object with `created` (ISO 8601 UTC timestamp in the format "YYYY-MM-DDThh:mm:ssZ"), `creators` (list of creator strings from SBOM_CreationInfo in the format "Tool: <name>" or "Organization: <name>" or "Person: <name>"), and `licenseListVersion` set to the SPDX License List version used by the SBOM_Document.
7. THE SPDX23_Writer SHALL map SBOM_Checksum entries to SPDX 2.3 checksum objects with `algorithm` (e.g., "SHA256") and `checksumValue` fields.
8. WHEN an SBOM_Package has a package_url value, THE SPDX23_Writer SHALL include an `externalRefs` entry with `referenceCategory` "PACKAGE-MANAGER", `referenceType` "purl", and `referenceLocator` set to the PURL string.
9. WHEN the SBOM_Document contains SBOM_ExtractedLicense entries, THE SPDX23_Writer SHALL map each to a `hasExtractedLicensingInfos` array entry with `licenseId`, `extractedText`, `name`, and `seeAlsos` fields.
10. THE SPDX23_Writer SHALL format the output JSON with 2-space indentation, sorted keys within each object, and UTF-8 encoding without BOM.
11. THE SPDX23_Writer SHALL validate the serialized output against the SPDX 2.3 JSON schema before returning the Writer_Result.
12. IF schema validation fails, THEN THE SPDX23_Writer SHALL still write the output but SHALL include all validation error messages in the Writer_Result diagnostics list.
13. WHEN an SBOM_Package has no download_location set, THE SPDX23_Writer SHALL use the SPDX 2.3 "NOASSERTION" sentinel value for the `downloadLocation` field.

### Requirement 6: CycloneDX JSON Writer

**User Story:** As a security engineer, I want to generate CycloneDX JSON output, so that I can integrate SBOMs with vulnerability management tools that consume CycloneDX format.

#### Acceptance Criteria

1. WHEN an SBOM_Document is provided, THE CycloneDX_Writer SHALL serialize the document into valid CycloneDX 1.5 JSON format conforming to the CycloneDX 1.5 specification structure.
2. THE CycloneDX_Writer SHALL populate the top-level `bomFormat` field with "CycloneDX", `specVersion` with "1.5", and `version` with 1 (integer).
3. THE CycloneDX_Writer SHALL generate a deterministic `serialNumber` in URN UUID format using UUID v5 derived from the SBOM_Document namespace to maintain consistency with the determinism requirement.
4. THE CycloneDX_Writer SHALL map each SBOM_Package to a `components` array entry with `type` set to "library", `name`, `version`, `purl` (from package_url), `hashes`, `licenses`, and `copyright` fields populated from the corresponding SBOM_Package fields; optional fields that are null in the SBOM_Package SHALL be omitted from the JSON output.
5. THE CycloneDX_Writer SHALL map SBOM_Checksum entries to CycloneDX `hashes` array entries with `alg` (e.g., "SHA-256") and `content` fields.
6. WHEN an SBOM_Package has a concluded_license value, THE CycloneDX_Writer SHALL include a `licenses` array entry with an `expression` field containing the SPDX license expression string. WHEN an SBOM_Package has no concluded_license value, THE CycloneDX_Writer SHALL omit the `licenses` field entirely.
7. THE CycloneDX_Writer SHALL populate the `metadata` object with `timestamp` (ISO 8601 UTC), `tools` array (containing tool objects with `name` and `version` fields parsed from SBOM_CreationInfo tool identifiers by splitting on the last hyphen in the "Tool: name-version" format), and `component` (describing the root package as the subject of the BOM).
8. WHEN the SBOM_Document contains DEPENDS_ON relationships, THE CycloneDX_Writer SHALL populate the `dependencies` array with dependency objects mapping each component `ref` (using the component bom-ref) to its `dependsOn` list of dependency bom-refs.
9. THE CycloneDX_Writer SHALL format the output JSON with 2-space indentation, sorted keys within each object, and UTF-8 encoding without BOM.
10. THE CycloneDX_Writer SHALL validate the serialized output against the CycloneDX 1.5 JSON schema before writing to disk.
11. IF schema validation fails, THEN THE CycloneDX_Writer SHALL still write the output file but SHALL include all validation error messages in the Writer_Result diagnostics list.
12. THE CycloneDX_Writer SHALL generate deterministic `bom-ref` values for each component based on the package name, version, and PURL to enable stable cross-referencing in the dependencies section.
13. IF the SBOM_Document contains zero component packages, THEN THE CycloneDX_Writer SHALL produce a valid CycloneDX document with an empty `components` array and an empty `dependencies` array.

### Requirement 7: Schema Validation

**User Story:** As a compliance officer, I want SBOM output validated against specification schemas, so that I can be confident the generated documents are specification-compliant and interoperable with other tools.

#### Acceptance Criteria

1. THE Schema_Validator SHALL validate a JSON string against a specified schema identified by Output_Format (SPDX_3_0, SPDX_2_3, or CYCLONEDX) and SHALL return a list of validation error messages (empty list indicates valid output).
2. THE Schema_Validator SHALL bundle the SPDX 3.0, SPDX 2.3, and CycloneDX 1.5 JSON schema files, including all referenced sub-schemas (`$ref` dependencies), as package data within the debcraft distribution, loading and resolving schemas from bundled resources without requiring network access.
3. WHEN validating against the SPDX 2.3 schema, THE Schema_Validator SHALL use the official SPDX 2.3 JSON schema from the SPDX specification repository.
4. WHEN validating against the SPDX 3.0 schema, THE Schema_Validator SHALL use the official SPDX 3.0 JSON schema from the SPDX specification repository.
5. WHEN validating against the CycloneDX 1.5 schema, THE Schema_Validator SHALL use the official CycloneDX 1.5 JSON schema from the CycloneDX specification repository.
6. WHEN a validation error is found, THE Schema_Validator SHALL produce an error message containing the JSON path of the failing element (in RFC 6901 JSON Pointer format), the constraint that was violated (e.g., "required property missing", "type mismatch", "pattern mismatch"), and the actual value that failed validation (truncated to 200 characters if longer).
7. IF the schema file for the specified Output_Format is missing or cannot be parsed as valid JSON, THEN THE Schema_Validator SHALL raise a domain-specific error indicating which schema is unavailable and the reason.
8. IF the input string is not valid JSON (malformed syntax), THEN THE Schema_Validator SHALL return a single validation error message indicating the parse failure location (line and column number if available) without attempting schema validation.
9. THE Schema_Validator SHALL complete validation of a document containing up to 10,000 components within 5 seconds when executed on a machine with at least 2 CPU cores and 4 GB available memory.

### Requirement 8: SBOM Writer Plugin Registry

**User Story:** As a platform developer, I want writers registered as plugins, so that new output formats can be supported by adding packages without modifying core code.

#### Acceptance Criteria

1. THE Writer_Registry SHALL discover writer implementations via `importlib.metadata` entry points in the `debcraft.sbom_writers` group, where each entry point name corresponds to an Output_Format enum value (e.g., "spdx_3_0", "spdx_2_3", "cyclonedx").
2. WHEN the Writer_Registry is initialized, THE Writer_Registry SHALL load all registered entry points, resolve each entry point name to its corresponding Output_Format enum member, instantiate the writer class, and map the resulting instance to that Output_Format.
3. IF a registered entry point name does not match any defined Output_Format enum member, THEN THE Writer_Registry SHALL skip that entry point, log a warning identifying the unrecognized entry point name, and continue loading remaining entry points.
4. IF a registered entry point fails to load (due to ImportError or other resolution error), THEN THE Writer_Registry SHALL skip that entry point, log a warning identifying the failing entry point name and error reason, and continue loading remaining entry points.
5. IF multiple entry points register the same Output_Format, THEN THE Writer_Registry SHALL use the last successfully loaded entry point for that format and log a warning identifying the overridden entry point name.
6. WHEN a write operation is requested for a given Output_Format, THE Writer_Registry SHALL return the registered SBOM_Writer instance for that format.
7. IF no writer is registered for a requested Output_Format, THEN THE Writer_Registry SHALL raise a domain-specific error identifying the unsupported format by name and listing all currently registered Output_Format values.
8. THE Writer_Registry SHALL validate that each loaded writer conforms to the SBOM_Writer protocol (exposes an async `write(self, document: SBOM_Document, output_path: Path, context: WorkflowContext) -> Writer_Result` method) at registration time and SHALL reject non-conforming implementations with an error indicating the entry point name and which required method or signature is missing.
9. IF a loaded writer fails protocol validation, THEN THE Writer_Registry SHALL skip that writer, log a warning identifying the entry point name and the conformance failure reason, and continue loading remaining entry points without affecting their registration.

### Requirement 9: SBOM Workflow

**User Story:** As an operator, I want a complete SBOM generation workflow, so that I can produce SBOMs from artifacts in a single orchestrated operation that coordinates all subsystems.

#### Acceptance Criteria

1. THE SBOM_Workflow SHALL implement the Workflow protocol and execute the following steps in sequence: scan the artifact, enrich identified packages with metadata, assemble the internal SBOM_Document, write the document using all requested output formats, and persist SBOMDocument records to the database.
2. WHEN the SBOM_Workflow completes successfully, THE SBOM_Workflow SHALL produce a WorkflowSummary with workflow_name "sbom", start_time and end_time as UTC timestamps where start_time ≤ end_time, and final_state COMPLETED.
3. WHILE the SBOM_Workflow is in progress, THE SBOM_Workflow SHALL check the WorkflowContext cancellation token between each major step (scan, enrich, assemble, write, persist) and SHALL terminate early with CANCELLED state if cancellation is requested, without deleting any output files already successfully written prior to cancellation.
4. WHILE the SBOM_Workflow is executing, THE SBOM_Workflow SHALL report progress through the WorkflowContext progress reporter at each major step boundary (0% at start, 25% after scan, 50% after enrichment, 75% after assembly, 100% after writing and persistence).
5. THE SBOM_Workflow SHALL publish lifecycle events via the WorkflowContext event bus: a WorkflowStartedEvent at workflow start, a step-completion event after each major step completes, and a terminal event (WorkflowCompletedEvent, WorkflowFailedEvent, or WorkflowCancelledEvent) at workflow end.
6. WHEN writing is complete, THE SBOM_Workflow SHALL persist an SBOMDocument record for each output format produced, storing the format identifier, the output file path, and the SHA-256 hash from the Writer_Result.
7. IF any step of the workflow fails, THEN THE SBOM_Workflow SHALL produce a WorkflowSummary with FAILED final_state and error_details containing the step name that failed and the error message.
8. THE SBOM_Workflow SHALL accept configuration specifying the artifact path to scan and which output formats to produce (one or more of SPDX_3_0, SPDX_2_3, CYCLONEDX), defaulting to all three formats when no explicit format selection is provided.
9. THE SBOM_Workflow SHALL resolve all dependencies (scanner registry, enricher, model assembler, writer registry, SBOM repository) from the WorkflowContext scope via dependency injection per AC-02.
10. IF one or more writer implementations fail while other formats succeed during the write step, THEN THE SBOM_Workflow SHALL persist SBOMDocument records for the successfully written formats, include the failing format names and error messages in the WorkflowSummary error_details, and produce a WorkflowSummary with FAILED final_state.

### Requirement 10: SBOM JSON Serializer Round-Trip

**User Story:** As a developer, I want round-trip serialization correctness for each writer format, so that the internal model is faithfully represented in every output format.

#### Acceptance Criteria

1. THE SBOM_Printer SHALL format SBOM_Document value objects into JSON strings with 2-space indentation, sorted keys, UTF-8 encoding without BOM, and a trailing newline character.
2. FOR ALL valid SBOM_Document instances, WHEN an SBOM_Document is serialized by the SPDX23_Writer and the resulting JSON is parsed back into a dictionary, THE dictionary SHALL contain all package names (exact string equality), versions (exact string equality), SPDX identifiers (exact string equality), relationship types (exact string equality), and checksum values (exact string equality) present in the original SBOM_Document.
3. FOR ALL valid SBOM_Document instances, WHEN an SBOM_Document is serialized by the SPDX3_Writer and the resulting JSON is parsed back into a dictionary, THE dictionary SHALL contain all package names (exact string equality), versions (exact string equality), element identifiers (exact string equality), relationship types (exact string equality), and hash values (exact string equality) present in the original SBOM_Document.
4. FOR ALL valid SBOM_Document instances, WHEN an SBOM_Document is serialized by the CycloneDX_Writer and the resulting JSON is parsed back into a dictionary, THE dictionary SHALL contain all component names (exact string equality), versions (exact string equality), PURLs (exact string equality), hash values (exact string equality), and dependency references (set equality) present in the original SBOM_Document.
5. THE SBOM_Printer SHALL produce deterministic output: serializing the same SBOM_Document twice SHALL produce byte-identical JSON strings.
6. FOR ALL valid SBOM_Document instances containing SBOM_Package fields with Unicode characters (including CJK, Arabic, emoji, and combining characters), WHEN serialized by any writer and parsed back, THE resulting field values SHALL preserve all Unicode code points without normalization, replacement, or loss.
7. FOR ALL valid SBOM_Document instances, WHEN an SBOM_Package has optional fields set to None, THE serialized output SHALL omit those fields or represent them according to the format-specific sentinel value rules (e.g., "NOASSERTION" for SPDX 2.3), and parsing back SHALL not introduce spurious values for those fields.

### Requirement 11: CLI SBOM Command

**User Story:** As an operator, I want a `debcraft sbom` CLI command, so that I can generate SBOMs from the command line for automation and scripting.

#### Acceptance Criteria

1. WHEN `debcraft sbom <artifact-path>` is invoked, THE CLI SHALL execute the SBOM_Workflow for the specified artifact and write output files to the current directory (or a specified output directory), and exit with status code 0.
2. THE CLI SHALL accept a `--format` option (repeatable) to select output formats from "spdx3", "spdx23", "cyclonedx", defaulting to all three formats when no `--format` option is specified.
3. THE CLI SHALL accept an `--output-dir` option specifying the directory where SBOM files are written, defaulting to the current working directory.
4. THE CLI SHALL accept a `--type` option specifying the artifact type (directory, docker, oci, iso, qcow2, img, ami), with automatic type detection when not specified.
5. WHEN the workflow completes successfully, THE CLI SHALL display a summary table (using Rich) showing: each output format produced, the output file path, the file size, and the SHA-256 hash.
6. IF the workflow fails, THEN THE CLI SHALL display an error message describing the failure, exit with a non-zero status code, and clean up any partial output files created during the current invocation.
7. WHEN schema validation produces warnings for any output format, THE CLI SHALL display the validation diagnostics in a warnings section below the summary table.
8. THE CLI SHALL support a `--quiet` flag that suppresses progress output and displays only the final summary or error.
9. IF the specified artifact path does not exist or is not readable, THEN THE CLI SHALL display an error message indicating the path is invalid, and exit with a non-zero status code without invoking the SBOM_Workflow.
10. IF a `--format` value is not one of "spdx3", "spdx23", or "cyclonedx", THEN THE CLI SHALL display an error message listing the valid format options and exit with a non-zero status code.
11. IF the `--output-dir` path does not exist or is not writable, THEN THE CLI SHALL display an error message indicating the directory is invalid and exit with a non-zero status code without invoking the SBOM_Workflow.
