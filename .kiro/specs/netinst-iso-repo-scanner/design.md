# Design Document: NETINST ISO Repository Scanner

## Overview

This design extends the existing `ISOScanner` to detect and scan Debian NETINST ISO images structured as package repositories (with `dists/` and `pool/` directories) rather than root filesystems with squashfs. The new scanning strategy is inserted between squashfs search and direct rootfs dpkg status check in the fallback chain.

The core approach is:
1. After squashfs search fails, check for `dists/` at the ISO root
2. If found, walk the `dists/<codename>/<component>/binary-<arch>/` tree to discover `Packages` and `Packages.gz` files
3. Parse each Packages file using the existing stanza parser, handling the absence of `Status` fields by treating valid stanzas as "installed"
4. Deduplicate packages across multiple Packages files by (name, version, architecture) tuple
5. Return a standard `ScanResult` with `DPKG_METADATA` strategy

This requires no changes to the `ISOReader` protocol, no new dependencies, and integrates cleanly with the existing cancellation and progress infrastructure.

## Architecture

The change is localized to `ISOScanner` in `src/debcraft/infrastructure/scanners/iso.py`. No new classes or modules are introduced — only new private methods on the existing scanner class.

```mermaid
flowchart TD
    A[scan()] --> B{squashfs found?}
    B -->|Yes| C[_scan_squashfs]
    B -->|No| D{dists/ at root?}
    D -->|Yes| E[_scan_repository]
    D -->|No / IOError| F[_scan_direct_rootfs]
    E --> G{packages found?}
    G -->|Yes| H[Return ScanResult]
    G -->|No| F
    F --> I{dpkg status found?}
    I -->|Yes| H
    I -->|No| J[_fallback_iso_filesystem]
    J --> H
```

### Strategy Order

```
1. Squashfs search (existing)
2. Repository structure detection (NEW)
3. Direct rootfs dpkg status check (existing)
4. Filesystem analysis fallback (existing)
```

## Components and Interfaces

### Modified: `ISOScanner.scan()`

The `scan()` method is updated to call `_scan_repository()` between squashfs search failure and `_scan_direct_rootfs()`. If `_scan_repository()` returns packages, those are the final result. If it returns zero packages or the ISO has no repository structure, fall through to existing fallback logic.

### New Private Methods

#### `_has_repository_structure() -> bool`

Checks for `dists/` in the root directory listing. Returns `False` on any I/O error (graceful degradation).

```python
def _has_repository_structure(self, diagnostics: list[str]) -> bool:
    """Check if ISO root contains a dists/ directory."""
    try:
        root_entries = self._iso_reader.list_dir("")
    except (FileNotFoundError, OSError):
        return False
    return "dists" in root_entries
```

#### `_scan_repository(artifact, context, start_time, diagnostics) -> ScanResult | None`

Orchestrates repository scanning:
1. Check for `dists/` (via `_has_repository_structure`)
2. Discover all Packages files (via `_discover_packages_files`)
3. Parse each Packages file (via `_parse_packages_file`)
4. Deduplicate and aggregate
5. Return `ScanResult` or `None` if no packages found (signals fallback)

Returns `None` to indicate that the caller should fall through to the next strategy.

#### `_discover_packages_files(diagnostics) -> list[str]`

Walks `dists/<codename>/<component>/binary-<arch>/` looking for Packages files. For each architecture directory:
- If `Packages.gz` exists, add it to the list
- Else if `Packages` exists, add it
- Records diagnostics for I/O errors on directory listings

#### `_parse_packages_file(path, diagnostics) -> list[IdentifiedPackage]`

Reads and parses a single Packages file:
1. Read bytes via `ISOReader.read_file()`
2. If path ends with `.gz`, decompress with `gzip.decompress()`
3. Decode as UTF-8
4. Split into stanzas using existing `split_stanzas()`
5. Parse each stanza with `parse_stanza_fields_ordered()`
6. For stanzas with `Package` and `Version` fields, create `IdentifiedPackage(status="installed")`
7. Record diagnostics for stanzas missing required fields

#### `_deduplicate_packages(packages) -> list[IdentifiedPackage]`

Deduplicates by `(name, version, architecture)` tuple, retaining first occurrence and preserving discovery order.

```python
def _deduplicate_packages(self, packages: list[IdentifiedPackage]) -> list[IdentifiedPackage]:
    seen: set[tuple[str, str, str]] = set()
    result: list[IdentifiedPackage] = []
    for pkg in packages:
        key = (pkg.name, pkg.version, pkg.architecture)
        if key not in seen:
            seen.add(key)
            result.append(pkg)
    return result
```

### Existing Components Used (No Modifications)

| Component | Role |
|-----------|------|
| `ISOReader` protocol | Read directory listings and file contents from ISO |
| `split_stanzas()` | Split RFC822 content into stanza blocks |
| `parse_stanza_fields_ordered()` | Parse individual stanza fields |
| `ScannerMixin._check_cancellation()` | Check cancellation token |
| `ScannerMixin._build_success_result()` | Construct final ScanResult |
| `ScanningStrategy.DPKG_METADATA` | Strategy value for the result |

