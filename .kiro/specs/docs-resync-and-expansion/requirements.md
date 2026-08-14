# Requirements Document

## Introduction

This feature expands and resynchronizes the DebCraft project documentation to cover three key areas:

1. **User-facing usage guides** for all supported artifact types (ISO, Docker, IMG, QCOW2)
2. **Developer-focused documentation** with additional code examples and scanner implementation guides
3. **SDK API reference** auto-generated from source code docstrings using mkdocstrings

The current documentation has a solid foundation (user guide, developer getting-started, architecture overview, and one detailed scanner guide for ISO). This expansion fills the gaps by adding per-artifact usage guides, more developer samples following the ISO scanner guide pattern, and a complete API reference section generated from the well-documented platform contracts and domain ports.

## Glossary

- **Documentation_System**: The MkDocs-based documentation site configured in `mkdocs.yml`, using the Material theme and mkdocstrings plugin for API reference generation.
- **User_Guide**: The `docs/user/` section targeting end users who invoke debcraft CLI commands.
- **Developer_Guide**: The `docs/developer/` section targeting contributors and plugin authors.
- **API_Reference**: A new `docs/api/` section containing auto-generated documentation from Python docstrings using the mkdocstrings plugin.
- **Artifact_Type**: One of the supported scan target formats: ISO, Docker, OCI, QCOW2, IMG, AMI, Directory.
- **Scanner**: An infrastructure adapter implementing the `ArtifactScanner` protocol to extract package metadata from a specific artifact type.
- **SDK**: The platform SDK (`src/debcraft/platform/sdk/`) and contracts (`src/debcraft/platform/contracts/`) providing interfaces for plugin development.
- **Code_Sample**: A self-contained, runnable Python script demonstrating a scanner or SDK component (following the pattern established in `docs/developer/iso-scanner.md`).
- **Fixture_Scripts**: Shell scripts under `fixtures/` that generate minimal test artifacts from standard tools, avoiding large binary files in the git repository (following the pattern of the existing `fixtures/build-iso.sh`).

## Requirements

### Requirement 1: User Guide — ISO Artifact Usage

**User Story:** As a user, I want documentation on how to scan ISO images with debcraft, so that I can generate SBOMs from Debian installer and live media ISOs.

#### Acceptance Criteria

1. WHEN a user navigates to the User_Guide, THE Documentation_System SHALL display a page covering ISO artifact scanning with CLI invocation examples.
2. THE User_Guide SHALL include at least one complete CLI example showing `debcraft sbom` invoked against a fixture ISO file path with expected output.
3. THE User_Guide SHALL document the prerequisites for ISO scanning (no root privileges required, no mount operations needed).
4. THE User_Guide SHALL reference the existing `fixtures/build-iso.sh` script for generating the test ISO fixture.
5. IF the ISO file does not exist at the specified path, THEN THE User_Guide SHALL document the expected error behavior.

### Requirement 2: User Guide — Docker Artifact Usage

**User Story:** As a user, I want documentation on how to scan Docker images with debcraft, so that I can generate SBOMs from exported Docker image tarballs.

#### Acceptance Criteria

1. WHEN a user navigates to the User_Guide, THE Documentation_System SHALL display a dedicated section covering Docker artifact scanning that includes at least one complete CLI invocation example showing the `debcraft sbom` command with a `--type docker` option and a tarball path argument.
2. THE User_Guide SHALL include at least one complete CLI example showing how to export a Docker image using `docker save` and scan the resulting tarball, with the two commands presented as sequential steps that a user can copy and execute without modification other than substituting the image name.
3. THE User_Guide SHALL document the layer merging behavior by explaining that layers are applied bottom-to-top, that `.wh.<filename>` entries remove the named file from lower layers, and that `.wh..wh..opq` entries remove all files in the containing directory from lower layers while preserving files added in the same layer.
4. THE User_Guide SHALL describe the expected output format as a table containing at minimum the columns: package name, version, architecture, and installation status for each identified package.
5. THE User_Guide SHALL reference a fixture script under `fixtures/` that generates a minimal Docker image tarball suitable for testing, and the script path SHALL correspond to an existing or newly created executable file in the `fixtures/` directory.
6. IF the Docker image tarball is missing, unreadable, or does not contain a valid `manifest.json`, THEN THE User_Guide SHALL document that debcraft reports a diagnostic error message and produces an empty package list without crashing.

### Requirement 3: User Guide — IMG Artifact Usage

**User Story:** As a user, I want documentation on how to scan raw disk images (IMG) with debcraft, so that I can generate SBOMs from embedded Linux images.

#### Acceptance Criteria

