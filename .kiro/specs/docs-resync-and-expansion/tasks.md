# Implementation Plan: Documentation Resync and Expansion

## Overview

This plan implements the documentation expansion in a layered approach: fixture scripts first (since documentation pages reference them), then user guides, developer guides, API reference pages, and finally the mkdocs.yml navigation update with build validation. Tests validate documentation content and fixture correctness.

## Tasks

- [x] 1. Create fixture scripts for Docker, IMG, and QCOW2 artifacts
  - [x] 1.1 Create `fixtures/build-docker.sh` fixture script
    - Generate a minimal Docker-format tarball at `fixtures/images/test.tar`
    - Must contain `manifest.json`, at least two layer tarballs (one with `var/lib/dpkg/status` containing a synthetic package entry), and a `repositories` file
    - Follow the pattern of `fixtures/build-iso.sh`: shebang, `set -euo pipefail`, tool presence check with actionable error, trap-based cleanup, deterministic output
    - Document required tools (`tar`) in comments at script top
    - Output must not exceed 100 KB
    - Must be idempotent (byte-identical output on repeated runs)
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.7, 11.8, 11.9_

  - [x] 1.2 Create `fixtures/build-img.sh` fixture script
    - Generate a raw disk image at `fixtures/images/test.img` (≤4 MB)
    - Must contain an ext4 partition with a synthetic `var/lib/dpkg/status` file holding at least one valid package entry
    - Use `dd` and `mkfs.ext4` (from e2fsprogs); document tool dependencies in comments
    - Follow `build-iso.sh` patterns for error handling and cleanup
    - Must be idempotent
    - _Requirements: 11.1, 11.2, 11.3, 11.5, 11.7, 11.8, 11.9_

  - [x] 1.3 Create `fixtures/build-qcow2.sh` fixture script
    - Generate a QCOW2 image at `fixtures/images/test.qcow2` derived from the IMG fixture using `qemu-img convert`
    - Require `qemu-img` (from qemu-utils); document in comments
    - Must invoke `build-img.sh` if `test.img` doesn't exist, or depend on it being present
    - Output must not exceed 100 KB
    - Must be idempotent
    - _Requirements: 11.1, 11.2, 11.3, 11.6, 11.7, 11.8, 11.9_

- [x] 2. Checkpoint — Verify fixture scripts
  - Ensure all three fixture scripts run successfully and produce output at expected paths. Ask the user if questions arise.

- [x] 3. Write User Guide pages
  - [x] 3.1 Create `docs/user/iso.md` — ISO scanning user guide
    - Cover CLI invocation with `debcraft sbom` against a fixture ISO path
    - Document prerequisites (no root, no mount needed)
    - Reference `fixtures/build-iso.sh` for fixture generation
    - Document error behavior when ISO file doesn't exist
    - Include expected output example
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 3.2 Create `docs/user/docker.md` — Docker scanning user guide
    - Include CLI example with `--type docker` and tarball path
    - Show `docker save` + scan as sequential steps
    - Explain layer merging: bottom-to-top application, `.wh.<filename>` whiteout, `.wh..wh..opq` opaque whiteout
    - Describe output format table (package name, version, architecture, installation status)
    - Reference `fixtures/build-docker.sh` script
    - Document error behavior for missing/invalid tarball
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [x] 3.3 Create `docs/user/img.md` — IMG scanning user guide
    - Include heading, scanning workflow description, and at least two CLI examples with different options
    - Document `python3-guestfs` dependency with install command and `debcraft doctor` verification
    - Explain filesystem analysis fallback and its limitations
    - List prerequisite packages by system name
    - Reference `fixtures/build-img.sh` with description and execution command
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x] 3.4 Create `docs/user/qcow2.md` — QCOW2 scanning user guide
    - Include CLI example with `--type qcow2` option
    - State libguestfs requirement with install command
    - Explain QCOW2 vs IMG guidance (QFI magic header vs raw)
    - Reference `fixtures/build-qcow2.sh` script with example command
    - Document diagnostic behavior when libguestfs is unavailable
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

- [x] 4. Write Developer Guide pages
  - [x] 4.1 Create `docs/developer/docker-scanner.md` — Docker scanner developer guide
    - Structure: Introduction → Prerequisites → How It Works → Mermaid diagram → Code Example → Running the Example → Extending
    - Include runnable Python code sample importing `DockerScanner`, constructing `Artifact`, stub ports, calling `scan()`, printing results
    - Explain three-stage process: manifest parsing, layer extraction with whiteout, dpkg metadata parsing
    - Include Mermaid diagram showing: open tarball → read manifest.json → iterate layers with whiteout → check dpkg/status → fallback
    - Code sample uses fixture from `fixtures/build-docker.sh`; must produce deterministic output
    - Include Prerequisites section with fixture generation steps
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

  - [x] 4.2 Create `docs/developer/disk-image-scanner.md` — IMG/QCOW2 scanner developer guide
    - Dedicated sections for IMGScanner and QCOW2Scanner covering purpose, constructor deps, GuestfsInspector usage
    - Include syntactically valid Python code sample with stub GuestfsInspector, importing from domain scanner ports
    - Describe two-stage fallback: partition inspection via `inspect_os()` → dpkg status read → filesystem analysis fallback
    - Include Mermaid diagram with decision points: guestfs check → inspect_os() → dpkg read → filesystem fallback
    - Use fixtures from `fixtures/build-img.sh` and `fixtures/build-qcow2.sh` (≤10 MB)
    - Include Prerequisites and "Running the Example" sections with exact commands and expected output
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7_

  - [x] 4.3 Create `docs/developer/writing-a-scanner.md` — Plugin authoring guide
    - Document `ArtifactScanner` protocol: `scan(artifact: Artifact, context: WorkflowContext) -> ScanResult` with parameter descriptions
    - Include skeleton scanner code sample (async `scan` method, returns `ScanResult` with empty packages list)
    - Document entry-point registration: `[project.entry-points."debcraft.scanners"]` in pyproject.toml
    - Explain `WorkflowContext` usage: `cancellation_token.is_cancelled`, `progress.report(percentage, message)`, `logger` attribute
    - Document `Artifact` value object (`type`, `path`, `options`) and `ScanResult` value object (`packages`, `strategy`, `diagnostics`, `duration_seconds`, `artifact_path`)
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

