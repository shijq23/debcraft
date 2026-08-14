# Requirements Document

## Introduction

This feature wires the existing SBOM CLI command (`debcraft sbom`) to use the M5 package intelligence indexes for enriching scanned packages. Currently, all packages in generated SBOM documents show "NOASSERTION" for license, supplier, download location, and other metadata fields because the enrichment step is completely bypassed. The CLI uses a `_NoOpCacheAdapter` and never resolves a valid `snapshot_id`, so the `MetadataEnricher` skips enrichment entirely.

This feature connects the CLI path to the real `EnrichmentCacheAdapter` (backed by cache.db via SQLAlchemy), resolves the latest published `RepositorySnapshot` from metadata.db, and implements a fallback query against the `PackageInstance` table when the enrichment cache misses. The result is that SBOM documents contain real metadata values (license expressions, download URLs, PURLs, maintainer information) instead of "NOASSERTION" placeholders.

## Glossary

- **CLI**: The `debcraft sbom` command-line interface entry point defined in `src/debcraft/cli/sbom.py`.
- **Enrichment_Cache**: The SQLAlchemy-backed cache (cache.db) storing `CachedEnrichment` records keyed by (package_name, version, architecture, snapshot_id).
- **Metadata_DB**: The SQLAlchemy-backed metadata database (metadata.db) containing `RepositorySnapshot`, `PackageInstance`, and `LicenseExpression` records.
- **MetadataEnricher**: The domain service in `src/debcraft/infrastructure/scanners/enricher.py` that enriches identified packages with metadata from the cache and repository.
- **RepositorySnapshot**: An immutable point-in-time capture of repository state in metadata.db, with a `published` flag indicating readiness for use.
- **PackageInstance**: A binary package record in metadata.db identified by (package_name, version, architecture, filename) linked to a RepositorySnapshot.
- **PackageEnrichment**: A domain value object carrying enrichment metadata (license, download URL, PURL, maintainer, etc.).
- **EnrichmentCacheAdapter**: The SQLAlchemy adapter class that reads/writes `PackageEnrichment` data from/to cache.db.
- **Snapshot_ID**: An integer identifier for a published `RepositorySnapshot`. A value of 0 means "skip enrichment".
- **PURL**: Package URL — a standardized identifier for software packages.

## Requirements

### Requirement 1: Snapshot ID Resolution

**User Story:** As a user running `debcraft sbom`, I want the CLI to automatically resolve the latest published RepositorySnapshot ID from metadata.db, so that enrichment runs without requiring manual configuration.

#### Acceptance Criteria

1. WHEN the `debcraft sbom` command is invoked without a `--snapshot-id` flag, THE CLI SHALL query metadata.db for the published RepositorySnapshot with the highest `id` value and use that `id` as the Snapshot_ID for enrichment.
2. WHEN the `debcraft sbom` command is invoked with a `--snapshot-id <id>` flag, THE CLI SHALL use the provided value as the Snapshot_ID for enrichment, accepting any positive integer in the range 1 to 2,147,483,647.
3. IF no published RepositorySnapshot exists in metadata.db and no `--snapshot-id` flag is provided, THEN THE CLI SHALL set Snapshot_ID to 0, log a warning message at WARNING level indicating that enrichment is skipped due to no published snapshot, and proceed with SBOM generation without enrichment.
4. IF the `--snapshot-id` flag is provided with a value that does not correspond to a published RepositorySnapshot, THEN THE CLI SHALL log a warning at WARNING level indicating the snapshot was not found and proceed using the provided Snapshot_ID without validating existence.
5. IF the `--snapshot-id` flag is provided with a value that is not a positive integer, THEN THE CLI SHALL exit with a non-zero exit code and display an error message indicating the expected format.

### Requirement 2: Real Enrichment Cache Wiring

**User Story:** As a user running `debcraft sbom`, I want the CLI to use the real SQLAlchemy-backed EnrichmentCacheAdapter connected to cache.db, so that enrichment results are cached and reused across runs.

#### Acceptance Criteria

1. WHEN the CLI creates the DI scope for SBOM generation, THE CLI SHALL create an async SQLAlchemy session factory pointing to the cache.db file resolved via the XDG-compliant storage path (`$XDG_CACHE_HOME/debcraft/cache.db`, defaulting to `~/.cache/debcraft/cache.db`) and pass it to the `EnrichmentCacheAdapter` constructor.
2. WHEN the CLI creates the DI scope for SBOM generation, THE CLI SHALL pass the `EnrichmentCacheAdapter` instance to the `MetadataEnricher` constructor instead of the `_NoOpCacheAdapter`.
3. IF cache.db does not exist at the expected path, THEN THE CLI SHALL create the cache.db file and apply the `CachedEnrichment` table schema (via SQLAlchemy `metadata.create_all`) before instantiating the `EnrichmentCacheAdapter`.
4. IF the cache.db connection fails during DI scope creation (e.g., permission denied, disk I/O error, or SQLAlchemy engine creation raises an exception), THEN THE CLI SHALL fall back to using the `_NoOpCacheAdapter`, log a warning message indicating that caching is unavailable and including the failure reason, and continue SBOM generation without interruption.
5. WHEN the `EnrichmentCacheAdapter` is successfully instantiated, THE CLI SHALL register it in the DI scope so that the `MetadataEnricher` resolved from the scope uses the real cache adapter for enrichment lookups during that SBOM generation run.

