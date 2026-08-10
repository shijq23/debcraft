# Requirements Document

## Introduction

This document specifies the requirements for a Makefile that provides standard development workflow tasks for the debcraft project. The Makefile consolidates common development commands (testing, linting, cleaning, building, and running CLI subcommands) into short, memorable targets that developers can invoke from the project root.

## Glossary

- **Makefile**: A build automation file processed by GNU Make that defines targets and their associated shell commands
- **Target**: A named entry point in the Makefile that executes one or more shell commands when invoked via `make <target>`
- **Test_Suite**: The pytest-based test runner configured in pyproject.toml that executes unit, integration, and other test categories
- **Linter**: The collection of static analysis tools (ruff, basedpyright) used to check code quality and type correctness
- **Build_System**: The hatchling-based Python package build pipeline invoked through `uv build`
- **CLI**: The debcraft command-line interface entry point (`debcraft`) registered as a console script
- **Build_Artifacts**: Files generated during the build process, including `dist/`, `*.egg-info`, `__pycache__/`, and `.ruff_cache/` directories

## Requirements

### Requirement 1: Run Test Suite

**User Story:** As a developer, I want a single `make test` command, so that I can run the full test suite without remembering pytest flags.

#### Acceptance Criteria

1. WHEN the developer invokes `make test`, THE Makefile SHALL execute `uv run pytest` with no additional command-line arguments beyond those defined in `pyproject.toml` `[tool.pytest.ini_options]`
2. THE Makefile SHALL use the pytest configuration defined in pyproject.toml for test paths, markers, and options
3. IF pytest returns a non-zero exit code, THEN THE Makefile SHALL propagate the non-zero exit code to the caller
4. IF `uv` is not found on the system PATH, THEN THE Makefile SHALL exit with a non-zero exit code

### Requirement 2: Run Linting and Type Checking

**User Story:** As a developer, I want a single `make lint` command, so that I can run all static analysis checks in one step.

#### Acceptance Criteria

1. WHEN the developer invokes `make lint`, THE Makefile SHALL execute the following tools sequentially in this order: first `uv run ruff format --check .`, then `uv run ruff check .`, then `uv run basedpyright`
2. IF any tool in the `make lint` sequence returns a non-zero exit code, THEN THE Makefile SHALL skip execution of subsequent tools and propagate the non-zero exit code to the caller
3. IF all three tools return exit code 0, THEN THE Makefile SHALL return exit code 0 to the caller

### Requirement 3: Clean Build Artifacts

**User Story:** As a developer, I want a single `make clean` command, so that I can remove all generated files and start with a fresh workspace.

#### Acceptance Criteria

1. WHEN the developer invokes `make clean`, THE Makefile SHALL remove the `dist/` directory if it exists
2. WHEN the developer invokes `make clean`, THE Makefile SHALL remove all `__pycache__/` directories recursively from the project root
3. WHEN the developer invokes `make clean`, THE Makefile SHALL remove all `*.egg-info` directories
4. WHEN the developer invokes `make clean`, THE Makefile SHALL remove the `.ruff_cache/` directory if it exists
5. WHEN the developer invokes `make clean`, THE Makefile SHALL remove the `.pytest_cache/` directory if it exists
6. WHEN the developer invokes `make clean`, THE Makefile SHALL remove the `.coverage` file and `htmlcov/` directory if they exist
7. IF a target directory or file does not exist, THEN THE Makefile SHALL complete the `clean` target with exit code 0 and produce no error output to stderr
8. WHEN the developer invokes `make clean` and all targeted artifacts have been processed, THE Makefile SHALL exit with code 0

### Requirement 4: Build Python Package

**User Story:** As a developer, I want a single `make build` command, so that I can produce distributable package artifacts.

#### Acceptance Criteria

1. WHEN the developer invokes `make build`, THE Makefile SHALL build the Python package using `uv build`
2. WHEN the build completes successfully, THE Makefile SHALL have produced both an sdist (`.tar.gz`) and a wheel (`.whl`) artifact in the `dist/` directory
3. IF the build command returns a non-zero exit code, THEN THE Makefile SHALL propagate the non-zero exit code to the caller
4. WHEN the developer invokes `make build`, THE Makefile SHALL remove the `dist/` directory before invoking `uv build` so that only artifacts from the current build are present

### Requirement 5: Run Mirror Command

**User Story:** As a developer, I want a `make mirror` command, so that I can invoke the debcraft mirror subcommand without typing the full CLI invocation.

#### Acceptance Criteria

1. WHEN the developer invokes `make mirror`, THE Makefile SHALL execute the debcraft mirror subcommand using `uv run debcraft mirror`
2. IF the mirror command returns a non-zero exit code, THEN THE Makefile SHALL propagate the non-zero exit code to the caller

### Requirement 6: Run Index Command

**User Story:** As a developer, I want a `make index` command, so that I can invoke the debcraft index subcommand without typing the full CLI invocation.

#### Acceptance Criteria

1. WHEN the developer invokes `make index`, THE Makefile SHALL execute the debcraft index subcommand using `uv run debcraft index`
2. IF the index command returns a non-zero exit code, THEN THE Makefile SHALL propagate the non-zero exit code to the caller
