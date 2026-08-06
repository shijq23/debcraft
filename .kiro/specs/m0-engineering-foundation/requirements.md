# Requirements Document

## Introduction

This document defines the requirements for Milestone M0 (Engineering Foundation) of the DebCraft platform. M0 produces zero business logic — it establishes production-quality engineering infrastructure including project configuration, testing framework, CI/CD pipelines, documentation scaffolding, a minimal CLI, and architecture compliance enforcement. The goal is a fully functional development environment where `uv sync`, `uv run pytest`, `uv run ruff check`, `uv run basedpyright`, and `uv run debcraft version` all succeed immediately.

## Glossary

- **Platform**: The DebCraft Python application and its associated tooling, configuration, and CI/CD infrastructure.
- **CLI**: The command-line interface entry point for DebCraft, built with Typer and Rich.
- **Architecture_Test**: An automated test that verifies import dependency rules between source packages.
- **Linter**: Ruff, configured for Google Python Style Guide enforcement.
- **Type_Checker**: BasedPyright, configured for strict type checking of public APIs.
- **CI_Pipeline**: A GitHub Actions or GitLab CI workflow that runs format checks, linting, type checking, and tests.
- **Package_Manager**: uv, used as the sole tool for dependency management, virtual environments, and script execution.
- **Doc_Builder**: MkDocs with Material theme, used for generating project documentation.
- **Marker**: A pytest marker used to categorize tests by type, feature, environment, or speed.

## Requirements

### Requirement 1: Project Initialization with uv

**User Story:** As a developer, I want the project initialized as a uv-managed Python package with src layout, so that I have a reproducible and standards-compliant development environment.

#### Acceptance Criteria

1. THE Platform SHALL use a `src/debcraft/` source layout with a PEP 621 compliant `pyproject.toml`.
2. THE Platform SHALL require Python 3.13 or higher as specified in the `pyproject.toml` metadata.
3. THE Platform SHALL use Hatchling as the build backend.
4. THE Platform SHALL declare typer, rich, sqlalchemy, and aiohttp as runtime dependencies.
5. THE Platform SHALL declare ruff, basedpyright, pytest, pytest-cov, pytest-asyncio, coverage, mkdocs, mkdocstrings, import-linter, bandit, and pre-commit as development dependencies.
6. WHEN a developer runs `uv sync` THEN the Package_Manager SHALL install all runtime and development dependencies without errors.

### Requirement 2: Google Python Style Guide Enforcement

**User Story:** As a developer, I want automated style enforcement following the Google Python Style Guide, so that the codebase maintains consistent quality without manual review burden.

#### Acceptance Criteria

1. THE Linter SHALL be configured in `pyproject.toml` with rules enforcing Google Python Style Guide conventions.
2. THE Linter SHALL enforce Google-style docstrings using the pydocstyle napoleon convention.
3. THE Type_Checker SHALL be configured to validate type annotations on all public API functions and methods.
4. WHEN a developer runs `uv run ruff check src/ tests/` THEN the Linter SHALL report zero violations on compliant code.
5. WHEN a developer runs `uv run ruff format --check src/ tests/` THEN the Linter SHALL report zero formatting issues on compliant code.
6. WHEN a developer runs `uv run basedpyright src/` THEN the Type_Checker SHALL report zero errors on compliant code.

### Requirement 3: Project Package Structure

**User Story:** As a developer, I want a well-organized package structure with empty module placeholders, so that future development has clear boundaries for domain logic, infrastructure, platform internals, and plugins.

#### Acceptance Criteria

1. THE Platform SHALL provide a `src/debcraft/__init__.py` that exposes the package version.
2. THE Platform SHALL provide a `src/debcraft/__main__.py` that enables execution via `python -m debcraft`.
3. THE Platform SHALL provide a `src/debcraft/version.py` that defines the package version string.
4. THE Platform SHALL provide empty packages at `src/debcraft/cli/`, `src/debcraft/platform/`, `src/debcraft/platform/contracts/`, `src/debcraft/platform/kernel/`, `src/debcraft/platform/sdk/`, `src/debcraft/domain/`, `src/debcraft/infrastructure/`, and `src/debcraft/plugins/`, each containing an `__init__.py` file.
5. THE Platform SHALL include no placeholder TODO comments in any generated source file.

### Requirement 4: Testing Infrastructure

**User Story:** As a developer, I want a comprehensive pytest configuration with markers and directory structure, so that tests are organized by type and can be selectively executed.

#### Acceptance Criteria

1. THE Platform SHALL configure pytest in `pyproject.toml` with a default marker expression of `unit and not slow`.
2. THE Platform SHALL register test type markers: unit, integration, contract, architecture, benchmark, regression, and e2e.
3. THE Platform SHALL register feature markers: repository, package, dep5, license, spdx, cyclonedx, docker, oci, iso, qcow2, ami, mirror, workflow, plugin, storage, and database.
4. THE Platform SHALL register environment markers: sqlite, network, filesystem, aws, container, cross_platform, linux_only, windows_only, and macos_only.
5. THE Platform SHALL register speed markers: slow, serial, and parallel.
6. THE Platform SHALL provide test directories: `tests/unit/`, `tests/integration/`, `tests/contract/`, `tests/architecture/`, `tests/e2e/`, `tests/benchmark/`, and `tests/regression/`.
7. THE Platform SHALL provide fixture directories: `fixtures/packages/`, `fixtures/repositories/`, `fixtures/images/`, and `fixtures/licenses/`.
8. WHEN a developer runs `uv run pytest` THEN the Platform SHALL execute only tests marked as `unit` and not marked as `slow`.

