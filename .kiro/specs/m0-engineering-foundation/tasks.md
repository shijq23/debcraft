# Implementation Plan: M0 Engineering Foundation

## Overview

This plan implements the engineering foundation for DebCraft with zero business logic. Each task produces immediately functional files — no placeholder TODOs. Tasks are ordered to establish the build system first, then add source structure, tooling configuration, CLI, tests, CI/CD, and documentation.

## Tasks

- [x] 1. Initialize project with pyproject.toml and src layout
  - [x] 1.1 Create `pyproject.toml` with PEP 621 metadata, Hatchling build backend, runtime dependencies (typer, rich, sqlalchemy, aiohttp), dev dependencies (ruff, basedpyright, pytest, pytest-cov, pytest-asyncio, coverage, mkdocs, mkdocs-material, mkdocstrings[python], import-linter, bandit, pre-commit), Python >=3.13 requirement, and `[project.scripts] debcraft = "debcraft.cli:app"`
    - Include full Ruff configuration (target-version py313, line-length 100, Google style rules, pydocstyle convention google, per-file-ignores for tests)
    - Include BasedPyright configuration (standard mode, Python 3.13)
    - Include pytest configuration (testpaths, addopts with `unit and not slow`, all markers registered)
    - Include coverage configuration (source = debcraft, branch coverage)
    - Include import-linter contracts (domain independence, contracts purity, plugin isolation)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.2, 2.3, 4.1, 4.2, 4.3, 4.4, 4.5, 8.1, 8.2, 8.3_
  - [x] 1.2 Create `src/debcraft/__init__.py` that imports and exposes VERSION from version module
    - _Requirements: 3.1_
  - [x] 1.3 Create `src/debcraft/version.py` with `VERSION: str = "0.1.0"`
    - _Requirements: 3.3_
  - [x] 1.4 Create `src/debcraft/__main__.py` that imports and runs the CLI app
    - _Requirements: 3.2_
  - [x] 1.5 Create empty packages with `__init__.py` files: `src/debcraft/cli/`, `src/debcraft/platform/`, `src/debcraft/platform/contracts/`, `src/debcraft/platform/kernel/`, `src/debcraft/platform/sdk/`, `src/debcraft/domain/`, `src/debcraft/infrastructure/`, `src/debcraft/plugins/`
    - Each `__init__.py` should have a module docstring only (no TODOs)
    - _Requirements: 3.4, 3.5_

- [x] 2. Implement minimal CLI commands
  - [x] 2.1 Implement `src/debcraft/cli/__init__.py` with Typer app and three commands: `version`, `doctor`, `info`
    - `version`: Print VERSION using Rich console
    - `doctor`: Check Python >= 3.13, writable temp dir, writable current dir; report PASS/FAIL for each using Rich
    - `info`: Display version, Python version/path, platform, architecture, package location, venv path using Rich
    - Use `pathlib.Path` for all path operations
    - Use dataclasses `DoctorCheck` and `EnvironmentInfo` as defined in design
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 10.1, 10.2_

- [x] 3. Checkpoint - Verify core functionality
  - Run `uv sync` to install all dependencies
  - Run `uv run debcraft version` to verify CLI works
  - Run `uv run debcraft doctor` to verify health checks
  - Run `uv run debcraft info` to verify info display
  - Run `uv run ruff check src/` to verify linting passes
  - Run `uv run ruff format --check src/` to verify formatting
  - Run `uv run basedpyright src/` to verify type checking
  - Ensure all commands pass, ask the user if questions arise.

- [x] 4. Create test infrastructure and write tests
  - [x] 4.1 Create test directory structure with `conftest.py` files
    - Create directories: `tests/unit/`, `tests/integration/`, `tests/contract/`, `tests/architecture/`, `tests/e2e/`, `tests/benchmark/`, `tests/regression/`
    - Create fixture directories: `fixtures/packages/`, `fixtures/repositories/`, `fixtures/images/`, `fixtures/licenses/` (each with `.gitkeep`)
    - Create `tests/__init__.py` and `tests/conftest.py` with shared fixtures
    - _Requirements: 4.6, 4.7_
  - [x] 4.2 Write unit tests for CLI commands in `tests/unit/test_cli.py`
    - Test `version` command outputs version string
    - Test `doctor` command reports checks with PASS/FAIL
    - Test `info` command outputs environment details
    - Test doctor fails gracefully when Python version check would fail (mock sys.version_info)
    - Mark all tests with `@pytest.mark.unit`
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 4.8_
  - [x] 4.3 Write architecture compliance tests in `tests/architecture/test_architecture.py`
    - Test: domain modules do not import infrastructure (AST-based import scanning)
    - Test: plugin modules do not cross-import other plugins
    - Test: contracts modules have no implementation dependencies
    - Test: key modules contain no mutable module-level global state (scan for bare list/dict/set assignments without Final)
    - Mark all tests with `@pytest.mark.architecture`
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

