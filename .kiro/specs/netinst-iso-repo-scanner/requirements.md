# Requirements Document

## Introduction

This feature extends the ISOScanner to support Debian NETINST (network installer) ISO images. Unlike live ISOs that contain squashfs filesystems with installed rootfs packages, NETINST ISOs are structured as Debian package repositories with `dists/` and `pool/` directories. The scanner must detect this repository-style structure, locate and parse the `Packages` metadata files, and enumerate all packages available on the ISO using the existing dpkg stanza parser.

## Glossary

- **ISOScanner**: The existing scanner class in `debcraft.infrastructure.scanners.iso` that scans ISO 9660 images for Debian packages.
- **NETINST_ISO**: A Debian network installer ISO image structured as a package repository with `dists/` and `pool/` directories rather than a root filesystem.
- **Repository_Structure**: An ISO filesystem layout containing a `dists/` directory at the root level, indicating the ISO is structured as a Debian package repository.
- **Packages_File**: A Debian repository metadata file (RFC822-style stanzas) listing all packages in a component/architecture combination, located at paths like `dists/<codename>/<component>/binary-<arch>/Packages` or `Packages.gz`.
- **ISOReader**: The protocol abstraction over ISO 9660 reading that provides `open`, `list_dir`, `read_file`, and `close` methods.
- **Codename**: A distribution release identifier found as a subdirectory under `dists/` (e.g., "aria", "stable", "bookworm").
- **Component**: A repository section found under a codename directory (e.g., "main", "contrib", "non-free").
- **DpkgParseResult**: The result object returned by `parse_dpkg_status()` containing packages, diagnostics, and raw stanzas.
- **ScanningStrategy**: An enum indicating how packages were identified during scanning (e.g., `DPKG_METADATA`, `FILESYSTEM_ANALYSIS`).

## Requirements

### Requirement 1: Detect Repository Structure

**User Story:** As a scanner user, I want the ISOScanner to detect NETINST ISOs with repository structure, so that packages can be enumerated without requiring a squashfs filesystem.

#### Acceptance Criteria

1. WHEN the ISOScanner opens an ISO and finds no squashfs filesystem at any path in SQUASHFS_SEARCH_PATHS, THE ISOScanner SHALL attempt to list the ISO root level and check for the presence of a `dists/` directory entry.
2. WHEN a `dists/` directory exists at the ISO root level, THE ISOScanner SHALL identify the ISO as having Repository_Structure, record a diagnostic message indicating repository structure was detected, and proceed with repository-based scanning.
3. WHEN no `dists/` directory exists at the ISO root level and no squashfs is found, THE ISOScanner SHALL fall back to the direct rootfs dpkg status check first, then to filesystem analysis if no dpkg metadata is found.
4. IF listing the ISO root directory fails with an I/O error during the `dists/` directory check, THEN THE ISOScanner SHALL treat the ISO as not having Repository_Structure and proceed to the direct rootfs dpkg status check fallback.

### Requirement 2: Discover Packages Files

**User Story:** As a scanner user, I want the scanner to locate all Packages metadata files across codenames, components, and architectures, so that all available packages on the ISO are enumerated.

#### Acceptance Criteria

1. WHEN Repository_Structure is detected, THE ISOScanner SHALL enumerate all subdirectories under `dists/` and treat each subdirectory as a Codename directory.
2. WHEN enumerating a Codename directory, THE ISOScanner SHALL discover all subdirectories that do not match known metadata filenames (e.g., not "Release", "InRelease") and treat each as a Component directory.
3. WHEN enumerating a Component directory, THE ISOScanner SHALL identify architecture directories by matching the naming pattern `binary-<arch>/` and search each for a `Packages` file and a `Packages.gz` file.
4. WHEN both `Packages` and `Packages.gz` exist for the same component and architecture, THE ISOScanner SHALL use only the `Packages.gz` file and skip the uncompressed `Packages` file.
5. IF a directory listing operation fails for a specific Codename or Component directory during enumeration, THEN THE ISOScanner SHALL record a diagnostic message identifying the failed path and continue enumerating remaining directories.
6. IF no Packages_File is found across all codename/component/architecture combinations after enumeration completes, THEN THE ISOScanner SHALL record a diagnostic message and return an empty package list with the scan strategy set to `DPKG_METADATA`.

### Requirement 3: Parse Packages Files

**User Story:** As a scanner user, I want the scanner to parse Packages metadata files using the existing stanza parser, so that package information is extracted consistently with the rest of the system.

#### Acceptance Criteria

1. WHEN a Packages_File is located, THE ISOScanner SHALL read the file content via the ISOReader as raw bytes and decode them as UTF-8.
2. WHEN a `Packages.gz` file is read, THE ISOScanner SHALL decompress the gzip content using Python's `gzip` module before decoding and parsing.
3. WHEN Packages_File content is available as a decoded string, THE ISOScanner SHALL parse the content using the existing `parse_dpkg_status()` function from `debcraft.domain.scanner.dpkg_parser`.
4. WHEN the Packages_File lacks a `Status` field in its stanzas (as is normal for repository Packages files), THE ISOScanner SHALL treat each stanza containing `Package` and `Version` fields as an installed package regardless of the absence of a `Status` field.
5. IF reading or decompressing a Packages_File fails with an I/O error, THEN THE ISOScanner SHALL record a diagnostic message identifying the failed path and the error, and continue processing remaining Packages_Files.
6. WHEN a Packages_File contains malformed stanzas, THE ISOScanner SHALL record parse diagnostics from the parser and continue processing remaining stanzas.

