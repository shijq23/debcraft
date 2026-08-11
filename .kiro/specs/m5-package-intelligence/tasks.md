# Implementation Plan: Package Intelligence

## Overview

This plan implements the Package Intelligence subsystem (Milestone 5) for DebCraft. It follows the layered architecture: pure domain parsers and services first, infrastructure adapters second, then wiring and integration. Each task builds incrementally on previous work, with property-based tests validating correctness properties from the design document.

## Tasks

- [x] 1. Set up domain sub-package structure and foundational types
  - [x] 1.1 Create domain package skeleton and error hierarchy
    - Create `src/debcraft/domain/package_intelligence/__init__.py`
    - Create `src/debcraft/domain/package_intelligence/errors.py` with `DebParseError`, `DEP5ParseError`, `SPDXTokenizeError`, `SPDXParseError`, `PURLGenerationError`, `DependencyParseError` extending `PlatformError`
    - Each error carries contextual fields as defined in the design (file_path, offset, paragraph_index, etc.)
    - _Requirements: 1.6, 1.7, 1.8, 1.12, 2.6, 2.8, 2.9, 4.3, 5.5, 5.7, 10.6_

  - [x] 1.2 Create value objects module
    - Create `src/debcraft/domain/package_intelligence/values.py`
    - Implement all frozen dataclass value objects: `DebParseResult`, `DependencyRelation`, `DEP5Document`, `DEP5Header`, `DEP5FilesParagraph`, `DEP5LicenseParagraph`, `SimpleNode`, `WithNode`, `AndNode`, `OrNode`, `SPDXNode` union type, `SPDXTokenType` enum, `SPDXToken`, `MappingAlgorithm` enum, `LicenseMappingResult`, `SymlinkResolutionResult`
    - _Requirements: 1.10, 1.11, 2.1, 2.2, 2.3, 4.1, 5.1, 7.8_

  - [x] 1.3 Create port interfaces
    - Create `src/debcraft/domain/package_intelligence/ports.py`
    - Define `DebFileReader` Protocol with `read_ar_member()` and `compute_sha256()` methods
    - Define `ParseCachePort` Protocol with async `get()` and `store()` methods
    - Define `ContentsLookupPort` Protocol with `find_owner()` and `get_copyright_content()` methods
    - _Requirements: 11.1, 11.2, 12.5, 12.6_

- [x] 2. Implement SPDX expression processing
  - [x] 2.1 Implement SPDX tokenizer
    - Create `src/debcraft/domain/package_intelligence/spdx_tokenizer.py`
    - Implement `SPDXTokenizer.tokenize()` producing typed tokens with zero-based character offsets
    - Handle license identifiers, AND/OR/WITH operators (case-insensitive), parentheses, `+` suffix (or-later), `LicenseRef-*`, and `DocumentRef-*:LicenseRef-*`
    - Raise `SPDXTokenizeError` with offset for invalid characters
    - Return empty list for empty/whitespace input
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [x] 2.2 Implement SPDX recursive-descent parser
    - Create `src/debcraft/domain/package_intelligence/spdx_parser.py`
    - Implement `SPDXExpressionParser.parse()` with correct precedence: WITH > AND > OR
    - Support parenthesized grouping up to MAX_NESTING_DEPTH = 32
    - Raise `SPDXParseError` for malformed expressions (unbalanced parens, missing operands, empty input, excessive depth)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

  - [x] 2.3 Implement SPDX printer
    - Create `src/debcraft/domain/package_intelligence/spdx_printer.py`
    - Implement `SPDXPrinter.print()` serializing AST nodes to canonical SPDX strings
    - Insert parentheses around Or children when they appear as operands of And nodes
    - Handle SimpleNode (with or-later `+`), WithNode, AndNode, OrNode
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [x] 2.4 Write property test for SPDX round-trip (Property 3)
    - **Property 3: SPDX Expression Round-Trip**
    - Create `tests/properties/domain/package_intelligence/test_spdx_round_trip.py`
    - Use recursive Hypothesis strategy to generate valid `SPDXNode` ASTs
    - Verify: print → tokenize → parse produces structurally identical AST
    - **Validates: Requirements 4.1, 4.2, 4.4, 4.6, 5.1, 5.2, 5.3, 5.4, 5.6, 6.1, 6.2, 6.3, 6.4, 6.5**

  - [x] 2.5 Write property test for SPDX tokenizer error offset (Property 4)
    - **Property 4: SPDX Tokenizer Error Offset Accuracy**
    - Add to `tests/properties/domain/package_intelligence/test_spdx_round_trip.py`
    - Generate strings with at least one invalid SPDX character
    - Verify: error offset points to a character that is indeed invalid in SPDX
    - **Validates: Requirements 4.3**

  - [x] 2.6 Write property test for SPDX parser malformed input rejection (Property 5)
    - **Property 5: SPDX Parser Rejects Malformed Input**
    - Add to `tests/properties/domain/package_intelligence/test_spdx_round_trip.py`
    - Generate malformed token sequences (unbalanced parens, consecutive operators, missing operands, excessive depth)
    - Verify: SPDXParseError is raised with valid token position
    - **Validates: Requirements 5.5**

