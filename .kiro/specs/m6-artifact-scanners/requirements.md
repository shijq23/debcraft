# Requirements Document

## Introduction

Artifact Scanners (Milestone 6) implements the scanning subsystem of DebCraft that identifies installed Debian packages inside various artifact types. Scanners use a hybrid approach: prefer package manager metadata (the dpkg status database at `/var/lib/dpkg/status`) and fall back to filesystem analysis when metadata is unavailable. The subsystem supports scanning directories, Docker image tarballs, OCI image layouts, ISO 9660 images, QCOW2 virtual machine disks, raw disk images, and AWS AMI images. All scanners implement a common `ArtifactScanner` protocol interface, produce a uniform scan result model, and operate without root privileges.

## Glossary

- **Artifact_Scanner**: The protocol interface that all scanner implementations conform to, defining the `scan(artifact, context) -> ScanResult` method signature.
- **Scan_Result**: A value object produced by every scanner containing the list of identified packages, the scanning strategy used, diagnostic messages, and timing metadata.
- **Artifact**: A value object describing the target to be scanned, including its type, path, and optional configuration parameters.
- **Artifact_Type**: An enumeration of supported artifact formats: Directory, Docker, OCI, ISO, QCOW2, IMG, and AMI.
- **Dpkg_Status_Parser**: The domain parser that reads a dpkg status file (`/var/lib/dpkg/status`) and extracts installed package entries into structured metadata.
- **Dpkg_Status_Printer**: The domain serializer that formats parsed dpkg status entries back into valid dpkg status file text representation.
- **Identified_Package**: A value object representing a single package found during scanning, containing package name, version, architecture, and installation status.
- **Scanner_Registry**: The plugin registry that discovers and manages available scanner implementations via importlib.metadata entry points.
- **Directory_Scanner**: The scanner implementation for local directory (rootfs) artifacts.
- **Docker_Scanner**: The scanner implementation for Docker image tarball artifacts (docker save format).
- **OCI_Scanner**: The scanner implementation for OCI image layout directory artifacts.
- **ISO_Scanner**: The scanner implementation for ISO 9660 image artifacts.
- **QCOW2_Scanner**: The scanner implementation for QCOW2 virtual machine disk image artifacts.
- **IMG_Scanner**: The scanner implementation for raw disk image artifacts.
- **AMI_Scanner**: The scanner implementation for AWS AMI artifacts (raw or QCOW2 format).
- **Scanning_Strategy**: An enumeration indicating how packages were identified: "dpkg_metadata" when the dpkg status file was found, or "filesystem_analysis" when falling back to file-based heuristics.
- **WorkflowContext**: The platform context object providing scoped dependency injection, cooperative cancellation, progress reporting, resource management, logging, and event publishing.
- **Filesystem_Analyzer**: The fallback domain service that identifies packages by matching filesystem paths against the Contents index when dpkg metadata is unavailable.

## Requirements

### Requirement 1: ArtifactScanner Protocol Interface

**User Story:** As a platform developer, I want a common scanner protocol interface, so that all artifact scanners are interchangeable and new scanners can be added without modifying existing code.

#### Acceptance Criteria

1. THE Artifact_Scanner SHALL define an async method with signature `scan(self, artifact: Artifact, context: WorkflowContext) -> Scan_Result` as its sole public contract.
2. THE Artifact_Scanner SHALL be defined as a Python Protocol class in the domain layer, allowing structural subtyping without requiring inheritance.
3. THE Artifact type SHALL be a frozen dataclass containing: a `type` field of type Artifact_Type (an enumeration identifying the artifact format), a `path` field containing the filesystem path to the artifact (string, maximum 4096 characters), and an optional `options` dictionary (string keys to string values) for scanner-specific configuration with a maximum of 64 entries.
4. THE Scan_Result type SHALL be a frozen dataclass containing: a list of Identified_Package entries (zero or more), the Scanning_Strategy used (a string identifying the scanning approach that produced this result), a list of diagnostic messages (each a string, zero or more entries), the scan duration as a non-negative float in seconds, and the artifact path that was scanned.
5. THE Identified_Package type SHALL be a frozen dataclass containing: package name (non-empty string), version (non-empty string), architecture (non-empty string), and status (a string restricted to one of: "installed", "config-files", "half-installed", "unpacked", "half-configured", "triggers-awaited", "triggers-pending", or "not-installed").
6. WHEN a scanner implementation structurally satisfies the Artifact_Scanner protocol, THE scanner SHALL be independently testable by providing a mock WorkflowContext and an Artifact value object without requiring other scanners or infrastructure.
7. THE Artifact_Scanner protocol SHALL be stateless such that calling `scan` multiple times with the same Artifact and WorkflowContext produces Scan_Result values with identical Identified_Package lists, identical Scanning_Strategy, and identical artifact path fields.
8. IF the artifact path does not exist or is not readable, THEN THE Artifact_Scanner SHALL raise a domain-specific error indicating the inaccessible path, without returning a partial Scan_Result.
9. THE Artifact_Type enumeration SHALL define at least one member representing a supported artifact format, and SHALL be extensible by adding new members without modifying existing scanner implementations.

