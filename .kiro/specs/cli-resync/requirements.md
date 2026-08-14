# Requirements Document

## Introduction

This document defines the requirements for resynchronizing the `debcraft` CLI entry point so that all commands defined across milestones M0 through M7 are properly registered, discoverable, and functional when running `uv run debcraft`. The CLI uses Typer as the framework, Rich for output formatting, and entry points declared in `pyproject.toml`. The goal is a single audit-and-fix pass ensuring every intended command is wired into the Typer application and that running `uv run debcraft --help` shows the complete command surface.

## Glossary

- **CLI_App**: The top-level Typer application instance defined in `src/debcraft/cli/__init__.py` and exposed as the `debcraft` console script.
- **Sub_App**: A Typer instance registered via `app.add_typer()` that groups related sub-commands under a parent command name (e.g., `mirror`, `index`).
- **Command**: A function decorated with `@app.command()` or registered via `app.command()(fn)` that becomes a top-level invocable sub-command of `debcraft`.
- **Entry_Point**: A `[project.scripts]` or `[project.entry-points]` declaration in `pyproject.toml` that maps a console command or plugin name to a Python callable or class.
- **Milestone**: A numbered development phase (M0–M7) that introduced specific CLI-visible functionality.

## Requirements

### Requirement 1: Top-Level Commands from M0

**User Story:** As a user, I want the foundational CLI commands available at the top level, so that I can check my installation health and tool version.

#### Acceptance Criteria

1. WHEN a user runs `debcraft version`, THE CLI_App SHALL display the version string in the format `debcraft <semver>` to standard output and exit with code 0.
2. WHEN a user runs `debcraft doctor`, THE CLI_App SHALL execute environment health checks (Python version >= 3.13, writable temp directory, writable current directory) and display a status table with one row per check showing PASS or FAIL, the check name, and a descriptive message.
3. IF any `debcraft doctor` health check fails, THEN THE CLI_App SHALL exit with code 1.
4. IF all `debcraft doctor` health checks pass, THEN THE CLI_App SHALL exit with code 0.
5. WHEN a user runs `debcraft info`, THE CLI_App SHALL display a table containing the following properties: version, Python version, Python path, platform, architecture, package location, and virtual environment path (or "None" if not in a virtual environment), and exit with code 0.

### Requirement 2: Mirror Sub-App from M3

**User Story:** As a user, I want all mirror management commands accessible under `debcraft mirror`, so that I can synchronize, verify, inspect, list, and clean my local repository cache.

#### Acceptance Criteria

1. WHEN a user runs `debcraft mirror sync` and at least one repository is configured, THE CLI_App SHALL invoke the mirror synchronization workflow and display a summary table showing files downloaded, files skipped, files failed, and bytes transferred.
2. WHEN a user runs `debcraft mirror verify`, THE CLI_App SHALL compute SHA256 checksums of all cached files tracked in mirror.db (in VERIFIED or INDEXED state), compare each against the stored checksum, and display the count of files checked, files missing, and mismatches found.
3. IF one or more checksum mismatches are detected during `debcraft mirror verify`, THEN THE CLI_App SHALL list each mismatched file path with its expected and actual checksum and exit with a non-zero exit code.
4. WHEN a user runs `debcraft mirror status`, THE CLI_App SHALL display the count of configured repositories, last sync timestamp (or "never" if no sync has completed), cached file count, failed file count, and cache size in human-readable byte units.
5. WHEN a user runs `debcraft mirror list`, THE CLI_App SHALL display a table of configured repositories with name, base URL, suites, components, and architectures columns.
6. WHEN a user runs `debcraft mirror clean`, THE CLI_App SHALL identify files in the mirror cache directory that are not tracked as VERIFIED or INDEXED in mirror.db, display the count and total size of unreferenced files, prompt for confirmation, and remove them upon approval.
7. IF the user declines the confirmation prompt during `debcraft mirror clean`, THEN THE CLI_App SHALL abort the operation without removing any files and exit with code 0.
8. IF no repositories are configured when a user runs `debcraft mirror sync` or `debcraft mirror clean`, THEN THE CLI_App SHALL display an error message indicating no repositories are configured and exit with a non-zero exit code.

### Requirement 3: Index Sub-App from M4

**User Story:** As a user, I want repository indexing commands accessible under `debcraft index`, so that I can trigger indexing and look up package metadata.

#### Acceptance Criteria

1. WHEN a user runs `debcraft index`, THE CLI_App SHALL index all repositories that have VERIFIED files in the mirror cache and SHALL display a summary table showing each repository name, the count of packages indexed, source packages indexed, file ownerships indexed, files skipped, and a success or failure status.
2. WHEN a user runs `debcraft index --repository <name>`, THE CLI_App SHALL index only the specified repository and SHALL display the same summary table limited to that repository.
3. WHEN a user runs `debcraft index package <name>`, THE CLI_App SHALL display the latest indexed metadata for the named package including: package name, version, architecture, source package, source version, section, priority, maintainer, homepage, and description.
4. WHEN a user runs `debcraft index` and no VERIFIED files exist in the mirror cache, THE CLI_App SHALL display a message indicating no files are available for indexing and SHALL exit with code 0.
5. IF a user runs `debcraft index package <name>` and the package does not exist in the metadata database, THEN THE CLI_App SHALL display a message indicating the package was not found and SHALL exit with a non-zero exit code.
6. IF a user runs `debcraft index --repository <name>` and the specified repository name does not match any configured repository, THEN THE CLI_App SHALL display a message indicating the repository was not found and SHALL exit with a non-zero exit code.