- [x] 3. Implement DEP-5 copyright parsing and printing
  - [x] 3.1 Implement DEP-5 parser
    - Create `src/debcraft/domain/package_intelligence/dep5_parser.py`
    - Implement `DEP5Parser.parse()` with `PARSER_VERSION = 1`
    - Parse header paragraph (require Format field), Files paragraphs (require Files, Copyright, License fields), standalone License paragraphs
    - Handle continuation lines (space/tab prefix), lone-dot empty line markers, multiline values
    - Support Comment fields and extra fields on all paragraph types
    - Raise `DEP5ParseError` for: missing Format field, missing required fields in Files paragraphs, empty/whitespace-only input
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9_

  - [x] 3.2 Implement DEP-5 printer
    - Create `src/debcraft/domain/package_intelligence/dep5_printer.py`
    - Implement `DEP5Printer.print()` formatting DEP5Document back to DEP-5 text
    - Emit fields in stored order, separate paragraphs with one blank line
    - Format continuation lines with single-space prefix, empty lines as ` .`
    - End output with exactly one trailing newline
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x] 3.3 Write property test for DEP-5 round-trip (Property 1)
    - **Property 1: DEP-5 Parse–Print Round-Trip**
    - Create `tests/properties/domain/package_intelligence/test_dep5_round_trip.py`
    - Use composite Hypothesis strategy to generate valid `DEP5Document` objects
    - Verify: print → parse produces structurally equal document
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.7, 3.1, 3.2, 3.3, 3.4**

  - [x] 3.4 Write property test for DEP-5 printer trailing newline (Property 2)
    - **Property 2: DEP-5 Printer Trailing Newline Invariant**
    - Add to `tests/properties/domain/package_intelligence/test_dep5_round_trip.py`
    - Verify: output ends with `\n` and not `\n\n`
    - **Validates: Requirements 3.5**

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement license mapping
  - [x] 5.1 Embed SPDX license data
    - Create `src/debcraft/domain/package_intelligence/data/` directory
    - Create `src/debcraft/domain/package_intelligence/data/spdx_licenses.json` with SPDX license list (identifiers + full names)
    - Create a loader utility to read and parse the embedded JSON at import time
    - _Requirements: 7.1, 7.4_

  - [x] 5.2 Implement license mapper
    - Create `src/debcraft/domain/package_intelligence/license_mapper.py`
    - Implement `LicenseMapper.__init__()` accepting `SPDXLicenseData`
    - Implement `LicenseMapper.map()` applying algorithms in precedence order: ExactSPDX → DebianAlias → NormalizedSpelling → SPDXFullName → LicenseTextHash → FuzzySimilarity → Unmapped
    - Handle empty/whitespace input → `LicenseRef-debcraft-unknown`
    - Handle input > 512 chars → truncate with rationale note
    - Clamp fuzzy confidence to [90, 97] range
    - Unmapped fallback → `LicenseRef-debcraft-<normalized-name>` with confidence 0
    - Ensure result invariant: spdx_expression ≤ 1024 chars, confidence 0–100, non-empty rationale ≤ 512 chars
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10, 7.11_

  - [x] 5.3 Write property tests for license mapper (Properties 6–10)
    - **Property 6: License Mapper Result Invariant**
    - **Property 7: License Mapper Exact Match Confidence**
    - **Property 8: License Mapper Normalized Spelling**
    - **Property 9: License Mapper Unmapped Fallback**
    - **Property 10: License Mapper Fuzzy Confidence Clamping**
    - Create `tests/properties/domain/package_intelligence/test_license_mapper_properties.py`
    - Generate arbitrary strings and known SPDX identifiers with spelling variations
    - Verify each property's invariants as defined in the design
    - **Validates: Requirements 7.1, 7.3, 7.5, 7.7, 7.8, 7.9**

