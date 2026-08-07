# Implementation Plan: Debian Test Repositories

## Overview

Create two shell scripts (`create-package.sh` and `create-repo.sh`), a `Makefile`, a `README.md`, and property-based tests that together provide minimal Debian package and APT repository fixtures for testing debcraft's repository parsing and package analysis.

## Tasks

- [x] 1. Create the package skeleton generator script
  - [x] 1.1 Implement `fixtures/create-package.sh`
    - Create the shell script with `set -euo pipefail`
    - Implement CLI argument parsing for `--version`, `--arch`, `--depends`, `--description`, `--output-dir`, `--build`, and `--help`
    - Validate package name against regex `[a-z0-9][a-z0-9.+\-]+`
    - Generate `debian/control`, `debian/changelog`, `debian/rules`, `debian/compat` files with proper content
    - Implement `--build` flag to invoke `dpkg-buildpackage -us -uc -b`
    - Add error handling for missing tools (`dpkg-buildpackage`, `dpkg-dev`)
    - Default values: version `1.0-1`, arch `all`, output-dir `fixtures/packages/`
    - Make the script executable
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2, 2.3, 6.1, 6.2, 6.3, 6.4, 7.1_

  - [x] 1.2 Write property tests for package skeleton creation
    - **Property 1: Package skeleton completeness**
    - **Validates: Requirements 1.1**
    - Use hypothesis to generate valid Debian package names
    - For each generated name, invoke the skeleton creation logic and assert presence of `debian/control`, `debian/changelog`, `debian/rules`, `debian/compat`

  - [x] 1.3 Write property tests for control file argument reflection
    - **Property 2: Control file reflects arguments**
    - **Validates: Requirements 1.3, 1.5, 6.1, 6.2**
    - Use hypothesis to generate valid combinations of name, architecture, version, dependencies, and description
    - Assert generated `debian/control` contains the exact field values and `debian/changelog` contains the version

- [x] 2. Create the repository assembler script
  - [x] 2.1 Implement `fixtures/create-repo.sh`
    - Create the shell script with `set -euo pipefail`
    - Implement CLI argument parsing for `--suite`, `--arch`, `--component`, `--output-dir`, `--add-package`, `--generate-metadata`, and `--help`
    - Create `pool/{component}/` and `dists/{suite}/{component}/binary-{arch}/` directory structures
    - Implement `--add-package` to copy .deb files into pool using Debian naming conventions (first letter for regular packages, first four letters for lib* packages)
    - Implement `--generate-metadata` to run `dpkg-scanpackages`, compress with gzip, and run `apt-ftparchive release`
    - Add error handling for missing tools (`dpkg-scanpackages`, `apt-ftparchive`, `gzip`)
    - Default values: suite `stable`, arch `amd64`, component `main`, output-dir `fixtures/repositories/`
    - Make the script executable
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 4.1, 4.2, 4.3, 4.4, 4.5, 7.2_

  - [x] 2.2 Write property tests for repository directory structure
    - **Property 3: Repository directory structure**
    - **Validates: Requirements 3.1, 3.2, 3.5**
    - Use hypothesis to generate valid repo name, suite, and architecture list combinations
    - Assert `pool/main/` exists and `dists/{suite}/main/binary-{arch}/` exists for each architecture

  - [x] 2.3 Write property tests for pool naming convention
    - **Property 4: Pool naming convention**
    - **Validates: Requirements 3.6**
    - Use hypothesis to generate valid package names (including lib* prefixed names)
    - Assert pool path uses first character prefix for regular packages and first four characters for lib* packages

- [x] 3. Checkpoint - Ensure scripts are correct
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Create Makefile and README
  - [x] 4.1 Create `fixtures/Makefile`
    - Implement `all`, `packages`, `repos`, and `clean` targets
    - `packages` target builds hello (all, 1.0-1), libfoo (amd64, 2.0-1), and depends-on-hello (all, 1.0-1, depends on hello)
    - `repos` target creates test-repo with amd64,arm64 architectures and adds built packages with metadata generation
    - `clean` target removes generated packages and repositories
    - _Requirements: 5.4, 7.1, 7.2_

  - [x] 4.2 Create `fixtures/README.md`
    - Document the purpose of the fixture scripts
    - Document usage instructions for `create-package.sh` with examples
    - Document usage instructions for `create-repo.sh` with examples
    - Document expected directory layout of generated artifacts
    - Document prerequisites (dpkg-dev, apt-utils)
    - Document Makefile targets
    - _Requirements: 7.3, 7.4, 7.5_

- [x] 5. Implement property-based test infrastructure
  - [x] 5.1 Create test file `tests/test_debian_test_repos.py` with hypothesis strategies and helper utilities
    - Define hypothesis strategies: `package_names`, `versions`, `architectures`, `arch_sets`, `suites`, `dependencies`, `descriptions`
    - Create Python helper functions that replicate the script logic for skeleton generation and pool path calculation (to test without requiring dpkg tools)
    - Configure pytest markers for `unit` and `integration`
    - _Requirements: 1.1, 3.6_

  - [x] 5.2 Write property test for metadata regeneration idempotence
    - **Property 5: Metadata regeneration idempotence**
    - **Validates: Requirements 4.5**
    - Mark with `@pytest.mark.integration` (requires actual Debian tools)
    - Generate a repository with fixed .deb files, run metadata generation twice, assert identical output

  - [x] 5.3 Write property test for Release file parsability
    - **Property 6: Release file parsability**
    - **Validates: Requirements 8.2**
    - Mark with `@pytest.mark.integration` (requires actual Debian tools)
    - Generate a repository, parse the Release file with debcraft's `ReleaseParser`, assert non-null date, architectures, and non-empty files list

  - [x] 5.4 Write unit tests for specific behaviors
    - Test default architecture is "all"
    - Test default version is "1.0-1"
    - Test default suite is "stable"
    - Test script files exist at expected fixture paths
    - Test invalid package names are rejected
    - _Requirements: 1.4, 1.6, 3.3, 3.4_

- [x] 6. Wire integration and validate size constraints
  - [x] 6.1 Wire end-to-end flow and validate size constraints
    - Remove `.gitkeep` from `fixtures/repositories/` and `fixtures/packages/` if scripts create those directories
    - Ensure `create-package.sh` and `create-repo.sh` work together (package built by create-package.sh can be added to repo by create-repo.sh)
    - Add integration test verifying built .deb < 5KB and single-package repository < 20KB
    - Mark integration tests with `@pytest.mark.integration`
    - _Requirements: 5.1, 5.2, 5.3, 8.1, 8.3_

- [x] 7. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Properties 1-4 can be tested with Python helpers replicating script logic (no dpkg tools needed)
- Properties 5-6 require actual Debian tools and are marked as integration tests
- Shell scripts use `set -euo pipefail` for strict error handling

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "5.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "2.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "4.1", "4.2"] },
    { "id": 3, "tasks": ["5.2", "5.3", "5.4"] },
    { "id": 4, "tasks": ["6.1"] }
  ]
}
```
