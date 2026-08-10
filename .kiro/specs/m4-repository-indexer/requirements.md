# Requirements Document

## Introduction

The Repository Indexer parses Debian repository metadata files (Packages, Sources, Contents, Release) from the local mirror cache and persists structured package metadata into the metadata SQLite database (`metadata.db`). It transforms raw repository index files into queryable domain objects, supporting multiple versions, architectures, and repositories with incremental indexing to avoid redundant work.

## Glossary

- **Indexer**: The domain service that orchestrates parsing of cached repository metadata files and produces domain objects for persistence.
- **Packages_Parser**: The domain parser that extracts full package metadata from decompressed Packages index file content into PackageMetadata value objects.
- **Sources_Parser**: The domain parser that extracts source package metadata from decompressed Sources index file content into SourcePackageMetadata value objects.
- **Contents_Parser**: The domain parser that extracts file-ownership mappings from decompressed Contents index file content into FileOwnership value objects.
- **Release_Metadata_Parser**: The domain parser that extracts repository-level metadata (suite, codename, architectures, components) from Release file content.
- **PackageMetadata**: A domain value object representing a single binary package entry with all extracted fields (name, version, architecture, sha256, source, depends, etc.).
- **SourcePackageMetadata**: A domain value object representing a single source package entry with name, version, maintainer, and build metadata.
- **FileOwnership**: A domain value object mapping a filesystem path to the package that owns it, extracted from Contents files.
- **Metadata_Database**: The SQLite database (`metadata.db`) storing authoritative package metadata via PackageInstance, SourcePackage, Repository, and RepositorySnapshot tables.
- **Mirror_Database**: The SQLite database (`mirror.db`) tracking repository files with download states and local cache paths.
- **RepositoryFile**: An infrastructure model in mirror.db tracking a downloaded repository metadata file with its local_path and state.
- **Index_Session**: A logical unit of work representing one indexing run for a repository, producing a RepositorySnapshot.
- **Parser_Version**: An integer identifying the current version of a parser's extraction logic, used to invalidate cached results when parsing logic changes.

## Requirements

### Requirement 1: Parse Packages Files

**User Story:** As a compliance engineer, I want binary package metadata extracted from Packages index files, so that I can query package details across repositories.

#### Acceptance Criteria

1. WHEN a decompressed Packages file content is provided, THE Packages_Parser SHALL parse each package stanza into a PackageMetadata value object containing: package_name, version, architecture, filename, sha256, size_bytes, source_package, source_version, homepage, maintainer, depends, provides, section, priority, and description.
2. WHEN a package stanza is missing the Package, Version, Architecture, Filename, SHA256, or Size field, THE Packages_Parser SHALL skip that stanza and log a debug message identifying the package name and missing fields.
3. WHEN a package stanza contains a Source field with a version in parentheses (e.g. `pkg (1.0-1)`), THE Packages_Parser SHALL extract the source package name and source version separately.
4. WHEN a package stanza contains a Source field without a version, THE Packages_Parser SHALL use the binary package version as the source version.
5. WHEN a package stanza omits the Source field entirely, THE Packages_Parser SHALL use the binary package name as the source package name and the binary version as the source version.
6. FOR ALL valid PackageMetadata objects, parsing then formatting then parsing SHALL produce an equivalent object (round-trip property).

### Requirement 2: Parse Sources Files

**User Story:** As a compliance engineer, I want source package metadata extracted from Sources index files, so that I can trace binary packages back to their build origins.

#### Acceptance Criteria

1. WHEN a decompressed Sources file content is provided, THE Sources_Parser SHALL parse each source stanza into a SourcePackageMetadata value object containing: name, version, maintainer, uploaders, section, homepage, build_depends, and binary_packages.
2. WHEN a source stanza is missing the Package or Version field, THE Sources_Parser SHALL skip that stanza and log a debug message.
3. WHEN a source stanza contains a Binary field listing multiple package names, THE Sources_Parser SHALL split the comma-separated list and store each name trimmed of whitespace.
4. FOR ALL valid SourcePackageMetadata objects, parsing then formatting then parsing SHALL produce an equivalent object (round-trip property).

### Requirement 3: Parse Contents Files

**User Story:** As a compliance engineer, I want file-to-package ownership mappings extracted from Contents files, so that copyright files can be resolved to their owning packages.

#### Acceptance Criteria

1. WHEN a decompressed Contents file content is provided, THE Contents_Parser SHALL parse each line into a FileOwnership value object containing the filesystem path and the qualified package name (section/package_name).
2. WHEN a Contents line maps a single path to multiple packages (comma-separated), THE Contents_Parser SHALL produce one FileOwnership record per package for that path.
3. WHEN a Contents line is malformed (fewer than two whitespace-separated columns), THE Contents_Parser SHALL skip that line and log a debug message.
4. THE Contents_Parser SHALL handle Contents files with or without the initial header section (lines before the first file entry).

### Requirement 4: Extract Repository Metadata from Release Files

**User Story:** As a compliance engineer, I want repository-level metadata (suite, codename, components) extracted from Release files, so that repository identity is captured alongside package data.

#### Acceptance Criteria

1. WHEN a Release file content is provided, THE Release_Metadata_Parser SHALL extract the suite, codename, origin, label, architectures, components, and date fields into a structured result.
2. WHEN the Release file is missing the Suite field, THE Release_Metadata_Parser SHALL fall back to the Codename field as the suite identifier.
3. WHEN both Suite and Codename fields are absent, THE Release_Metadata_Parser SHALL return an error indicating insufficient repository identity.

### Requirement 5: Incremental Indexing

**User Story:** As an operator, I want the indexer to skip repository files that have already been parsed with the current parser version, so that repeated indexing runs complete quickly.

#### Acceptance Criteria

