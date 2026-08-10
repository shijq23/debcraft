# Indexer File Type Classification Bugfix Design

## Overview

The `_infer_file_type()` function in `src/debcraft/domain/indexer/service.py` performs naive substring matching on the full URL/path string to classify repository files. This causes `.deb` binary package files whose paths contain directory names like "packages", "sources", or "release" (e.g., `pool/main/l/lxde-metapackages/...`) to be misclassified as metadata types. The fix extracts the filename (last path segment), strips compression extensions, and matches against known Debian metadata filename patterns instead of searching the entire URL.

## Glossary

- **Bug_Condition (C)**: The input triggers the bug — a URL/path whose last path segment does NOT match a metadata filename pattern, but whose full path contains a metadata keyword as a substring
- **Property (P)**: The desired behavior — classification is based solely on the filename (last path segment after stripping compression extensions), not the full path
- **Preservation**: Existing correct classification of legitimate metadata files (Packages.gz, Sources.xz, Release, InRelease, Contents-amd64.gz) must remain unchanged
- **`_infer_file_type`**: The function in `src/debcraft/domain/indexer/service.py` (lines 39–55) that accepts a URL or path string and returns one of: "packages", "sources", "contents", "release", or "unknown"
- **Compression extensions**: `.gz`, `.xz`, `.bz2` — suffixes stripped before filename pattern matching

## Bug Details

### Bug Condition

The bug manifests when a URL/path is provided whose full string contains a metadata keyword ("packages", "sources", "contents", "release") as a substring, but whose actual filename (last path segment) does NOT start with or equal one of those metadata filenames. The most common case is `.deb` files in pool directories that contain these keywords in package names or directory names.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type string (URL or path)
  OUTPUT: boolean

  filename := lastPathSegment(input)
  base := stripCompressionExtension(filename)
  isMetadataByFilename := startsWithCaseInsensitive(base, "packages")
                       OR startsWithCaseInsensitive(base, "sources")
                       OR startsWithCaseInsensitive(base, "contents")
                       OR equalsCaseInsensitive(base, "release")
                       OR equalsCaseInsensitive(base, "inrelease")

  containsKeywordInFullPath := containsCaseInsensitive(input, "packages")
                            OR containsCaseInsensitive(input, "sources")
                            OR containsCaseInsensitive(input, "contents")
                            OR containsCaseInsensitive(input, "release")

  RETURN (NOT isMetadataByFilename) AND containsKeywordInFullPath
END FUNCTION
```

### Examples

- `pool/main/l/lxde-metapackages/lxde-core_11_all.deb` — directory contains "packages", but filename is `lxde-core_11_all.deb` → should be "unknown", currently returns "packages"
- `pool/main/t/testresources/python3-testresources_2.0.1-4_all.deb` — directory contains "sources" (via "resources"), filename is a `.deb` → should be "unknown", currently returns "sources"
- `pool/main/l/lsb-release-minimal/lsb-release-minimal_12.0-1_all.deb` — directory and filename contain "release", but filename ends `.deb` → should be "unknown", currently returns "release"
- `pool/main/i/importlib-resources/python3-importlib-resources_5.1.2-2_all.deb` — path contains "resources" (triggers "sources" match), filename is a `.deb` → should be "unknown", currently returns "sources"

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Legitimate metadata files (Packages, Packages.gz, Packages.xz, Packages.bz2) continue to be classified as "packages"
- Legitimate metadata files (Sources, Sources.gz, Sources.xz) continue to be classified as "sources"
- Legitimate metadata files (Contents-amd64, Contents-i386.gz) continue to be classified as "contents"
- Release and InRelease files continue to be classified as "release"
- Files that don't match any pattern continue to be classified as "unknown"
- The function signature `_infer_file_type(url_or_path: str) -> str` remains unchanged
- The return value set remains: "packages", "sources", "contents", "release", "unknown"

**Scope:**
All inputs whose filename (last path segment, after stripping compression extensions) matches a known metadata pattern should produce the same classification as before. The fix only changes behavior for inputs where the keyword match was coming from a directory segment rather than the filename.

## Hypothesized Root Cause

Based on the code at lines 39–55 of `service.py`, the root cause is clear:

1. **Full-path substring matching**: The function calls `url_or_path.lower()` and then checks `if "packages" in lowered`, which matches the keyword anywhere in the string — directory names, package names, etc.

2. **No filename extraction**: The function never isolates the last path segment (the actual filename). It treats the entire URL as a bag of characters for matching.

3. **No extension awareness**: The function does not account for file extensions like `.deb`, `.udeb`, or `.dsc` that would immediately indicate a non-metadata file.

4. **Greedy first-match ordering**: Even if multiple keywords appear in a path, the first matching `if` branch wins, producing potentially inconsistent results.

## Correctness Properties

Property 1: Bug Condition - Filename-Based Classification

_For any_ URL or path where the bug condition holds (the full path contains a metadata keyword but the filename does NOT match a metadata pattern), the fixed `_infer_file_type` function SHALL return "unknown" instead of misclassifying based on directory names.

**Validates: Requirements 2.5**

Property 2: Preservation - Legitimate Metadata File Classification

_For any_ URL or path where the bug condition does NOT hold (the filename itself matches a known metadata pattern), the fixed `_infer_file_type` function SHALL produce the same result as the original function, preserving correct classification of Packages, Sources, Contents, Release, and InRelease files.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**

## Fix Implementation

### Changes Required

**File**: `src/debcraft/domain/indexer/service.py`

**Function**: `_infer_file_type`

**Specific Changes**:

1. **Extract filename**: Use the last segment of the path (split on `/`, take the last non-empty element) instead of matching against the full URL string.

2. **Strip compression extensions**: Remove trailing `.gz`, `.xz`, or `.bz2` from the filename before pattern matching.

3. **Match against filename patterns**:
   - If base filename starts with "packages" (case-insensitive) → return "packages"
   - If base filename starts with "sources" (case-insensitive) → return "sources"
   - If base filename starts with "contents" (case-insensitive) → return "contents"
   - If base filename equals "release" or "inrelease" (case-insensitive) → return "release"
   - Otherwise → return "unknown"

4. **Handle edge cases**: Empty strings, paths ending in `/`, URLs with query strings — extract filename robustly using `posixpath` or simple string splitting.

**Pseudocode for fixed function:**
```
FUNCTION _infer_file_type(url_or_path)
  INPUT: url_or_path of type string
  OUTPUT: one of "packages", "sources", "contents", "release", "unknown"

  filename := last non-empty segment after splitting on "/"
  IF filename is empty THEN RETURN "unknown"

  base := filename
  FOR ext IN [".gz", ".xz", ".bz2"] DO
    IF base ends with ext (case-insensitive) THEN
      base := base without trailing ext
      BREAK
    END IF
  END FOR

  lowered := lowercase(base)
  IF lowered starts with "packages" THEN RETURN "packages"
  IF lowered starts with "sources" THEN RETURN "sources"
  IF lowered starts with "contents" THEN RETURN "contents"
  IF lowered = "release" OR lowered = "inrelease" THEN RETURN "release"
  RETURN "unknown"
