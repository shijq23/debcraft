# Implementation Plan

## Overview

Fix the `derive_mirror_root` function to prevent dot-segment path collisions by replacing `Path /` operator joining with string-based path construction for the `url_path` portion.

## Notes

- Task 1 writes an exploration test BEFORE the fix to confirm the bug exists (test is expected to FAIL on unfixed code)
- Task 2 implements the fix and verifies both the exploration test passes and existing tests still pass
- Task 3 runs the full test suite as a final validation
- Task 4 removes the exploration test since the existing property tests already cover the collision property

## Tasks

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Dot Segment Path Collision
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate dot-segment URLs collide with dot-free URLs
  - Create `tests/properties/infrastructure/test_path_dot_collision_exploration.py`
  - Use Hypothesis to generate URLs with dot segments (`.`, `..`, `a/..`) and assert that `derive_mirror_root` produces a path DIFFERENT from the same host with no path or with the dot segments removed
  - Bug condition from design: `isBugCondition(url)` returns true when `url_path.split("/")` contains any segment equal to `"."` or `".."`
  - Expected behavior: `derive_mirror_root(engine, "http://host/.")` SHALL produce a path distinct from `derive_mirror_root(engine, "http://host")`
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves the bug exists via `Path` normalization)
  - Document counterexamples: e.g., `"http://host/."` and `"http://host"` produce identical paths
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2_

- [x] 2. Implement the fix
  - [x] 2.1 Apply dot-segment encoding fix in `derive_mirror_root`
    - In `src/debcraft/infrastructure/mirror/paths.py`, after computing `url_path = parsed.path.strip("/")`, split it into segments and URL-encode any segment that is exactly `"."` or `".."` (replace `.` with `%2E` and `..` with `%2E%2E`), then rejoin with `/`
    - Use `Path(f"{mirror_base}/{hostname}/{escaped_url_path}")` for the final construction to avoid any remaining Path normalization
    - Keep the `mirror_base / hostname` branch (no url_path case) unchanged since hostnames cannot contain standalone dot segments
    - Normal path segments containing dots (like `elxr3.0`) are NOT affected since they don't equal `"."` or `".."` exactly
    - _Bug_Condition: isBugCondition(url) where url_path contains "." or ".." segments_
    - _Expected_Behavior: distinct paths for every distinct (hostname, url_path) pair_
    - _Preservation: URLs with normal segments produce same paths as before_
    - _Requirements: 2.1, 2.2, 3.1, 3.2, 3.3_

  - [x] 2.2 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Dot Segment Path Collision
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - Run `pytest tests/properties/infrastructure/test_path_dot_collision_exploration.py`
    - **EXPECTED OUTCOME**: Test PASSES (confirms dot-segment URLs now produce distinct paths)
    - _Requirements: 2.1, 2.2_

  - [x] 2.3 Verify preservation via existing property tests
    - **Property 2: Preservation** - Normal URL Path Derivation
    - **IMPORTANT**: Re-run existing tests - do NOT write new tests
    - Run `pytest tests/properties/infrastructure/test_path_properties.py`
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions for normal URLs)
    - _Requirements: 3.1, 3.2, 3.3_

- [x] 3. Run full test suite to verify fix and preservation
  - Run unit tests: `pytest tests/unit/infrastructure/test_mirror_paths.py`
  - Run property tests: `pytest tests/properties/infrastructure/test_path_properties.py`
  - Run exploration test: `pytest tests/properties/infrastructure/test_path_dot_collision_exploration.py`
  - **EXPECTED OUTCOME**: All tests PASS
  - Confirm the bug is fixed (exploration property test passes)
  - Confirm no regressions (unit tests and property tests still pass)
  - _Requirements: 2.1, 2.2, 3.1, 3.2, 3.3_

- [x] 4. Cleanup - Remove exploration test file
  - Delete `tests/properties/infrastructure/test_path_dot_collision_exploration.py`
  - The exploration test served its purpose of demonstrating the bug on unfixed code and verifying the fix
  - The existing `test_path_properties.py` already contains `test_different_urls_produce_different_paths` which covers the collision property going forward
  - Run `pytest tests/properties/infrastructure/test_path_properties.py tests/unit/infrastructure/test_mirror_paths.py` one final time to confirm suite is green without the exploration test

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1"] },
    { "id": 2, "tasks": ["2.2", "2.3"] },
    { "id": 3, "tasks": ["3"] },
    { "id": 4, "tasks": ["4"] }
  ]
}
```
