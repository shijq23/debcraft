# Design Document: Mirror Property Tests

## Overview

This design adds two new property-based test files that validate formal correctness properties for the mirror domain's value objects (`FileEntry`, `SyncDecision`, `DownloadResult`) and the `PackagesParser`. These tests complement existing example-based unit tests by exhaustively exploring input spaces with Hypothesis.

No production code changes are required. The deliverables are:
1. `tests/properties/domain/mirror/test_mirror_values_properties.py`
2. `tests/properties/domain/mirror/test_packages_parser_properties.py`

## Architecture

The tests live in the existing property test directory alongside `test_mirror_config_properties.py` and `test_release_parser_properties.py`. They follow the same conventions: module-level docstrings, `@pytest.mark.unit` and `@pytest.mark.mirror` decorators on classes, `@settings(max_examples=200)` on each test method, and Hypothesis strategies defined as module-level helper functions.

```
tests/properties/domain/mirror/
├── __init__.py                              (existing)
├── test_mirror_config_properties.py         (existing)
├── test_release_parser_properties.py        (existing)
├── test_mirror_comparator_properties.py     (existing)
├── test_mirror_values_properties.py         (NEW)
└── test_packages_parser_properties.py       (NEW)
```

## Components and Interfaces

### test_mirror_values_properties.py

**Strategies:**
- `_file_entry()` — Builds `FileEntry` with arbitrary `st.text` for relative_path/sha256 and `st.integers(min_value=0)` for size_bytes
- `_sync_decision()` — Builds `SyncDecision` with a generated `FileEntry`, `st.sampled_from(["download", "skip", "verify"])` for action, and `st.text` for reason
- `_download_result()` — Builds `DownloadResult` with `st.text` for url, `st.booleans` for success/sha256_verified, `st.integers(min_value=0)` for bytes_transferred, `st.none() | st.text()` for error, `st.integers(min_value=0)` for retry_count, `st.none() | st.integers()` for status_code, `st.none() | st.dictionaries(st.text(), st.text())` for response_headers

**Test Classes:**
- `TestProperty1ValueObjectImmutability` — Verifies `AttributeError` on field assignment for all 3 types
- `TestProperty2EqualityReflexivity` — Verifies `instance == instance` for all 3 types
- `TestProperty3EqualitySymmetry` — Verifies `a == b` and `b == a` for identically-constructed pairs of all 3 types
- `TestProperty4InequalityOnDifferingFields` — Verifies `a != b` when exactly one field differs, for all 3 types

### test_packages_parser_properties.py

**Strategies:**
- `_valid_packages_stanza()` — Composite strategy producing a single valid stanza string with Filename, SHA256, and Size fields plus optional extra fields (Package, Version, Architecture)
- `_valid_packages_content()` — Builds multi-stanza content by joining 1-50 valid stanzas with blank lines
- `_arbitrary_content()` — `st.text(min_size=0, max_size=10000)` for fully random input
- `_packages_content()` — `st.one_of(_valid_packages_content(), _arbitrary_content())` for mixed testing
- `_valid_file_entry_fields()` — Generates (relative_path, sha256, size_bytes) tuples constrained to parser-safe values (no newlines/colons in strings, non-negative size)
- `_stanza_missing_field(excluded_field)` — Generates stanzas that include other valid Key:Value lines but omit the specified required field
- `_stanza_with_invalid_size()` — Generates stanzas with Filename and SHA256 but a non-parseable or negative Size

**Test Classes:**
- `TestProperty5ParserIdempotency` — Verifies `parse(x) == parse(x)` for all inputs
- `TestProperty6OutputValidity` — Verifies all returned entries have non-empty relative_path, non-empty sha256, and size_bytes >= 0
- `TestProperty7StanzaCountBound` — Verifies `len(parse(content)) <= stanza_count(content)`
- `TestProperty8Monotonicity` — Verifies appending a valid stanza does not decrease result count
- `TestProperty9RoundTrip` — Verifies constructing a stanza from known values and parsing it recovers those values
- `TestProperty10MissingFieldRejection` — Verifies stanzas missing Filename, SHA256, or Size produce no entries
- `TestProperty11InvalidSizeRejection` — Verifies stanzas with non-integer or negative Size produce no entries
- `TestProperty12InvalidStanzaIsolation` — Verifies invalid stanzas don't affect parsing of adjacent valid stanzas

## Data Models

No new data models are introduced. The tests operate on existing frozen dataclasses:

| Class | Fields | Source |
|-------|--------|--------|
| `FileEntry` | `relative_path: str`, `sha256: str`, `size_bytes: int` | `values.py` |
| `SyncDecision` | `file_entry: FileEntry`, `action: Literal["download","skip","verify"]`, `reason: str` | `values.py` |
| `DownloadResult` | `url: str`, `success: bool`, `sha256_verified: bool`, `bytes_transferred: int`, `error: str\|None`, `retry_count: int`, `status_code: int\|None`, `response_headers: dict[str,str]\|None` | `values.py` |

The `PackagesParser.parse(content: str) -> list[FileEntry]` interface is tested as-is.

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Value Object Immutability

*For any* generated value object instance (FileEntry, SyncDecision, or DownloadResult), attempting to assign a new value to any of its fields SHALL raise an `AttributeError`.

**Validates: Requirements 1.1, 1.2, 1.3**