END FUNCTION
```

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm the root cause analysis.

**Test Plan**: Write a property-based test that generates URLs with metadata keywords in directory names but non-metadata filenames (e.g., `.deb` files in `pool/main/l/lxde-metapackages/`). Assert that `_infer_file_type` returns "unknown". Run on UNFIXED code to observe failures.

**Test Cases**:
1. **Deb file in "packages" directory**: `pool/main/l/lxde-metapackages/lxde-core_11_all.deb` → expect "unknown" (will fail on unfixed code, returns "packages")
2. **Deb file in "sources" directory**: `pool/main/t/testresources/python3-testresources_2.0.1-4_all.deb` → expect "unknown" (will fail, returns "sources")
3. **Deb file in "release" directory**: `pool/main/l/lsb-release-minimal/lsb-release-minimal_12.0-1_all.deb` → expect "unknown" (will fail, returns "release")
4. **Partial keyword match**: `pool/main/i/importlib-resources/foo_1.0_amd64.deb` → expect "unknown" (will fail, returns "sources")

**Expected Counterexamples**:
- All `.deb` files in directories containing metadata keywords are misclassified
- Root cause confirmed: substring match on full path, not filename

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function returns "unknown".

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := _infer_file_type_fixed(input)
  ASSERT result = "unknown"
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold (legitimate metadata filenames), the fixed function produces the same classification as the original.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT _infer_file_type_original(input) = _infer_file_type_fixed(input)
END FOR
```

**Testing Approach**: Property-based testing generates diverse metadata file URLs (Packages.gz, Sources.xz, Contents-amd64.gz, Release, InRelease with various prefixes/cases) and asserts the fixed function returns the same type as the original for all of them.

**Test Cases**:
1. **Packages file preservation**: `dists/bookworm/main/binary-amd64/Packages.gz` → still "packages"
2. **Sources file preservation**: `dists/bookworm/main/source/Sources.xz` → still "sources"
3. **Contents file preservation**: `dists/bookworm/main/Contents-amd64.gz` → still "contents"
4. **Release file preservation**: `dists/bookworm/Release`, `dists/bookworm/InRelease` → still "release"

### Unit Tests

- Test each metadata filename pattern (with/without compression extensions)
- Test `.deb` files in directories containing metadata keywords
- Test edge cases: empty string, path ending in slash, URLs with query strings

### Property-Based Tests

- Generate random `.deb`/`.udeb`/`.dsc` filenames in directories containing metadata keywords → assert "unknown"
- Generate random legitimate metadata filenames with various prefixes/compressions → assert correct type
- Generate random non-matching filenames → assert "unknown"

### Integration Tests

- Test that existing `IndexerService.index_repository` correctly classifies files when using the fixed function
- Test that `.deb` files are no longer passed to metadata parsers (no more UnicodeDecodeError)
