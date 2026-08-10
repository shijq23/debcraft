# Implementation Plan: Makefile Tasks

## Overview

Create a Makefile at the project root with six independent targets (`test`, `lint`, `clean`, `build`, `mirror`, `index`) that wrap development commands behind `uv run`. The Makefile uses `.PHONY` declarations, sequential lint execution with early exit, and silent cleanup behavior.

## Tasks

- [x] 1. Create Makefile with phony declarations and test target
  - [x] 1.1 Create `Makefile` at project root with `.PHONY` declaration and `test` target
    - Create the file `Makefile` in the project root directory
    - Declare `.PHONY: test lint clean build mirror index` at the top
    - Implement the `test` target as the first target (making it the default)
    - The `test` recipe: `uv run pytest`
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 2. Add lint target with sequential execution
  - [x] 2.1 Add `lint` target with chained linter commands
    - Add `lint` target after `test`
    - Recipe chains three commands with `&&`: `uv run ruff format --check . && uv run ruff check . && uv run basedpyright`
    - Ensures first failure stops execution and propagates exit code
    - _Requirements: 2.1, 2.2, 2.3_

- [x] 3. Add clean target
  - [x] 3.1 Add `clean` target that removes all build artifacts
    - Add `clean` target that removes: `dist/`, all `__pycache__/` directories (recursively), `*.egg-info` directories, `.ruff_cache/`, `.pytest_cache/`, `.coverage` file, and `htmlcov/` directory
    - Use `rm -rf` for directories and files (naturally succeeds when targets don't exist)
    - Use `find . -type d -name "__pycache__" -exec rm -rf {} +` for recursive pycache removal
    - Use `find . -type d -name "*.egg-info" -exec rm -rf {} +` for egg-info removal
    - Target must always exit 0 with no stderr output
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

- [x] 4. Add build target with pre-clean
  - [x] 4.1 Add `build` target that removes `dist/` before building
    - Add `build` target that first removes `dist/` directory, then runs `uv build`
    - Ensures only current build artifacts are present in `dist/`
    - Propagates `uv build` exit code on failure
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

- [x] 5. Add mirror and index targets
  - [x] 5.1 Add `mirror` and `index` targets for CLI subcommands
    - Add `mirror` target with recipe: `uv run debcraft mirror`
    - Add `index` target with recipe: `uv run debcraft index`
    - Both propagate exit codes from the CLI subcommands
    - _Requirements: 5.1, 5.2, 6.1, 6.2_

- [x] 6. Checkpoint - Verify Makefile syntax and dry-run
  - Ensure the Makefile parses correctly by running `make -n test`, `make -n lint`, `make -n clean`, `make -n build`, `make -n mirror`, `make -n index`
  - Verify all targets appear in `.PHONY` declaration
  - Ask the user if questions arise.

## Notes

- No property-based tests are applicable — the Makefile is a static build automation artifact
- Each task references specific requirements for traceability
- The checkpoint uses `make -n` (dry-run) to validate Makefile syntax without executing commands
- All targets are independent with no inter-target dependencies
- The `test` target is first in the file, making it the default target

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "3.1", "4.1", "5.1"] }
  ]
}
```
