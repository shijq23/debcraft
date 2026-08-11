# Requirements Document

## Introduction

Package Intelligence (Milestone 5) implements the core package analysis subsystem of DebCraft. It extracts metadata from `.deb` binary packages, parses DEP-5 copyright files, resolves SPDX license expressions from Debian license identifiers, generates Package URLs and download locations, and resolves symbolic-link copyright paths using Contents.gz data. This milestone transforms raw package archives into rich, normalized metadata suitable for SBOM generation and compliance auditing.

## Glossary

- **Deb_Parser**: The domain service that extracts `control.tar` and `data.tar` members from a `.deb` archive file and parses the `control` file within `control.tar` into structured metadata.
- **DEP5_Parser**: The domain parser that parses Debian DEP-5 formatted copyright files into a structured document model of typed paragraphs (Header, Files, License, standalone License).
- **DEP5_Printer**: The domain serializer that formats a parsed DEP-5 document model back into a compliant DEP-5 text representation.
- **SPDX_Expression_Parser**: The domain parser implementing a recursive-descent parser for SPDX license expressions supporting AND, OR, WITH operators and parenthesized grouping.
- **SPDX_Tokenizer**: The lexical analyzer that converts a raw SPDX expression string into a sequence of typed tokens (license identifiers, operators, parentheses).
- **SPDX_AST**: The abstract syntax tree representing a parsed SPDX expression as a tree of nodes (Simple, With, And, Or).
- **SPDX_Printer**: The serializer that formats an SPDX_AST back into a canonical SPDX expression string.
- **License_Mapper**: The domain service that converts a Debian license identifier (or free-text license name) into an SPDX expression with confidence, algorithm, and rationale metadata.
- **Symlink_Resolver**: The domain service that resolves `/usr/share/doc/*/copyright` symbolic links using file ownership data from the Contents index to determine the actual copyright file path.
- **Download_Location_Resolver**: The domain service that constructs a fully-qualified download URL from a repository base URL and the relative package filename field.
- **PURL_Generator**: The domain service that generates Package URL (PURL) strings in the `pkg:deb` scheme for Debian binary packages.
- **Control_File**: The Debian control metadata file within a `.deb` archive containing fields such as Package, Version, Architecture, Depends, Description.
- **DEP5_Document**: A structured representation of a complete DEP-5 copyright file consisting of a header paragraph, zero or more Files paragraphs, and zero or more standalone License paragraphs.
- **License_Mapping_Result**: A value object containing the mapped SPDX expression, a confidence score (0–100), the algorithm used, and a human-readable rationale.
- **Package_URL**: A string conforming to the PURL specification in the format `pkg:deb/debian/package_name@version?arch=architecture`.
- **Confidence_Score**: An integer from 0 to 100 representing the certainty of a license mapping.

## Requirements

### Requirement 1: Parse .deb Archive Files

**User Story:** As a compliance engineer, I want binary package metadata extracted from `.deb` files, so that package control information and copyright data can be analyzed without requiring external tools or root access.

#### Acceptance Criteria