1. WHEN a user navigates to the User_Guide, THE Documentation_System SHALL display a section covering IMG artifact scanning that includes a heading, a description of the scanning workflow (partition inspection via guestfs, dpkg status extraction), and at least two CLI invocation examples demonstrating different option combinations.
2. THE User_Guide SHALL include at least one complete CLI example showing `debcraft sbom` invoked against a fixture IMG file with the `--type` option specified, followed by a representative sample of expected terminal output (progress indication and summary table).
3. THE User_Guide SHALL document that IMG scanning depends on `python3-guestfs` (libguestfs Python bindings) for partition inspection, explain that when guestfs is unavailable the scanner returns zero packages with a diagnostic message, and describe the filesystem analysis fallback behavior including its limitation of inferring packages from file paths rather than dpkg metadata.
4. THE User_Guide SHALL list each prerequisite package required for IMG scanning by its system package name, specify the install command for Debian/Ubuntu systems, and describe how to verify the dependency is available by running `debcraft doctor`.
5. THE User_Guide SHALL reference the fixture script under `fixtures/` that generates a minimal raw disk image for testing, including the script filename, a one-line description of what it produces, and the shell command to execute it.

### Requirement 4: User Guide — QCOW2 Artifact Usage

**User Story:** As a user, I want documentation on how to scan QCOW2 virtual disk images with debcraft, so that I can generate SBOMs from VM disk images.

#### Acceptance Criteria

1. WHEN a user navigates to the User_Guide, THE Documentation_System SHALL display a dedicated section for QCOW2 artifact scanning that includes at least one CLI invocation example and a description of the scanning workflow.
2. THE User_Guide SHALL include at least one CLI example showing `debcraft sbom` invoked against a `.qcow2` file path, including any required options (such as `--type qcow2` if auto-detection is not supported) and a description of the expected output (SBOM file generation).
3. THE User_Guide SHALL state that libguestfs (package `python3-guestfs`) is required for QCOW2 inspection, provide the installation command, and state that scanning returns an empty result with a diagnostic message when libguestfs is unavailable.
4. THE User_Guide SHALL state that QCOW2 and IMG scanning both depend on the GuestfsInspector interface and provide guidance on when to use each artifact type: QCOW2 for images with the QFI magic header, and IMG for raw disk images without a QCOW2 wrapper.
5. THE User_Guide SHALL include the path to a fixture script under `fixtures/` that generates a minimal QCOW2 image for testing, along with an example command to run the script.
6. WHEN libguestfs is not installed, THE User_Guide SHALL instruct the user that invoking `debcraft sbom` against a QCOW2 file produces zero packages and a diagnostic indicating the missing dependency.

### Requirement 5: Developer Guide — Docker Scanner Code Sample

**User Story:** As a developer, I want a detailed guide with a runnable code sample for the Docker scanner, so that I can understand the layer merging logic and contribute to the scanner.

#### Acceptance Criteria

1. WHEN a developer navigates to the Developer_Guide, THE Documentation_System SHALL display a Docker scanner guide page containing the following sections in order: Introduction, Prerequisites, How It Works (with subsections for each stage), a Mermaid diagram, Code Example, Running the Example (with expected output), and Extending.
2. THE Developer_Guide SHALL include a self-contained runnable Python code sample that imports `DockerScanner`, constructs an `Artifact` pointing at the fixture tarball, provides stub implementations for `ContentsIndexPort` and `PackageLookupPort`, invokes `scanner.scan()`, and prints each identified package in `name version architecture` format.
3. THE Developer_Guide SHALL explain the three-stage process: manifest parsing (reading manifest.json and extracting the layer list), layer extraction (iterating layers bottom-to-top, merging file entries into a virtual filesystem, and applying `.wh.*` and `.wh..wh..opq` whiteout semantics), and dpkg metadata parsing (locating and parsing `var/lib/dpkg/status` from the merged virtual filesystem).
4. THE Developer_Guide SHALL include a Mermaid diagram that depicts the Docker scanning flow showing at minimum: opening the tarball, reading manifest.json, iterating layers with whiteout processing, checking for `var/lib/dpkg/status` in the merged filesystem, and the fallback to filesystem analysis when dpkg status is absent.
5. THE Code_Sample SHALL use a fixture generated by a script located under `fixtures/` that creates a Docker-format tarball containing a valid `manifest.json` referencing at least two layer tarballs, where one layer includes a `var/lib/dpkg/status` file with at least one synthetic package entry, without requiring Docker to be installed.
6. WHEN a developer runs the code sample against the generated fixture, THE Code_Sample SHALL produce deterministic output listing the synthetic package entries embedded in the fixture (name, version, and architecture on each line).
7. THE Developer_Guide SHALL include a Prerequisites section that lists the steps to generate the fixture tarball by running the fixture script and specifies that no external dependencies beyond the Python standard library are required to run the code sample.

