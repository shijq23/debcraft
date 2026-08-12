# Implementation Plan: ISO Scanner Documentation & Fixture

## Overview

Create a reproducible ISO test fixture, a developer documentation page demonstrating the ISO scanner, and wire the page into the mkdocs navigation. The implementation uses shell for the build script and Python for the code example, leveraging the existing `ISOScanner` class and its protocol dependencies.

## Tasks

- [x] 1. Create the ISO fixture build script
  - [x] 1.1 Create `tests/fixtures/build-iso.sh`
    - Write a bash script that checks for `genisoimage`, creates a staging directory with `var/lib/dpkg/status` containing one synthetic package entry (`base-files`, version `13.5`, arch `amd64`, status `install ok installed`), runs `genisoimage` to produce `tests/fixtures/test.iso`, and cleans up the staging directory
    - Include tool-presence check that exits non-zero with stderr message if `genisoimage` is missing
    - Create `tests/fixtures/` with `mkdir -p` if it does not exist
    - Make the script executable (`chmod +x`)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

  - [x] 1.2 Generate the fixture ISO by running the build script
    - Execute `tests/fixtures/build-iso.sh` and verify it exits 0
    - Confirm `tests/fixtures/test.iso` exists and `file` reports it as ISO 9660
    - _Requirements: 1.1, 1.4_

- [x] 2. Checkpoint - Verify fixture ISO validity
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Create the ISO scanner documentation page
  - [x] 3.1 Create `docs/developer/iso-scanner.md` with prose sections
    - Write Introduction explaining what the ISO scanner does and when to use it
    - Write Prerequisites section directing the user to run `tests/fixtures/build-iso.sh` and install `pycdlib`
    - Write "How It Works" section explaining the scanning strategy fallback chain: (a) squashfs search at `live/filesystem.squashfs`, `casper/filesystem.squashfs`, `install/filesystem.squashfs`, (b) direct `var/lib/dpkg/status` in ISO root, (c) filesystem analysis via Contents index
    - Include a Mermaid diagram of the fallback flow
    - Reference the test fixture at `tests/fixtures/test.iso`
    - _Requirements: 2.1, 2.3, 2.4_

  - [x] 3.2 Add the self-contained Python code example to the documentation page
    - Implement a minimal `ISOReader` using `pycdlib` (satisfies `open`, `list_dir`, `read_file`, `close`)
    - Implement a stub `SquashfsReader` that raises `FileNotFoundError` on all operations
    - Implement stub `ContentsIndexPort` returning empty dict and stub `PackageLookupPort` returning `None`
    - Implement a minimal `WorkflowContext` stub with no-op progress, uncancelled token, and dummy attributes
    - Construct an `Artifact` with `ArtifactType.ISO` and path `tests/fixtures/test.iso`
    - Invoke `ISOScanner.scan()` with `asyncio.run()` and print each package's name, version, and architecture
    - Add inline comments explaining each step: dependency instantiation, artifact construction, scan invocation, result output
    - Add a file-existence check that prints an error message naming the missing file and directing the user to run `build-iso.sh`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

  - [x] 3.3 Add "Running the Example" and "Extending" sections
    - Show shell commands to generate fixture and run the example
    - Provide pointers for implementing a real SquashfsReader and custom ports
    - _Requirements: 2.1_

- [x] 4. Update mkdocs navigation
  - [x] 4.1 Add ISO Scanner entry to `mkdocs.yml`
    - Insert `- ISO Scanner: developer/iso-scanner.md` in the "Developer Guide" nav section immediately after "Getting Started"
    - _Requirements: 4.1, 4.2, 4.3_

- [x] 5. Checkpoint - Verify documentation builds
  - Ensure all tests pass, ask the user if questions arise.

  - [x] 5.1 Verify `mkdocs build --strict` completes without errors
    - Run `mkdocs build --strict` and confirm exit code 0
    - Confirm the generated site contains `developer/iso-scanner/index.html`
    - _Requirements: 4.3_

- [x] 6. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- No property-based tests are included because the feature consists of a shell script, a documentation page, and a configuration change — there are no pure functions with meaningful input variation to test with PBT.
- The code example uses `pycdlib` as an optional dependency for demonstration only; it is not added to the project's runtime dependencies.
- The fixture ISO uses the "direct rootfs" scanning path (strategy fallback step b) which exercises the scanner's primary dpkg-parsing code without needing squashfs generation tooling.
- Each task references specific acceptance criteria from the requirements document for traceability.
- Checkpoints ensure incremental validation of the build script and documentation build.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "3.1"] },
    { "id": 2, "tasks": ["3.2", "3.3", "4.1"] },
    { "id": 3, "tasks": ["5.1"] }
  ]
}
```
