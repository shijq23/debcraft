"""Property-based tests for SBOM serialization determinism and writer result integrity.

# Feature: sbom-writers, Property 5: Serialization determinism

**Validates: Requirements 3.5, 10.1, 10.5**

Property 5: Serialization determinism.
For any valid SBOMDocument, serializing the document with any writer twice
SHALL produce byte-identical output. The output SHALL use 2-space indentation,
sorted keys, UTF-8 encoding without BOM, and a trailing newline character.

# Feature: sbom-writers, Property 6: Writer result integrity

**Validates: Requirements 3.6**

Property 6: Writer result integrity.
For any valid SBOMDocument written by any writer, the WriterResult.sha256 field
SHALL equal the SHA-256 hash independently computed from the written file's bytes,
and WriterResult.file_size SHALL equal the actual byte count of the written file.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.infrastructure.sbom_writers.printer import SBOMPrinter
from debcraft.platform.contracts.workflow import CancellationToken, WorkflowContext

from .strategies import sbom_documents

# ---------------------------------------------------------------------------
# Strategies for generating arbitrary nested dicts (for determinism testing)
# ---------------------------------------------------------------------------

# Generate JSON-compatible leaf values including Unicode
_unicode_text = st.text(
    alphabet=st.characters(
        categories=("L", "M", "N", "P", "S", "Z"),
        exclude_characters="\x00",
    ),
    min_size=0,
    max_size=50,
)

_json_leaves = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**53), max_value=2**53),
    st.floats(allow_nan=False, allow_infinity=False),
    _unicode_text,
)


def _json_values() -> st.SearchStrategy:
    """Generate arbitrary JSON-compatible values (nested dicts, lists, scalars)."""
    return st.recursive(
        _json_leaves,
        lambda children: st.one_of(
            st.lists(children, max_size=5),
            st.dictionaries(
                st.text(min_size=1, max_size=20),
                children,
                max_size=5,
            ),
        ),
        max_leaves=30,
    )


_nested_dicts = st.dictionaries(
    st.text(min_size=1, max_size=20),
    _json_values(),
    min_size=1,
    max_size=10,
)


# ---------------------------------------------------------------------------
# Property 5: Serialization determinism
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.spdx
class TestProperty5SerializationDeterminism:
    """Property 5: Serialization determinism.

    For any valid SBOMDocument, serializing the document with any writer twice
    SHALL produce byte-identical output. The output SHALL use 2-space
    indentation, sorted keys, UTF-8 encoding without BOM, and a trailing
    newline character.
    """

    # --- Determinism: same input produces byte-identical output ---

    @given(data=_nested_dicts)
    def test_same_dict_produces_identical_output(self, data: dict) -> None:
        """Same dict input produces byte-identical output on repeated calls."""
        printer = SBOMPrinter()
        output1 = printer.print(data)
        output2 = printer.print(data)
        assert output1 == output2

    # --- 2-space indentation ---

    @given(data=_nested_dicts)
    def test_output_uses_two_space_indentation(self, data: dict) -> None:
        """Output uses 2-space indentation (check presence of '  ' in output)."""
        printer = SBOMPrinter()
        output = printer.print(data)
        text = output.decode("utf-8")
        # Any nested structure must have 2-space indentation
        # Verify by re-parsing and comparing with expected formatting
        expected = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        assert text == expected

    # --- Sorted keys ---

    @given(data=_nested_dicts)
    def test_keys_are_sorted(self, data: dict) -> None:
        """Keys are sorted in the output (verify ordering)."""
        printer = SBOMPrinter()
        output = printer.print(data)
        text = output.decode("utf-8")
        parsed = json.loads(text)

        def _check_sorted_keys(obj: object) -> None:
            if isinstance(obj, dict):
                keys = list(obj.keys())
                assert keys == sorted(keys), f"Keys not sorted: {keys}"
                for v in obj.values():
                    _check_sorted_keys(v)
            elif isinstance(obj, list):
                for item in obj:
                    _check_sorted_keys(item)

        _check_sorted_keys(parsed)

    # --- UTF-8 encoding without BOM ---

    @given(data=_nested_dicts)
    def test_utf8_encoding_without_bom(self, data: dict) -> None:
        """UTF-8 encoding without BOM (no 0xEF 0xBB 0xBF prefix)."""
        printer = SBOMPrinter()
        output = printer.print(data)
        # UTF-8 BOM bytes
        bom = b"\xef\xbb\xbf"
        assert not output.startswith(bom), "Output should not have UTF-8 BOM"
        # Verify it's valid UTF-8
        output.decode("utf-8")

    # --- Trailing newline ---

    @given(data=_nested_dicts)
    def test_trailing_newline(self, data: dict) -> None:
        """Trailing newline character at end of output."""
        printer = SBOMPrinter()
        output = printer.print(data)
        assert output.endswith(b"\n"), "Output must end with a newline"

    # --- Unicode preservation ---

    @given(data=_nested_dicts)
    def test_unicode_characters_preserved(self, data: dict) -> None:
        """Unicode characters are preserved correctly in output."""
        printer = SBOMPrinter()
        output = printer.print(data)
        text = output.decode("utf-8")
        parsed = json.loads(text)
        # Round-trip: the parsed dict should equal the original
        assert parsed == data

    # --- SBOMDocument → dict determinism ---

    @given(doc=sbom_documents())
    def test_sbom_document_serialization_determinism(self, doc) -> None:
        """SBOMDocument produces identical output on repeated serializations.

        Convert the SBOMDocument to a JSON-serializable dict and verify
        the printer produces identical bytes each time.
        """
        import dataclasses
        from enum import Enum

        def _make_serializable(obj: object) -> object:
            """Recursively convert dataclass/enum values to JSON-serializable form."""
            if isinstance(obj, Enum):
                return obj.value
            if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
                return {k: _make_serializable(v) for k, v in dataclasses.asdict(obj).items()}
            if isinstance(obj, dict):
                return {k: _make_serializable(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_make_serializable(item) for item in obj]
            return obj

        printer = SBOMPrinter()
        doc_dict = _make_serializable(doc)
        output1 = printer.print(doc_dict)
        output2 = printer.print(doc_dict)
        assert output1 == output2
        # Also verify format properties hold
        assert output1.endswith(b"\n")
        assert not output1.startswith(b"\xef\xbb\xbf")
        output1.decode("utf-8")


# ---------------------------------------------------------------------------
# Property 6: Writer result integrity
# ---------------------------------------------------------------------------


def _make_context() -> WorkflowContext:
    """Create a mock WorkflowContext with cancellation disabled."""
    token = CancellationToken()
    ctx = MagicMock(spec=WorkflowContext)
    ctx.cancellation_token = token
    return ctx


@pytest.mark.unit
@pytest.mark.spdx
class TestProperty6WriterResultIntegrity:
    """Property 6: Writer result integrity.

    For any valid SBOMDocument written by any writer, the WriterResult.sha256
    field SHALL equal the SHA-256 hash independently computed from the written
    file's bytes, and WriterResult.file_size SHALL equal the actual byte count
    of the written file.
    """

    # --- SPDX 2.3 Writer ---

    @settings(deadline=None)
    @given(doc=sbom_documents())
    def test_spdx23_writer_sha256_matches_file_content(self, doc) -> None:
        """SPDX23Writer result sha256 matches independently computed hash of written file."""
        from debcraft.infrastructure.sbom_writers.spdx23 import SPDX23Writer

        writer = SPDX23Writer()
        context = _make_context()

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "test.spdx.json"
            result = asyncio.run(writer.write(doc, output_path, context))

            # Independently read the written file and compute SHA-256
            file_bytes = output_path.read_bytes()
            independent_sha256 = hashlib.sha256(file_bytes).hexdigest()

            assert result.sha256 == independent_sha256

    @settings(deadline=None)
    @given(doc=sbom_documents())
    def test_spdx23_writer_file_size_matches_actual_bytes(self, doc) -> None:
        """SPDX23Writer result file_size matches actual byte count of written file."""
        from debcraft.infrastructure.sbom_writers.spdx23 import SPDX23Writer

        writer = SPDX23Writer()
        context = _make_context()

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "test.spdx.json"
            result = asyncio.run(writer.write(doc, output_path, context))

            # Independently check the actual file size
            file_bytes = output_path.read_bytes()
            assert result.file_size == len(file_bytes)

    # --- SPDX 3.0 Writer ---

    @settings(deadline=None)
    @given(doc=sbom_documents())
    def test_spdx3_writer_sha256_matches_file_content(self, doc) -> None:
        """SPDX3Writer result sha256 matches independently computed hash of written file."""
        from debcraft.infrastructure.sbom_writers.spdx3 import SPDX3Writer

        writer = SPDX3Writer()
        context = _make_context()

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "test.spdx3.json"
            result = asyncio.run(writer.write(doc, output_path, context))

            # Independently read the written file and compute SHA-256
            file_bytes = output_path.read_bytes()
            independent_sha256 = hashlib.sha256(file_bytes).hexdigest()

            assert result.sha256 == independent_sha256

    @settings(deadline=None)
    @given(doc=sbom_documents())
    def test_spdx3_writer_file_size_matches_actual_bytes(self, doc) -> None:
        """SPDX3Writer result file_size matches actual byte count of written file."""
        from debcraft.infrastructure.sbom_writers.spdx3 import SPDX3Writer

        writer = SPDX3Writer()
        context = _make_context()

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "test.spdx3.json"
            result = asyncio.run(writer.write(doc, output_path, context))

            # Independently check the actual file size
            file_bytes = output_path.read_bytes()
            assert result.file_size == len(file_bytes)

    # --- CycloneDX Writer ---

    @settings(deadline=None)
    @given(doc=sbom_documents())
    def test_cyclonedx_writer_sha256_matches_file_content(self, doc) -> None:
        """CycloneDXWriter result sha256 matches independently computed hash of written file."""
        from debcraft.infrastructure.sbom_writers.cyclonedx import CycloneDXWriter

        writer = CycloneDXWriter()
        context = _make_context()

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "test.cdx.json"
            result = asyncio.run(writer.write(doc, output_path, context))

            # Independently read the written file and compute SHA-256
            file_bytes = output_path.read_bytes()
            independent_sha256 = hashlib.sha256(file_bytes).hexdigest()

            assert result.sha256 == independent_sha256

    @settings(deadline=None)
    @given(doc=sbom_documents())
    def test_cyclonedx_writer_file_size_matches_actual_bytes(self, doc) -> None:
        """CycloneDXWriter result file_size matches actual byte count of written file."""
        from debcraft.infrastructure.sbom_writers.cyclonedx import CycloneDXWriter

        writer = CycloneDXWriter()
        context = _make_context()

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "test.cdx.json"
            result = asyncio.run(writer.write(doc, output_path, context))

            # Independently check the actual file size
            file_bytes = output_path.read_bytes()
            assert result.file_size == len(file_bytes)