### Requirement 4: SBOM Command from M7

**User Story:** As a user, I want the `sbom` command properly registered and functional, so that I can generate SBOMs for artifacts in multiple formats.

#### Acceptance Criteria

1. WHEN a user runs `debcraft sbom <artifact_path>`, THE CLI_App SHALL execute the SBOM generation workflow scanning the artifact, enriching metadata, assembling the model, and writing output files, then exit with code 0.
2. WHEN a user provides one or more `--format` options, THE CLI_App SHALL validate each format value against the supported set (spdx_3_0, spdx_2_3, cyclonedx) and generate output files only in the requested formats.
3. IF a user provides a `--format` value that is not in the supported set (spdx_3_0, spdx_2_3, cyclonedx), THEN THE CLI_App SHALL display an error message listing the invalid value(s) and the valid options, and exit with a non-zero code without generating any files.
4. WHEN a user does not provide any `--format` option, THE CLI_App SHALL generate output files in all supported formats (spdx_3_0, spdx_2_3, cyclonedx).
5. WHEN a user provides `--output-dir`, THE CLI_App SHALL write SBOM output files to the specified directory, creating it (including parent directories) if it does not exist.
6. IF the specified output directory cannot be created or is not writable, THEN THE CLI_App SHALL display an error message indicating the directory issue and exit with a non-zero code without generating any files.
7. IF the artifact path does not exist, THEN THE CLI_App SHALL display an error message and exit with a non-zero code.
8. THE CLI_App SHALL register the `sbom` command so that it appears in `debcraft --help` output with its description.

### Requirement 5: Command Registration Completeness

**User Story:** As a user, I want `debcraft --help` to show all available commands, so that I can discover the full feature set without consulting external documentation.

#### Acceptance Criteria

1. WHEN a user runs `debcraft --help`, THE CLI_App SHALL list all top-level commands: version, doctor, info, sbom.
2. WHEN a user runs `debcraft --help`, THE CLI_App SHALL list all sub-apps: mirror, index.
3. WHEN the CLI_App starts, THE CLI_App SHALL register all commands and sub-apps such that `debcraft --help` produces the complete command list without import errors or tracebacks and exits with code 0.
4. WHEN a user runs `debcraft <command> --help` for any registered command or sub-app listed in criteria 1 and 2, THE CLI_App SHALL display the command's help text and exit with code 0 without import errors or tracebacks.
5. WHEN a user runs `debcraft mirror --help` or `debcraft index --help`, THE CLI_App SHALL list the sub-commands available within that sub-app.

### Requirement 6: Entry Point Synchronization

**User Story:** As a developer, I want the pyproject.toml entry points to match the actual implementation, so that `uv run debcraft` resolves to the correct CLI app and plugin registries load without errors.

#### Acceptance Criteria

1. THE `[project.scripts]` section in pyproject.toml SHALL declare `debcraft = "debcraft.cli:app"` where `debcraft.cli:app` resolves to an importable `typer.Typer` instance.
2. THE `[project.entry-points."debcraft.scanners"]` section SHALL declare entry points for all seven scanner implementations (directory, docker, oci, iso, qcow2, img, ami), and each entry point SHALL resolve to an importable class from its declared module path.
3. THE `[project.entry-points."debcraft.sbom_writers"]` section SHALL declare entry points for all three SBOM writer implementations (spdx_3_0, spdx_2_3, cyclonedx), and each entry point SHALL resolve to an importable class from its declared module path.
4. WHEN `uv sync` is executed after modifying entry points, THE package manager SHALL install the package so that running `uv run debcraft --help` exits with code 0 and all entry points registered under `debcraft.scanners` and `debcraft.sbom_writers` groups are loadable via `importlib.metadata.entry_points()`.
5. IF an entry point declared in pyproject.toml references a module path that cannot be imported, THEN THE system SHALL raise an `ImportError` or `AttributeError` at entry point load time, indicating the unresolvable module path.

### Requirement 7: Verbose and Help Global Options

**User Story:** As a user, I want global options (--verbose, --help) to work consistently across all commands, so that I can enable debug logging or get help at any level of the command hierarchy.

#### Acceptance Criteria

1. WHEN a user passes `--verbose` or `-v` before any command, THE CLI_App SHALL set the `debcraft` logger to DEBUG level and direct all debug log output to stderr.
2. WHEN a user passes `--help` after any command or sub-command name, THE CLI_App SHALL display the help text for that specific command, including the command name, a description, and all available options and arguments, without executing the command, and SHALL exit with code 0.
3. WHEN no sub-command is provided (bare `debcraft` invocation), THE CLI_App SHALL display the top-level help text listing all registered commands and global options, and SHALL exit with code 0.
4. IF `--verbose` is passed and no sub-command is provided, THEN THE CLI_App SHALL still display the top-level help text and exit with code 0.
