"""Property-based tests for write_with_cancellation utility.

# Feature: pylint-refactoring, Property 4: Write Utility Hash and Size Correctness

For any non-empty byte sequence and any output path (on a writable filesystem)
with cancellation not triggered, the `write_with_cancellation` utility SHALL
return a WriterResult where `sha256` equals `hashlib.sha256(output_bytes).hexdigest()`
and `file_size` equals `len(output_bytes)`.

# Feature: pylint-refactoring, Property 5: Write Utility Pre-Cancellation Safety

For any byte sequence and any output path, if the cancellation token is already
cancelled before the utility is called, then write_with_cancellation SHALL raise
WriterCancellationError and no file SHALL exist at the output path after the call.
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.domain.sbom.errors import WriterCancellationError
from debcraft.domain.sbom.values import OutputFormat
from debcraft.infrastructure.sbom_writers._write_utils import write_with_cancellation
from debcraft.platform.contracts.workflow import CancellationToken


@pytest.mark.property
@pytest.mark.unit
class TestProperty4WriteUtilityHashAndSizeCorrectness:
    """Property 4: Write Utility Hash and Size Correctness.

    For any non-empty byte sequence and any output path (on a writable
    filesystem) with cancellation not triggered, write_with_cancellation
    SHALL return a WriterResult where sha256 equals
    hashlib.sha256(output_bytes).hexdigest() and file_size equals
    len(output_bytes).
    """

    @given(data=st.binary(min_size=1))
    def test_hash_and_size_match_expected(self, data: bytes, tmp_path_factory: pytest.TempPathFactory) -> None:
        """**Validates: Requirements 7.1, 7.3**.

        The returned WriterResult sha256 and file_size match the expected
        values computed directly from the input bytes.
        """
        tmp_path = tmp_path_factory.mktemp("write_utils")
        output_path = tmp_path / "output.sbom"

        token = CancellationToken()

        result = asyncio.run(
            write_with_cancellation(
                output_bytes=data,
                output_path=output_path,
                cancellation_token=token,
                output_format=OutputFormat.CYCLONEDX,
                diagnostics=[],
            )
        )

        expected_sha256 = hashlib.sha256(data).hexdigest()
        expected_size = len(data)

        assert result.sha256 == expected_sha256, f"SHA-256 mismatch: got {result.sha256}, expected {expected_sha256}"
        assert result.file_size == expected_size, (
            f"File size mismatch: got {result.file_size}, expected {expected_size}"
        )


@pytest.mark.property
@pytest.mark.unit
class TestProperty5WriteUtilityPreCancellationSafety:
    """Property 5: Write Utility Pre-Cancellation Safety.

    For any byte sequence and any output path, if the cancellation token is
    already cancelled before the utility is called, then write_with_cancellation
    SHALL raise WriterCancellationError and no file SHALL exist at the output
    path after the call.
    """

    @given(
        data=st.binary(min_size=1),
    )
    def test_pre_cancelled_token_raises_and_leaves_no_file(self, data: bytes) -> None:
        """**Validates: Requirements 7.4**.

        When the cancellation token is already cancelled before calling
        write_with_cancellation, WriterCancellationError is raised and
        no file exists at the output path.
        """
        token = CancellationToken()
        token.cancel()

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "output.json"

            with pytest.raises(WriterCancellationError):
                asyncio.run(
                    write_with_cancellation(
                        output_bytes=data,
                        output_path=output_path,
                        cancellation_token=token,
                        output_format=OutputFormat.CYCLONEDX,
                        diagnostics=[],
                    )
                )

            assert not output_path.exists(), (
                f"File should not exist at {output_path} after pre-cancellation, but it does"
            )