1. WHEN a valid `.deb` archive file path is provided, THE Deb_Parser SHALL extract the `control.tar` member (with any supported compression: gz, xz, zst, or uncompressed) and parse the `control` file within it into structured metadata fields.
2. WHEN a valid `.deb` archive file path is provided, THE Deb_Parser SHALL extract the `data.tar` member listing (with any supported compression: gz, xz, zst, bz2, lzma, or uncompressed) to enumerate file paths contained within the package as a list of strings representing each entry's full archive-relative path.
3. WHEN the `.deb` archive contains a `copyright` file at the path `usr/share/doc/<package_name>/copyright` within `data.tar`, THE Deb_Parser SHALL extract and preserve its full text content for downstream DEP-5 parsing and storage.
4. IF the `.deb` archive does not contain a copyright file at the expected path `usr/share/doc/<package_name>/copyright` within `data.tar`, THEN THE Deb_Parser SHALL indicate the absence in the result (e.g., null or empty value for the copyright field) without raising an error.
5. WHEN a copyright file is extracted, THE Deb_Parser SHALL store the raw copyright text alongside the parsed metadata so that it is available for auditing, display, and license analysis without re-extracting the archive.
6. IF the `.deb` file is malformed, corrupted, or uses an unsupported format version (i.e., the `debian-binary` member does not contain a version string starting with "2."), THEN THE Deb_Parser SHALL raise a descriptive error identifying the file path and nature of the problem.
7. IF the `control.tar` member is missing from the `.deb` archive, THEN THE Deb_Parser SHALL raise a descriptive error rather than returning partial results.
8. IF the `data.tar` member is missing from the `.deb` archive, THEN THE Deb_Parser SHALL raise a descriptive error rather than returning partial results.
9. THE Deb_Parser SHALL operate entirely in user space without requiring root privileges or `dpkg` tools.
10. WHEN the `control` file within `control.tar` is parsed, THE Deb_Parser SHALL extract at minimum: Package, Version, Architecture, Maintainer, Description, Depends, Pre-Depends, Recommends, Suggests, Conflicts, Breaks, Replaces, Provides, Section, Priority, Installed-Size, and Homepage fields. Fields not present in the control file SHALL be represented as absent (null or not included) rather than as empty strings.
11. WHEN the control file contains dependency fields (Depends, Pre-Depends, Recommends, Suggests), THE Deb_Parser SHALL parse each field into a structured list of dependency relationships preserving version constraints (e.g. `libc6 (>= 2.17)`) and alternative groups (separated by `|`).
12. IF a dependency field value in the control file cannot be parsed into valid dependency relationships (e.g., unbalanced parentheses or missing package name), THEN THE Deb_Parser SHALL raise a descriptive error identifying the package name and the malformed field.

### Requirement 2: Parse DEP-5 Copyright Files

**User Story:** As a compliance engineer, I want DEP-5 copyright files parsed into a structured model, so that license and copyright information can be programmatically analyzed per file glob.

#### Acceptance Criteria

1. WHEN a valid DEP-5 formatted text is provided, THE DEP5_Parser SHALL parse it into a DEP5_Document containing a header paragraph with Format, Upstream-Name, Upstream-Contact, and Source fields, followed by zero or more Files and License paragraphs preserved in their original document order.
2. WHEN a DEP-5 document contains Files paragraphs, THE DEP5_Parser SHALL parse each into a structured record containing the Files glob pattern(s), Copyright field, License short-name, and optional License full text.
3. WHEN a DEP-5 document contains standalone License paragraphs (License field with full text but no Files field), THE DEP5_Parser SHALL parse each into a structured record containing the License short-name and the full license body text.
4. WHEN a field value spans multiple lines using continuation lines (lines starting with a space or tab), THE DEP5_Parser SHALL concatenate them preserving paragraph structure by converting lone dot (`.`) continuation lines into empty lines in the resulting text.
5. WHEN a DEP-5 document contains Comment fields on any paragraph type, THE DEP5_Parser SHALL preserve them in the parsed model.
6. IF the input text lacks a Format field in the first paragraph, THEN THE DEP5_Parser SHALL return a parse error indicating the document is not a valid DEP-5 file.
7. WHEN a Files paragraph contains multiple glob patterns (space-separated), THE DEP5_Parser SHALL store all patterns as a list associated with that paragraph.
8. IF a Files paragraph is missing a required field (Files, Copyright, or License), THEN THE DEP5_Parser SHALL return a parse error indicating which paragraph and field is missing.
9. IF the input text is empty or contains only whitespace, THEN THE DEP5_Parser SHALL return a parse error indicating no content was found.

### Requirement 3: DEP-5 Pretty Printer (Round-Trip)

