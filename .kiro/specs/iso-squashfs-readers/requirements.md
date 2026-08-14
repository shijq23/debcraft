# Requirements Document

## Introduction

This feature replaces the no-op `_NoOpISOReader` and `_NoOpSquashfsReader` stubs in the `debcraft sbom` CLI command with production implementations. The `PyCdlibISOReader` uses the `pycdlib` library to read ISO 9660 images with Rock Ridge extension support. The `SquashfsReader` implementation reads squashfs filesystem images from raw bytes without requiring mount operations or root privileges. Once wired into the CLI's scanner registry, the `debcraft sbom` command will correctly identify Debian packages inside ISO images containing squashfs filesystems.

## Glossary

- **PyCdlibISOReader**: A production implementation of the `ISOReader` protocol that uses the `pycdlib` library to read ISO 9660 images with Rock Ridge path support.
- **SquashfsReader_Impl**: A production implementation of the `SquashfsReader` protocol that reads squashfs filesystem images from raw bytes in memory.
- **ISOReader**: A Protocol interface defined in `src/debcraft/infrastructure/scanners/iso.py` with methods `open`, `list_dir`, `read_file`, and `close`.
- **SquashfsReader**: A Protocol interface defined in `src/debcraft/infrastructure/scanners/iso.py` with methods `open`, `read_file`, `list_dir`, and `close`.
- **ISOScanner**: The existing scanner class that orchestrates ISO reading, squashfs extraction, and dpkg status parsing.
- **Scanner_Registry**: The `_create_scanner_registry()` function in `src/debcraft/cli/sbom.py` that wires scanner dependencies for CLI mode.
- **Rock_Ridge**: An extension to the ISO 9660 filesystem standard that supports POSIX long filenames, symbolic links, and permissions — used by Linux distribution ISOs.
- **Squashfs**: A compressed read-only filesystem format used by Debian and Ubuntu live/installer ISOs to store the root filesystem.
- **dpkg_status**: The file at `var/lib/dpkg/status` inside a root filesystem that contains metadata for all installed Debian packages.

## Requirements

### Requirement 1: Open and Close ISO Images

**User Story:** As a CLI user, I want the ISO reader to open valid ISO 9660 image files, so that the scanner can access their contents for package discovery.

#### Acceptance Criteria

1. WHEN a valid ISO 9660 image path is provided, THE PyCdlibISOReader SHALL open the image for reading with Rock Ridge extension support enabled without raising an exception.
2. IF a nonexistent file path is provided, THEN THE PyCdlibISOReader SHALL raise an OSError.
3. IF a file that is not a valid ISO 9660 image is provided, THEN THE PyCdlibISOReader SHALL raise an OSError.
4. WHEN close is called on an opened image, THE PyCdlibISOReader SHALL release the underlying file handle such that subsequent calls to list_dir or read_file raise an exception.
5. WHEN close is called without a prior open, THE PyCdlibISOReader SHALL complete without raising an exception.

### Requirement 2: List ISO Directories with Rock Ridge Support

**User Story:** As a CLI user, I want the ISO reader to list directory contents using Rock Ridge paths, so that Linux distribution ISOs with long filenames are navigable.

#### Acceptance Criteria

1. WHEN a directory path that exists within the ISO filesystem is provided, THE PyCdlibISOReader SHALL return a list of entry names (basenames only, not full paths) present in that directory using Rock Ridge path resolution.
2. WHEN an empty string is provided as the directory path, THE PyCdlibISOReader SHALL treat it as the root directory and return all top-level entries in the ISO image.
3. WHEN a path is provided that does not exist within the ISO filesystem, THE PyCdlibISOReader SHALL raise a FileNotFoundError.
4. WHEN a path that refers to a file (not a directory) is provided, THE PyCdlibISOReader SHALL raise a FileNotFoundError.
5. THE PyCdlibISOReader SHALL exclude the "." and ".." entries from directory listings.
6. THE PyCdlibISOReader SHALL accept paths without a leading slash (e.g., "live", "casper/filesystem.squashfs") as relative to the ISO root directory.