### Design Decision: Direct Stanza Parsing vs. Modifying `parse_dpkg_status()`

**Decision:** Parse stanzas directly in `_parse_packages_file()` using `split_stanzas()` and `parse_stanza_fields_ordered()` rather than modifying `parse_dpkg_status()`.

**Rationale:**
- `parse_dpkg_status()` requires a `Status` field and applies dpkg-specific classification logic (install/hold/deinstall/purge). Repository Packages files never have a Status field.
- Modifying `parse_dpkg_status()` to optionally skip Status validation would complicate its contract and risk regression for all other callers.
- The stanza parsing primitives (`split_stanzas`, `parse_stanza_fields_ordered`) are already factored out and available for direct use.
- The repository parsing logic is simpler: any stanza with `Package` + `Version` is "installed."

### Design Decision: `_scan_repository()` Returns `None` for Fallback

**Decision:** Return `None` instead of an empty `ScanResult` when repository scanning finds no packages.

**Rationale:**
- This cleanly signals to `scan()` that the strategy didn't produce useful results and fallback should continue.
- An empty `ScanResult` with `DPKG_METADATA` strategy would be indistinguishable from a successful scan of an empty repository, which shouldn't short-circuit fallbacks.
- Requirement 7.3 explicitly states that zero packages should trigger fallback.

### Design Decision: Packages.gz Preference

**Decision:** When both `Packages` and `Packages.gz` exist, use only `Packages.gz`.

**Rationale:**
- `Packages.gz` is the canonical compressed form; the uncompressed `Packages` is often a convenience copy.
- Parsing both would produce duplicates that deduplication would remove anyway — wasteful I/O.
- The requirement (2.4) is explicit about this preference.

## Data Models

### No New Domain Objects

All results are expressed using existing types:
- `IdentifiedPackage(name, version, architecture, status="installed")`
- `ScanResult(packages, strategy, diagnostics, duration_seconds, artifact_path)`

### Internal Data Flow

```mermaid
flowchart LR
    A[ISO bytes] -->|ISOReader.read_file| B[Raw bytes]
    B -->|gzip.decompress if .gz| C[UTF-8 string]
    C -->|split_stanzas| D[Stanza texts]
    D -->|parse_stanza_fields_ordered| E[Field tuples]
    E -->|Extract Package/Version/Architecture| F[IdentifiedPackage]
    F -->|Aggregate across files| G[All packages]
    G -->|Deduplicate by name+version+arch| H[Final package list]
```

### Deduplication Key

```python
(package.name, package.version, package.architecture)
```

This 3-tuple uniquely identifies a package in a Debian repository context. The same binary package may appear in multiple component/architecture Packages files (e.g., cross-listed), and deduplication prevents inflated counts.

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Repository Detection from Root Entries

*For any* set of root directory entries in an ISO, the scanner SHALL identify the ISO as having repository structure if and only if the entry "dists" is present in that set, and SHALL record a detection diagnostic when repository structure is identified.

**Validates: Requirements 1.1, 1.2**

### Property 2: Packages File Discovery Respects Naming Patterns

*For any* directory structure under `dists/`, the scanner SHALL discover Packages files only within directories matching the pattern `binary-<arch>/` and SHALL exclude entries matching known metadata filenames (Release, InRelease) from being treated as component directories.

**Validates: Requirements 2.2, 2.3**

### Property 3: Packages.gz Preference

*For any* architecture directory containing both a `Packages` and a `Packages.gz` file, the scanner SHALL parse only the `Packages.gz` file and SHALL not parse the uncompressed `Packages` file.

**Validates: Requirements 2.4**

### Property 4: Gzip Decompression Round-Trip

*For any* valid Packages file content, gzip-compressing it and then having the scanner decompress it SHALL produce the original content that parses into the same set of packages.

**Validates: Requirements 3.2**

### Property 5: Status-less Stanzas Produce Installed Packages

*For any* RFC822 stanza containing `Package` and `Version` fields (and optionally `Architecture`, `Section`, or other fields) but no `Status` field, the scanner SHALL produce an `IdentifiedPackage` with status `"installed"`, regardless of whether the stanza represents a `.deb` or `.udeb` package.

**Validates: Requirements 3.4, 8.1, 8.3**

### Property 6: Missing Required Fields Produce Diagnostics

*For any* RFC822 stanza that is missing the `Package` field or the `Version` field, the scanner SHALL skip that stanza and produce a diagnostic message identifying which required field is absent.

**Validates: Requirements 8.2**

### Property 7: Deduplication Retains First Occurrence

*For any* sequence of `IdentifiedPackage` entries containing duplicates (same name, version, architecture), the scanner SHALL retain only the first occurrence of each unique (name, version, architecture) tuple, preserving the relative order of first occurrences.

