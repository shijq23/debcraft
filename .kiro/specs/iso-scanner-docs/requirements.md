# Requirements Document

## Introduction

This feature adds documentation and a test fixture for the ISO scanner component. The goal is to provide developers and users with a working example of how to use the `ISOScanner` programmatically, backed by a reproducible tiny ISO image that can be generated from a build script in the project's test fixtures directory. The documentation will live in the Developer Guide section alongside existing guides.

## Glossary

- **ISO_Image**: An ISO 9660 filesystem image file used as a scanning artifact.
- **Test_Fixture**: A static or generated test resource stored in the project's `tests/fixtures/` directory used by tests and documentation examples.
- **Build_Script**: A shell script in `tests/fixtures/` that generates the tiny ISO image from minimal Debian packages using standard command-line tools.
- **ISO_Scanner**: The `ISOScanner` class in `src/debcraft/infrastructure/scanners/iso.py` that scans ISO images for installed Debian packages.
- **Documentation_Site**: The MkDocs-based documentation served from the `docs/` directory.
- **Code_Snippet**: A self-contained Python example demonstrating programmatic usage of the ISO_Scanner.
- **Developer_Guide**: The `docs/developer/` section of the Documentation_Site aimed at contributors and integrators.

## Requirements

### Requirement 1: ISO Fixture Build Script

**User Story:** As a developer, I want a script that builds a tiny ISO image from packages, so that I have a reproducible test fixture for ISO scanner tests and documentation examples.

#### Acceptance Criteria

1. THE Build_Script SHALL produce a file named `test.iso` in `tests/fixtures/` that is a valid ISO 9660 filesystem image verifiable by running `file` on the output and confirming it reports an ISO 9660 type.
2. THE Build_Script SHALL create an ISO_Image that contains a `var/lib/dpkg/status` file with at least one package entry containing the mandatory dpkg status fields: `Package`, `Status`, `Version`, and `Architecture`, each on its own line, separated from other entries by a blank line.
3. THE Build_Script SHALL use only tools commonly available on Debian-based systems (genisoimage or xorriso, dpkg-deb).
4. THE Build_Script SHALL complete execution in under 30 seconds on a standard development machine.
5. THE Build_Script SHALL be idempotent, meaning repeated executions produce an ISO_Image with identical directory structure and identical `var/lib/dpkg/status` file content, though file-level metadata such as creation timestamps may differ.
6. IF a required tool is missing, THEN THE Build_Script SHALL exit with a non-zero status code and print a message to stderr identifying the missing tool by name.
7. IF the output directory `tests/fixtures/` does not exist, THEN THE Build_Script SHALL create it before writing the ISO_Image.

### Requirement 2: ISO Scanner Documentation Page

**User Story:** As a developer, I want documentation showing how to use the ISO scanner programmatically, so that I can integrate ISO scanning into my workflows.

#### Acceptance Criteria

1. THE Documentation_Site SHALL include a page at `docs/developer/iso-scanner.md` explaining ISO_Scanner usage.
2. THE documentation page SHALL contain a Code_Snippet demonstrating how to instantiate and invoke the ISO_Scanner with protocol-compliant reader implementations including `ISOReader`, `SquashfsReader`, `ContentsIndexPort`, and `PackageLookupPort`.
3. THE documentation page SHALL explain the scanning strategy fallback chain in order: (a) squashfs search at paths `live/filesystem.squashfs`, `casper/filesystem.squashfs`, `install/filesystem.squashfs`, (b) direct `var/lib/dpkg/status` in the ISO root, (c) filesystem analysis via Contents index lookup.
4. THE documentation page SHALL reference the Test_Fixture ISO_Image at `tests/fixtures/test.iso` as the example artifact used in the Code_Snippet.
5. THE documentation page SHALL be linked from the Developer_Guide navigation in `mkdocs.yml` under the "Developer Guide" section.

### Requirement 3: Self-Contained Code Example

**User Story:** As a developer, I want a runnable code example that scans the fixture ISO, so that I can verify the scanner works end-to-end on a real artifact.

#### Acceptance Criteria

1. THE Code_Snippet SHALL demonstrate creating minimal implementations of the `ISOReader` and `SquashfsReader` protocols that satisfy all methods defined in those protocols (`open`, `list_dir`, `read_file`, `close`).
2. THE Code_Snippet SHALL demonstrate creating stub implementations of the `ContentsIndexPort` and `PackageLookupPort` protocols required by the `ISOScanner` constructor.
3. THE Code_Snippet SHALL construct an `Artifact` instance with type `ArtifactType.ISO` and a path pointing to the Test_Fixture ISO_Image in `tests/fixtures/`, and a `WorkflowContext` (or minimal stub satisfying its interface) to pass to `ISOScanner.scan()`.
4. THE Code_Snippet SHALL invoke `ISOScanner.scan()` against the Test_Fixture ISO_Image and print at least the name, version, and architecture of each package in the resulting `ScanResult.packages` list.
5. THE Code_Snippet SHALL handle the async nature of the `scan()` method using `asyncio.run()` or equivalent.
6. THE Code_Snippet SHALL include inline comments explaining each step of the scanning process, covering at minimum: dependency instantiation, artifact construction, scan invocation, and result output.
7. IF the Test_Fixture ISO_Image does not exist at the expected `tests/fixtures/` path, THEN THE Code_Snippet SHALL print an error message that names the missing file and directs the user to run the Build_Script to generate it.

### Requirement 4: MkDocs Navigation Integration

**User Story:** As a documentation reader, I want the ISO scanner page discoverable from the site navigation, so that I can find it without searching.

#### Acceptance Criteria

1. THE Documentation_Site SHALL include the ISO scanner page under the "Developer Guide" section in the navigation with the label "ISO Scanner".
2. THE navigation entry SHALL appear immediately after the "Getting Started" entry in the Developer_Guide section.
3. THE navigation entry SHALL link to a reachable page file at `docs/developer/iso-scanner.md` that renders without errors when the documentation site is built.
