"""Preservation property tests for indexer .deb file errors bugfix.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**

Property 2: Preservation — Known File Types Indexed and Non-Exception Formatting Unchanged

These tests capture the baseline behavior on UNFIXED code that must be preserved
after the fix is applied:
- Preservation A: Known file types (packages, sources, contents, release) are still
  read and processed correctly when encountered in the indexer pipeline.
- Preservation B: Non-exception log formatting produces the same
  `LEVELNAME logger.name: message key=value` output for records without exc_info.

All tests in this file MUST PASS on the current unfixed code.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.cli import _StructuredFormatter
from debcraft.domain.indexer.service import IndexerService, _infer_file_type

# ---------------------------------------------------------------------------
# Helpers for Preservation A
# ---------------------------------------------------------------------------


@dataclass
class FakeFileInfo:
    """Minimal FileInfo implementation for testing."""

    id: int
    url: str
    sha256: str
    local_path: str
    size_bytes: int = 1024


def _make_indexer_service_with_mocks() -> tuple[IndexerService, AsyncMock]:
    """Create an IndexerService with mock dependencies that record calls.

    Returns the service and the mock file_reader so we can assert on read_file calls.
    """
    file_reader = AsyncMock()
    file_reader.read_file = AsyncMock(return_value="Package: test\nVersion: 1.0\n\n")

    metadata_repository = AsyncMock()
    metadata_repository.find_or_create_repository = AsyncMock(return_value=1)
    metadata_repository.create_snapshot = AsyncMock(return_value=1)
    metadata_repository.publish_snapshot = AsyncMock()
    metadata_repository.add_package_instances = AsyncMock(return_value=1)
    metadata_repository.add_source_packages = AsyncMock(return_value=1)
    metadata_repository.replace_file_ownerships = AsyncMock(return_value=1)

    mirror_file_repository = AsyncMock()
    mirror_file_repository.get_indexing_record = AsyncMock(return_value=None)
    mirror_file_repository.mark_indexed = AsyncMock()

    event_bus = AsyncMock()
    event_bus.publish = AsyncMock()

    service = IndexerService(
        file_reader=file_reader,
        metadata_repository=metadata_repository,
        mirror_file_repository=mirror_file_repository,
        event_bus=event_bus,
    )

    return service, file_reader


# Strategies for generating known file type URLs
_KNOWN_FILE_TYPE_KEYWORDS = ["packages", "sources", "contents", "release"]

_url_prefix_strategy = st.sampled_from(
    [
        "http://deb.debian.org/debian/dists/bookworm/main/binary-amd64/",
        "https://mirror.example.com/ubuntu/dists/jammy/",
        "http://archive.ubuntu.com/ubuntu/dists/noble/main/",
        "https://repo.example.org/dists/stable/contrib/",
    ]
)

_url_suffix_strategy = st.sampled_from(
    [
        "",
        ".gz",
        ".xz",
        ".bz2",
        "-amd64",
        "-i386",
        "-arm64",
    ]
)


@st.composite
def known_file_type_url(draw: st.DrawFn) -> tuple[str, str]:
    """Generate a URL that _infer_file_type classifies as a known type.

    Returns (url, expected_type) tuple.
    """
    prefix = draw(_url_prefix_strategy)
    keyword = draw(st.sampled_from(_KNOWN_FILE_TYPE_KEYWORDS))
    suffix = draw(_url_suffix_strategy)

    # Capitalize in various ways to test case-insensitivity
    case_variant = draw(
        st.sampled_from(
            [
                keyword,
                keyword.capitalize(),
                keyword.upper(),
            ]
        )
    )

    url = f"{prefix}{case_variant}{suffix}"

    # Determine expected type based on what _infer_file_type will actually return
    expected_type = _infer_file_type(url)
    return url, expected_type


# ---------------------------------------------------------------------------
# Strategies for Preservation B
# ---------------------------------------------------------------------------

_LOG_LEVELS = [logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL]

_logger_name_strategy = st.sampled_from(
    [
        "debcraft.domain.indexer.service",
        "debcraft.cli",
        "debcraft.infrastructure.download",
        "root",
        "test.module",
    ]
)

_message_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z"), whitelist_characters=" .-_:/"),
    min_size=1,
    max_size=80,
).filter(lambda s: s.strip())

_extra_key_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("Ll",)),
    min_size=1,
    max_size=15,
).filter(lambda s: s.isidentifier())

_extra_value_strategy = st.one_of(
    st.integers(min_value=0, max_value=10000),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_.:/"),
        min_size=1,
        max_size=30,
    ).filter(lambda s: s.strip()),
)


# ---------------------------------------------------------------------------
# Preservation A Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPreservationKnownFileTypesProcessed:
    """Preservation A: Known file types are still read and processed correctly.

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4**

    For all URLs where _infer_file_type returns a known type ("packages",
    "sources", "contents", "release"), file_reader.read_file() IS called
    and the appropriate parser is invoked.
    """

    @given(data=st.data())
    def test_known_file_types_trigger_read_file(self, data: st.DataObject) -> None:
        """For any URL with a known file type, read_file is called.

        **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
        """
        url, expected_type = data.draw(known_file_type_url())

        # Only test URLs that actually map to a known type
        from hypothesis import assume

        assume(expected_type in ("packages", "sources", "contents", "release"))

        service, file_reader = _make_indexer_service_with_mocks()

        file_info = FakeFileInfo(
            id=1,
            url=url,
            sha256="a" * 64,
            local_path="/tmp/test_file",
        )

        # Set up mirror to return this file
        service._mirror_file_repository.get_verified_files = AsyncMock(return_value=[file_info])

        async def _run() -> None:
            await service.index_repository(
                repository_name="test-repo",
                base_url="http://example.com",
                suite="bookworm",
                component="main",
            )

        asyncio.run(_run())

        # The key assertion: read_file WAS called for known file types
        file_reader.read_file.assert_called_once_with(file_info.local_path)

    @given(keyword=st.sampled_from(["packages", "sources", "contents", "release"]))
    def test_each_known_type_is_correctly_classified(self, keyword: str) -> None:
        """_infer_file_type correctly classifies known file type keywords.

        **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
        """
        # Test various URL patterns containing the keyword
        urls = [
            f"http://repo/dists/bookworm/main/binary-amd64/{keyword.capitalize()}.gz",
            f"http://repo/dists/stable/{keyword}",
            f"https://mirror.example.com/{keyword}.xz",
        ]

        for url in urls:
            result = _infer_file_type(url)
            assert result == keyword, f"_infer_file_type({url!r}) returned {result!r}, expected {keyword!r}"

    @given(keyword=st.sampled_from(["packages", "sources", "contents", "release"]))
    def test_known_type_files_are_marked_indexed(self, keyword: str) -> None:
        """After processing a known file type, mark_indexed is called.

        **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
        """
        url = f"http://repo/dists/bookworm/main/binary-amd64/{keyword.capitalize()}.gz"
        service, file_reader = _make_indexer_service_with_mocks()

        # Provide content appropriate for each parser
        if keyword == "release":
            file_reader.read_file = AsyncMock(
                return_value="Suite: bookworm\nCodename: bookworm\nDate: Thu, 01 Jan 2024 00:00:00 UTC\n"
            )
        elif keyword == "packages":
            file_reader.read_file = AsyncMock(
                return_value=(
                    "Package: test\n"
                    "Version: 1.0\n"
                    "Architecture: amd64\n"
                    "Filename: pool/t/test/test_1.0_amd64.deb\n"
                    "SHA256: abcd1234\n"
                    "Size: 1024\n\n"
                )
            )
        # sources and contents parsers handle arbitrary content gracefully

        file_info = FakeFileInfo(
            id=42,
            url=url,
            sha256="b" * 64,
            local_path="/tmp/test_file",
        )

        service._mirror_file_repository.get_verified_files = AsyncMock(return_value=[file_info])

        async def _run() -> None:
            await service.index_repository(
                repository_name="test-repo",
                base_url="http://example.com",
                suite="bookworm",
                component="main",
            )

        asyncio.run(_run())

        # mark_indexed should have been called for the known file type
        service._mirror_file_repository.mark_indexed.assert_called_once()


# ---------------------------------------------------------------------------
# Preservation B Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPreservationNonExceptionFormatting:
    """Preservation B: Non-exception log formatting is unchanged.

    **Validates: Requirements 3.5, 3.6**

    For all log records where exc_info is None, the formatter produces the
    same LEVELNAME name: message base format with optional key=value pairs.
    """

    @given(
        level=st.sampled_from(_LOG_LEVELS),
        name=_logger_name_strategy,
        message=_message_strategy,
    )
    def test_base_format_without_extras(self, level: int, name: str, message: str) -> None:
        """Log records without extras produce 'LEVELNAME name: message' format.

        **Validates: Requirements 3.5**
        """
        formatter = _StructuredFormatter()
        record = logging.LogRecord(
            name=name,
            level=level,
            pathname="",
            lineno=0,
            msg=message,
            args=(),
            exc_info=None,
        )

        result = formatter.format(record)

        expected_level = logging.getLevelName(level)
        expected = f"{expected_level} {name}: {message}"
        assert result == expected, f"Expected {expected!r}, got {result!r}"

    @given(
        level=st.sampled_from(_LOG_LEVELS),
        name=_logger_name_strategy,
        message=_message_strategy,
        extra_data=st.dictionaries(
            keys=_extra_key_strategy,
            values=_extra_value_strategy,
            min_size=1,
            max_size=4,
        ),
    )
    def test_extra_data_dict_appended_as_key_value_pairs(
        self, level: int, name: str, message: str, extra_data: dict[str, Any]
    ) -> None:
        """Log records with extra_data dict append key=value pairs.

        **Validates: Requirements 3.6**
        """
        formatter = _StructuredFormatter()
        record = logging.LogRecord(
            name=name,
            level=level,
            pathname="",
            lineno=0,
            msg=message,
            args=(),
            exc_info=None,
        )
        # Set extra_data as the _CliLogger does
        record.extra_data = extra_data  # type: ignore[attr-defined]

        result = formatter.format(record)

        expected_level = logging.getLevelName(level)
        # Verify base format is present
        assert result.startswith(f"{expected_level} {name}: {message}"), (
            f"Result should start with base format. Got: {result!r}"
        )

        # Verify each key=value pair is present
        for key, value in extra_data.items():
            if value is not None:
                assert f"{key}={value}" in result, f"Expected {key}={value} in formatted output. Got: {result!r}"

    @given(
        level=st.sampled_from(_LOG_LEVELS),
        name=_logger_name_strategy,
        message=_message_strategy,
        extras=st.dictionaries(
            keys=_extra_key_strategy,
            values=_extra_value_strategy,
            min_size=1,
            max_size=3,
        ),
    )
    def test_direct_extra_fields_appended_as_key_value_pairs(
        self, level: int, name: str, message: str, extras: dict[str, Any]
    ) -> None:
        """Log records with direct extra={} fields append key=value pairs.

        **Validates: Requirements 3.6**
        """
        formatter = _StructuredFormatter()
        record = logging.LogRecord(
            name=name,
            level=level,
            pathname="",
            lineno=0,
            msg=message,
            args=(),
            exc_info=None,
        )
        # Set extras directly on the record (as download.py does via extra={})
        for key, value in extras.items():
            setattr(record, key, value)

        result = formatter.format(record)

        expected_level = logging.getLevelName(level)
        # Verify base format is present
        assert result.startswith(f"{expected_level} {name}: {message}"), (
            f"Result should start with base format. Got: {result!r}"
        )

        # Verify each key=value pair is present
        for key, value in extras.items():
            if value is not None:
                assert f"{key}={value}" in result, f"Expected {key}={value} in formatted output. Got: {result!r}"

    @given(
        level=st.sampled_from(_LOG_LEVELS),
        name=_logger_name_strategy,
        message=_message_strategy,
    )
    def test_no_traceback_in_output_without_exc_info(self, level: int, name: str, message: str) -> None:
        """Log records without exc_info do NOT contain 'Traceback' in output.

        **Validates: Requirements 3.5**
        """
        formatter = _StructuredFormatter()
        record = logging.LogRecord(
            name=name,
            level=level,
            pathname="",
            lineno=0,
            msg=message,
            args=(),
            exc_info=None,
        )

        result = formatter.format(record)

        assert "Traceback" not in result, f"Non-exception record should not contain 'Traceback'. Got: {result!r}"

    @given(
        level=st.sampled_from(_LOG_LEVELS),
        name=_logger_name_strategy,
        message=_message_strategy,
    )
    def test_format_output_is_single_line_without_exc_info(self, level: int, name: str, message: str) -> None:
        """Non-exception log records produce single-line output.

        **Validates: Requirements 3.5**
        """
        formatter = _StructuredFormatter()
        record = logging.LogRecord(
            name=name,
            level=level,
            pathname="",
            lineno=0,
            msg=message,
            args=(),
            exc_info=None,
        )

        result = formatter.format(record)

        assert "\n" not in result, f"Non-exception formatted output should be single line. Got: {result!r}"