### Requirement 2: Parse dpkg Status Files

**User Story:** As a scanner developer, I want a dpkg status file parser, so that installed packages can be reliably extracted from any artifact containing Debian package metadata.

#### Acceptance Criteria

1. WHEN a valid dpkg status file content (text) is provided, THE Dpkg_Status_Parser SHALL split the content into stanzas delimited by one or more consecutive blank lines and parse each stanza into an Identified_Package entry by extracting the Package, Version, Architecture, and Status fields.
2. WHEN a stanza in the dpkg status file has a Status field containing "install ok installed", THE Dpkg_Status_Parser SHALL classify that package as status "installed".
3. WHEN a stanza in the dpkg status file has a Status field containing "deinstall" or "purge" as the desired action (first word of the Status field), THE Dpkg_Status_Parser SHALL exclude that package from the result list.
4. WHEN a stanza contains continuation lines (lines starting with a space or tab), THE Dpkg_Status_Parser SHALL append each continuation line to the value of the preceding field, joining with a newline character and preserving the continuation line content after the leading whitespace character, so that multiline field values are captured without corrupting adjacent fields.
5. IF a stanza is missing the Package field or the Version field, THEN THE Dpkg_Status_Parser SHALL skip that stanza and record a diagnostic warning that includes the 1-based stanza index and the name of each missing field.
6. IF the dpkg status file content is empty or contains only whitespace, THEN THE Dpkg_Status_Parser SHALL return an empty list of packages without raising an error.
7. WHEN the dpkg status file contains stanzas with "config-files" in the Status field (third word is "config-files"), THE Dpkg_Status_Parser SHALL include those packages with status "config-files" in the result list.
8. THE Dpkg_Status_Parser SHALL process stanzas sequentially without loading all stanza objects into memory simultaneously, supporting dpkg status files of at least 10,000 stanzas without requiring memory proportional to the total number of stanzas squared.
9. WHEN a stanza has a Status field whose desired action (first word) is "install" or "hold" but whose current state (third word) is neither "installed" nor "config-files", THEN THE Dpkg_Status_Parser SHALL exclude that package from the result list and record a diagnostic message noting the unrecognized installation state.
10. IF a stanza is missing the Architecture field but contains valid Package and Version fields, THEN THE Dpkg_Status_Parser SHALL include that package in the result list with an empty string as the architecture value.

### Requirement 3: dpkg Status Pretty Printer (Round-Trip)

**User Story:** As a developer, I want a serializer for dpkg status entries, so that round-trip correctness of the parser can be verified.

#### Acceptance Criteria

1. THE Dpkg_Status_Printer SHALL format a list of parsed dpkg status stanzas back into valid dpkg status file text by emitting each stanza's fields as `Field-Name: value` lines separated by a newline, with stanzas separated by exactly one blank line, and the output SHALL end with exactly one trailing newline character after the final stanza.
2. THE Dpkg_Status_Printer SHALL format multiline field values using continuation-line syntax where each continuation line is prefixed with a single space character, and empty lines within a multiline value SHALL be emitted as a continuation line containing only a space followed by a dot (` .`).
3. FOR ALL valid dpkg status files, WHEN text is parsed by Dpkg_Status_Parser into stanzas and then formatted by Dpkg_Status_Printer and then parsed again by Dpkg_Status_Parser, THE resulting list of Identified_Package entries SHALL be equal to the original parsed result, where equality means identical values for package name, version, architecture, and status fields in the same order.
4. THE Dpkg_Status_Printer SHALL preserve field ordering within each stanza as encountered during parsing.
5. IF the Dpkg_Status_Printer receives an empty list of stanzas, THEN THE Dpkg_Status_Printer SHALL return an empty string.

### Requirement 4: Directory Scanner

**User Story:** As a security analyst, I want to scan a local directory (rootfs), so that I can identify all installed Debian packages in filesystem images and build outputs.

#### Acceptance Criteria

