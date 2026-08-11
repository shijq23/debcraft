# Mirror Path Dot Collision Bugfix Design

## Overview

The `derive_mirror_root` function uses Python's `Path /` operator to join URL path segments onto the mirror base path. The `Path` class normalizes `.` and `..` segments during construction, causing URLs with dot-only paths (e.g., `http://host/.`) to collapse to the same local path as URLs with no path (`http://host`). The fix replaces `Path /` joining for the url_path portion with string-based concatenation, preserving dot segments literally on the filesystem.

## Glossary

- **Bug_Condition (C)**: The URL path, after stripping slashes, contains segments that `Path` would normalize away (`.` or `..` components), causing the derived local path to collide with a different URL's path.
- **Property (P)**: Every distinct URL (differing in hostname or stripped path) SHALL produce a distinct local filesystem path.
- **Preservation**: All URLs whose paths contain only normal segments (no `.` or `..` components) must continue to produce the same paths as before the fix.
- **derive_mirror_root**: The function in `src/debcraft/infrastructure/mirror/paths.py` that maps a base URL to a local cache directory.
- **url_path**: The path portion of the parsed URL after stripping leading/trailing slashes.

## Bug Details

### Bug Condition

The bug manifests when `derive_mirror_root` is called with a URL whose path portion (after stripping slashes) contains `.` or `..` segments. The `Path /` operator normalizes these away, producing a path identical to a URL that lacks those segments entirely.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type BaseURL (string)
  OUTPUT: boolean

  parsed ← urlparse(input)
  url_path ← parsed.path.strip("/")
  segments ← url_path.split("/")

  RETURN url_path != "" AND ANY(segment IN (".", "..") FOR segment IN segments)
END FUNCTION
```

### Examples

- `http://host/.` → normalizes to same path as `http://host` (collision)
- `http://host/..` → normalizes to parent of host directory (collision + path escape)
- `http://host/a/..` → normalizes to same path as `http://host` (collision)
- `http://host/repo` → no dot segments, works correctly (not a bug condition)
- `http://host/dists/elxr3.0` → dots within segment names, works correctly (not a bug condition)

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- URLs with normal path segments (e.g., `https://mirror.elxr.dev/elxr`) must continue to produce `{mirror_base}/mirror.elxr.dev/elxr`
- URLs with no path (e.g., `https://mirror.elxr.dev`) must continue to produce `{mirror_base}/mirror.elxr.dev`
- URLs with nested paths (e.g., `https://example.com/repos/debian/main`) must continue to produce `{mirror_base}/example.com/repos/debian/main`
- URLs with dots within segment names (e.g., `dists/elxr3.0/InRelease`) must continue to work correctly
- HTTP and HTTPS URLs must both continue to be handled correctly

**Scope:**
All inputs where `isBugCondition` returns false (no `.` or `..` path components) should produce identical results before and after the fix.

## Hypothesized Root Cause

The root cause is the use of Python's `Path /` operator for joining the `url_path` portion onto `mirror_base / hostname`:

```python
return mirror_base / hostname / url_path
```

`pathlib.Path` applies POSIX path normalization rules during construction:
- `Path("/a/b") / "."` resolves to `Path("/a/b")` — the `.` is consumed
- `Path("/a/b") / ".."` resolves to `Path("/a")` — the `..` traverses up
- `Path("/a/b") / "c/.."` resolves to `Path("/a/b")` — the segment is cancelled

Since URLs can legally contain `.` and `..` as path segments, and these are semantically meaningful in HTTP (they represent distinct resources), the normalization causes distinct URLs to map to the same filesystem path.

## Correctness Properties

Property 1: Bug Condition - Dot Segment Path Collision

_For any_ URL where the path portion contains `.` or `..` segments (isBugCondition returns true), the fixed `derive_mirror_root` function SHALL produce a local path that is distinct from the path produced by the same hostname with a different or empty URL path, preserving dot segments literally in the filesystem path.

**Validates: Requirements 2.1, 2.2**

Property 2: Preservation - Normal URL Path Derivation