1. WHEN a RepositoryFile has state INDEXED and the SHA256 of the cached file matches the recorded SHA256 and the current Parser_Version matches the version used for previous indexing, THE Indexer SHALL skip that file without re-parsing.
2. WHEN a RepositoryFile has state VERIFIED and has not been indexed previously, THE Indexer SHALL parse the file and transition its state to INDEXED upon successful completion.
3. WHEN the Parser_Version has been incremented since the last indexing of a file, THE Indexer SHALL re-parse that file regardless of SHA256 match.
4. WHEN multiple repository files are pending indexing, THE Indexer SHALL process them in deterministic order (sorted by repository name, then file type, then path).

### Requirement 6: Persist Package Instances

**User Story:** As a compliance engineer, I want parsed binary package metadata stored in the metadata database, so that packages are queryable across repositories and snapshots.

#### Acceptance Criteria

1. WHEN a PackageMetadata value object is produced by the Packages_Parser, THE Indexer SHALL create or update a PackageInstance record in the Metadata_Database with all extracted fields.
2. WHEN a PackageInstance with the same natural key (package_name, version, architecture, filename) already exists in the current snapshot, THE Indexer SHALL skip insertion of that duplicate.
3. THE Indexer SHALL store the additional metadata fields (source_package, source_version, homepage, maintainer, depends, provides, section, priority, description) on each PackageInstance record.
4. WHEN a PackageInstance is created, THE Indexer SHALL compute the download_url by joining the repository base_url with the package filename field.

### Requirement 7: Persist Source Packages

**User Story:** As a compliance engineer, I want source packages stored as first-class entities, so that binary-to-source traceability is maintained.

#### Acceptance Criteria

1. WHEN a binary package references a source package (via Source field or inferred name), THE Indexer SHALL create a SourcePackage record with the name, version, and maintainer if one does not already exist with that natural key (name, version).
2. WHEN a SourcePackageMetadata is produced by the Sources_Parser, THE Indexer SHALL create or update the SourcePackage record with the parsed maintainer field.
3. WHEN a SourcePackage with the same natural key already exists, THE Indexer SHALL retain the existing record without modification.

### Requirement 8: Manage Repository and Snapshot Records

**User Story:** As an operator, I want each indexing run to produce an immutable snapshot linked to its repository, so that I can track repository state over time.

#### Acceptance Criteria

1. WHEN indexing begins for a repository, THE Indexer SHALL find or create a Repository record matching the repository name, base_url, suite, and component.
2. WHEN indexing begins for a repository, THE Indexer SHALL create a new RepositorySnapshot record with the current timestamp, the active schema_version, and published set to false.
3. WHEN all packages for a snapshot have been successfully indexed, THE Indexer SHALL set the RepositorySnapshot published field to true.
4. IF indexing fails partway through a snapshot, THEN THE Indexer SHALL leave the RepositorySnapshot with published set to false and log the failure reason.

### Requirement 9: Persist File Ownership Records

**User Story:** As a compliance engineer, I want file-to-package ownership mappings stored in the database, so that M5 copyright symlink resolution can identify which package owns a file path.

#### Acceptance Criteria

1. WHEN FileOwnership value objects are produced by the Contents_Parser, THE Indexer SHALL persist each mapping as a record associating the filesystem path with the owning package name and the current snapshot.
2. WHEN a file path is owned by multiple packages, THE Indexer SHALL store one record per package for that path.
3. THE Indexer SHALL replace all file ownership records for a repository snapshot on each indexing run rather than appending incrementally.

### Requirement 10: Event Bus Integration

**User Story:** As a platform operator, I want the indexer to publish lifecycle events, so that other components can react to indexing progress.

#### Acceptance Criteria

1. WHEN indexing begins for a repository, THE Indexer SHALL publish an IndexingStarted event containing the repository name and snapshot ID.
2. WHEN indexing completes successfully for a repository, THE Indexer SHALL publish an IndexingCompleted event containing the repository name, snapshot ID, and count of packages indexed.
3. IF indexing fails for a repository, THEN THE Indexer SHALL publish an IndexingFailed event containing the repository name, snapshot ID, and error description.

### Requirement 11: CLI Commands

**User Story:** As an operator, I want CLI commands to trigger indexing and inspect package metadata, so that I can manage the indexing workflow from the terminal.

#### Acceptance Criteria

1. WHEN the operator invokes `debcraft index`, THE CLI SHALL index all repositories that have VERIFIED files in the mirror cache.
2. WHEN the operator invokes `debcraft index --repository <name>`, THE CLI SHALL index only the specified repository.
3. WHEN the operator invokes `debcraft index` and no VERIFIED files exist, THE CLI SHALL display a message indicating no files are available for indexing.
4. WHEN the operator invokes `debcraft package <name>`, THE CLI SHALL display the latest indexed metadata for the named package including version, architecture, source package, and repository membership.
5. WHEN the operator invokes `debcraft package <name>` and the package does not exist in the metadata database, THE CLI SHALL display a message indicating the package was not found.

### Requirement 12: Architectural Compliance

**User Story:** As a developer, I want the indexer to follow clean architecture boundaries, so that the domain layer remains independent of infrastructure.

#### Acceptance Criteria

1. THE Packages_Parser SHALL reside in the domain layer and SHALL NOT import from the infrastructure layer.
2. THE Sources_Parser SHALL reside in the domain layer and SHALL NOT import from the infrastructure layer.
3. THE Contents_Parser SHALL reside in the domain layer and SHALL NOT import from the infrastructure layer.
4. THE Indexer SHALL receive all infrastructure dependencies (database sessions, file readers) through constructor injection.
5. THE Indexer SHALL produce domain value objects as output; conversion to SQLAlchemy models SHALL occur in the infrastructure persistence layer.