### Requirement 3: Metadata DB Fallback Lookup

**User Story:** As a user running `debcraft sbom`, I want the enricher to fall back to querying metadata.db directly when the enrichment cache misses, so that packages get enrichment data even on the first run before the cache is populated.

#### Acceptance Criteria

1. WHEN the MetadataEnricher encounters a cache miss for a package and Snapshot_ID is greater than 0, THE MetadataEnricher SHALL query the `PackageInstance` table in metadata.db using (package_name, version, architecture, snapshot_id) to find matching enrichment data.
2. WHEN multiple PackageInstance records match the query (same name, version, architecture, snapshot_id), THE MetadataEnricher SHALL use the record with the highest `id` value (most recently inserted).
3. WHEN a matching PackageInstance is found in metadata.db, THE MetadataEnricher SHALL construct a `PackageEnrichment` value object from the PackageInstance fields (source_package, maintainer, homepage, depends, section, priority, description, sha256, download_url) and the associated `LicenseExpression` records.
4. WHEN a PackageEnrichment is successfully constructed from metadata.db, THE MetadataEnricher SHALL store the result in the Enrichment_Cache for future lookups. IF the cache store operation fails, THE MetadataEnricher SHALL log a warning and still return the constructed PackageEnrichment to the caller.
5. IF no matching PackageInstance is found in metadata.db for a given (package_name, version, architecture, snapshot_id), THEN THE MetadataEnricher SHALL return None enrichment for that package and record a diagnostic message containing the package name, version, and architecture.
6. IF the metadata.db query fails due to a database error, THEN THE MetadataEnricher SHALL log a warning including the exception message, return None enrichment for that package, and continue processing remaining packages without interruption.

### Requirement 4: PURL Generation

**User Story:** As a user generating SBOMs, I want each enriched package to include a valid Package URL (PURL), so that SBOM consumers can unambiguously identify packages.

#### Acceptance Criteria

1. WHEN a PackageEnrichment is constructed from a PackageInstance, THE MetadataEnricher SHALL generate a PURL using the existing `generate_purl` function from `debcraft.domain.package_intelligence.purl_generator` with the package_name, version, and architecture, and include the result in the PackageEnrichment `purl` field.
2. WHEN the `generate_purl` function raises a `PURLGenerationError` (due to missing or empty package_name, version, or architecture), THE MetadataEnricher SHALL set the `purl` field to None and log a debug-level message noting the PURL generation failure.
3. WHEN the PackageInstance has a download_url field populated, THE MetadataEnricher SHALL include the download_url value in the PackageEnrichment `download_url` field.
4. WHEN the PackageInstance has associated LicenseExpression records, THE MetadataEnricher SHALL include all (expression, source) pairs in the PackageEnrichment `license_expressions` list.

### Requirement 5: Workflow Configuration Integration

**User Story:** As a user running `debcraft sbom`, I want the resolved Snapshot_ID to flow through to the SBOMWorkflowConfig, so that the enrichment step uses the correct snapshot for lookups.

#### Acceptance Criteria

1. WHEN the CLI constructs the `SBOMWorkflowConfig`, THE CLI SHALL set the `snapshot_id` field to the resolved Snapshot_ID value (either auto-detected or from the `--snapshot-id` flag), defaulting to 0 if no Snapshot_ID was resolved.
2. WHEN the `SBOMWorkflow` executes the enrichment step, THE SBOMWorkflow SHALL pass the configured `snapshot_id` to the `MetadataEnricher.enrich()` method as the `snapshot_id` parameter.
3. IF the `snapshot_id` is 0, THEN THE MetadataEnricher SHALL skip both cache lookups and metadata.db queries, return all packages with None enrichment, and include a diagnostic message indicating that enrichment was skipped due to no available snapshot.

### Requirement 6: Database Session Management

**User Story:** As a developer, I want the CLI to properly manage SQLAlchemy async sessions for both cache.db and metadata.db, so that database connections are created efficiently and cleaned up after use.

#### Acceptance Criteria