**User Story:** As a developer, I want a serializer for DEP-5 documents, so that round-trip correctness of the parser can be verified and modified documents can be written back.

#### Acceptance Criteria

1. THE DEP5_Printer SHALL format a DEP5_Document back into valid DEP-5 text by emitting each paragraph's fields in their original stored order, separating paragraphs with exactly one blank line, and preserving the paragraph order from the DEP5_Document model (header first, followed by Files paragraphs, followed by standalone License paragraphs).
2. THE DEP5_Printer SHALL format multiline field values using continuation-line syntax where each continuation line is prefixed with a single space character, and lines that are empty within the multiline value are represented as a single space followed by a period (` .`).
3. WHEN a field value fits on a single line (contains no newline characters), THE DEP5_Printer SHALL format it as `Field-Name: value` followed by a newline character.
4. FOR ALL valid DEP-5 documents, WHEN a DEP5_Document is produced by DEP5_Parser and then formatted by DEP5_Printer and then parsed again by DEP5_Parser, THE resulting DEP5_Document SHALL be structurally equal to the original (same paragraph types in same order, same field names and values in each paragraph), confirming the round-trip property.
5. THE DEP5_Printer SHALL terminate its output with a single trailing newline character and SHALL NOT emit trailing blank lines after the last paragraph.

### Requirement 4: Tokenize SPDX License Expressions

**User Story:** As a compliance engineer, I want SPDX license expression strings tokenized into typed tokens, so that they can be fed into a parser for structural analysis.

#### Acceptance Criteria

1. WHEN a valid SPDX expression string is provided, THE SPDX_Tokenizer SHALL consume whitespace (one or more ASCII space characters) between tokens without producing whitespace tokens, and SHALL produce a sequence of tokens in input order identifying: license identifiers (e.g. `MIT`, `GPL-2.0-or-later`), `AND` operators, `OR` operators, `WITH` operators, open parentheses, close parentheses, and `LicenseRef-*` identifiers.
2. WHEN the expression string contains `+` appended to a license identifier (e.g. `GPL-2.0+`), THE SPDX_Tokenizer SHALL produce a single token of a distinct or-later type carrying the base license identifier (e.g. `GPL-2.0`) as its value.
3. IF the expression string contains characters that do not form a valid SPDX token, THEN THE SPDX_Tokenizer SHALL produce an error token or raise a tokenization error identifying the zero-based character offset of the invalid character within the input string.
4. THE SPDX_Tokenizer SHALL treat operator keywords (AND, OR, WITH) as case-insensitive during tokenization.
5. IF the expression string is empty or contains only whitespace, THEN THE SPDX_Tokenizer SHALL produce an empty token sequence.
6. WHEN the expression string contains `DocumentRef-` prefixed identifiers followed by `:` and a `LicenseRef-` identifier (e.g. `DocumentRef-ext:LicenseRef-Custom`), THE SPDX_Tokenizer SHALL produce a single token capturing the full external document reference.

### Requirement 5: Parse SPDX License Expressions into AST

**User Story:** As a compliance engineer, I want SPDX expressions parsed into an abstract syntax tree, so that compound license conditions can be programmatically analyzed and serialized.

#### Acceptance Criteria