### Property 2: Value Object Equality Reflexivity

*For any* generated value object instance (FileEntry, SyncDecision, or DownloadResult) with arbitrary valid field values including both None and non-None optional fields, the instance SHALL equal itself under the `==` operator.

**Validates: Requirements 2.1, 2.2, 2.3**

### Property 3: Value Object Equality Symmetry

*For any* set of field values used to construct two distinct value object instances of the same type, if `a == b` then `b == a` (both evaluate to True).

**Validates: Requirements 3.1, 3.2, 3.3**

### Property 4: Value Object Inequality on Differing Fields

*For any* pair of value object instances of the same type where exactly one field differs and all other fields are identical, `a != b` SHALL hold.

**Validates: Requirements 4.1, 4.2, 4.3**

### Property 5: PackagesParser Idempotency

*For any* content string, calling `PackagesParser.parse(content)` twice on the same instance SHALL produce identical results (same length, same order, same element values).

**Validates: Requirements 5.1, 5.3**

### Property 6: PackagesParser Output Validity

*For any* content string, every `FileEntry` returned by `PackagesParser.parse(content)` SHALL have `len(relative_path) >= 1`, `len(sha256) >= 1`, and `size_bytes >= 0`.

**Validates: Requirements 6.1, 6.2, 6.3, 6.4**

### Property 7: PackagesParser Stanza Count Bound

*For any* content string, the number of `FileEntry` instances returned by `PackagesParser.parse(content)` SHALL be less than or equal to the number of stanzas in the input (where a stanza is a maximal group of consecutive non-blank lines).

**Validates: Requirements 7.1, 7.2**

### Property 8: PackagesParser Monotonicity

*For any* content string and *for any* valid stanza containing non-empty Filename, 64-character hex SHA256, and non-negative integer Size, appending the valid stanza to the content (separated by a blank line) SHALL result in `len(parse(combined)) >= len(parse(content))`.

**Validates: Requirements 8.1, 8.2**

### Property 9: PackagesParser Round-Trip

*For any* valid FileEntry field values (relative_path and sha256 containing no newlines or colons, size_bytes non-negative), constructing a stanza in the format `"Filename: {path}\nSHA256: {hash}\nSize: {size}\n"` and parsing it SHALL produce exactly one FileEntry with field values equal to the original inputs.

**Validates: Requirements 9.1**

### Property 10: PackagesParser Missing Field Rejection

*For any* stanza containing at least one valid Key: Value line but missing one of the required fields (Filename, SHA256, or Size), `PackagesParser.parse` SHALL return an empty list.

**Validates: Requirements 10.1, 10.2, 10.3**

### Property 11: PackagesParser Invalid Size Rejection

*For any* stanza containing Filename and SHA256 fields but with a Size value that is either not parseable as an integer or is a negative integer, `PackagesParser.parse` SHALL return an empty list.

**Validates: Requirements 10.4, 10.5**

### Property 12: PackagesParser Invalid Stanza Isolation

*For any* invalid stanza (missing Filename) concatenated with a valid stanza (separated by a blank line), `PackagesParser.parse` SHALL return exactly 1 FileEntry corresponding to the valid stanza only.

**Validates: Requirements 10.6**

## Error Handling

These are test-only files. No production error handling is introduced.

**Test-level error handling considerations:**
- Hypothesis strategies must avoid generating values that cause strategy errors (e.g., `min_size > max_size`). Use `assume()` or `.filter()` where constraints are complex.
- The `_file_entry()` strategy uses `min_size=1` for string fields and `min_value=0` for size_bytes to match dataclass semantics (the dataclass itself doesn't enforce these, but the parser does).
- For inequality tests (Property 4), strategies must guarantee the differing field is actually different from the base value — use `.filter(lambda x: x != original)` to ensure this.
- Round-trip tests (Property 9) constrain generated strings to exclude newlines and colons, since those characters would break the stanza format.

## Testing Strategy

**Framework:** Hypothesis (already a project dependency, used in existing property tests)

**Test configuration:**
- Each test method uses `@settings(max_examples=200)` — consistent with project conventions
- Each test class is decorated with `@pytest.mark.unit` and `@pytest.mark.mirror`
- Tests are runnable via `pytest tests/properties/domain/mirror/ -m "unit and mirror"`

**Dual testing approach:**
- **Property tests** (this spec): Validate universal invariants across random inputs. These are the primary deliverable.
- **Example-based unit tests** (existing): Cover specific known scenarios, integration points, and edge cases. Already exist in the project and are not modified.

**Property test tagging:**
Each property test method references its design property in the class or method docstring:
- Format: `Property {N}: {title}` in the class docstring
- Format: `**Validates: Requirements X.Y**` in the module docstring

**Property-based testing library:** Hypothesis (already installed and configured in the project)
- Minimum 200 iterations per property test via `@settings(max_examples=200)`
- Strategies use `st.builds`, `st.text`, `st.integers`, `st.sampled_from`, `st.none`, `st.one_of`, `st.dictionaries`, `st.booleans`, and `@st.composite`
- Tag format in docstrings: **Feature: mirror-property-tests, Property {number}: {property_text}**

**File structure follows existing conventions:**
- Module-level docstring listing all properties
- Underscore-prefixed module-level strategy helper functions
- Test classes grouped by property with class-level docstrings
- Individual test methods for specific aspects of each property