### Requirement 3: Read ISO Files with Rock Ridge Support

**User Story:** As a CLI user, I want the ISO reader to read file contents by Rock Ridge path, so that files with long names inside Linux ISOs can be accessed.

#### Acceptance Criteria

1. WHEN a valid file path without a leading slash is provided, THE PyCdlibISOReader SHALL return the complete raw bytes of that file using Rock Ridge path resolution.
2. IF a non-existent file path is provided, THEN THE PyCdlibISOReader SHALL raise a FileNotFoundError.
3. IF a path that refers to a directory rather than a file is provided, THEN THE PyCdlibISOReader SHALL raise a FileNotFoundError.
4. THE PyCdlibISOReader SHALL support reading files up to 1 GB in size from the ISO image without truncation.

### Requirement 4: Open Squashfs Images from Raw Bytes

**User Story:** As a CLI user, I want the squashfs reader to open squashfs images from in-memory bytes, so that the scanner can decompress squashfs extracted from ISOs without writing temporary files.

#### Acceptance Criteria

1. WHEN valid squashfs image bytes are provided, THE SquashfsReader_Impl SHALL open the image for reading without raising an exception.
2. IF the bytes provided are not a valid squashfs image (including empty bytes of length 0), THEN THE SquashfsReader_Impl SHALL raise an OSError.
3. WHEN close is called after a successful open, THE SquashfsReader_Impl SHALL release all resources associated with the opened squashfs image such that no temporary files or file descriptors remain held.
4. WHEN close is called without a prior open, THE SquashfsReader_Impl SHALL complete without raising an exception.
5. IF open is called when the reader already has an image open, THEN THE SquashfsReader_Impl SHALL raise an OSError without altering the state of the previously opened image.

### Requirement 5: Read Files from Squashfs

**User Story:** As a CLI user, I want the squashfs reader to read file contents by path, so that the scanner can extract `var/lib/dpkg/status` from the squashfs root filesystem.

#### Acceptance Criteria

1. WHEN a valid file path is provided, THE SquashfsReader_Impl SHALL return the complete raw bytes of that file from the squashfs image, including files up to at least 1 MB in size.
2. WHEN a non-existent file path is provided, THE SquashfsReader_Impl SHALL raise a FileNotFoundError.
3. THE SquashfsReader_Impl SHALL accept paths without a leading slash (e.g., "var/lib/dpkg/status") consistent with the ISOScanner's calling convention.
4. IF a provided path refers to a directory rather than a file, THEN THE SquashfsReader_Impl SHALL raise a FileNotFoundError.
5. IF a provided path includes a leading slash (e.g., "/var/lib/dpkg/status"), THEN THE SquashfsReader_Impl SHALL resolve it equivalently to the same path without the leading slash.

### Requirement 6: List Squashfs Directories

**User Story:** As a CLI user, I want the squashfs reader to list directory contents, so that the scanner can perform filesystem analysis as a fallback when dpkg status is unavailable.

#### Acceptance Criteria

1. WHEN a directory path that exists within the squashfs filesystem is provided, THE SquashfsReader_Impl SHALL return a list of entry base names (not full paths) present in that directory.
2. WHEN an empty string path is provided, THE SquashfsReader_Impl SHALL return all top-level entries in the squashfs root.
3. WHEN a non-existent directory path is provided, THE SquashfsReader_Impl SHALL raise a FileNotFoundError.
4. WHEN a path that refers to a file rather than a directory is provided, THE SquashfsReader_Impl SHALL raise a FileNotFoundError.
5. THE SquashfsReader_Impl SHALL accept paths without a leading slash (e.g., "var/lib") consistent with the ISOScanner's calling convention.

### Requirement 7: Wire Production Readers into CLI Scanner Registry

**User Story:** As a CLI user, I want the `debcraft sbom` command to use production ISO and squashfs readers, so that scanning ISO images produces actual package results instead of empty SBOMs.

#### Acceptance Criteria