### Requirement 4: Aggregate Packages Across Multiple Sources

**User Story:** As a scanner user, I want the scanner to combine packages from multiple Packages files into a single deduplicated result, so that the scan output accurately reflects all unique packages on the ISO.

#### Acceptance Criteria

1. WHEN multiple Packages_Files are found across different codenames, components, or architectures, THE ISOScanner SHALL aggregate all parsed packages into a single result list preserving the order in which Packages_Files were discovered.
2. WHEN the same package name, version, and architecture combination appear in multiple Packages_Files, THE ISOScanner SHALL deduplicate by retaining only the first-encountered entry and discarding subsequent duplicates.
3. IF one or more Packages_Files fail to read or decompress while other Packages_Files are successfully parsed, THEN THE ISOScanner SHALL include packages from the successful files in the aggregated result and record a diagnostic message for each failed file.
4. WHEN aggregation and deduplication are complete, THE ISOScanner SHALL record a diagnostic message indicating the total number of deduplicated packages and the number of Packages_Files processed.

### Requirement 5: Return Scan Result

**User Story:** As a scanner user, I want the repository scan to return a standard ScanResult, so that downstream processing handles NETINST ISOs identically to other ISO types.

#### Acceptance Criteria

1. WHEN repository-based scanning completes without cancellation or unrecoverable error, THE ISOScanner SHALL return a ScanResult with strategy set to `DPKG_METADATA`, the `packages` field containing all deduplicated IdentifiedPackage entries found, and the `artifact_path` set to the ISO file path.
2. THE ISOScanner SHALL measure scan duration as wall-clock elapsed time from the start of the scan method invocation to result construction, and include it as a non-negative float in the ScanResult `duration_seconds` field.
3. THE ISOScanner SHALL include all accumulated diagnostic messages in the ScanResult `diagnostics` field as an ordered list preserving the sequence in which diagnostics were recorded during scanning.
4. IF the ISO cannot be opened or an unrecoverable I/O error occurs during repository scanning, THEN THE ISOScanner SHALL return a ScanResult with an empty packages list, strategy set to `DPKG_METADATA`, and a diagnostic message indicating the failure reason.

### Requirement 6: Support Cancellation

**User Story:** As a scanner user, I want the repository scan to respect cancellation tokens, so that long-running scans of large NETINST ISOs can be interrupted gracefully.

#### Acceptance Criteria

1. WHILE scanning Repository_Structure, THE ISOScanner SHALL check the cancellation token at each of these points: after discovering codenames, after discovering each Packages_File, and after parsing each Packages_File.
2. WHEN the cancellation token indicates cancellation during repository scanning, THE ISOScanner SHALL stop processing and return a ScanResult with strategy set to `DPKG_METADATA`, the `packages` field containing all packages successfully parsed up to the cancellation point, and a diagnostic message indicating that scanning was cancelled and at which step.

### Requirement 7: Scanning Strategy Order

**User Story:** As a scanner user, I want the repository scan to be attempted after squashfs scanning fails and before the filesystem analysis fallback, so that NETINST ISOs are handled efficiently without unnecessary filesystem walking.

#### Acceptance Criteria

1. THE ISOScanner SHALL attempt scanning strategies in this order: squashfs search, repository structure detection, direct rootfs dpkg status check, filesystem analysis fallback.
2. WHEN Repository_Structure is detected and repository scanning produces at least one IdentifiedPackage, THE ISOScanner SHALL return the repository scan result without attempting further fallback strategies.
3. WHEN Repository_Structure is detected but no Packages_Files are found, all Packages_Files are empty, or parsing produces zero IdentifiedPackage entries, THE ISOScanner SHALL proceed to the direct rootfs dpkg status check as the next fallback.

### Requirement 8: Parse Packages File Without Status Field

**User Story:** As a scanner user, I want packages from repository Packages files to be correctly identified even though they lack dpkg `Status` fields, so that the parser handles repository metadata gracefully.

#### Acceptance Criteria

1. WHEN a stanza in a Packages_File contains `Package` and `Version` fields but no `Status` field, THE ISOScanner SHALL create an IdentifiedPackage with status "installed" for that stanza.
2. WHEN a stanza in a Packages_File is missing the `Package` field or the `Version` field, THE ISOScanner SHALL skip that stanza and record a diagnostic message identifying the stanza position and which required field is absent.
3. WHEN a Packages_File contains `.udeb` packages (identified by the `Section` field value starting with "debian-installer"), THE ISOScanner SHALL include those packages in the result with the same "installed" status.
