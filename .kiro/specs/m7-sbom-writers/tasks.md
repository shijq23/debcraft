# Implementation Plan: SBOM Writers

## Overview

Implements the M7 SBOM Writers subsystem following hexagonal architecture. The plan builds the domain model and assembler first, then adds the schema validator and infrastructure writers, wires them through the registry and workflow, and connects via the CLI. Property-based tests validate correctness properties from the design document using Hypothesis.

## Tasks

- [x] 1. Define domain value objects and error hierarchy
  - [x] 1.1 Create `src/debcraft/domain/sbom/__init__.py` and `src/debcraft/domain/sbom/values.py` with all frozen dataclass value objects (`SBOMDocument`, `SBOMPackage`, `SBOMRelationship`, `SBOMChecksum`, `SBOMCreationInfo`, `SBOMExternalReference`, `SBOMExtractedLicense`, `WriterResult`) and enums (`RelationshipType`, `ChecksumAlgorithm`, `ExternalReferenceCategory`, `OutputFormat`)
    - Implement `__post_init__` validation for all constraints (non-empty fields, SPDX ID pattern, hash length, license ID pattern)
    - Raise `ValueError` with descriptive messages on invalid construction
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9_

  - [x] 1.2 Create `src/debcraft/domain/sbom/errors.py` with the domain error hierarchy (`SBOMError`, `ModelValidationError`, `WriterError`, `OutputPathError`, `WriterCancellationError`, `DocumentValidationError`, `UnsupportedFormatError`, `SchemaUnavailableError`)
    - All inherit from `PlatformError` base
    - _Requirements: 3.8, 3.9, 3.10, 7.7, 8.7_

  - [x] 1.3 Create `src/debcraft/domain/sbom/ports.py` with the `SBOMWriter` Protocol class and re-export `WriterResult`
    - Define async `write(self, document, output_path, context) -> WriterResult` method signature
    - _Requirements: 3.1, 3.2_

  - [x] 1.4 Write property tests for model construction validity (Property 1)
    - **Property 1: Model construction preserves valid inputs**
    - **Validates: Requirements 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8**
    - Create `tests/properties/domain/sbom/__init__.py` and `tests/properties/domain/sbom/strategies.py` with Hypothesis strategies for all value objects
    - Create `tests/properties/domain/sbom/test_model_properties.py`

  - [x] 1.5 Write property tests for model construction rejection (Property 2)
    - **Property 2: Model construction rejects invalid inputs**
    - **Validates: Requirements 1.9**
    - Add to `tests/properties/domain/sbom/test_model_properties.py`

- [x] 2. Implement Model Assembler
  - [x] 2.1 Create `src/debcraft/domain/sbom/assembler.py` with `ModelAssembler` class
    - Map `EnrichedPackage` → `SBOMPackage` (name, version, architecture → description)
    - Map purl → `package_url` + PACKAGE_MANAGER external reference
    - Map sha256 → `SBOMChecksum` with SHA256 algorithm
    - Map license_expressions → `concluded_license` and `declared_license`
    - Generate DESCRIBES relationships from root to all components
    - Parse depends string and generate DEPENDS_ON relationships for matching packages
    - Generate unique SPDX IDs with collision suffix (`SPDXRef-Package-<name>-<version>[-N]`)
    - Generate namespace: `https://debcraft.io/spdxdocs/<16-hex-of-sha256(artifact_path)>-<uuid4>`
    - Populate `SBOMCreationInfo` with debcraft version from `importlib.metadata`
    - Handle zero-package case with empty components and document comment
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10_

  - [x] 2.2 Write property tests for assembler field mapping (Property 3)
    - **Property 3: Model assembler field mapping correctness**
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.10**
    - Create `tests/properties/domain/sbom/test_assembler_properties.py`

  - [x] 2.3 Write property tests for assembler SPDX ID uniqueness (Property 4)
    - **Property 4: Model assembler SPDX ID uniqueness**
    - **Validates: Requirements 2.7**
    - Add to `tests/properties/domain/sbom/test_assembler_properties.py`

