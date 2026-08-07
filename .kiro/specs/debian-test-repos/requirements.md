# Requirements Document

## Introduction

This feature provides scripts and tooling to create minimal Debian packages and APT repositories for testing purposes within the debcraft project. The generated artifacts are designed to be tiny enough to commit to git, enabling reproducible test fixtures for repository parsing, package analysis, and integration testing. The approach is inspired by geofft's "newpackage" and "fakerepo" utilities.

## Glossary

- **Package_Creator**: A shell script that generates a minimal Debian package skeleton using debhelper 7 compat rules, producing a buildable source tree
- **Repository_Creator**: A shell script that creates a local APT repository with proper directory structure and metadata files
- **APT_Repository**: A directory structure following the standard Debian repository layout with pool/, dists/, and metadata indexes
- **Package_Skeleton**: A minimal set of Debian packaging files (debian/control, debian/changelog, debian/rules, debian/compat) sufficient to build a .deb
- **Repository_Metadata**: The Packages, Packages.gz, and Release files that describe the contents of an APT repository
- **Fixtures_Directory**: The project directory at fixtures/ where test packages and repositories are stored

## Requirements

### Requirement 1: Package Skeleton Creation

**User Story:** As a developer, I want to generate minimal Debian package skeletons from a script, so that I can quickly create tiny test .deb packages without manual boilerplate.

#### Acceptance Criteria

1. WHEN a package name is provided, THE Package_Creator SHALL generate a debian/ directory containing control, changelog, rules, and compat files
2. WHEN a package name is provided, THE Package_Creator SHALL create a debhelper 7 compat-level rules file using the minimal dh $@ pattern
3. WHEN an architecture argument is provided, THE Package_Creator SHALL set the Architecture field in debian/control to the specified value
4. WHEN no architecture argument is provided, THE Package_Creator SHALL default the Architecture field to "all"
5. WHEN a version argument is provided, THE Package_Creator SHALL use the specified version in debian/changelog
6. WHEN no version argument is provided, THE Package_Creator SHALL default the version to "1.0-1"
7. THE Package_Creator SHALL produce package skeletons where the total built .deb file size is under 5KB for empty packages

### Requirement 2: Package Building

**User Story:** As a developer, I want to build .deb files from generated skeletons, so that I can populate test repositories with real packages.

#### Acceptance Criteria

1. WHEN a valid package skeleton directory exists, THE Package_Creator SHALL support building a .deb file using dpkg-buildpackage or equivalent tooling
2. WHEN building a package, THE Package_Creator SHALL produce a .deb file in the parent directory of the source tree
3. IF the build tools are not available on the system, THEN THE Package_Creator SHALL exit with a descriptive error message indicating which tools are missing

### Requirement 3: Repository Structure Creation

**User Story:** As a developer, I want to create APT repositories with proper directory layout, so that I can test debcraft's repository parsing against realistic structures.

#### Acceptance Criteria

1. WHEN a repository name is provided, THE Repository_Creator SHALL create a pool/main/ directory for storing .deb files
2. WHEN a repository name is provided, THE Repository_Creator SHALL create a dists/{suite}/main/binary-{arch}/ directory structure
3. WHEN no suite argument is provided, THE Repository_Creator SHALL default the suite name to "stable"
4. WHEN no architecture argument is provided, THE Repository_Creator SHALL default the architecture to "amd64"
5. WHEN multiple architectures are specified, THE Repository_Creator SHALL create separate binary-{arch}/ directories for each architecture
6. THE Repository_Creator SHALL place .deb files into the pool/main/ directory following Debian pool naming conventions

### Requirement 4: Repository Metadata Generation

**User Story:** As a developer, I want repository metadata to be auto-generated from pool contents, so that the repository is usable by APT tools.

#### Acceptance Criteria

1. WHEN .deb files exist in the pool directory, THE Repository_Creator SHALL generate a Packages index file in each binary-{arch}/ directory
2. WHEN .deb files exist in the pool directory, THE Repository_Creator SHALL generate a compressed Packages.gz file alongside the Packages file
3. WHEN a Packages file is generated, THE Repository_Creator SHALL generate a Release file in the dists/{suite}/ directory containing archive metadata
4. THE Repository_Creator SHALL use dpkg-scanpackages or apt-ftparchive to generate the Packages index
5. WHEN the repository contents change, THE Repository_Creator SHALL support regenerating all metadata files from the current pool contents

### Requirement 5: Git-Friendly Size Constraints

**User Story:** As a developer, I want all test fixtures to be small enough to commit to git, so that the test repository remains lightweight and fast to clone.

#### Acceptance Criteria

1. THE Package_Creator SHALL produce .deb files that are under 5KB each for packages with no installed files
2. THE Repository_Creator SHALL produce a complete single-package repository where total size including metadata is under 20KB
3. THE Package_Creator SHALL avoid generating unnecessary files that increase package size beyond the minimum required for a valid .deb
4. WHEN committing fixtures to git, THE Repository_Creator SHALL store regeneration scripts alongside any generated binary artifacts

### Requirement 6: Multiple Package Variants

**User Story:** As a developer, I want to create packages with different characteristics, so that I can test debcraft against diverse repository contents.

#### Acceptance Criteria

1. WHEN a dependencies argument is provided, THE Package_Creator SHALL add the specified dependencies to the Depends field in debian/control
2. WHEN a description argument is provided, THE Package_Creator SHALL set the Description field in debian/control to the specified text
3. THE Package_Creator SHALL support creating packages with different architectures including "all", "amd64", "arm64", and "any"
4. THE Package_Creator SHALL support creating multiple versions of the same package name for version-comparison testing

### Requirement 7: Script Preservation and Documentation

**User Story:** As a developer, I want all creation scripts and documentation committed to the repository, so that fixtures can be reproduced and understood by other contributors.

#### Acceptance Criteria

1. THE Package_Creator SHALL be a self-contained shell script stored in the fixtures/ directory
2. THE Repository_Creator SHALL be a self-contained shell script stored in the fixtures/ directory
3. WHEN the scripts are created, THE Repository_Creator SHALL generate a README.md file in the fixtures/ directory documenting usage instructions
4. THE README.md SHALL document the commands needed to create packages, build them, and assemble repositories
5. THE README.md SHALL document the expected directory layout of generated repositories

### Requirement 8: Repository Usability

**User Story:** As a developer, I want generated repositories to be usable with standard APT tools, so that I can verify debcraft behaves identically to real APT clients.

#### Acceptance Criteria

1. WHEN a repository is fully generated, THE APT_Repository SHALL be usable as a local file:// source in an APT sources.list configuration
2. THE Repository_Creator SHALL generate Release files that contain valid Date, Architectures, and Components fields
3. WHEN the repository is configured as an APT source, THE APT_Repository SHALL allow apt-get update to complete without errors when signature checking is disabled