1. WHEN a Directory artifact with a valid path to an existing directory is provided, THE Directory_Scanner SHALL look for the dpkg status file at `<path>/var/lib/dpkg/status` and parse it to identify installed packages.
2. WHEN the dpkg status file exists and is readable at the expected location, THE Directory_Scanner SHALL set the Scanning_Strategy to "dpkg_metadata" in the Scan_Result.
3. WHEN the dpkg status file does not exist at `<path>/var/lib/dpkg/status`, THE Directory_Scanner SHALL fall back to the Filesystem_Analyzer and set the Scanning_Strategy to "filesystem_analysis".
4. IF the directory path does not exist or is not accessible, THEN THE Directory_Scanner SHALL return a Scan_Result with an empty package list and a diagnostic message indicating the path that failed and the nature of the access failure.
5. WHILE the scan is in progress, THE Directory_Scanner SHALL check the WorkflowContext cancellation token before processing each package entry and SHALL terminate early with a partial Scan_Result containing only the packages processed so far if cancellation is requested.
6. WHEN the scan completes successfully, THE Directory_Scanner SHALL report 100% progress through the WorkflowContext progress reporter with a message indicating the total number of packages identified.
7. THE Directory_Scanner SHALL not follow symbolic links that resolve to a path outside the artifact root directory; such links SHALL be silently skipped during scanning to prevent path traversal attacks.
8. IF the dpkg status file exists at `<path>/var/lib/dpkg/status` but is not readable, THEN THE Directory_Scanner SHALL fall back to the Filesystem_Analyzer and set the Scanning_Strategy to "filesystem_analysis".

### Requirement 5: Docker Image Scanner

**User Story:** As a security analyst, I want to scan Docker image tarballs, so that I can identify installed packages in container images exported via `docker save`.

#### Acceptance Criteria

1. WHEN a Docker artifact with a valid path to a Docker image tarball (tar format) is provided, THE Docker_Scanner SHALL read the `manifest.json` to identify image layers, extract the layer tarballs, and locate the dpkg status file within the merged layer filesystem.
2. WHEN multiple layers are present in the Docker image, THE Docker_Scanner SHALL apply layers in order (bottom to top) so that upper layers override files from lower layers, including the dpkg status file.
3. WHEN a layer contains a whiteout file (`.wh.<filename>` marker), THE Docker_Scanner SHALL treat the corresponding file as deleted in the merged view. WHEN a layer contains an opaque whiteout marker (`.wh..wh..opq`), THE Docker_Scanner SHALL treat all sibling files in that directory from lower layers as deleted.
4. WHEN the merged layer filesystem contains a dpkg status file at `var/lib/dpkg/status`, THE Docker_Scanner SHALL parse it and set the Scanning_Strategy to "dpkg_metadata".
5. WHEN the merged layer filesystem does not contain a dpkg status file, THE Docker_Scanner SHALL fall back to the Filesystem_Analyzer and set the Scanning_Strategy to "filesystem_analysis".
6. IF the tarball is not a valid Docker image (missing manifest.json, invalid tar format, or the file path does not exist or is not accessible), THEN THE Docker_Scanner SHALL return a Scan_Result with an empty package list and a diagnostic message describing the error.
7. WHILE extracting layers, THE Docker_Scanner SHALL check the WorkflowContext cancellation token between each layer extraction and SHALL terminate early if cancellation is requested, returning a partial Scan_Result containing packages identified up to that point and a diagnostic message indicating the scan was cancelled.
8. THE Docker_Scanner SHALL operate without requiring the Docker daemon or root privileges.
9. WHEN the `manifest.json` contains multiple image entries, THE Docker_Scanner SHALL scan only the first image entry in the manifest array.

### Requirement 6: OCI Image Layout Scanner

**User Story:** As a security analyst, I want to scan OCI image layout directories, so that I can identify installed packages in OCI-compliant container images.

#### Acceptance Criteria