- [x] 3. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Implement Schema Validator
  - [x] 4.1 Add `jsonschema` to project dependencies in `pyproject.toml`
    - Add `"jsonschema>=4.20"` to the `dependencies` list
    - _Requirements: 7.1, 7.2_

  - [x] 4.2 Bundle JSON schema files and create `src/debcraft/domain/sbom/schemas/` directory
    - Download and store official SPDX 3.0, SPDX 2.3, and CycloneDX 1.5 JSON schemas (with `$ref` sub-schemas)
    - Create `src/debcraft/domain/sbom/schemas/__init__.py`
    - _Requirements: 7.2, 7.3, 7.4, 7.5_

  - [x] 4.3 Create `src/debcraft/domain/sbom/validator.py` with `SchemaValidator` class
    - Load schemas via `importlib.resources` from bundled package data
    - Validate JSON string against schema identified by `OutputFormat`
    - Return list of error messages in format `"<json_pointer>: <constraint> (got: <truncated_value>)"`
    - Handle malformed JSON input with parse error including line/column
    - Raise `SchemaUnavailableError` for missing/corrupt schema files
    - _Requirements: 7.1, 7.6, 7.7, 7.8, 7.9_

  - [x] 4.4 Write property tests for schema validation error format (Property 10)
    - **Property 10: Schema validation error message format**
    - **Validates: Requirements 7.6**
    - Create `tests/properties/domain/sbom/test_validator_properties.py`

- [x] 5. Implement deterministic JSON printer
  - [x] 5.1 Create `src/debcraft/infrastructure/sbom_writers/__init__.py` and `src/debcraft/infrastructure/sbom_writers/printer.py` with `SBOMPrinter` class
    - Implement deterministic JSON formatting: 2-space indent, sorted keys, UTF-8 without BOM, trailing newline
    - Accept Python dict, return bytes
    - _Requirements: 10.1, 10.5_

  - [x] 5.2 Write property tests for serialization determinism (Property 5)
    - **Property 5: Serialization determinism**
    - **Validates: Requirements 3.5, 10.1, 10.5**
    - Create `tests/properties/domain/sbom/test_serialization_properties.py`

- [x] 6. Implement SPDX 2.3 Writer
  - [x] 6.1 Create `src/debcraft/infrastructure/sbom_writers/spdx23.py` with `SPDX23Writer` class
    - Map `SBOMDocument` → SPDX 2.3 JSON structure (spdxVersion, dataLicense, SPDXID, name, documentNamespace)
    - Map `SBOMPackage` → packages array entries with NOASSERTION sentinels for null fields
    - Map `SBOMRelationship` → relationships array (fallback to OTHER for unmapped types)
    - Map `SBOMCreationInfo` → creationInfo object
    - Map `SBOMChecksum` → checksum objects
    - Map `SBOMExternalReference` and PURL → externalRefs entries
    - Map `SBOMExtractedLicense` → hasExtractedLicensingInfos array
    - Use `SBOMPrinter` for deterministic output
    - Validate via `SchemaValidator`, include diagnostics in result
    - Create parent dirs, compute SHA-256, check cancellation token
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10, 5.11, 5.12, 5.13, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10_

  - [x] 6.2 Write property tests for SPDX 2.3 round-trip (Property 7)
    - **Property 7: SPDX 2.3 round-trip data preservation**
    - **Validates: Requirements 10.2, 10.6, 10.7**
    - Create `tests/properties/domain/sbom/test_spdx23_roundtrip.py`

- [x] 7. Implement SPDX 3.0 JSON-LD Writer
  - [x] 7.1 Create `src/debcraft/infrastructure/sbom_writers/spdx3.py` with `SPDX3Writer` class
    - Map `SBOMDocument` → SPDX 3.0 JSON-LD with `@context` and `@type` fields
    - Map `SBOMPackage` → software_Package elements with NoAssertionValue substitution
    - Map `SBOMRelationship` → Relationship elements (omit unmapped types with diagnostic)
    - Map `SBOMCreationInfo` → CreationInfo structure
    - Map `SBOMChecksum` → Hash elements with SPDX 3.0 algorithm vocabulary URLs
    - Map `SBOMExtractedLicense` → LicenseExpression or CustomLicense elements
    - Use `SBOMPrinter` for deterministic output
    - Validate via `SchemaValidator`, include diagnostics in result
    - Create parent dirs, compute SHA-256, check cancellation token
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10, 4.11, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10_

  - [x] 7.2 Write property tests for SPDX 3.0 round-trip (Property 8)
    - **Property 8: SPDX 3.0 round-trip data preservation**
    - **Validates: Requirements 10.3, 10.6, 10.7**
    - Create `tests/properties/domain/sbom/test_spdx3_roundtrip.py`