### Requirement 6: Developer Guide — IMG/QCOW2 Scanner Code Sample

**User Story:** As a developer, I want a detailed guide with a runnable code sample for the IMG and QCOW2 scanners, so that I can understand the GuestfsInspector abstraction and contribute to disk image scanning.

#### Acceptance Criteria

1. WHEN a developer navigates to the Developer_Guide, THE Documentation_System SHALL display a disk image scanner guide that contains dedicated sections for both the IMGScanner and QCOW2Scanner, describing each scanner's purpose, constructor dependencies, and invocation of the shared GuestfsInspector protocol.
2. THE Developer_Guide SHALL include a code sample that is syntactically valid Python, imports the GuestfsInspector protocol from the project's domain scanner ports, instantiates at least one disk image scanner with a stub GuestfsInspector implementation, and invokes the scan() method against a fixture-generated disk image.
3. THE Developer_Guide SHALL describe the two-stage fallback strategy by stating: (a) the scanner first attempts partition inspection via GuestfsInspector.inspect_os() and reads /var/lib/dpkg/status from the mounted partition, and (b) if no dpkg metadata is found on any partition, the scanner falls back to filesystem analysis using the ContentsIndexPort.
4. THE Developer_Guide SHALL include a Mermaid diagram that shows at minimum the following decision points in sequence: guestfs availability check, partition inspection via inspect_os(), dpkg status file read attempt, and filesystem analysis fallback, with labeled edges indicating success and failure transitions.
5. THE Code_Sample SHALL use fixtures generated by scripts under `fixtures/` that create disk images not exceeding 10 MB in size using standard tools (e.g., `dd`, `mkfs`, `qemu-img`) without requiring pre-committed binary files in the repository.
6. THE Developer_Guide SHALL include a prerequisites section listing the tools required to generate the fixture disk images and the Python dependencies required to run the code sample.
7. THE Developer_Guide SHALL include a "Running the Example" section with the exact shell commands needed to generate fixtures and execute the code sample, along with the expected output.

### Requirement 7: Developer Guide — Writing a Custom Scanner Plugin

**User Story:** As a plugin developer, I want a guide explaining how to implement a new artifact scanner, so that I can extend debcraft with custom artifact type support.

#### Acceptance Criteria

1. WHEN a developer navigates to the Developer_Guide, THE Documentation_System SHALL display a plugin development guide for writing custom scanners that covers the scanner protocol, a code sample, entry-point registration, and workflow context usage.
2. THE Developer_Guide SHALL document the `ArtifactScanner` protocol contract including the `scan(artifact: Artifact, context: WorkflowContext) -> ScanResult` method signature, the purpose of each parameter, the `ScanResult` return value, and the `ArtifactAccessError` raised when the artifact path is inaccessible.
3. THE Developer_Guide SHALL include a code sample showing a skeleton scanner implementation that defines a class with an async `scan` method accepting an `Artifact` and `WorkflowContext`, returns a `ScanResult` containing at least an empty packages list, and is syntactically valid Python importable without error.
4. THE Developer_Guide SHALL document the entry point registration mechanism by showing the `[project.entry-points."debcraft.scanners"]` table in `pyproject.toml` with a key-value pair mapping a scanner name to the dotted path of the scanner class.
5. THE Developer_Guide SHALL explain `WorkflowContext` usage in scanners by documenting how to check `cancellation_token.is_cancelled` for cooperative cancellation, how to call `progress.report(percentage, message)` with a percentage from 0.0 to 100.0, and how to emit log entries via the `logger` attribute.
6. THE Developer_Guide SHALL document the `Artifact` value object (containing `type: ArtifactType`, `path: str`, and `options: dict[str, str]`) and the `ScanResult` value object (containing `packages`, `strategy`, `diagnostics`, `duration_seconds`, and `artifact_path` fields) so that developers know what inputs the scanner receives and what outputs it must produce.

### Requirement 8: SDK API Reference Generation

**User Story:** As a developer, I want auto-generated API reference documentation from source code docstrings, so that I can browse the platform contracts, domain ports, and value objects without reading source files directly.

#### Acceptance Criteria