1. WHEN an OCI artifact with a valid path to an OCI image layout directory is provided, THE OCI_Scanner SHALL read the `index.json` to identify manifests, read the image manifest to locate layer blobs, extract layers from the `blobs/` directory, and locate the dpkg status file at `var/lib/dpkg/status` within the merged layer filesystem to extract installed package metadata.
2. WHEN OCI layers use the `application/vnd.oci.image.layer.v1.tar+gzip` media type, THE OCI_Scanner SHALL decompress and extract the tar contents.
3. WHEN OCI layers use the `application/vnd.oci.image.layer.v1.tar+zstd` media type, THE OCI_Scanner SHALL decompress and extract the tar contents.
4. WHEN the OCI image layout contains an `oci-layout` file with imageLayoutVersion "1.0.0", THE OCI_Scanner SHALL accept it as a valid OCI layout.
5. IF the directory is not a valid OCI image layout (missing oci-layout file, missing index.json, or unsupported imageLayoutVersion), THEN THE OCI_Scanner SHALL return a Scan_Result with an empty package list and a diagnostic message identifying which validation check failed (missing file name or unsupported version value).
6. WHEN multiple layers are present, THE OCI_Scanner SHALL apply layers in order (bottom to top) with whiteout file handling consistent with the OCI image specification, removing files prefixed with `.wh.` and clearing directories containing `.wh..wh..opq` opaque whiteout markers.
7. WHILE extracting layers, THE OCI_Scanner SHALL check the WorkflowContext cancellation token between each layer extraction.
8. IF cancellation is requested via the WorkflowContext cancellation token during layer extraction, THEN THE OCI_Scanner SHALL terminate processing immediately and return a Scan_Result with an empty package list and a diagnostic message indicating cancellation.
9. THE OCI_Scanner SHALL operate without requiring container runtimes or root privileges.
10. IF the merged layer filesystem does not contain a dpkg status file at `var/lib/dpkg/status`, THEN THE OCI_Scanner SHALL return a Scan_Result with an empty package list and a diagnostic message indicating the status file was not found.
11. IF a layer blob uses a media type other than `application/vnd.oci.image.layer.v1.tar+gzip` or `application/vnd.oci.image.layer.v1.tar+zstd`, THEN THE OCI_Scanner SHALL skip that layer and include a diagnostic message identifying the unsupported media type.

### Requirement 7: ISO 9660 Image Scanner

**User Story:** As a security analyst, I want to scan ISO images, so that I can identify installed packages in live/installer media.

#### Acceptance Criteria

1. WHEN an ISO artifact with a valid path to an ISO 9660 image file is provided, THE ISO_Scanner SHALL read the ISO filesystem and search for a squashfs image at the following paths in order: `live/filesystem.squashfs`, `casper/filesystem.squashfs`, `install/filesystem.squashfs`, and then check for a direct rootfs structure containing `var/lib/dpkg/status`.
2. WHEN the ISO contains a squashfs filesystem image at one of the known search paths, THE ISO_Scanner SHALL decompress the squashfs image using library-based reading (without mount operations or root privileges) to access the rootfs within it.
3. WHEN the rootfs within the ISO contains a dpkg status file at `var/lib/dpkg/status`, THE ISO_Scanner SHALL parse it and set the Scanning_Strategy to "dpkg_metadata".
4. WHEN the ISO contains a direct rootfs structure (no squashfs) with a dpkg status file at `var/lib/dpkg/status`, THE ISO_Scanner SHALL parse the status file directly and set the Scanning_Strategy to "dpkg_metadata".
5. IF the ISO image is not a valid ISO 9660 format or cannot be read, THEN THE ISO_Scanner SHALL return a Scan_Result with an empty package list and a diagnostic message describing the format error.
6. IF a squashfs image is found within the ISO but cannot be decompressed or read (corrupt or truncated), THEN THE ISO_Scanner SHALL return a Scan_Result with an empty package list and a diagnostic message indicating the squashfs extraction failure.
7. IF no dpkg status file can be located within the ISO (neither directly nor within squashfs), THEN THE ISO_Scanner SHALL fall back to the Filesystem_Analyzer and set the Scanning_Strategy to "filesystem_analysis".
8. WHILE scanning the ISO, THE ISO_Scanner SHALL check the WorkflowContext cancellation token after each of the following steps: opening the ISO, locating the squashfs image, extracting the squashfs contents, and parsing the dpkg status file, and SHALL terminate early if cancellation is requested.
9. THE ISO_Scanner SHALL operate without requiring mount operations or root privileges by using library-based ISO and squashfs reading.

### Requirement 8: QCOW2 Disk Image Scanner

**User Story:** As a security analyst, I want to scan QCOW2 virtual machine disk images, so that I can identify installed packages in VM images without booting them.

#### Acceptance Criteria

