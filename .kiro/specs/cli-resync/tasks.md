# Implementation Plan: CLI Resync

## Overview

This implementation plan focuses on verifying and hardening the existing CLI command registration, ensuring all commands from milestones M0–M7 are properly wired, discoverable, and tested. The existing code already has the correct structure — this work adds a comprehensive test harness (unit tests + property-based tests) that guarantees the full command surface is accessible and entry points resolve correctly.

## Tasks

- [x] 1. Verify CLI registration and add unit tests for command discoverability
  - [x] 1.1 Add unit tests for top-level help output completeness
    - Extend `tests/unit/test_cli.py` with tests that invoke `debcraft --help` and assert all top-level commands (`version`, `doctor`, `info`, `sbom`) and sub-apps (`mirror`, `index`) appear in the output
    - Add test for bare invocation (no subcommand) showing help text
    - Add test for `--verbose` with no subcommand still showing help
    - _Requirements: 5.1, 5.2, 5.3, 7.3, 7.4_

  - [x] 1.2 Add unit tests for sub-app help output completeness
    - Add tests invoking `debcraft mirror --help` and asserting sub-commands `sync`, `verify`, `status`, `list`, `clean` appear
    - Add tests invoking `debcraft index --help` and asserting sub-command `package` appears
    - Add test for `debcraft sbom --help` showing arguments and options
    - _Requirements: 5.4, 5.5_

  - [x] 1.3 Add unit test for verbose flag setting DEBUG level
    - Test that invoking with `--verbose` sets the `debcraft` logger to DEBUG level
    - _Requirements: 7.1_

  - [x] 1.4 Add unit tests for entry point resolution
    - Write tests that use `importlib.metadata.entry_points()` to load all declared scanner entry points (directory, docker, oci, iso, qcow2, img, ami) and call `.load()` without error
    - Write tests that load all SBOM writer entry points (spdx_3_0, spdx_2_3, cyclonedx) and call `.load()` without error
    - Write test that verifies `debcraft.cli:app` resolves to a `typer.Typer` instance
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

- [x] 2. Add property-based tests for CLI registration correctness
  - [x] 2.1 Write property test for format validation (Property 1)
    - **Property 1: Format validation accepts only valid formats**
    - Create `tests/properties/infrastructure/test_cli_registration_properties.py`
    - Use Hypothesis `@given(st.text())` to generate arbitrary strings and verify `_validate_formats` accepts only `{spdx_3_0, spdx_2_3, cyclonedx}` and rejects everything else
    - Use `@settings(max_examples=100)`
    - **Validates: Requirements 4.2, 4.3**

  - [x] 2.2 Write property test for help accessibility (Property 2)
    - **Property 2: Help accessibility for all registered commands**
    - Use Hypothesis `@given(st.sampled_from(ALL_COMMAND_PATHS))` where `ALL_COMMAND_PATHS` is the set of all valid command paths (e.g., `["version"]`, `["mirror", "sync"]`, `["index", "package", "--help"]`)
    - Assert that invoking each path with `--help` via CliRunner exits with code 0 and output contains no tracebacks
    - Use `@settings(max_examples=100)`
    - **Validates: Requirements 5.3, 5.4, 5.5, 7.2**

  - [x] 2.3 Write property test for entry point resolution (Property 3)
    - **Property 3: Entry point resolution for all declared plugins**
    - Use Hypothesis `@given(st.sampled_from(ALL_ENTRY_POINTS))` where `ALL_ENTRY_POINTS` is the combined list of scanner and writer entry point names
    - Assert that each entry point can be loaded via `importlib.metadata.entry_points()` and `.load()` resolves without `ImportError` or `AttributeError`
    - Use `@settings(max_examples=100)`
    - **Validates: Requirements 6.2, 6.3, 6.4**

- [x] 3. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Add unit tests for SBOM and mirror error paths
  - [x] 4.1 Add unit tests for SBOM command error handling
    - Test that `debcraft sbom /nonexistent/path` exits with non-zero code and displays error
    - Test that `debcraft sbom /tmp --format invalid_format` exits with non-zero code and lists valid formats
    - Test that `debcraft sbom /tmp` with no format option defaults to all formats (validates no crash on format validation)
    - _Requirements: 4.3, 4.4, 4.7_

  - [x] 4.2 Add unit tests for mirror sync with no repositories configured
    - Test that `debcraft mirror sync` with empty config exits with non-zero code and error message
    - Test that `debcraft mirror clean` with empty config exits with non-zero code and error message
    - _Requirements: 2.8_

- [x] 5. Verify and fix any registration gaps
  - [x] 5.1 Audit `src/debcraft/cli/__init__.py` for registration correctness
    - Verify that `mirror_app` is registered with `app.add_typer(mirror_app, name="mirror")`
    - Verify that `index_app` is registered with `app.add_typer(index_app, name="index")`
    - Verify that `sbom` is registered with `app.command()(sbom_command)`
    - Verify that `version`, `doctor`, `info` are decorated with `@app.command()`
    - Fix any missing or incorrect registrations if found
    - _Requirements: 5.1, 5.2, 5.3, 4.8, 6.1_

  - [x] 5.2 Verify `pyproject.toml` entry points match implementations
    - Confirm `[project.scripts]` declares `debcraft = "debcraft.cli:app"`
    - Confirm all 7 scanner entry points reference valid module paths
    - Confirm all 3 SBOM writer entry points reference valid module paths
    - Fix any mismatches if found
    - _Requirements: 6.1, 6.2, 6.3_

- [x] 6. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The design confirms no structural changes are needed to the CLI — this is a verification and hardening exercise
- All tests use `typer.testing.CliRunner` and `importlib.metadata` for non-invasive validation
- Hypothesis is already configured in `pyproject.toml` dev dependencies

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3", "1.4"] },
    { "id": 1, "tasks": ["2.1", "2.2", "2.3", "4.1", "4.2"] },
    { "id": 2, "tasks": ["5.1", "5.2"] }
  ]
}
```