- [x] 5. Checkpoint — Verify documentation content
  - Ensure all user and developer guide pages are well-formed Markdown. Ask the user if questions arise.

- [x] 6. Create API Reference pages
  - [x] 6.1 Create `docs/api/index.md` — API reference overview
    - Provide an introduction to the API reference section
    - Link to sub-pages for contracts, scanner ports, and scanner values
    - _Requirements: 8.1_

  - [x] 6.2 Create `docs/api/contracts.md` — Platform contracts API reference
    - Use `::: debcraft.platform.contracts` mkdocstrings directive
    - Configure with `show_root_heading: true`, `members_order: source`, `docstring_style: google`
    - Must render all `__all__` symbols: Workflow, WorkflowContext, CancellationToken, ProgressReporter, EventBus, Logger, Container, Scope, ResourceManager, ConfigurationService
    - _Requirements: 8.2, 8.5, 8.6_

  - [x] 6.3 Create `docs/api/scanner-ports.md` — Scanner ports API reference
    - Use `::: debcraft.domain.scanner.ports` mkdocstrings directive
    - Must render: ArtifactScanner, ContentsIndexPort, PackageLookupPort, GuestfsInspector
    - _Requirements: 8.3, 8.5, 8.6_

  - [x] 6.4 Create `docs/api/scanner-values.md` — Scanner values API reference
    - Use `::: debcraft.domain.scanner.values` mkdocstrings directive
    - Must render: Artifact, ArtifactType, ScanResult, IdentifiedPackage, EnrichedPackage, PackageEnrichment
    - _Requirements: 8.4, 8.5, 8.6_

- [x] 7. Update mkdocs.yml navigation
  - [x] 7.1 Update `mkdocs.yml` with complete navigation structure
    - Add all User Guide artifact pages under "User Guide" section
    - Add all new Developer Guide pages under "Developer Guide" section
    - Add new "API Reference" top-level section after Developer Guide with all API pages
    - Ensure every nav entry corresponds to an existing Markdown file
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

- [x] 8. Build validation and testing
  - [x] 8.1 Validate documentation build with `mkdocs build --strict`
    - Ensure exit code 0 with zero warnings/errors
    - Verify all mkdocstrings directives resolve without import errors
    - Verify all internal links are valid
    - Fix any issues that arise
    - _Requirements: 10.1, 10.2, 10.3, 10.4_

  - [x] 8.2 Write pytest tests for documentation content assertions
    - Create `tests/docs/` test directory
    - Write tests verifying each user guide contains required content (CLI examples, prerequisites, error docs)
    - Write tests verifying developer guides contain required sections and code samples
    - Write tests verifying navigation includes all expected pages
    - _Requirements: 1.1–1.5, 2.1–2.6, 3.1–3.5, 4.1–4.6, 5.1–5.7, 6.1–6.7, 7.1–7.6, 8.1–8.6, 9.1–9.4_

  - [x] 8.3 Write pytest tests for fixture script validation
    - Test that each fixture produces output at expected path
    - Test output size limits (100 KB for Docker/QCOW2, 4 MB for IMG)
    - Test Docker fixture internal structure (manifest.json, layer.tar presence)
    - Test idempotency (running twice produces identical output)
    - Test graceful failure on missing tools
    - _Requirements: 11.2, 11.3, 11.4, 11.5, 11.6, 11.8, 11.9_

- [x] 9. Final checkpoint — Ensure all tests pass
  - Ensure `mkdocs build --strict` passes and all tests pass. Ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Fixture scripts must be created first since documentation pages reference them
- The existing `fixtures/build-iso.sh` serves as the template for all new fixture scripts
- API reference pages rely on mkdocstrings which imports Python modules at build time — the source code must be importable
- Developer guide code samples should be validated as syntactically correct Python

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "3.1", "3.2", "3.3", "3.4"] },
    { "id": 2, "tasks": ["4.1", "4.2", "4.3"] },
    { "id": 3, "tasks": ["6.1", "6.2", "6.3", "6.4"] },
    { "id": 4, "tasks": ["7.1"] },
    { "id": 5, "tasks": ["8.1"] },
    { "id": 6, "tasks": ["8.2", "8.3"] }
  ]
}
```