1. WHEN a QCOW2 artifact with a valid path to a QCOW2 disk image is provided, THE QCOW2_Scanner SHALL use the guestfs inspection API (or equivalent) to identify the operating system root filesystem within the image, mount it read-only, and extract the dpkg status file from `/var/lib/dpkg/status`.
2. WHEN the QCOW2 image contains a filesystem with a dpkg status file at `/var/lib/dpkg/status`, THE QCOW2_Scanner SHALL parse it using the Dpkg_Status_Parser and set the Scanning_Strategy to "dpkg_metadata".
3. WHEN the QCOW2 image does not contain a dpkg status file, THE QCOW2_Scanner SHALL fall back to the Filesystem_Analyzer and set the Scanning_Strategy to "filesystem_analysis".
4. IF the file is not a valid QCOW2 image (missing `QFI\xfb` magic bytes at offset 0 or unsupported QCOW2 version), THEN THE QCOW2_Scanner SHALL return a Scan_Result with an empty package list and a diagnostic message describing the validation failure.
5. IF the QCOW2 file path does not exist or is not readable, THEN THE QCOW2_Scanner SHALL return a Scan_Result with an empty package list and a diagnostic message describing the access failure.
6. IF the QCOW2 image contains no inspectable operating system root (e.g., unrecognized partition layout, encrypted volumes, or unsupported filesystem types), THEN THE QCOW2_Scanner SHALL return a Scan_Result with an empty package list and a diagnostic message describing the inspection failure.
7. WHILE inspecting the QCOW2 image, THE QCOW2_Scanner SHALL check the WorkflowContext cancellation token at major processing boundaries (image open, filesystem inspection, file extraction, parsing) and SHALL terminate early if cancellation is requested.
8. WHEN the scan completes or is cancelled, THE QCOW2_Scanner SHALL report progress through the WorkflowContext progress reporter.
9. THE QCOW2_Scanner SHALL operate without requiring root privileges or mount operations by using guestfs Python bindings (libguestfs) or equivalent rootless inspection tools.
10. IF the guestfs library or required inspection tools are not available in the runtime environment, THEN THE QCOW2_Scanner SHALL return a Scan_Result with an empty package list and a diagnostic message indicating the missing dependency.

### Requirement 9: Raw Disk Image Scanner

**User Story:** As a security analyst, I want to scan raw disk images, so that I can identify installed packages in disk images used for embedded systems and cloud deployments.

#### Acceptance Criteria

1. WHEN an IMG artifact with a valid path to a raw disk image file (file exists and is readable) is provided, THE IMG_Scanner SHALL inspect the disk image to locate and extract the dpkg status file from the filesystem within the image.
2. WHEN the raw disk image contains multiple partitions, THE IMG_Scanner SHALL inspect all partitions containing a supported filesystem and SHALL use the dpkg status file from the first partition where `/var/lib/dpkg/status` is found (ordered by partition table entry order).
3. WHEN the raw disk image contains a filesystem with a dpkg status file at `/var/lib/dpkg/status`, THE IMG_Scanner SHALL parse it and set the Scanning_Strategy to "dpkg_metadata".
4. WHEN the raw disk image does not contain a dpkg status file on any inspected partition, THE IMG_Scanner SHALL fall back to the Filesystem_Analyzer and set the Scanning_Strategy to "filesystem_analysis".
5. IF the artifact path does not exist or is not readable, THEN THE IMG_Scanner SHALL return a Scan_Result with an empty package list and a diagnostic message describing the access failure.
6. IF the file cannot be inspected as a valid disk image (unrecognized partition table or filesystem on all partitions), THEN THE IMG_Scanner SHALL return a Scan_Result with an empty package list and a diagnostic message describing the inspection failure.
7. WHILE inspecting the raw disk image, THE IMG_Scanner SHALL check the WorkflowContext cancellation token at major processing boundaries (partition enumeration, filesystem inspection, file extraction) and SHALL terminate early if cancellation is requested, returning a partial Scan_Result containing packages identified up to that point and a diagnostic message indicating the scan was cancelled.
8. THE IMG_Scanner SHALL operate without requiring root privileges or mount operations by using guestfs Python bindings (libguestfs) or equivalent rootless inspection tools.
9. IF the guestfs library or required inspection tools are not available in the runtime environment, THEN THE IMG_Scanner SHALL return a Scan_Result with an empty package list and a diagnostic message indicating the missing dependency.

### Requirement 10: AMI Image Scanner

**User Story:** As a security analyst, I want to scan AWS AMI images, so that I can identify installed packages in cloud machine images without launching instances.

#### Acceptance Criteria