### Requirement 5: CI/CD Pipelines

**User Story:** As a developer, I want CI/CD pipelines for both GitHub Actions and GitLab CI, so that code quality is validated automatically on every push across multiple platforms.

#### Acceptance Criteria

1. THE CI_Pipeline SHALL define a GitHub Actions workflow running on Ubuntu, Windows, and macOS.
2. THE CI_Pipeline SHALL execute stages in order: format check, lint, type check, unit tests, and architecture tests.
3. THE CI_Pipeline SHALL define a GitLab CI configuration (`.gitlab-ci.yml`) with equivalent stages.
4. THE Platform SHALL provide a Dockerfile that enables container-based execution of the full test suite.
5. WHEN the CI_Pipeline runs on any supported platform THEN the CI_Pipeline SHALL complete all stages without failures on compliant code.

### Requirement 6: Documentation Infrastructure

**User Story:** As a developer, I want MkDocs configured with Material theme and a structured docs directory, so that project documentation can be authored and built immediately.

#### Acceptance Criteria

1. THE Doc_Builder SHALL be configured via `mkdocs.yml` with the Material theme.
2. THE Platform SHALL provide documentation directories: `docs/architecture/`, `docs/specifications/`, `docs/adr/`, `docs/developer/`, and `docs/user/`.
3. THE Platform SHALL provide an ADR (Architecture Decision Record) template at `docs/adr/template.md`.
4. THE Platform SHALL provide a developer getting-started guide at `docs/developer/getting-started.md`.
5. WHEN a developer runs `uv run mkdocs build` THEN the Doc_Builder SHALL generate a static documentation site without errors.

### Requirement 7: Minimal CLI Commands

**User Story:** As a developer, I want basic CLI commands for version display, environment diagnostics, and configuration info, so that I can verify the installation is functional.

#### Acceptance Criteria

1. WHEN a user runs `debcraft version` THEN the CLI SHALL display the current package version string.
2. WHEN a user runs `debcraft doctor` THEN the CLI SHALL check the Python version meets the minimum requirement, verify writable paths exist, and report pass/fail status for each check.
3. WHEN a user runs `debcraft info` THEN the CLI SHALL display current configuration details including Python version, platform, and package location.
4. IF the Python version is below 3.13 THEN the CLI SHALL report a failure in the doctor output with a descriptive message.
5. THE CLI SHALL use Rich for formatted terminal output.

### Requirement 8: Architecture Compliance Tests

**User Story:** As a developer, I want automated architecture tests enforcing layer boundaries, so that the codebase maintains separation of concerns as it grows.

#### Acceptance Criteria

1. THE Architecture_Test SHALL verify that modules in `src/debcraft/domain/` do not import from `src/debcraft/infrastructure/`.
2. THE Architecture_Test SHALL verify that modules in `src/debcraft/plugins/` do not import from other plugin sub-packages.
3. THE Architecture_Test SHALL verify that modules in `src/debcraft/platform/contracts/` do not import implementation modules.
4. THE Architecture_Test SHALL verify that key modules contain no mutable module-level global state.
5. WHEN a developer runs `uv run pytest -m architecture` THEN the Architecture_Test SHALL pass for compliant code.

### Requirement 9: Supporting Configuration Files

**User Story:** As a developer, I want standard project configuration files present, so that editors, Git, and contribution workflows are properly configured from day one.

#### Acceptance Criteria

1. THE Platform SHALL provide an `.editorconfig` file specifying UTF-8 encoding, LF line endings, 4-space indentation for Python, and 2-space indentation for YAML.
2. THE Platform SHALL provide a `.gitattributes` file with line-ending normalization rules.
3. THE Platform SHALL provide a `.python-version` file specifying Python 3.13.
4. THE Platform SHALL provide a `.pre-commit-config.yaml` with hooks for ruff formatting, ruff linting, and basedpyright type checking.
5. THE Platform SHALL provide a `CHANGELOG.md` with an initial Unreleased section.
6. THE Platform SHALL provide a `CONTRIBUTING.md` with development setup instructions using uv.
7. THE Platform SHALL provide a `SECURITY.md` with vulnerability reporting instructions.

### Requirement 10: Cross-Platform Compatibility

**User Story:** As a developer, I want the project to work on Linux, Windows, and macOS without requiring elevated privileges, so that any contributor can develop regardless of their operating system.

#### Acceptance Criteria

1. THE Platform SHALL use `pathlib.Path` for all file system operations with no hardcoded path separators.
2. THE Platform SHALL not require root or administrator privileges for any development operation.
3. THE CI_Pipeline SHALL validate cross-platform compatibility by running on Linux, Windows, and macOS.
4. WHEN a developer runs the CLI on any supported platform THEN the CLI SHALL produce correct output without platform-specific errors.
