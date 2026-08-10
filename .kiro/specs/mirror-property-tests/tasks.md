# Implementation Plan: Mirror Property Tests

## Overview

Create two new property-based test files validating formal correctness properties for mirror domain value objects and the PackagesParser. No production code changes needed. Tests follow existing conventions from `test_mirror_config_properties.py`.

## Tasks

- [x] 1. Implement value object property tests
  - [x] 1.1 Create `tests/properties/domain/mirror/test_mirror_values_properties.py` with Hypothesis strategies and Property 1-4 test classes
    - Add module-level docstring describing Properties 1-4
    - Define `_file_entry()` strategy using `st.builds(FileEntry, ...)` with `st.text` for strings and `st.integers(min_value=0)` for size_bytes
    - Define `_sync_decision()` strategy using `st.builds(SyncDecision, ...)` with generated FileEntry, `st.sampled_from(["download", "skip", "verify"])`, and `st.text` for reason
    - Define `_download_result()` strategy using `st.builds(DownloadResult, ...)` with all 8 fields including optional None variants
    - Implement `TestProperty1ValueObjectImmutability` class with `@pytest.mark.unit` and `@pytest.mark.mirror` decorators
      - Test method for FileEntry field assignment raises AttributeError (3 fields)
      - Test method for SyncDecision field assignment raises AttributeError (3 fields)
      - Test method for DownloadResult field assignment raises AttributeError (8 fields)
    - Implement `TestProperty2EqualityReflexivity` class
      - Test method confirming `instance == instance` for FileEntry
      - Test method confirming `instance == instance` for SyncDecision
      - Test method confirming `instance == instance` for DownloadResult
    - Implement `TestProperty3EqualitySymmetry` class
      - Test method confirming `a == b and b == a` for identically-constructed FileEntry pairs
      - Test method confirming `a == b and b == a` for identically-constructed SyncDecision pairs
      - Test method confirming `a == b and b == a` for identically-constructed DownloadResult pairs
    - Implement `TestProperty4InequalityOnDifferingFields` class
      - Test methods for FileEntry inequality when each of 3 fields differs individually
      - Test methods for SyncDecision inequality when each of 3 fields differs individually
      - Test methods for DownloadResult inequality when each of 8 fields differs individually
    - All test methods use `@settings(max_examples=200)`
    - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 4.1, 4.2, 4.3, 4.4, 11.1, 11.2, 11.3, 11.4, 11.5_

  - [x] 1.2 Run value object property tests and verify they pass
    - Execute `pytest tests/properties/domain/mirror/test_mirror_values_properties.py -v`
    - Confirm all property tests pass with 200 examples each
    - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 4.1, 4.2, 4.3_

- [x] 2. Implement PackagesParser property tests (Properties 5-9)
  - [x] 2.1 Create `tests/properties/domain/mirror/test_packages_parser_properties.py` with strategies and core property test classes
    - Add module-level docstring describing Properties 5-12
    - Define `_valid_packages_stanza()` composite strategy producing a single valid stanza string with Filename, SHA256, and Size fields plus optional extra fields
    - Define `_valid_packages_content()` strategy joining 1-50 valid stanzas with blank lines
    - Define `_arbitrary_content()` strategy using `st.text(min_size=0, max_size=10000)`
    - Define `_packages_content()` using `st.one_of(_valid_packages_content(), _arbitrary_content())`
    - Define `_valid_file_entry_fields()` generating (relative_path, sha256, size_bytes) tuples constrained to parser-safe values (no newlines/colons, non-negative size)
    - Implement `TestProperty5ParserIdempotency` class with `@pytest.mark.unit` and `@pytest.mark.mirror`
      - Test method confirming `parse(content) == parse(content)` for arbitrary content
      - Test method confirming `parse(content) == parse(content)` for valid Packages content
    - Implement `TestProperty6OutputValidity` class
      - Test method confirming all returned entries have non-empty relative_path, non-empty sha256, size_bytes >= 0
    - Implement `TestProperty7StanzaCountBound` class
      - Test method confirming `len(parse(content)) <= stanza_count(content)` with a helper function to count stanzas
    - Implement `TestProperty8Monotonicity` class
      - Test method confirming appending a valid stanza does not decrease result count
      - Test method confirming parsing a single valid stanza returns at least 1 entry
    - Implement `TestProperty9RoundTrip` class
      - Test method confirming constructing a stanza from known values and parsing recovers those exact values
    - All test methods use `@settings(max_examples=200)`
    - _Requirements: 5.1, 5.2, 5.3, 6.1, 6.2, 6.3, 6.4, 7.1, 7.2, 8.1, 8.2, 9.1, 11.1, 11.2, 11.3, 11.4, 11.5_

  - [x] 2.2 Add error condition property test classes (Properties 10-12) to `test_packages_parser_properties.py`
    - Define `_stanza_missing_field(excluded_field)` strategy generating stanzas with valid Key:Value lines but omitting the specified required field
    - Define `_stanza_with_invalid_size()` strategy generating stanzas with Filename and SHA256 but non-parseable or negative Size
    - Implement `TestProperty10MissingFieldRejection` class
      - Test method confirming stanzas missing Filename produce no entries
      - Test method confirming stanzas missing SHA256 produce no entries
      - Test method confirming stanzas missing Size produce no entries
    - Implement `TestProperty11InvalidSizeRejection` class
      - Test method confirming stanzas with non-integer Size produce no entries
      - Test method confirming stanzas with negative Size produce no entries
    - Implement `TestProperty12InvalidStanzaIsolation` class
      - Test method confirming invalid stanzas don't affect parsing of adjacent valid stanzas
    - All test methods use `@settings(max_examples=200)`
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 11.1, 11.2, 11.3, 11.4, 11.5_

  - [x] 2.3 Run PackagesParser property tests and verify they pass
    - Execute `pytest tests/properties/domain/mirror/test_packages_parser_properties.py -v`
    - Confirm all property tests pass with 200 examples each
    - _Requirements: 5.1, 6.1, 7.1, 8.1, 9.1, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

- [x] 3. Final checkpoint
  - Ensure all tests pass by running `pytest tests/properties/domain/mirror/test_mirror_values_properties.py tests/properties/domain/mirror/test_packages_parser_properties.py -v`
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- No production code changes are needed — this spec only adds test files
- Follow conventions from `tests/properties/domain/mirror/test_mirror_config_properties.py`
- Each test method uses `@settings(max_examples=200)` and classes use `@pytest.mark.unit` / `@pytest.mark.mirror`
- Property tests validate universal correctness properties defined in the design document
- Source modules under test: `src/debcraft/domain/mirror/values.py` and `src/debcraft/domain/mirror/packages_parser.py`

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "2.1"] },
    { "id": 2, "tasks": ["2.2"] },
    { "id": 3, "tasks": ["2.3"] },
    { "id": 4, "tasks": ["3.1"] }
  ]
}
```
