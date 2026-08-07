"""Preservation property tests for model-registry-and-download-errors bugfix.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

Property 3: Preservation — Model Import Compatibility and Download Behavior

These tests verify EXISTING behavior that must be preserved after the fix.
They MUST PASS on the current (unfixed) code.

1. Model imports work correctly via direct module import
2. Bidirectional relationships resolve when both modules are imported
3. Successful downloads produce correct DownloadResult
4. 4xx errors fail immediately without retry
5. Retry-then-success produces successful DownloadResult
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

# Import scan models to ensure relationships resolve (existing workaround)
import debcraft.infrastructure.models.scan  # noqa: F401
from debcraft.domain.mirror.config import MirrorConfig, RepositoryConfig
from debcraft.infrastructure.mirror.download import DownloadCoordinator
from debcraft.infrastructure.mirror.errors import HttpClientError
from debcraft.infrastructure.models.base import Base

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# File content for download tests (limit size for speed)
_content_strategy = st.binary(min_size=1, max_size=2048)

# HTTP 4xx status codes
_http_4xx_strategy = st.integers(min_value=400, max_value=499)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeStorageEngine:
    """Minimal fake StorageEngine for testing DownloadCoordinator."""

    def get_path(self, name: str) -> Path:
        return Path("/tmp/test-mirror")


def _make_test_config() -> MirrorConfig:
    """Create a minimal MirrorConfig for testing."""
    return MirrorConfig(
        repositories=[
            RepositoryConfig(
                name="test",
                base_url="http://localhost",
                suites=["test"],
                components=["main"],
                architectures=["amd64"],
            )
        ],
        download_timeout=30,
        max_connections_per_repo=5,
        max_total_connections=10,
    )


def _make_response_handler(body: bytes):
    """Create an async handler that returns a Response with the given body."""

    async def handler(request: web.Request) -> web.Response:
        return web.Response(body=body)

    return handler


async def _noop_sleep(delay: float) -> None:
    """No-op replacement for asyncio.sleep to speed up retry tests."""


# ---------------------------------------------------------------------------
# Test 1: Model Import Preservation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestModelImportPreservation:
    """Verify that direct model imports work.

    **Validates: Requirements 3.1**

    Individual model classes must remain importable via their module paths.
    """

    def test_repository_snapshot_importable(self) -> None:
        """RepositorySnapshot is importable from metadata module.

        **Validates: Requirements 3.1**
        """
        from debcraft.infrastructure.models.metadata import RepositorySnapshot

        assert RepositorySnapshot is not None
        # Verify it's a SQLAlchemy mapped class with a __tablename__
        assert hasattr(RepositorySnapshot, "__tablename__")
        assert RepositorySnapshot.__tablename__ == "repository_snapshots"

    def test_scan_session_importable(self) -> None:
        """ScanSession is importable from scan module.

        **Validates: Requirements 3.1**
        """
        from debcraft.infrastructure.models.scan import ScanSession

        assert ScanSession is not None
        # Verify it's a SQLAlchemy mapped class with a __tablename__
        assert hasattr(ScanSession, "__tablename__")
        assert ScanSession.__tablename__ == "scan_sessions"

    def test_both_classes_are_sqlalchemy_mapped(self) -> None:
        """Both RepositorySnapshot and ScanSession are SQLAlchemy mapped classes.

        **Validates: Requirements 3.1**
        """
        from debcraft.infrastructure.models.metadata import RepositorySnapshot
        from debcraft.infrastructure.models.scan import ScanSession

        # Both should have mapper-related attributes
        assert hasattr(RepositorySnapshot, "__mapper__")
        assert hasattr(ScanSession, "__mapper__")


# ---------------------------------------------------------------------------
# Test 2: Bidirectional Relationship Preservation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestBidirectionalRelationshipPreservation:
    """Verify that relationships between models resolve correctly.

    **Validates: Requirements 3.2**

    When both modules are imported (using the existing workaround),
    the relationship between RepositorySnapshot and ScanSession resolves.
    """

    async def test_relationship_resolves_with_both_modules_imported(self) -> None:
        """Relationships resolve when both model modules are loaded.

        **Validates: Requirements 3.2**
        """
        # Both modules imported at top of file via workaround
        from sqlalchemy.orm import selectinload

        from debcraft.infrastructure.models.metadata import (
            Repository,
            RepositorySnapshot,
        )
        from debcraft.infrastructure.models.scan import ScanSession

        # Create in-memory SQLite engine and tables
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            factory = async_sessionmaker(engine, expire_on_commit=False)

            # Verify relationship attributes exist on the mapper
            assert hasattr(RepositorySnapshot, "scan_sessions")

            # Verify reverse relationship
            assert hasattr(ScanSession, "snapshot")

            # Create a repository and snapshot to verify queryability
            async with factory() as session:
                repo = Repository(
                    name="test-repo",
                    base_url="https://example.com",
                    suite="bookworm",
                    component="main",
                )
                session.add(repo)
                await session.flush()

                from datetime import UTC, datetime

                snapshot = RepositorySnapshot(
                    repository_id=repo.id,
                    schema_version=1,
                    captured_at=datetime.now(UTC),
                    published=False,
                )
                session.add(snapshot)
                await session.commit()

                # Query the snapshot with eager loading for async access
                result = await session.execute(
                    select(RepositorySnapshot)
                    .where(RepositorySnapshot.id == snapshot.id)
                    .options(selectinload(RepositorySnapshot.scan_sessions))
                )
                loaded = result.scalar_one()
                # Access the relationship - should not raise
                assert loaded.scan_sessions == []
        finally:
            await engine.dispose()


# ---------------------------------------------------------------------------
# Test 3: Download Success Preservation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestDownloadSuccessPreservation:
    """Verify successful downloads produce correct DownloadResult.

    **Validates: Requirements 3.3**

    Successful downloads on first attempt must return DownloadResult with
    success=True, sha256_verified=True, correct bytes_transferred,
    error=None, and retry_count=0.
    """

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(data=_content_strategy)
    async def test_successful_download_returns_correct_result(self, data: bytes) -> None:
        """Successful download produces correct DownloadResult fields.

        **Validates: Requirements 3.3**
        """
        expected_sha256 = hashlib.sha256(data).hexdigest()
        expected_size = len(data)

        app = web.Application()
        app.router.add_get("/file", _make_response_handler(data))

        tmp_dir = Path(tempfile.mkdtemp())
        try:
            async with TestServer(app) as server:
                url = str(server.make_url("/file"))
                dest_path = tmp_dir / "downloaded_file"

                config = _make_test_config()
                coordinator = DownloadCoordinator(
                    storage_engine=_FakeStorageEngine(),
                    config=config,
                )
                await coordinator.start()
                try:
                    result = await coordinator.download_file(
                        url=url,
                        dest_path=dest_path,
                        expected_sha256=expected_sha256,
                        expected_size=expected_size,
                    )

                    # Verify all DownloadResult fields
                    assert result.success is True
                    assert result.sha256_verified is True
                    assert result.bytes_transferred == expected_size
                    assert result.error is None
                    assert result.retry_count == 0
                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 4: 4xx Immediate Fail Preservation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestFourXXImmediateFailPreservation:
    """Verify 4xx errors fail immediately without retry.

    **Validates: Requirements 3.4**

    4xx client errors must immediately raise HttpClientError without retrying.
    Only 1 attempt should be made.
    """

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(status_code=_http_4xx_strategy)
    async def test_4xx_raises_immediately_without_retry(self, status_code: int) -> None:
        """4xx errors raise HttpClientError with no retries.

        **Validates: Requirements 3.4**
        """
        call_count = {"value": 0}

        async def handler(request: web.Request) -> web.Response:
            call_count["value"] += 1
            return web.Response(status=status_code, body=b"client error")

        app = web.Application()
        app.router.add_get("/file", handler)

        tmp_dir = Path(tempfile.mkdtemp())
        try:
            async with TestServer(app) as server:
                url = str(server.make_url("/file"))
                dest_path = tmp_dir / "file"

                config = _make_test_config()
                coordinator = DownloadCoordinator(
                    storage_engine=_FakeStorageEngine(),
                    config=config,
                )
                await coordinator.start()
                try:
                    with pytest.raises(HttpClientError) as exc_info:
                        await coordinator.download_file(
                            url=url,
                            dest_path=dest_path,
                            expected_sha256="a" * 64,
                            expected_size=100,
                        )

                    # Only 1 attempt was made — no retries
                    assert call_count["value"] == 1
                    # The error carries the status code
                    assert exc_info.value.status_code == status_code
                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 5: Retry-then-success Preservation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
@pytest.mark.asyncio
class TestRetryThenSuccessPreservation:
    """Verify downloads that fail then succeed produce successful DownloadResult.

    **Validates: Requirements 3.5**

    Downloads that fail on first attempt (503) but succeed on retry should
    return a successful DownloadResult with no ERROR log.
    """

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(data=_content_strategy)
    async def test_retry_then_success_returns_successful_result(self, data: bytes) -> None:
        """Download that fails on first attempt but succeeds on retry.

        **Validates: Requirements 3.5**
        """
        expected_sha256 = hashlib.sha256(data).hexdigest()
        expected_size = len(data)
        call_count = {"value": 0}

        async def handler(request: web.Request) -> web.Response:
            call_count["value"] += 1
            if call_count["value"] == 1:
                # First attempt: 503 Service Unavailable
                return web.Response(status=503, body=b"server error")
            # Second attempt: success
            return web.Response(body=data)

        app = web.Application()
        app.router.add_get("/file", handler)

        tmp_dir = Path(tempfile.mkdtemp())
        try:
            async with TestServer(app) as server:
                url = str(server.make_url("/file"))
                dest_path = tmp_dir / "downloaded_file"

                config = _make_test_config()
                coordinator = DownloadCoordinator(
                    storage_engine=_FakeStorageEngine(),
                    config=config,
                )
                await coordinator.start()
                try:
                    # Capture ERROR logs to verify none are emitted
                    with patch(
                        "debcraft.infrastructure.mirror.download.asyncio.sleep",
                        _noop_sleep,
                    ):
                        logger = logging.getLogger("debcraft.infrastructure.mirror.download")
                        with _capture_log_records(logger) as records:
                            result = await coordinator.download_file(
                                url=url,
                                dest_path=dest_path,
                                expected_sha256=expected_sha256,
                                expected_size=expected_size,
                            )

                    # Verify successful result
                    assert result.success is True
                    assert result.sha256_verified is True
                    assert result.bytes_transferred == expected_size
                    assert result.error is None

                    # Verify NO ERROR log was emitted (only WARNING for retry)
                    error_records = [r for r in records if r.levelno >= logging.ERROR]
                    assert len(error_records) == 0, (
                        f"Expected no ERROR logs, got: {[r.getMessage() for r in error_records]}"
                    )
                finally:
                    await coordinator.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Log capture helper
# ---------------------------------------------------------------------------


@contextmanager
def _capture_log_records(logger: logging.Logger):
    """Context manager that captures log records from a logger."""

    class _RecordCapture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records: list[logging.LogRecord] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append(record)

    handler = _RecordCapture()
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    original_level = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(original_level)