1. THE Scanner_Registry SHALL instantiate the ISOScanner with PyCdlibISOReader as the iso_reader argument, where PyCdlibISOReader conforms to the ISOReader protocol defined in debcraft.infrastructure.scanners.iso.
2. THE Scanner_Registry SHALL instantiate the ISOScanner with SquashfsReader_Impl as the squashfs_reader argument, where SquashfsReader_Impl conforms to the SquashfsReader protocol defined in debcraft.infrastructure.scanners.iso.
3. WHEN the `debcraft sbom` command is run against a valid ISO image containing a squashfs filesystem with a parseable var/lib/dpkg/status file listing at least 1 package entry, THE Scanner_Registry SHALL enable the ISOScanner to return a list containing at least 1 package.
4. IF the production reader dependencies are not installed, THEN THE _create_scanner_registry function SHALL raise an ImportError with a message indicating which dependency is missing.

### Requirement 8: Add Required Dependencies

**User Story:** As a developer, I want the required libraries added to the project dependencies, so that the production readers can be imported and used at runtime.

#### Acceptance Criteria

1. THE pyproject.toml SHALL include `pycdlib` in the `[project] dependencies` list using the `>=` minimum version constraint format consistent with existing dependencies (e.g., `pycdlib>=1.14`).
2. THE pyproject.toml SHALL include a squashfs reading library in the `[project] dependencies` list using the `>=` minimum version constraint format consistent with existing dependencies.
3. THE pyproject.toml SHALL preserve all existing entries in the `[project] dependencies` list unchanged when adding the new dependencies.
4. WHEN `uv sync` is run after the dependencies are added, THE package manager SHALL resolve and install both new dependencies without conflicts against the existing dependency set.

### Requirement 9: Protocol Conformance

**User Story:** As a developer, I want the production readers to conform exactly to the existing Protocol interfaces, so that the ISOScanner works without modification.

#### Acceptance Criteria

1. THE PyCdlibISOReader SHALL implement all four methods defined in the ISOReader Protocol: open(path: str) -> None, list_dir(path: str) -> list[str], read_file(path: str) -> bytes, and close() -> None.
2. THE SquashfsReader_Impl SHALL implement all four methods defined in the SquashfsReader Protocol: open(data: bytes) -> None, read_file(path: str) -> bytes, list_dir(path: str) -> list[str], and close() -> None.
3. THE PyCdlibISOReader SHALL operate without requiring mount operations or root privileges.
4. THE SquashfsReader_Impl SHALL operate without requiring mount operations or root privileges.
5. THE PyCdlibISOReader SHALL raise OSError from open() and FileNotFoundError from read_file()/list_dir() consistent with the ISOReader Protocol docstrings.
6. THE SquashfsReader_Impl SHALL raise OSError from open() and FileNotFoundError from read_file()/list_dir() consistent with the SquashfsReader Protocol docstrings.

### Requirement 10: Round-Trip Path Consistency

**User Story:** As a developer, I want directory listing results to be usable as inputs to read_file and list_dir, so that recursive filesystem traversal works correctly.

#### Acceptance Criteria

1. FOR ALL entries returned by PyCdlibISOReader.list_dir(directory), WHEN directory is non-empty THE PyCdlibISOReader SHALL accept the path formed as directory + "/" + entry as a valid argument to read_file or list_dir. WHEN directory is an empty string THE PyCdlibISOReader SHALL accept the bare entry name as a valid argument.
2. FOR ALL entries returned by SquashfsReader_Impl.list_dir(directory), WHEN directory is non-empty THE SquashfsReader_Impl SHALL accept the path formed as directory + "/" + entry as a valid argument to read_file or list_dir. WHEN directory is an empty string THE SquashfsReader_Impl SHALL accept the bare entry name as a valid argument.
3. THE PyCdlibISOReader.list_dir SHALL return bare entry names (e.g., "lib", "dpkg") without path separators, so callers can construct child paths by simple concatenation.
4. THE SquashfsReader_Impl.list_dir SHALL return bare entry names (e.g., "lib", "dpkg") without path separators, so callers can construct child paths by simple concatenation.