1. WHEN a tokenized SPDX expression is provided, THE SPDX_Expression_Parser SHALL produce an SPDX_AST representing the expression structure with correct operator precedence (WITH binds tighter than AND, which binds tighter than OR).
2. WHEN the expression contains parenthesized sub-expressions, THE SPDX_Expression_Parser SHALL group them correctly in the AST up to a maximum nesting depth of 32 levels.
3. WHEN the expression is a single license identifier (e.g. `MIT`), THE SPDX_Expression_Parser SHALL produce a Simple leaf node in the AST.
4. WHEN the expression uses the WITH operator (e.g. `GPL-2.0-only WITH Classpath-exception-2.0`), THE SPDX_Expression_Parser SHALL produce a With node containing the license and exception identifiers.
5. IF the token sequence represents a malformed expression (unbalanced parentheses, missing operand, consecutive operators, or nesting depth exceeding 32 levels), THEN THE SPDX_Expression_Parser SHALL return a parse error indicating the error category and the zero-based token position where the problem was detected.
6. THE SPDX_Expression_Parser SHALL satisfy the round-trip property: for all valid SPDX expressions, parsing into an SPDX_AST then printing with SPDX_Printer then parsing again SHALL produce a structurally identical AST (same node types, same license identifiers, and same tree shape).
7. IF the token sequence is empty, THEN THE SPDX_Expression_Parser SHALL return a parse error indicating that the expression is empty.

### Requirement 6: Serialize SPDX AST to Expression String

**User Story:** As a developer, I want the SPDX AST serialized back to canonical expression strings, so that normalized SPDX expressions can be emitted for SBOM documents.

#### Acceptance Criteria

1. THE SPDX_Printer SHALL format a Simple node as the license identifier string (e.g. `MIT`).
2. THE SPDX_Printer SHALL format a With node as `<license> WITH <exception>` (e.g. `GPL-2.0-only WITH Classpath-exception-2.0`).
3. THE SPDX_Printer SHALL format an And node as `<left> AND <right>`, inserting parentheses around an Or child when it appears as a direct operand of And (since AND binds tighter than OR).
4. THE SPDX_Printer SHALL format an Or node as `<left> OR <right>`, without inserting parentheses around And children (since AND already binds tighter).
5. THE SPDX_Printer SHALL produce output conforming to the SPDX expression syntax specification.

### Requirement 7: Map Debian License Names to SPDX Expressions

**User Story:** As a compliance engineer, I want Debian license identifiers automatically mapped to SPDX expressions with confidence metadata, so that I can understand the provenance and certainty of each license determination.

#### Acceptance Criteria

1. WHEN a Debian license identifier exactly matches a known SPDX identifier (case-insensitive), THE License_Mapper SHALL return the canonical SPDX identifier with confidence 100 and algorithm "ExactSPDX".
2. WHEN a Debian license identifier matches a known Debian-to-SPDX alias (e.g. `GPL-2+` maps to `GPL-2.0-or-later`), THE License_Mapper SHALL return the SPDX expression with confidence 100 and algorithm "DebianAlias".
3. WHEN a Debian license identifier matches after normalized spelling (lowercased, with hyphens, underscores, dots, and spaces removed before comparison), THE License_Mapper SHALL return the SPDX expression with confidence 99 and algorithm "NormalizedSpelling".
4. WHEN a Debian license identifier matches the full name of an SPDX license entry (case-insensitive string equality against the "name" field in the SPDX license list), THE License_Mapper SHALL return the SPDX identifier with confidence 98 and algorithm "SPDXFullName".
5. IF a Debian license identifier does not match any exact method (ExactSPDX, DebianAlias, NormalizedSpelling, SPDXFullName) but has a fuzzy similarity score above the configured threshold (default 80 out of 100) against known SPDX identifiers, THEN THE License_Mapper SHALL return the single highest-scoring match with confidence equal to the similarity score clamped to the range 90–97, and algorithm "FuzzySimilarity".
6. WHEN a license text body is available and its SHA-256 hash matches a known license text in the SPDX license list, THE License_Mapper SHALL return the matching SPDX identifier with confidence 100 and algorithm "LicenseTextHash".
7. IF no mapping method produces a result above the minimum confidence threshold (default 80), THEN THE License_Mapper SHALL return `LicenseRef-debcraft-<normalized-name>` with confidence 0 and algorithm "Unmapped", where `<normalized-name>` is the input identifier lowercased with characters outside `[a-z0-9]` replaced by hyphens and consecutive hyphens collapsed to one.
8. THE License_Mapper SHALL always return a License_Mapping_Result containing: spdx_expression (string, maximum 1024 characters), confidence (integer 0–100), algorithm (one of "ExactSPDX", "DebianAlias", "NormalizedSpelling", "SPDXFullName", "FuzzySimilarity", "LicenseTextHash", or "Unmapped"), and rationale (a non-empty string of at most 512 characters explaining why the algorithm was selected and what input matched).
9. THE License_Mapper SHALL apply mapping algorithms in precedence order: ExactSPDX, DebianAlias, NormalizedSpelling, SPDXFullName, LicenseTextHash, FuzzySimilarity, and SHALL return the result from the first algorithm that produces a match above the minimum confidence threshold.
10. IF the input Debian license identifier is empty or contains only whitespace, THEN THE License_Mapper SHALL return `LicenseRef-debcraft-unknown` with confidence 0 and algorithm "Unmapped" and rationale indicating the input was empty.
11. IF the input Debian license identifier exceeds 512 characters in length, THEN THE License_Mapper SHALL truncate to 512 characters before processing and include a note in the rationale indicating truncation occurred.