_For any_ URL where the path portion contains only normal segments (no `.` or `..` components, i.e., isBugCondition returns false), the fixed `derive_mirror_root` function SHALL produce the same result as the original function, preserving the existing `{mirror_base}/{hostname}/{url_path}` layout.

**Validates: Requirements 3.1, 3.2, 3.3**

## Fix Implementation

### Changes Required

**File**: `src/debcraft/infrastructure/mirror/paths.py`

**Function**: `derive_mirror_root`

**Specific Changes**:

1. **Replace Path `/` join with string concatenation for url_path**: Instead of `mirror_base / hostname / url_path`, construct the path using string concatenation to avoid `Path` normalization of the url_path portion.

   Before:
   ```python
   if url_path:
       return mirror_base / hostname / url_path
   return mirror_base / hostname
   ```

   After:
   ```python
   if url_path:
       return Path(f"{mirror_base}/{hostname}/{url_path}")
   return mirror_base / hostname
   ```

2. **Preserve hostname joining via Path operator**: The hostname cannot contain `.` or `..` as a complete segment (DNS rules prevent this), so `mirror_base / hostname` remains safe and correct.

3. **No changes to `derive_file_path` or `set_file_mode`**: These functions are unaffected. `derive_file_path` operates on repository-relative paths (like `dists/suite/InRelease`) which don't contain standalone dot segments in practice, and its existing behavior is correct.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm that `Path /` normalization causes dot-segment URLs to collide.

**Test Plan**: Write a property-based test using Hypothesis that generates pairs of URLs where one has dot segments and the other doesn't, then asserts they produce different paths. Run on UNFIXED code to observe failures.

**Test Cases**:
1. **Dot-only path collision**: `http://host/.` vs `http://host` should produce different paths (will fail on unfixed code)
2. **Double-dot path collision**: `http://host/..` vs `http://host` should produce different paths (will fail on unfixed code)
3. **Mixed segment cancellation**: `http://host/a/..` vs `http://host` should produce different paths (will fail on unfixed code)

**Expected Counterexamples**:
- `derive_mirror_root(engine, "http://host/.")` returns the same path as `derive_mirror_root(engine, "http://host")`
- Root cause confirmed: `Path("/base/host") / "."` equals `Path("/base/host")`

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces distinct paths.

**Pseudocode:**
```
FOR ALL url WHERE isBugCondition(url) DO
  result_with_dot ← derive_mirror_root'(engine, url)
  result_without ← derive_mirror_root'(engine, url_without_dot_segments(url))
  ASSERT result_with_dot ≠ result_without
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL url WHERE NOT isBugCondition(url) DO
  ASSERT derive_mirror_root(engine, url) = derive_mirror_root'(engine, url)
END FOR
```

**Testing Approach**: Property-based testing with Hypothesis is recommended for preservation checking because:
- It generates many URL combinations automatically across the input domain
- It catches edge cases in hostname/path combinations that manual tests might miss
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

**Test Plan**: The existing property-based test `test_different_urls_produce_different_paths` already encodes the collision property. For preservation, observe the output of the unfixed code for normal URLs and write a property test asserting string-equivalence of output paths.

**Test Cases**:
1. **Normal path preservation**: Verify `https://mirror.elxr.dev/elxr` continues to produce `{base}/mirror.elxr.dev/elxr`
2. **No-path preservation**: Verify `https://mirror.elxr.dev` continues to produce `{base}/mirror.elxr.dev`
3. **Nested path preservation**: Verify multi-segment paths continue to work
4. **Dotted-name preservation**: Verify `dists/elxr3.0` (dots within names) continues to work

### Unit Tests

- Test that dot-segment URLs produce distinct paths from their normalized equivalents
- Test that `..` segments are preserved literally rather than traversing up
- Test that existing unit tests in `test_mirror_paths.py` continue to pass unchanged

### Property-Based Tests

- Generate random URLs with dot segments and verify no collisions with dot-free variants
- Generate random normal URLs and verify output matches string-concatenation formula
- The existing `test_different_urls_produce_different_paths` test validates the collision property

### Integration Tests

- Verify that the full mirror download workflow handles URLs with dot segments
- Verify that `derive_file_path` composed with the fixed `derive_mirror_root` still produces correct full paths