1. WHEN an AMI artifact with a valid path to an AMI disk image file (raw or QCOW2 format) is provided, THE AMI_Scanner SHALL delegate to the appropriate underlying scanner (IMG_Scanner or QCOW2_Scanner) based on detected image format.
2. WHEN the AMI image is in QCOW2 format (detected by the QCOW2 magic bytes `QFI\xfb` at offset 0), THE AMI_Scanner SHALL delegate scanning to the QCOW2_Scanner.
3. WHEN the AMI image is in raw format (not QCOW2), THE AMI_Scanner SHALL delegate scanning to the IMG_Scanner.
4. THE AMI_Scanner SHALL propagate the Scan_Result from the delegated scanner, preserving all identified packages, scanning strategy, diagnostic messages, and scan duration.
5. IF the AMI image format cannot be determined (file size is less than 4 bytes, file does not exist, or file is not readable), THEN THE AMI_Scanner SHALL return a Scan_Result with an empty package list and a diagnostic message describing the detection failure.
6. THE AMI_Scanner SHALL operate without requiring AWS credentials or network access, operating entirely on local image files.
7. WHILE the AMI_Scanner is performing format detection, THE AMI_Scanner SHALL check the WorkflowContext cancellation token before delegating to the underlying scanner and SHALL return a Scan_Result with an empty package list and a diagnostic message indicating cancellation if cancellation is requested.

### Requirement 11: Filesystem Analyzer Fallback

**User Story:** As a scanner developer, I want a filesystem analysis fallback, so that packages can still be identified when dpkg metadata is unavailable.

#### Acceptance Criteria

1. WHEN dpkg metadata is unavailable and the Filesystem_Analyzer is invoked with a list of filesystem paths from the artifact, THE Filesystem_Analyzer SHALL match paths against the Contents index data (built during M4 indexing) to identify which packages own the observed files.
2. WHEN a filesystem path matches an entry in the Contents index, THE Filesystem_Analyzer SHALL record the owning package name, and SHALL determine the package version and architecture from the metadata database (PackageInstance records from M4/M5).
3. WHEN multiple files map to the same package, THE Filesystem_Analyzer SHALL produce a single Identified_Package entry for that package rather than duplicates.
4. THE Filesystem_Analyzer SHALL set the status field of identified packages to "inferred" to distinguish them from packages identified via dpkg metadata.
5. IF no Contents index data is available for the target distribution, THEN THE Filesystem_Analyzer SHALL return an empty package list and a diagnostic message indicating that Contents index data is required for filesystem analysis.
6. THE Filesystem_Analyzer SHALL limit the number of filesystem paths processed to a configurable maximum (default 100,000) to prevent excessive memory and CPU usage during analysis.
7. IF a filesystem path matches an entry in the Contents index but no corresponding PackageInstance record exists in the metadata database, THEN THE Filesystem_Analyzer SHALL skip that path and record a diagnostic warning identifying the unresolved package name.
8. WHEN the configurable maximum path limit is exceeded, THE Filesystem_Analyzer SHALL process only the first N paths (where N is the configured limit), include a diagnostic message indicating that the limit was reached and how many paths were skipped, and return the packages identified from the processed paths.

### Requirement 12: Scanner Plugin Registry

**User Story:** As a platform developer, I want scanners registered as plugins, so that new artifact types can be supported by adding packages without modifying core code.

#### Acceptance Criteria

1. THE Scanner_Registry SHALL discover scanner implementations via `importlib.metadata` entry points in the `debcraft.scanners` group, where each entry point name corresponds to an Artifact_Type enum value.
2. WHEN the Scanner_Registry is initialized, THE Scanner_Registry SHALL load all registered entry points and map each to its declared Artifact_Type.
3. IF a registered entry point fails to load (due to ImportError or other resolution error), THEN THE Scanner_Registry SHALL skip that entry point, record a diagnostic warning identifying the failing entry point name and error reason, and continue loading remaining entry points.
4. WHEN a scan is requested for a given Artifact_Type, THE Scanner_Registry SHALL return the registered scanner implementation for that type.
5. IF no scanner is registered for a requested Artifact_Type, THEN THE Scanner_Registry SHALL raise an error identifying the unsupported artifact type by name and listing all currently registered Artifact_Type values.
6. THE Scanner_Registry SHALL validate that each loaded scanner conforms to the Artifact_Scanner protocol at registration time and SHALL reject non-conforming implementations with an error indicating the entry point name and which required method or signature is missing.
7. IF a loaded scanner fails protocol validation, THEN THE Scanner_Registry SHALL skip that scanner, record a diagnostic warning, and continue loading remaining entry points without affecting their registration.
8. THE Scanner_Registry SHALL support multiple scanner implementations for the same Artifact_Type, selecting the one with the highest declared priority (integer, higher wins); IF two scanners declare equal priority for the same Artifact_Type, THEN THE Scanner_Registry SHALL select the one whose entry point name is lexicographically first.

### Requirement 13: Cooperative Cancellation and Progress