- [x] 5. Checkpoint - Verify test infrastructure
  - Run `uv run pytest` (should run unit tests only, all passing)
  - Run `uv run pytest -m architecture` (architecture tests passing)
  - Run `uv run pytest --co` to verify test collection works
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Create CI/CD pipelines and Dockerfile
  - [x] 6.1 Create GitHub Actions workflow at `.github/workflows/ci.yml`
    - Trigger on push to main and pull requests
    - Matrix strategy: ubuntu-latest, windows-latest, macos-latest
    - Steps: checkout, install uv, uv sync, ruff format --check, ruff check, basedpyright, pytest (unit), pytest -m architecture
    - Use `astral-sh/setup-uv` action
    - _Requirements: 5.1, 5.2, 5.5, 10.3_
  - [x] 6.2 Create GitLab CI configuration at `.gitlab-ci.yml`
    - Stages: lint, typecheck, test
    - Install uv, sync dependencies, run same checks as GitHub Actions
    - _Requirements: 5.3_
  - [x] 6.3 Create `Dockerfile` for container-based test execution
    - Base image: python:3.13-slim
    - Install uv, copy project, sync dependencies
    - Default CMD: pytest
    - _Requirements: 5.4_

- [x] 7. Create documentation infrastructure
  - [x] 7.1 Create `mkdocs.yml` with Material theme, mkdocstrings plugin, navigation structure
    - _Requirements: 6.1_
  - [x] 7.2 Create documentation directory structure and content files
    - Create `docs/index.md` (project overview)
    - Create `docs/architecture/` with `index.md`
    - Create `docs/specifications/` with `index.md`
    - Create `docs/adr/` with `index.md` and `template.md` (ADR template)
    - Create `docs/developer/` with `index.md` and `getting-started.md`
    - Create `docs/user/` with `index.md`
    - _Requirements: 6.2, 6.3, 6.4, 6.5_

- [x] 8. Create supporting configuration files
  - [x] 8.1 Create `.editorconfig` (UTF-8, LF line endings, 4-space Python, 2-space YAML, trim trailing whitespace)
    - _Requirements: 9.1_
  - [x] 8.2 Create `.gitattributes` (text=auto, LF for .py/.yml/.md/.toml, binary for images)
    - _Requirements: 9.2_
  - [x] 8.3 Create `.python-version` containing `3.13`
    - _Requirements: 9.3_
  - [x] 8.4 Create `.pre-commit-config.yaml` with hooks for ruff-format, ruff check --fix, and basedpyright
    - _Requirements: 9.4_
  - [x] 8.5 Create `CHANGELOG.md` (Keep a Changelog format, Unreleased section)
    - _Requirements: 9.5_
  - [x] 8.6 Create `CONTRIBUTING.md` (development setup with uv, coding standards, testing instructions)
    - _Requirements: 9.6_
  - [x] 8.7 Create `SECURITY.md` (vulnerability reporting instructions)
    - _Requirements: 9.7_

- [x] 9. Final checkpoint - Full validation
  - Run `uv sync` and verify success
  - Run `uv run ruff check src/ tests/` and verify zero violations
  - Run `uv run ruff format --check src/ tests/` and verify zero issues
  - Run `uv run basedpyright src/` and verify zero errors
  - Run `uv run pytest` and verify all unit tests pass
  - Run `uv run pytest -m architecture` and verify architecture tests pass
  - Run `uv run debcraft version` and verify output
  - Run `uv run debcraft doctor` and verify all checks pass
  - Verify no TODO comments in any source file: `grep -r "TODO" src/`
  - Ensure all commands pass, ask the user if questions arise.

## Notes

- No tasks are marked optional since this milestone has no property-based tests (PBT is not applicable to configuration and simple CLI commands)
- All source files must pass ruff check, ruff format, and basedpyright before proceeding to next task
- Use `pathlib.Path` exclusively — never `os.path` or hardcoded separators
- Every generated file must be complete and functional with no placeholder TODOs
- The `uv run mkdocs build` validation is deferred to CI since it requires all docs content to exist first