- [x] 8. Implement CycloneDX Writer
  - [x] 8.1 Create `src/debcraft/infrastructure/sbom_writers/cyclonedx.py` with `CycloneDXWriter` class
    - Map `SBOMDocument` → CycloneDX 1.5 JSON (bomFormat, specVersion, version, serialNumber)
    - Generate deterministic serial number via UUID v5 from document namespace
    - Map `SBOMPackage` → components array entries (type "library"), omit null fields
    - Generate deterministic `bom-ref` values from name/version/purl
    - Map `SBOMChecksum` → hashes array (alg: "SHA-256" format)
    - Map concluded_license → licenses[].expression
    - Map `SBOMCreationInfo` → metadata object with tools and timestamp
    - Map DEPENDS_ON relationships → dependencies array
    - Handle zero-components case (empty arrays)
    - Use `SBOMPrinter` for deterministic output
    - Validate via `SchemaValidator`, include diagnostics in result
    - Create parent dirs, compute SHA-256, check cancellation token
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 6.11, 6.12, 6.13, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10_

  - [x] 8.2 Write property tests for CycloneDX round-trip (Property 9)
    - **Property 9: CycloneDX round-trip data preservation**
    - **Validates: Requirements 10.4, 10.6, 10.7**
    - Create `tests/properties/domain/sbom/test_cyclonedx_roundtrip.py`

- [x] 9. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Implement Writer Registry and writer result integrity tests
  - [x] 10.1 Create `src/debcraft/infrastructure/sbom_writers/registry.py` with `WriterRegistry` class
    - Discover writers from `debcraft.sbom_writers` entry point group
    - Map entry point names to `OutputFormat` enum values
    - Validate async `write` method protocol conformance at registration
    - Handle unrecognized names, load failures, duplicate formats with diagnostics
    - Provide `get_writer(format)` method, raise `UnsupportedFormatError` if not found
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9_

  - [x] 10.2 Write property tests for writer result integrity (Property 6)
    - **Property 6: Writer result integrity**
    - **Validates: Requirements 3.6**
    - Add to `tests/properties/domain/sbom/test_serialization_properties.py`

- [x] 11. Implement SBOM Workflow
  - [x] 11.1 Create `src/debcraft/infrastructure/sbom_writers/workflow.py` with `SBOMWorkflow` class implementing the `Workflow` protocol
    - Execute steps in sequence: scan → enrich → assemble → write → persist
    - Report progress at step boundaries (0%, 25%, 50%, 75%, 100%)
    - Check cancellation token between major steps
    - Publish lifecycle events (started, step-completion, terminal)
    - Persist `SBOMDocument` records for each successfully written format
    - Handle partial write failures (persist successes, include failures in error_details)
    - Resolve dependencies from `WorkflowContext.scope`
    - Accept artifact path and format selection configuration
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9, 9.10_

  - [x] 11.2 Write unit tests for SBOM Workflow
    - Test step sequencing with mocked dependencies
    - Test cancellation between steps
    - Test partial failure handling
    - Test progress reporting at correct percentages
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.7, 9.10_

- [x] 12. Wire entry points and CLI command
  - [x] 12.1 Add `debcraft.sbom_writers` entry points to `pyproject.toml`
    - Register `spdx_3_0`, `spdx_2_3`, `cyclonedx` entry points pointing to writer classes
    - _Requirements: 8.1_

  - [x] 12.2 Add `debcraft sbom` CLI command to the Typer app
    - Accept `artifact_path` positional argument
    - Add `--format` (repeatable), `--output-dir`, `--type`, `--quiet` options
    - Validate artifact path exists, format values, output-dir writability before workflow
    - Execute `SBOMWorkflow` and display Rich summary table on success
    - Display validation diagnostics as warnings section
    - Exit with non-zero on failure, clean up partial files
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 11.9, 11.10, 11.11_

  - [x] 12.3 Write unit tests for CLI argument validation and output formatting
    - Test format validation, path validation, quiet mode
    - Test summary table output
    - _Requirements: 11.2, 11.9, 11.10, 11.11_

- [x] 13. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document using Hypothesis
- Unit tests validate specific examples and edge cases
- The design uses Python explicitly — no language selection needed
- Domain layer (`src/debcraft/domain/sbom/`) must have zero infrastructure imports (import linter enforced)
- Schema files are bundled as package data in `domain/sbom/schemas/` per design decision
- Writers follow the same plugin pattern as the existing `ScannerRegistry`

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3"] },
    { "id": 1, "tasks": ["1.4", "1.5", "2.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "4.1"] },
    { "id": 3, "tasks": ["4.2", "5.1"] },
    { "id": 4, "tasks": ["4.3", "4.4", "5.2"] },
    { "id": 5, "tasks": ["6.1", "7.1", "8.1"] },
    { "id": 6, "tasks": ["6.2", "7.2", "8.2", "10.1"] },
    { "id": 7, "tasks": ["10.2", "11.1"] },
    { "id": 8, "tasks": ["11.2", "12.1"] },
    { "id": 9, "tasks": ["12.2"] },
    { "id": 10, "tasks": ["12.3"] }
  ]
}
```