**User Story:** As an operator, I want long-running scans to be cancellable and to report progress, so that I can monitor and control scan operations.

#### Acceptance Criteria

1. WHILE a scan is in progress, THE Artifact_Scanner implementation SHALL check the WorkflowContext cancellation token at least once per major processing step (layer extraction, file enumeration, parsing).
2. WHEN the cancellation token indicates cancellation is requested, THE Artifact_Scanner implementation SHALL stop processing within no more than 5 seconds after the most recent cancellation check and SHALL return a partial Scan_Result containing all packages identified up to the point of cancellation.
3. WHEN the cancellation token indicates cancellation is requested, THE Artifact_Scanner implementation SHALL include a diagnostic message in the Scan_Result indicating that the scan was cancelled and the result is partial.
4. WHILE a scan is in progress, THE Artifact_Scanner implementation SHALL report progress via the WorkflowContext progress reporter as a percentage value from 0.0 to 100.0 that is monotonically non-decreasing and reflects the proportion of completed processing steps relative to total anticipated steps.
5. WHEN a scan completes successfully without cancellation, THE Artifact_Scanner implementation SHALL report a final progress value of 100.0 via the WorkflowContext progress reporter.
6. THE Artifact_Scanner implementation SHALL log scan start, scan completion (or cancellation), and any errors via the WorkflowContext structured logger, including the scan identifier in each log entry.
7. IF a scan is cancelled, THEN THE Artifact_Scanner implementation SHALL log the cancellation event via the WorkflowContext structured logger before returning the partial Scan_Result.

### Requirement 14: Metadata Enrichment via Repository Intelligence (M3/M4/M5 Integration)

**User Story:** As a compliance engineer, I want identified packages enriched with metadata from the local mirror and metadata database, so that scan results contain license, download location, PURL, and dependency information without re-downloading or re-parsing packages.

#### Acceptance Criteria

1. WHEN a scan produces Identified_Package entries, THE Scan_Result SHALL include an enrichment step that cross-references each identified package (by name, version, architecture) against PackageInstance records in the metadata database (populated by M4 Repository Indexer).
2. WHEN a matching PackageInstance record is found in the metadata database, THE Scan_Result SHALL augment the Identified_Package with: source package name, maintainer, homepage, dependencies, section, priority, description, SHA256, and download URL from the stored metadata.
3. WHEN a matching PackageInstance record has associated LicenseExpression records (populated by M5 Package Intelligence), THE Scan_Result SHALL include the mapped SPDX license expressions and the source algorithm identifier for each license expression associated with the identified package.
4. WHEN the PURL_Generator (from M5) is available via the DI container, THE Scan_Result enrichment step SHALL generate a Package URL for each identified package that has a matching PackageInstance record with a known distribution origin.
5. WHEN the Download_Location_Resolver (from M5) is available via the DI container, THE Scan_Result enrichment step SHALL construct the fully-qualified download URL for each identified package that has a matching PackageInstance record.
6. IF no matching PackageInstance record is found in the metadata database for an identified package, THEN THE Scan_Result SHALL retain the basic identification (name, version, architecture, status) and include a diagnostic message noting that enrichment data is unavailable for that package.
7. THE enrichment step SHALL use the latest published RepositorySnapshot (the published snapshot with the most recent captured_at timestamp from M3 Repository Mirror) as the reference point for metadata lookups to ensure consistency.
8. THE enrichment step SHALL access metadata through repository interfaces (PackageRepository, LicenseRepository from M2 Storage Layer) resolved from the WorkflowContext scoped container rather than directly querying the database.
9. IF no published RepositorySnapshot exists in the metadata database at the time of enrichment, THEN THE Scan_Result SHALL skip the enrichment step entirely and include a diagnostic message indicating that no published snapshot is available for metadata lookups.
10. IF the PURL_Generator or Download_Location_Resolver is not available via the DI container, THEN THE Scan_Result enrichment step SHALL proceed without generating the corresponding field and SHALL include a diagnostic message noting which enrichment service was unavailable.

### Requirement 15: Local Mirror Data Utilization (M3 Integration)

**User Story:** As an operator, I want artifact scanners to validate identified packages against the local mirror, so that scan results reflect what is known from mirrored repository data.

#### Acceptance Criteria