1. THE Documentation_System SHALL include an "API Reference" top-level section in the site navigation containing sub-pages for each documented module, positioned after the Developer Guide section.
2. THE API_Reference SHALL include documentation for all symbols exported in the `debcraft.platform.contracts` module's `__all__` list, including at minimum: Workflow, WorkflowContext, CancellationToken, ProgressReporter, EventBus, Logger, Container, Scope, ResourceManager, and ConfigurationService.
3. THE API_Reference SHALL include documentation for the `debcraft.domain.scanner.ports` module (ArtifactScanner, ContentsIndexPort, PackageLookupPort, GuestfsInspector protocols).
4. THE API_Reference SHALL include documentation for the `debcraft.domain.scanner.values` module (Artifact, ArtifactType, ScanResult, IdentifiedPackage, EnrichedPackage, PackageEnrichment value objects).
5. WHEN the `mkdocs build` command is executed, THE Documentation_System SHALL generate API reference pages using the mkdocstrings plugin with Google docstring convention, completing with exit code 0.
6. WHEN an API reference page is generated for a module, THE Documentation_System SHALL render for each documented class or protocol: the class name as a heading, the class docstring, each public method signature with parameter types and return type, and each method's docstring including Args, Returns, and Raises sections where present in the source.
7. IF a documented symbol lacks a docstring, THEN THE Documentation_System SHALL still render the symbol's signature and type annotations on the generated page without causing a build failure.

### Requirement 9: MkDocs Navigation Update

**User Story:** As a documentation reader, I want the navigation to reflect all new documentation pages, so that I can discover and access the expanded content.

#### Acceptance Criteria

1. THE Documentation_System SHALL update `mkdocs.yml` navigation to include all new User_Guide artifact pages under the "User Guide" section.
2. THE Documentation_System SHALL update `mkdocs.yml` navigation to include all new Developer_Guide pages under the "Developer Guide" section.
3. THE Documentation_System SHALL add a new "API Reference" top-level navigation section containing all generated API pages, positioned after the Developer Guide section.
4. WHEN `mkdocs build` is executed, THE Documentation_System SHALL produce a site with zero broken internal links across all pages (new and existing).

### Requirement 10: Documentation Build Validation

**User Story:** As a maintainer, I want the documentation build to succeed without warnings for all new content, so that documentation quality is maintained.

#### Acceptance Criteria

1. WHEN `mkdocs build --strict` is executed, THE Documentation_System SHALL complete with exit code 0 and produce zero warning or error messages in its output for all new and existing pages.
2. WHEN the documentation build is executed, THE Documentation_System SHALL resolve all mkdocstrings cross-references for the API_Reference pages such that every `::: module.path` directive produces rendered output without import errors or missing-module warnings.
3. IF a documented module's docstrings are missing or do not conform to the configured Google docstring style, THEN THE Documentation_System SHALL produce a build warning that identifies the module's fully-qualified name and the line number or symbol where the issue occurs.
4. IF a mkdocstrings cross-reference cannot be resolved due to an import error or undefined symbol, THEN THE Documentation_System SHALL fail the build with a non-zero exit code and produce an error message identifying the unresolved reference and its source page.

### Requirement 11: Fixture Scripts for Documentation Samples

**User Story:** As a developer, I want lightweight fixture generation scripts for all artifact types, so that documentation samples can be run without bundling large binary files in the git repository.

#### Acceptance Criteria

1. THE Fixture_Scripts SHALL exist under `fixtures/` and generate test artifacts using only tools available in standard Debian/Ubuntu package repositories.
2. THE Fixture_Scripts SHALL produce artifacts that do not exceed 100 KB per generated file.
3. WHEN a fixture script is executed, THE Fixture_Script SHALL create the corresponding artifact in `fixtures/images/` using the naming convention `test.<format-extension>` (e.g., `test.tar`, `test.img`, `test.qcow2`).
4. THE Fixture_Scripts SHALL include a script for generating a minimal Docker-format tarball containing at minimum a `manifest.json`, a layer directory with a `layer.tar` holding a synthetic `var/lib/dpkg/status` file with at least one valid package entry, and a `repositories` file.
5. THE Fixture_Scripts SHALL include a script for generating a raw disk image (IMG) of no more than 4 MB containing an ext4 partition with a synthetic `var/lib/dpkg/status` file holding at least one valid package entry.
6. THE Fixture_Scripts SHALL include a script for generating a QCOW2 image derived from the IMG fixture using `qemu-img convert`.
7. THE Fixture_Scripts SHALL document their tool dependencies as comment lines at the top of each script before any executable code, listing exact package names required for installation (e.g., `# Requires: genisoimage, qemu-img, tar`).
8. THE Fixture_Scripts SHALL be idempotent — running a script multiple times produces byte-identical output files without errors or leftover temporary files.
9. IF a required tool is not found on the system, THEN THE Fixture_Script SHALL exit with a non-zero exit code and print an error message to stderr indicating which tool is missing and how to install it.