### Requirement 8: Resolve Copyright Symlinks

**User Story:** As a compliance engineer, I want symbolic-link copyright paths resolved to their actual target, so that packages sharing copyright files via symlinks have correct license information attributed.

#### Acceptance Criteria

1. WHEN a package's copyright path (`/usr/share/doc/<package>/copyright`) is a symbolic link within the `data.tar` member listing, THE Symlink_Resolver SHALL resolve the link target to determine the actual copyright file path and return the owning package name and its copyright content.
2. WHEN the symlink target is a relative path (e.g. `../other-package/copyright`), THE Symlink_Resolver SHALL resolve it relative to the symlink's directory to produce an absolute path within the filesystem namespace.
3. WHEN the symlink target is an absolute path (e.g. `/usr/share/doc/other-package/copyright`), THE Symlink_Resolver SHALL use it directly without further path manipulation.
4. WHEN the resolved path references another package's documentation directory (e.g. `/usr/share/doc/other-package/copyright`), THE Symlink_Resolver SHALL use file ownership data from the Contents index to identify the owning package and retrieve its copyright content.
5. WHEN the resolved target is itself a symbolic link (multi-hop chain), THE Symlink_Resolver SHALL continue resolving each hop until reaching a non-symlink entry or exceeding the maximum resolution depth of 10 hops.
6. IF the symlink target cannot be resolved (broken link, missing Contents data, or target file not found in any indexed package), THEN THE Symlink_Resolver SHALL return a resolution failure with a descriptive reason rather than raising an unhandled exception.
7. IF the resolution chain exceeds 10 hops or a circular symlink chain is detected (a previously visited path is encountered again), THEN THE Symlink_Resolver SHALL return a resolution failure indicating the circular or excessive-depth condition.

### Requirement 9: Construct Download Locations

**User Story:** As a compliance engineer, I want download URLs constructed for each binary package, so that SBOM documents contain valid SPDX downloadLocation fields.

#### Acceptance Criteria

1. WHEN a binary package has a filename field (e.g. `pool/main/g/glibc/libc6_2.40_amd64.deb`) and belongs to a repository with a known base URL, THE Download_Location_Resolver SHALL construct the full download URL by joining the base URL with the filename such that the result contains exactly one slash separating the base URL authority/path from the filename path.
2. WHEN the repository base URL has a trailing slash, THE Download_Location_Resolver SHALL not produce a double-slash in the resulting URL.
3. WHEN the repository base URL lacks a trailing slash, THE Download_Location_Resolver SHALL insert exactly one slash between the base URL and the filename.
4. IF the package filename field is absent, empty, or contains only whitespace, or if the repository base URL is null or empty, THEN THE Download_Location_Resolver SHALL return the SPDX value `NOASSERTION`.
5. WHEN the package filename field begins with a leading slash, THE Download_Location_Resolver SHALL strip the leading slash before joining it with the base URL so that no double-slash is produced between the base path and the filename.