1. WHEN a scan identifies a package by name and version, THE enrichment step SHALL verify package existence by checking that a corresponding RepositoryFile record in mirror.db has state "Verified" or "Indexed" (from M3 Repository Mirror).
2. WHEN the local mirror contains the `.deb` file for an identified package (state "Verified" or "Indexed" in mirror.db), THE Scan_Result SHALL record the local cache path of the `.deb` file for potential downstream use by SBOM generation.
3. IF the Contents index data (parsed by M4 Contents_Parser) is available for the target distribution (i.e., FileOwnership records exist for a RepositorySnapshot matching the target distribution), THEN THE Filesystem_Analyzer SHALL use the FileOwnership records to map observed filesystem paths to package names.
4. IF the local mirror has not been synchronized for the distribution present in the scanned artifact (no published RepositorySnapshot exists for that distribution), THEN THE Scan_Result SHALL include a diagnostic message recommending a mirror synchronization for full enrichment coverage.
5. IF no matching RepositoryFile record is found in mirror.db for an identified package, THEN THE Scan_Result SHALL include a diagnostic message noting that the package is not present in the local mirror cache.

### Requirement 16: Architectural Compliance

**User Story:** As a developer, I want the artifact scanner components to follow clean architecture boundaries, so that domain logic remains independent of infrastructure concerns.

#### Acceptance Criteria

1. THE Artifact_Scanner protocol and all value objects (Artifact, Scan_Result, Identified_Package, Artifact_Type, Scanning_Strategy) SHALL reside in the domain layer (`src/debcraft/domain/scanner/`).
2. THE Dpkg_Status_Parser SHALL reside in the domain layer and SHALL NOT import from the infrastructure layer.
3. THE Filesystem_Analyzer SHALL reside in the domain layer, and its port interface Protocol for Contents index data access SHALL also be defined in the domain layer (`src/debcraft/domain/scanner/`), so that the Filesystem_Analyzer depends only on the domain-layer Protocol rather than directly querying the database.
4. THE concrete scanner implementations (Directory_Scanner, Docker_Scanner, OCI_Scanner, ISO_Scanner, QCOW2_Scanner, IMG_Scanner, AMI_Scanner) SHALL reside in the infrastructure layer (`src/debcraft/infrastructure/scanners/`).
5. THE Scanner_Registry SHALL reside in the infrastructure layer and SHALL be registered as a singleton in the DI container during the scanner bootstrap function, following the existing `storage_bootstrap` pattern.
6. THE import-linter "Domain independence" contract (which forbids `debcraft.domain` from importing `debcraft.infrastructure`) SHALL pass when `lint-imports` is executed, verifying that `debcraft.domain.scanner` contains no imports from `debcraft.infrastructure.scanners` or any other infrastructure module.
7. THE Dpkg_Status_Parser SHALL be a pure function that accepts a string parameter and returns a result value, performing no file I/O, no network access, no mutation of shared state, and producing deterministic output for identical input.
8. THE concrete scanner implementations SHALL receive all external tool dependencies (e.g., guestfs bindings, tar libraries) through constructor injection rather than importing them at module level, such that each scanner can be instantiated in tests with substitute implementations for all external dependencies.

### Requirement 17: Scan Result Caching

**User Story:** As an operator performing repeated scans, I want scan results cached per package so that subsequent scans of the same packages complete faster without re-querying and re-enriching metadata.

#### Acceptance Criteria

1. WHEN a scan completes enrichment for an identified package, THE caching layer SHALL store the enriched package metadata (including SPDX license expressions, confidence scores, PURL, download URL, source package, maintainer, homepage, dependencies, section, priority, description, and SHA256) keyed by (package_name, version, architecture).
2. WHEN a subsequent scan identifies a package for which a cache entry exists whose stored RepositorySnapshot ID matches the current latest published RepositorySnapshot ID, THE enrichment step SHALL return the cached metadata without querying the PackageRepository or LicenseRepository again.
3. WHEN a new RepositorySnapshot is published (indicating the metadata database has been updated), THE cache SHALL treat all entries whose stored RepositorySnapshot ID differs from the newly published snapshot ID as invalid, ensuring stale license or metadata information is not served on subsequent lookups.
4. THE cache SHALL persist across process restarts by storing entries in the cache database (cache.db) rather than only in-memory.
5. THE cache SHALL store the RepositorySnapshot ID that was used to produce the cached enrichment data, so that cache validity can be determined by comparing against the current latest published snapshot.
6. WHEN the cache is hit for all identified packages in a scan, THE scan duration for the enrichment step SHALL be no more than 50% of the duration required to perform the same enrichment without cache (i.e., querying PackageRepository and LicenseRepository for the same package set).
7. IF the cache database is unavailable or a cache read/write operation fails, THEN THE enrichment step SHALL proceed by querying the PackageRepository and LicenseRepository directly and SHALL log a diagnostic warning indicating the cache failure reason without interrupting the scan.
