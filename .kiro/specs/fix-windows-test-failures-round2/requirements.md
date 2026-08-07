# Requirements Document

## Introduction

This bugfix specification addresses 4 remaining Windows test failures in the debcraft test suite. A prior spec (`fix-windows-cross-platform-tests`) resolved some Windows failures, but these 4 tests still fail on Windows 11 due to platform-specific assumptions in test code. The production source code is correct and must NOT be modified — all fixes are test-only.

## Glossary

- **Test_Suite**: The collection of pytest test files under `tests/` in the debcraft repository
- **PurePosixPath**: A `pathlib.PurePosixPath` object that interprets paths with forward-slash separators regardless of host OS
- **Windows_Reserved_Name**: A filename that Windows treats as a device name (NUL, CON, PRN, AUX, COM1–COM9, LPT1–LPT9) and cannot be created as a regular file on NTFS
- **Host_Platform**: The operating system on which the test suite is currently executing (`sys.platform`)
- **NTFS**: The Windows NT File System which does not support Unix-style permission bits
- **Path_Separator**: The character used to separate directory components (forward slash `/` on POSIX, backslash `\` on Windows)

## Requirements

### Requirement 1: Path Separator Normalization in Assertions

**User Story:** As a developer running tests on Windows, I want path comparison assertions to normalize separators, so that tests pass regardless of the host platform's native path separator.

#### Acceptance Criteria

1. WHEN a test constructs a PurePosixPath from a `pathlib.Path` result for comparison, THE Test_Suite SHALL use `.as_posix()` conversion before constructing the PurePosixPath object
2. WHEN `.as_posix()` is used, THE Test_Suite SHALL produce forward-slash-separated strings that PurePosixPath interprets correctly as path separators
3. WHEN running on Linux or macOS, THE Test_Suite SHALL produce identical test behavior to the current implementation since `.as_posix()` is a no-op on POSIX platforms

### Requirement 2: Windows Reserved Name Filtering in Filename Generation

**User Story:** As a developer running property-based tests on Windows, I want generated filenames to exclude Windows reserved device names, so that file creation operations behave consistently across platforms.

#### Acceptance Criteria

1. WHILE the Host_Platform is Windows (`sys.platform == "win32"`), THE Test_Suite SHALL exclude filenames matching the pattern `^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\..+)?$` (case-insensitive) from generated file specifications
2. WHILE the Host_Platform is Linux or macOS, THE Test_Suite SHALL allow all filenames without Windows-specific filtering
3. WHEN a sanitized filename matches a Windows_Reserved_Name on Windows, THE Test_Suite SHALL skip that filename entry rather than attempting file creation

### Requirement 3: Platform-Appropriate Absoluteness Validation

**User Story:** As a developer running tests on Windows, I want path absoluteness assertions to use the correct validation method for the platform under test, so that Windows drive-letter paths are correctly recognized as absolute.

#### Acceptance Criteria

1. WHEN the test simulates Windows path resolution on a non-Windows host, THE Test_Suite SHALL use PurePosixPath-style absoluteness checks with POSIX-formatted environment variables
2. WHEN the test runs Windows path resolution on an actual Windows host (`sys.platform == "win32"` and `platform == "win32"`), THE Test_Suite SHALL provide Windows-style environment variables (drive-letter paths for USERPROFILE, LOCALAPPDATA, APPDATA)
3. WHEN validating path absoluteness for native Windows paths, THE Test_Suite SHALL use `Path.is_absolute()` instead of `PurePosixPath(...).is_absolute()`
4. WHEN running on Linux or macOS with `platform` set to "linux" or "darwin", THE Test_Suite SHALL continue using the existing PurePosixPath absoluteness validation without changes

### Requirement 4: Unix Permission Tests Gated by Platform

**User Story:** As a developer running tests on Windows, I want Unix-specific permission tests to be skipped on Windows, so that tests do not fail due to NTFS permission model differences.

#### Acceptance Criteria

1. WHEN the Host_Platform is Windows, THE Test_Suite SHALL skip tests that assert specific Unix file permission mode values (e.g., `0o644`)
2. WHEN the Host_Platform is Windows, THE Test_Suite SHALL skip tests that verify individual Unix permission bits (owner read/write, group read, other read)
3. WHEN the Host_Platform is Linux or macOS, THE Test_Suite SHALL continue executing all Unix permission tests without modification
4. WHEN a test is skipped on Windows, THE Test_Suite SHALL provide a descriptive skip reason indicating that Windows does not support Unix file permissions

### Requirement 5: Regression Prevention

**User Story:** As a maintainer, I want all fixes to be confined to test code only, so that production behavior remains unchanged across all platforms.

#### Acceptance Criteria

1. THE Test_Suite fixes SHALL NOT modify any files under `src/debcraft/`
2. WHEN running on Linux, THE Test_Suite SHALL produce identical pass/fail results as before these fixes
3. WHEN running on macOS, THE Test_Suite SHALL produce identical pass/fail results as before these fixes
4. WHEN running on Windows after these fixes, THE Test_Suite SHALL pass all 4 previously-failing tests