- [x] 6. Implement utility domain services
  - [x] 6.1 Implement download location resolver
    - Create `src/debcraft/domain/package_intelligence/download_location.py`
    - Implement `resolve_download_location(base_url, filename)` as a pure function
    - Join with exactly one `/` separator, handle trailing/leading slashes
    - Return `NOASSERTION` for missing/empty/whitespace inputs
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

  - [x] 6.2 Implement PURL generator
    - Create `src/debcraft/domain/package_intelligence/purl_generator.py`
    - Implement `generate_purl(package_name, version, architecture, distro)` as a pure function
    - Format: `pkg:deb/<distro>/<name>@<version>?arch=<architecture>`
    - Percent-encode special characters (`:` → `%3A`, `+` → `%2B`)
    - Default distro to `"debian"` when unspecified
    - Include `?arch=all` for arch-independent packages
    - Raise `PURLGenerationError` for missing required fields
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [x] 6.3 Implement symlink resolver
    - Create `src/debcraft/domain/package_intelligence/symlink_resolver.py`
    - Implement `SymlinkResolver.__init__()` accepting `ContentsLookupPort`
    - Implement `SymlinkResolver.resolve()` with `MAX_RESOLUTION_DEPTH = 10`
    - Resolve relative paths by joining with source directory and normalizing
    - Use absolute paths directly
    - Follow multi-hop chains, detect cycles via visited-set
    - Return `SymlinkResolutionResult` (never raise, failure encoded in result)
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_

  - [x] 6.4 Write property tests for download location (Properties 11, 12)
    - **Property 11: Download Location URL Join**
    - **Property 12: Download Location NOASSERTION for Missing Inputs**
    - Create `tests/properties/domain/package_intelligence/test_download_location_properties.py`
    - Generate URLs with/without trailing slashes and filenames with/without leading slashes
    - Verify no double-slash at join boundary; verify NOASSERTION for missing inputs
    - **Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5**

  - [x] 6.5 Write property tests for PURL generator (Properties 13, 14)
    - **Property 13: PURL Format Conformance**
    - **Property 14: PURL Generation Error for Missing Fields**
    - Create `tests/properties/domain/package_intelligence/test_purl_properties.py`
    - Generate valid inputs and verify format pattern; generate invalid inputs and verify PURLGenerationError
    - **Validates: Requirements 10.1, 10.2, 10.4, 10.5, 10.6**

  - [x] 6.6 Write property tests for symlink resolver (Properties 15, 16)
    - **Property 15: Symlink Resolution Terminates Within Bounds**
    - **Property 16: Symlink Relative Path Resolution**
    - Create `tests/properties/domain/package_intelligence/test_symlink_resolver_properties.py`
    - Generate symlink chains of varying depth (including cycles) and verify termination
    - Generate relative paths and verify normalization equivalence
    - **Validates: Requirements 8.2, 8.5, 8.7**

- [x] 7. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Implement .deb archive parser
  - [x] 8.1 Implement deb parser domain logic
    - Create `src/debcraft/domain/package_intelligence/deb_parser.py`
    - Implement `DebParser.__init__()` accepting `DebFileReader` protocol
    - Implement `DebParser.parse()` with `PARSER_VERSION = 1`
    - Validate ar archive magic bytes (`!<arch>\n`), reject with `DebParseError` if invalid
    - Validate `debian-binary` version (must start with "2.")
    - Extract and parse control file fields (Package, Version, Architecture, Maintainer, Description, dependency fields, etc.)
    - Parse dependency fields into `DependencyRelation` lists preserving version constraints and alternatives
    - Extract file listing from `data.tar`
    - Extract copyright text from `data.tar` at `usr/share/doc/<package>/copyright` (None if absent)
    - Raise `DebParseError` for missing `control.tar` or `data.tar` members
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.12_

  - [x] 8.2 Write property tests for deb parser (Properties 17, 18, 19)
    - **Property 17: Control File Field Extraction**
    - **Property 18: Dependency String Parsing Preservation**
    - **Property 19: Invalid Input Rejection by Deb Parser**
    - Create `tests/properties/domain/package_intelligence/test_deb_parser_properties.py`
    - Generate valid control file text and verify field extraction
    - Generate dependency strings and verify structural preservation
    - Generate byte sequences without ar magic and verify DebParseError
    - **Validates: Requirements 1.6, 1.10, 1.11**