1. WHEN the CLI initializes database connections, THE CLI SHALL create separate async engines for cache.db and metadata.db using file paths resolved from the XDG data directory.
2. WHEN the SBOM workflow completes (successfully or with an error), THE CLI SHALL dispose of all async database engines to release file handles and connections.
3. THE CLI SHALL use async session factories (not raw sessions) for both database connections to support concurrent enrichment lookups.
4. IF the metadata.db file does not exist at the resolved path, THEN THE CLI SHALL set snapshot_id to 0 and log a warning indicating that the metadata database is unavailable.
5. IF the cache.db file does not exist at the resolved path, THEN THE CLI SHALL create the cache.db file and initialize its schema before returning a session factory.

### Requirement 7: Enrichment Data Flow to SBOM Writers

**User Story:** As a user generating SBOMs, I want enriched metadata to appear in the final SBOM documents, so that packages show real license, supplier, and download values instead of "NOASSERTION".

#### Acceptance Criteria

1. WHEN a package has a non-None PackageEnrichment with a non-empty license_expressions list, THE ModelAssembler SHALL use the SPDX expression string (first element of the first tuple) from license_expressions as both the package's concluded_license and declared_license fields in the SBOMPackage.
2. WHEN a package has a non-None PackageEnrichment with a non-None download_url, THE ModelAssembler SHALL set the SBOMPackage download_location field to the value of download_url.
3. WHEN a package has a non-None PackageEnrichment with a non-None maintainer, THE ModelAssembler SHALL set the SBOMPackage supplier field to the value of maintainer.
4. WHEN a package has a non-None PackageEnrichment with a non-None purl, THE ModelAssembler SHALL set the SBOMPackage package_url field to the purl value and add an external reference with category PACKAGE_MANAGER and the purl as the URL.
5. WHEN a package has None enrichment or a PackageEnrichment where a given field is None (or license_expressions is empty), THE ModelAssembler SHALL leave the corresponding SBOMPackage fields as None, and the SBOM writer layer SHALL serialize those None fields as the format-appropriate "NOASSERTION" sentinel (the string "NOASSERTION" for SPDX 2.3, or the NoAssertionElement IRI for SPDX 3.0).
6. WHEN a package has a non-None PackageEnrichment with a non-empty license_expressions list containing only one tuple, THE ModelAssembler SHALL set concluded_license and declared_license to the same single SPDX expression string (first element of that tuple), not duplicate entries.

### Requirement 8: Direct .deb Extraction Fallback

**User Story:** As a user running `debcraft sbom` on an ISO without a pre-built repository index, I want the enricher to extract metadata directly from `.deb` files found in the ISO's pool directory, so that packages still get real license, maintainer, and download data even when no RepositorySnapshot exists.

#### Acceptance Criteria

1. WHEN the Snapshot_ID is 0 (no published RepositorySnapshot available) and the scanned artifact is an ISO containing a repository structure with a `pool/` directory, THE MetadataEnricher SHALL attempt to extract enrichment metadata directly from the `.deb` files within the ISO for each identified package.
2. WHEN a `.deb` file matching an identified package (by name, version, architecture) is located in the ISO's pool directory, THE MetadataEnricher SHALL use the `DebParser` to parse the `.deb` archive, extracting control fields (maintainer, homepage, depends, section, priority, description) and the copyright file content.
3. WHEN the `DebParser` successfully extracts a copyright file from a `.deb` archive, THE MetadataEnricher SHALL use the `DEP5Parser` to parse the copyright into structured license paragraphs. IF the copyright is in DEP5 format, THE MetadataEnricher SHALL use the `LicenseMapper` to map the Debian license identifiers to SPDX expressions and include them in the PackageEnrichment `license_expressions` list.
4. WHEN the copyright file is not in DEP5 format (free-form text), THE MetadataEnricher SHALL attempt to use the `LicenseMapper` with the raw license text and include the resulting SPDX expression in the PackageEnrichment `license_expressions` list. IF no license can be determined, the `license_expressions` list SHALL remain empty for that package.
5. WHEN a `.deb` file is successfully parsed, THE MetadataEnricher SHALL generate a PURL using the `generate_purl` function with the package_name, version, and architecture extracted from the control file, and include it in the PackageEnrichment `purl` field.
6. IF no `.deb` file matching an identified package can be found in the ISO's pool directory, THEN THE MetadataEnricher SHALL return None enrichment for that package and record a diagnostic message indicating the `.deb` was not found.
7. IF parsing a `.deb` file fails (corrupt archive, missing control file, etc.), THEN THE MetadataEnricher SHALL log a warning, return None enrichment for that package, and continue processing remaining packages.
8. WHEN the direct .deb extraction fallback is active, THE MetadataEnricher SHALL read `.deb` files from within the ISO using an adapter that reads from the ISO filesystem (via the ISOReader) rather than requiring files to be extracted to the local filesystem first.
9. WHEN both a RepositorySnapshot lookup (Requirement 3) and direct .deb extraction are available for a package, THE MetadataEnricher SHALL prefer the RepositorySnapshot data and only fall back to .deb extraction when the snapshot lookup returns no match.
