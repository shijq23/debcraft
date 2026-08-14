"""Bug condition exploration tests for indexer .deb file errors.

**Validates: Requirements 1.1, 1.2, 1.3, 1.4**

Property 1: Bug Condition — Unknown File Types Processed and Tracebacks Swallowed

This module contains exploration tests that demonstrate two related bugs:

Bug A: IndexerService calls file_reader.read_file() for files with type "unknown"
(including .deb files) BEFORE checking the file type, causing UTF-8 decode errors
on binary content.

Bug B: _StructuredFormatter never calls self.formatException() when record.exc_info
is set, so exception tracebacks are silently discarded from log output.

These tests encode the EXPECTED behavior. On unfixed code they FAIL, confirming
both bugs exist. After the fix is applied, they PASS.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from debcraft.cli import _StructuredFormatter
from debcraft.domain.indexer.service import IndexerService

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeFileInfo:
    """Minimal implementation of FileInfo protocol for testing."""

    id: int
    url: str
    sha256: str
    local_path: str
    size_bytes: int = 1024


@pytest.mark.unit
class TestBugAIndexerProcessesUnknownFiles:
    """Bug A: Indexer calls read_file() for files with unknown type.

    On unfixed code, read_file() IS called before the type dispatch handles
    "unknown", so these tests FAIL (proving the bug exists).

    **Validates: Requirements 1.1, 1.2**
    """

    def test_deb_file_does_not_trigger_read_file(self) -> None:
        """A .deb URL should be skipped without calling read_file().

        On UNFIXED code: test FAILS because read_file() IS called before
        the type dispatch handles the "unknown" case.
        """
        # Arrange: create a file info with a .deb URL
        deb_file = FakeFileInfo(
            id=1,
            url="http://repo/pool/main/h/hello/hello_2.10-3_amd64.deb",
            sha256="abc123def456",
            local_path="/tmp/cache/hello_2.10-3_amd64.deb",
        )

        # Create mock dependencies
        file_reader = AsyncMock()
        file_reader.read_file = AsyncMock(return_value="dummy content")

        metadata_repo = AsyncMock()
        metadata_repo.find_or_create_repository = AsyncMock(return_value=1)
        metadata_repo.create_snapshot = AsyncMock(return_value=100)
        metadata_repo.publish_snapshot = AsyncMock()

        mirror_file_repo = AsyncMock()
        mirror_file_repo.get_verified_files = AsyncMock(return_value=[deb_file])
        mirror_file_repo.get_indexing_record = AsyncMock(return_value=None)

        event_bus = AsyncMock()
        event_bus.publish = AsyncMock()

        # Act: run the indexer
        service = IndexerService(
            file_reader=file_reader,
            metadata_repository=metadata_repo,
            mirror_file_repository=mirror_file_repo,
            event_bus=event_bus,
        )

        asyncio.run(
            service.index_repository(
                repository_name="test-repo",
                base_url="http://repo",
                suite="bookworm",
                component="main",
            )
        )

        # Assert: read_file should NOT have been called for the .deb file
        (
            file_reader.read_file.assert_not_called(),
            (
                "read_file() was called for a .deb file with type 'unknown'. "
                "The indexer should skip unknown file types BEFORE attempting to read them."
            ),
        )


@pytest.mark.unit
class TestBugBFormatterSwallowsTracebacks:
    """Bug B: _StructuredFormatter discards exception tracebacks.

    On unfixed code, format() never calls self.formatException(), so log
    records with exc_info lose their traceback. These tests FAIL on unfixed
    code (proving the bug exists).

    **Validates: Requirements 1.3, 1.4**
    """

    def test_format_includes_traceback_when_exc_info_set(self) -> None:
        """Formatted output should contain traceback text when exc_info is set.

        On UNFIXED code: test FAILS because format() never calls
        self.formatException() — the traceback is silently discarded.
        """
        # Arrange: create a log record with exc_info
        formatter = _StructuredFormatter()

        # Generate a real exception tuple
        try:
            raise ValueError("test error for exploration")
        except ValueError:
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="debcraft.domain.indexer.service",
            level=logging.ERROR,
            pathname="service.py",
            lineno=42,
            msg="Error processing file: %s",
            args=("http://repo/pool/main/h/hello/hello_2.10-3_amd64.deb",),
            exc_info=exc_info,
        )

        # Act: format the record
        output = formatter.format(record)

        # Assert: the formatted output should contain the traceback
        assert "Traceback" in output, (
            f"Formatted output does not contain 'Traceback'. "
            f"The _StructuredFormatter.format() never calls self.formatException(). "
            f"Got: {output!r}"
        )
        assert "ValueError: test error for exploration" in output, (
            f"Formatted output does not contain the exception message. "
            f"The _StructuredFormatter.format() discards exc_info entirely. "
            f"Got: {output!r}"
        )