- [x] 9. Implement infrastructure adapters
  - [x] 9.1 Implement file reader adapter
    - Create `src/debcraft/infrastructure/package_intelligence/__init__.py`
    - Create `src/debcraft/infrastructure/package_intelligence/file_reader.py`
    - Implement `DebFileReader` protocol: `read_ar_member()` for extracting ar members with compression support (gz, xz, zst, bz2, lzma, uncompressed), `compute_sha256()` for file hashing
    - _Requirements: 1.1, 1.2, 1.9, 12.5_

  - [x] 9.2 Implement cache adapter with SQLAlchemy model
    - Create `src/debcraft/infrastructure/package_intelligence/cache_adapter.py`
    - Create or add to appropriate models file: `ParsedDebPackage` SQLAlchemy model with `sha256`, `parser_version`, `control_metadata` (JSON), `copyright_text`, `file_listing` (JSON array), `TimestampMixin`
    - Implement `ParseCachePort` protocol: async `get()` checks sha256 + parser_version match, async `store()` persists parse results
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_

  - [x] 9.3 Implement contents lookup adapter
    - Create `src/debcraft/infrastructure/package_intelligence/contents_adapter.py`
    - Implement `ContentsLookupPort` protocol: `find_owner()` and `get_copyright_content()` using existing Contents index data
    - _Requirements: 8.4, 12.6_

  - [x] 9.4 Write property tests for cache behavior (Properties 20, 21, 22)
    - **Property 20: Cache Store on Success, Skip on Failure**
    - **Property 21: Cache Hit Returns Cached Result Without Re-Extraction**
    - **Property 22: Cache Invalidation on Version Change**
    - Create `tests/properties/domain/package_intelligence/test_cache_properties.py`
    - Use mock protocols to verify cache interaction semantics
    - Verify: successful parse → store called; failed parse → store NOT called; matching version → cached result returned; mismatched version → re-parse triggered
    - **Validates: Requirements 11.1, 11.2, 11.3, 11.5**

- [x] 10. Wire components and integration
  - [x] 10.1 Create domain package `__init__.py` exports
    - Update `src/debcraft/domain/package_intelligence/__init__.py` with public API exports
    - Export all parser classes, value objects, error types, port protocols, and pure functions
    - Ensure clean import paths for consumers
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.7, 12.8_

  - [x] 10.2 Add import-linter contract verification
    - Verify existing `importlinter` "Domain independence" contract covers the new `debcraft.domain.package_intelligence` sub-package
    - Run `lint-imports` to confirm no prohibited cross-layer imports
    - _Requirements: 12.9_

  - [x] 10.3 Write integration tests for .deb extraction pipeline
    - Create `tests/integration/package_intelligence/test_deb_extraction.py`
    - Test full extraction pipeline with real `.deb` fixture files
    - Verify control field extraction, file listing, and copyright text extraction end-to-end
    - _Requirements: 1.1, 1.2, 1.3, 1.10_

  - [x] 10.4 Write integration tests for cache persistence
    - Create `tests/integration/package_intelligence/test_cache_persistence.py`
    - Test SQLAlchemy round-trip: store parse result then retrieve by SHA256
    - Verify parser version matching and cache invalidation on version change
    - _Requirements: 11.1, 11.2, 11.3, 11.4_

- [x] 11. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- All domain code is pure (no infrastructure imports) — verified by import-linter
- The SPDX license JSON data file should be sourced from the official SPDX license-list-data repository
- Hypothesis strategies for recursive AST generation should use `st.deferred()` with depth limiting to avoid unbounded recursion

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3"] },
    { "id": 1, "tasks": ["2.1", "5.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "3.1", "6.1", "6.2", "6.3"] },
    { "id": 3, "tasks": ["2.4", "2.5", "2.6", "3.2", "5.2", "6.4", "6.5", "6.6"] },
    { "id": 4, "tasks": ["3.3", "3.4", "5.3", "8.1"] },
    { "id": 5, "tasks": ["8.2", "9.1", "9.2", "9.3"] },
    { "id": 6, "tasks": ["9.4", "10.1", "10.2"] },
    { "id": 7, "tasks": ["10.3", "10.4"] }
  ]
}
```
