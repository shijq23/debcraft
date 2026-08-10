# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Filename-Based Classification
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate `.deb` files in directories containing metadata keywords are misclassified
  - **Scoped PBT Approach**: Generate URLs of the form `pool/main/<letter>/<package-name-containing-keyword>/<pkg>_<version>_<arch>.deb` where directory names contain "packages", "sources", "release", or "resources" as substrings, and assert `_infer_file_type(url)` returns "unknown"
  - Bug Condition from design: `isBugCondition(input)` = full path contains metadata keyword AND filename does NOT match metadata pattern
  - Expected behavior: `_infer_file_type` SHALL return "unknown" for all such inputs
  - Create test file at `tests/properties/domain/indexer/test_file_type_classification_bug.py`
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct — it proves the bug exists)
  - Document counterexamples found (e.g., `_infer_file_type("pool/main/l/lxde-metapackages/lxde-core_11_all.deb")` returns "packages" instead of "unknown")
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Legitimate Metadata File Classification
  - **IMPORTANT**: Follow observation-first methodology
  - Observe: `_infer_file_type("dists/bookworm/main/binary-amd64/Packages.gz")` returns "packages" on unfixed code
  - Observe: `_infer_file_type("dists/bookworm/main/source/Sources.xz")` returns "sources" on unfixed code
  - Observe: `_infer_file_type("dists/bookworm/main/Contents-amd64.gz")` returns "contents" on unfixed code
  - Observe: `_infer_file_type("dists/bookworm/Release")` returns "release" on unfixed code
  - Observe: `_infer_file_type("dists/bookworm/InRelease")` returns "release" on unfixed code
  - Write property-based test: for all URLs whose filename (last path segment, compression stripped) starts with or equals a known metadata name, result matches expected type
  - Strategy should generate diverse metadata filenames: Packages, Packages.gz, Packages.xz, Packages.bz2, Sources, Sources.gz, Contents-amd64, Contents-i386.gz, Release, InRelease (with various path prefixes and case variants)
  - Add test to `tests/properties/domain/indexer/test_file_type_classification_bug.py`
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [x] 3. Fix `_infer_file_type` to use filename-based classification

  - [x] 3.1 Implement the fix in `src/debcraft/domain/indexer/service.py`
    - Replace the existing `_infer_file_type` implementation (lines 39–55) with filename-based logic
    - Extract the filename: last non-empty segment after splitting on "/"
    - Strip compression extensions (.gz, .xz, .bz2) from the filename
    - Match lowercased base filename: starts with "packages" → "packages", starts with "sources" → "sources", starts with "contents" → "contents", equals "release" or "inrelease" → "release", otherwise → "unknown"
    - Preserve function signature: `_infer_file_type(url_or_path: str) -> str`
    - _Bug_Condition: isBugCondition(input) where full path contains metadata keyword but filename does not match metadata pattern_
    - _Expected_Behavior: return "unknown" for all non-metadata filenames regardless of directory names_
    - _Preservation: legitimate metadata filenames continue to be classified correctly_
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [x] 3.2 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Filename-Based Classification
    - **IMPORTANT**: Re-run the SAME test from task 1 — do NOT write a new test
    - The test from task 1 encodes the expected behavior
    - When this test passes, it confirms the expected behavior is satisfied
    - Run `pytest tests/properties/domain/indexer/test_file_type_classification_bug.py -k "bug_condition"` (or equivalent marker)
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 3.3 Verify preservation tests still pass
    - **Property 2: Preservation** - Legitimate Metadata File Classification
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Run `pytest tests/properties/domain/indexer/test_file_type_classification_bug.py -k "preservation"` (or equivalent marker)
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all preservation tests still pass after fix (no regressions)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [x] 3.4 Update existing property tests that depend on the old substring behavior
    - Update `tests/properties/domain/indexer/test_indexer_service_properties.py`: the `_repository_file_url` strategy generates URLs where the keyword appears in the filename (Packages, Sources, etc.) — these should still pass without changes, but verify
    - Update `tests/test_indexer_deb_file_errors_preservation.py`: the `known_file_type_url` strategy generates URLs by placing keywords in paths — update the strategy to generate URLs where the keyword is in the FILENAME position (last path segment) so the test correctly validates behavior under the new logic
    - Run the full test suite to confirm no other tests break
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [x] 4. Checkpoint - Ensure all tests pass
  - Run `pytest` to confirm all tests pass
  - Verify no regressions in existing property tests (Property 10, 11, 12, 13)
  - Verify the exploration test (Property 1) passes with the fix
  - Verify the preservation test (Property 2) passes with the fix
  - Verify the updated existing tests pass
  - Ask the user if questions arise