**Validates: Requirements 4.1, 4.2**

### Property 8: Partial Failure Resilience

*For any* set of Packages file paths where some paths produce I/O errors and others succeed, the scanner SHALL include all packages from successful files in the result and SHALL record a diagnostic for each failed file, without aborting the scan.

**Validates: Requirements 2.5, 3.5, 4.3**

### Property 9: Cancellation Produces Partial Results

*For any* cancellation point during repository scanning, when the cancellation token is set before that point is reached, the scanner SHALL stop processing and return a `ScanResult` containing all packages parsed before the cancellation point, plus a cancellation diagnostic.

**Validates: Requirements 6.1, 6.2**

### Property 10: Scan Result Invariants

*For any* repository scan execution (whether successful, cancelled, or errored), the resulting `ScanResult` SHALL have `duration_seconds >= 0` and `diagnostics` as an ordered list preserving the sequence in which diagnostics were recorded.

**Validates: Requirements 5.2, 5.3**

### Property 11: Successful Repository Scan Short-Circuits Fallback

*For any* ISO with repository structure that produces at least one `IdentifiedPackage`, the scanner SHALL return the repository scan result with strategy `DPKG_METADATA` without attempting the direct rootfs dpkg check or filesystem analysis fallback.

**Validates: Requirements 7.2**

## Error Handling

| Error Scenario | Behavior | Diagnostic |
|---|---|---|
| ISO root listing fails (OSError) | Treat as no repository structure, fall through | None (silent fallback) |
| Codename directory listing fails | Skip that codename, continue others | "Failed to list directory: dists/{codename}: {error}" |
| Component directory listing fails | Skip that component, continue others | "Failed to list directory: dists/{codename}/{component}: {error}" |
| Packages file read fails (OSError) | Skip that file, continue others | "Failed to read {path}: {error}" |
| Gzip decompression fails | Skip that file, continue others | "Failed to decompress {path}: {error}" |
| UTF-8 decode fails | Use `errors="replace"` for lossy decode | "UTF-8 decode used replacement characters for {path}" |
| Stanza missing Package or Version | Skip stanza | "Stanza {n} in {path}: skipped, missing field: {field}" |
| Cancellation token set | Stop immediately, return partial results | "Repository scan cancelled during {step}" |
| All Packages files fail or produce 0 packages | Return None to trigger fallback | "No packages found in repository structure" |

All error handling follows the principle of graceful degradation: individual failures never abort the entire scan, and partial results are always preferred over complete failure.

## Testing Strategy

### Property-Based Testing (Hypothesis)

This feature is well-suited for property-based testing because:
- The core logic (stanza parsing, deduplication, directory discovery) is pure-function or near-pure with clear input/output behavior
- The input space is large (arbitrary RFC822 content, arbitrary directory structures)
- Universal properties can be stated about correctness

**Library:** Hypothesis (already used in the project — `.hypothesis/` directory exists)

**Configuration:**
- Minimum 100 iterations per property test (`@settings(max_examples=100)`)
- Each test tagged with: `# Feature: netinst-iso-repo-scanner, Property {N}: {description}`

**Properties to implement:**
1. Repository detection (Property 1)
2. Packages file discovery patterns (Property 2)
3. Packages.gz preference (Property 3)
4. Gzip round-trip (Property 4)
5. Status-less stanza parsing (Property 5)
6. Missing field diagnostics (Property 6)
7. Deduplication first-occurrence (Property 7)
8. Partial failure resilience (Property 8)
9. Cancellation partial results (Property 9)
10. Scan result invariants (Property 10)
11. Short-circuit fallback (Property 11)

### Unit Tests (Example-Based)

Unit tests cover specific scenarios and edge cases not well-suited to property generation:

- ISO with no squashfs + no dists/ falls through to direct rootfs (Req 1.3)
- I/O error on root listing triggers fallback (Req 1.4)
- Empty repository structure returns None/fallback (Req 2.6)
- Strategy ordering verification (Req 7.1)
- Repository structure detected but 0 packages triggers fallback (Req 7.3)
- Summary diagnostic content verification (Req 4.4)
- ScanResult field correctness (Req 5.1)
- ISO open failure returns error result (Req 5.4)

### Test Architecture

All tests use mock `ISOReader` implementations (already the pattern in the existing test suite). No real ISO images are needed. The mock reader is configured with a dictionary mapping paths to contents or errors, enabling precise control over what the scanner sees.

```python
class MockISOReader:
    """Configurable mock for property-based testing."""

    def __init__(self, dirs: dict[str, list[str]], files: dict[str, bytes], errors: dict[str, Exception]):
        self._dirs = dirs
        self._files = files
        self._errors = errors
```

### Dual Testing Balance

- **Property tests** verify universal correctness (deduplication, parsing, detection logic)
- **Unit tests** verify specific integration scenarios (strategy ordering, fallback triggers, error result shapes)
- Together they provide comprehensive coverage without redundancy