### Requirement 10: Generate Package URLs (PURL)

**User Story:** As a compliance engineer, I want Package URLs generated for each binary package, so that packages are universally identifiable in SBOM documents using the PURL specification.

#### Acceptance Criteria

1. WHEN a binary package has a non-empty name, non-empty version, and non-empty architecture, THE PURL_Generator SHALL produce a PURL string in the format `pkg:deb/<distro>/<package_name>@<version>?arch=<architecture>`.
2. WHEN the repository origin identifies the distribution (e.g. "debian", "ubuntu", "elxr"), THE PURL_Generator SHALL use its lowercased value as the namespace component of the PURL.
3. WHEN the architecture is "all" (architecture-independent package), THE PURL_Generator SHALL still include `?arch=all` in the PURL qualifier.
4. THE PURL_Generator SHALL percent-encode any special characters in the package name or version according to the PURL specification (e.g. `:` in epoch `1:2.0+dfsg` becomes `%3A`, `+` becomes `%2B`).
5. IF the distribution origin cannot be determined, THEN THE PURL_Generator SHALL use "debian" as the default namespace.
6. IF any required input (package name, version, or architecture) is absent or empty, THEN THE PURL_Generator SHALL raise a descriptive error identifying which field is missing rather than producing a malformed PURL.

### Requirement 11: Permanent Parse Cache

**User Story:** As an operator, I want parsed package metadata cached permanently by SHA256, so that identical packages are never parsed twice across indexing runs.

#### Acceptance Criteria

1. WHEN a `.deb` file is successfully parsed, THE Deb_Parser SHALL store the parsed control metadata, raw copyright text, file listing, and the current parser version integer in the cache keyed by the SHA256 of the `.deb` file.
2. WHEN a `.deb` file with a matching SHA256 already exists in the cache and the stored parser version equals the current parser version, THE Deb_Parser SHALL return the cached result (including the preserved copyright text) without re-extracting the archive.
3. WHEN the parser version changes (logic update that affects output), THE Deb_Parser SHALL treat cache entries created by a previous parser version as invalid and re-parse the `.deb` file on next access.
4. THE cache SHALL store entries in the cache database (`cache.db`) using SQLAlchemy models consistent with the existing storage layer patterns.
5. IF parsing of a `.deb` file fails, THEN THE Deb_Parser SHALL NOT store an entry in the cache for that SHA256, so that the file is re-attempted on subsequent indexing runs.

### Requirement 12: Architectural Compliance

**User Story:** As a developer, I want the package intelligence components to follow clean architecture boundaries, so that domain logic remains independent of infrastructure.

#### Acceptance Criteria

1. THE DEP5_Parser SHALL reside in the domain layer (`src/debcraft/domain/`) and SHALL NOT import from the infrastructure layer (`src/debcraft/infrastructure/`).
2. THE SPDX_Expression_Parser SHALL reside in the domain layer and SHALL NOT import from the infrastructure layer.
3. THE SPDX_Tokenizer SHALL reside in the domain layer and SHALL NOT import from the infrastructure layer.
4. THE License_Mapper SHALL reside in the domain layer and SHALL NOT import from the infrastructure layer.
5. THE Deb_Parser SHALL receive file system and cache dependencies through constructor injection using Protocol-typed parameters rather than accessing global state.
6. THE Symlink_Resolver SHALL receive Contents data access through a port interface (Protocol) rather than directly querying the database.
7. THE Download_Location_Resolver SHALL be a pure function operating on domain value objects with no infrastructure dependencies.
8. THE PURL_Generator SHALL be a pure function operating on domain value objects with no infrastructure dependencies.
9. THE import-linter architecture contract tests SHALL pass for all package intelligence modules, verifying no prohibited cross-layer imports exist.
