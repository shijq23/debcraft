"""Unit tests for mirror CLI commands."""

from datetime import UTC
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from debcraft.cli import app
from debcraft.domain.mirror.config import MirrorConfig, RepositoryConfig
from debcraft.infrastructure.mirror.errors import MirrorConfigurationError

runner = CliRunner()


# ─── Helpers ──────────────────────────────────────────────────────────────────

_EMPTY_CONFIG = MirrorConfig(repositories=[])

_VALID_CONFIG = MirrorConfig(
    repositories=[
        RepositoryConfig(
            name="test-repo",
            base_url="https://example.com/repo",
            suites=["stable"],
            components=["main"],
            architectures=["amd64"],
        ),
    ],
)


@pytest.mark.unit
def test_mirror_list_displays_default_repositories():
    """The list command displays the default eLxr repository when no config exists."""
    result = runner.invoke(app, ["mirror", "list"])
    assert result.exit_code == 0
    assert "elxr" in result.output
    assert "https://mirror.elxr.dev/elxr" in result.output
    assert "aria" in result.output
    assert "main" in result.output
    assert "amd64" in result.output
    assert "arm64" in result.output


@pytest.mark.unit
def test_mirror_list_displays_table_columns():
    """The list command displays a table with expected column headers."""
    result = runner.invoke(app, ["mirror", "list"])
    assert result.exit_code == 0
    assert "Name" in result.output
    assert "Base URL" in result.output
    assert "Suites" in result.output
    assert "Components" in result.output
    # Rich may truncate column headers in narrow terminals (e.g. "Architectu…")
    assert "Architectu" in result.output


@pytest.mark.unit
def test_mirror_list_displays_multiple_repositories():
    """The list command displays all configured repositories."""
    multi_config = MirrorConfig(
        repositories=[
            RepositoryConfig(
                name="repo-one",
                base_url="https://example.com/repo1",
                suites=["stable", "testing"],
                components=["main", "contrib"],
                architectures=["amd64"],
            ),
            RepositoryConfig(
                name="repo-two",
                base_url="https://example.com/repo2",
                suites=["bookworm"],
                components=["main"],
                architectures=["arm64", "riscv64"],
            ),
        ],
    )

    with patch("debcraft.cli.mirror._read_config", return_value=multi_config):
        result = runner.invoke(app, ["mirror", "list"])

    assert result.exit_code == 0
    assert "repo-one" in result.output
    assert "repo-two" in result.output
    # Rich may truncate long URLs in narrow terminals, check prefix
    assert "https://example.com/" in result.output
    assert "/repo1" in result.output
    assert "/repo2" in result.output
    assert "stable" in result.output
    assert "testing" in result.output
    assert "main" in result.output
    assert "contrib" in result.output
    assert "bookworm" in result.output
    assert "arm64" in result.output
    assert "riscv64" in result.output


@pytest.mark.unit
def test_mirror_list_exits_with_code_zero():
    """The list command exits with code 0 on success."""
    result = runner.invoke(app, ["mirror", "list"])
    assert result.exit_code == 0


@pytest.mark.unit
def test_mirror_verify_no_database(tmp_path, monkeypatch):
    """Verify command reports error when mirror.db does not exist."""
    # Point XDG_DATA_HOME to a temp dir with no mirror.db
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    result = runner.invoke(app, ["mirror", "verify"])
    assert result.exit_code == 1
    assert "Mirror database not found" in result.output


@pytest.mark.unit
def test_mirror_verify_no_verified_files(tmp_path, monkeypatch):
    """Verify command exits 0 when database exists but has no verified files."""
    from sqlalchemy import create_engine

    from debcraft.infrastructure.models.base import Base

    # Set up XDG paths so resolve_xdg_path("database") -> tmp_path
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    db_dir = tmp_path / "debcraft"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "mirror.db"

    # Create the database with empty repository_files table
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    engine.dispose()

    result = runner.invoke(app, ["mirror", "verify"])
    assert result.exit_code == 0
    assert "No verified files" in result.output


@pytest.mark.unit
def test_mirror_verify_all_pass(tmp_path, monkeypatch):
    """Verify command exits 0 and shows PASS when all files match their checksums."""
    import hashlib

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from debcraft.infrastructure.models.base import Base
    from debcraft.infrastructure.models.mirror import (
        RepositoryFile,
        RepositoryFileState,
    )

    # Set up XDG paths
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    db_dir = tmp_path / "debcraft"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "mirror.db"

    # Create a test file with known content
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    test_file = cache_dir / "test_pkg.deb"
    test_content = b"hello world package content"
    test_file.write_bytes(test_content)
    expected_sha = hashlib.sha256(test_content).hexdigest()

    # Create the database and insert a VERIFIED entry
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        entry = RepositoryFile(
            url="https://example.com/pool/main/t/test/test_pkg.deb",
            sha256=expected_sha,
            size_bytes=len(test_content),
            state=RepositoryFileState.VERIFIED,
            retry_count=0,
            local_path=str(test_file),
        )
        session.add(entry)
        session.commit()

    engine.dispose()

    result = runner.invoke(app, ["mirror", "verify"])
    assert result.exit_code == 0
    assert "PASS" in result.output
    assert "Files checked:" in result.output


@pytest.mark.unit
def test_mirror_verify_mismatch_detected(tmp_path, monkeypatch):
    """Verify command exits 1 and shows FAIL when a checksum mismatch is found."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from debcraft.infrastructure.models.base import Base
    from debcraft.infrastructure.models.mirror import (
        RepositoryFile,
        RepositoryFileState,
    )

    # Set up XDG paths
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    db_dir = tmp_path / "debcraft"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "mirror.db"

    # Create a test file that will NOT match the stored checksum
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    test_file = cache_dir / "corrupted_pkg.deb"
    test_file.write_bytes(b"corrupted content")

    # Store a different SHA256 in the database
    wrong_sha = "a" * 64  # obviously wrong hash

    # Create the database and insert a VERIFIED entry with wrong hash
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        entry = RepositoryFile(
            url="https://example.com/pool/main/c/corrupted/corrupted_pkg.deb",
            sha256=wrong_sha,
            size_bytes=17,
            state=RepositoryFileState.VERIFIED,
            retry_count=0,
            local_path=str(test_file),
        )
        session.add(entry)
        session.commit()

    engine.dispose()

    result = runner.invoke(app, ["mirror", "verify"])
    assert result.exit_code == 1
    assert "FAIL" in result.output
    assert "Mismatches" in result.output
    # Rich line-wraps long paths; normalize output for assertion
    normalized_output = result.output.replace("\n", "")
    assert "corrupted_pkg.deb" in normalized_output


@pytest.mark.unit
def test_mirror_verify_missing_file_reported(tmp_path, monkeypatch):
    """Verify command reports missing files separately from mismatches."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from debcraft.infrastructure.models.base import Base
    from debcraft.infrastructure.models.mirror import (
        RepositoryFile,
        RepositoryFileState,
    )

    # Set up XDG paths
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    db_dir = tmp_path / "debcraft"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "mirror.db"

    # Point to a file that doesn't exist
    nonexistent_file = tmp_path / "cache" / "gone.deb"

    # Create the database with an entry pointing to a missing file
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        entry = RepositoryFile(
            url="https://example.com/pool/main/g/gone/gone.deb",
            sha256="b" * 64,
            size_bytes=100,
            state=RepositoryFileState.INDEXED,
            retry_count=0,
            local_path=str(nonexistent_file),
        )
        session.add(entry)
        session.commit()

    engine.dispose()

    result = runner.invoke(app, ["mirror", "verify"])
    # With only missing files and no actual checked files, should PASS
    assert result.exit_code == 0
    assert "Files missing:" in result.output
    assert "PASS" in result.output


@pytest.mark.unit
def test_mirror_status_no_database(tmp_path, monkeypatch):
    """Status command shows zeros and 'never' when no database exists."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    result = runner.invoke(app, ["mirror", "status"])
    assert result.exit_code == 0
    assert "never" in result.output
    assert "0" in result.output


@pytest.mark.unit
def test_mirror_status_exits_code_zero(tmp_path, monkeypatch):
    """Status command always exits with code 0 on success."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    result = runner.invoke(app, ["mirror", "status"])
    assert result.exit_code == 0


@pytest.mark.unit
def test_mirror_status_displays_table_metrics(tmp_path, monkeypatch):
    """Status command displays all expected metric rows."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    result = runner.invoke(app, ["mirror", "status"])
    assert result.exit_code == 0
    assert "Configured repositories" in result.output
    assert "Last sync" in result.output
    assert "Cached files" in result.output
    assert "Failed files" in result.output
    assert "Cache size" in result.output


@pytest.mark.unit
def test_mirror_status_with_data(tmp_path, monkeypatch):
    """Status command correctly reports file counts, cache size, and last sync."""
    from datetime import datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from debcraft.infrastructure.models.base import Base
    from debcraft.infrastructure.models.mirror import (
        RepositoryFile,
        RepositoryFileState,
        SyncSession,
    )

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    db_dir = tmp_path / "debcraft"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "mirror.db"

    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(
            RepositoryFile(
                url="https://example.com/file1.deb",
                sha256="a" * 64,
                size_bytes=1024 * 1024 * 5,
                state=RepositoryFileState.VERIFIED,
                retry_count=0,
                local_path="/tmp/f1.deb",
            )
        )
        session.add(
            RepositoryFile(
                url="https://example.com/file2.deb",
                sha256="b" * 64,
                size_bytes=1024 * 1024 * 10,
                state=RepositoryFileState.INDEXED,
                retry_count=0,
                local_path="/tmp/f2.deb",
            )
        )
        session.add(
            RepositoryFile(
                url="https://example.com/file3.deb",
                sha256="c" * 64,
                size_bytes=512,
                state=RepositoryFileState.FAILED,
                retry_count=3,
                local_path=None,
            )
        )
        session.add(
            SyncSession(
                session_id="sess-1",
                repository_name="elxr",
                status="completed",
                files_downloaded=2,
                files_skipped=0,
                files_failed=1,
                bytes_transferred=1024 * 1024 * 15,
                started_at=datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
                completed_at=datetime(2024, 6, 1, 12, 5, 0, tzinfo=UTC),
            )
        )
        session.commit()

    engine.dispose()

    result = runner.invoke(app, ["mirror", "status"])
    assert result.exit_code == 0
    # Check cached files count (2: VERIFIED + INDEXED)
    assert "2" in result.output
    # Check failed files count
    assert "1" in result.output
    # Check cache size (15 MiB)
    assert "15.0 MiB" in result.output
    # Check last sync timestamp appears
    assert "2024-06-01" in result.output


@pytest.mark.unit
def test_mirror_status_empty_database(tmp_path, monkeypatch):
    """Status command handles empty tables gracefully (shows zeros)."""
    from sqlalchemy import create_engine

    from debcraft.infrastructure.models.base import Base

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    db_dir = tmp_path / "debcraft"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "mirror.db"

    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    engine.dispose()

    result = runner.invoke(app, ["mirror", "status"])
    assert result.exit_code == 0
    assert "never" in result.output
    assert "0 B" in result.output


# ─── Sync command tests ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_mirror_sync_no_repositories_configured():
    """Sync command exits 1 with 'No repositories configured' when config has no repos."""
    with patch("debcraft.cli.mirror._read_config", return_value=_EMPTY_CONFIG):
        result = runner.invoke(app, ["mirror", "sync"])

    assert result.exit_code == 1
    assert "No repositories configured" in result.output


@pytest.mark.unit
def test_mirror_sync_config_error_exits_with_code_1():
    """Sync command exits 1 with structured error when config read fails."""
    with patch(
        "debcraft.cli.mirror._read_config",
        side_effect=MirrorConfigurationError("Invalid TOML", line_number=5),
    ):
        result = runner.invoke(app, ["mirror", "sync"])

    assert result.exit_code == 1
    assert "Configuration error" in result.output


@pytest.mark.unit
def test_mirror_sync_success_exits_with_code_0():
    """Sync command exits 0 on successful synchronization."""
    sync_result = {
        "downloaded": 10,
        "skipped": 5,
        "failed": 0,
        "bytes_transferred": 1024 * 1024,
    }

    def _return_sync_result(coro):
        coro.close()
        return sync_result

    with (
        patch("debcraft.cli.mirror._read_config", return_value=_VALID_CONFIG),
        patch("debcraft.cli.mirror.asyncio.run", side_effect=_return_sync_result),
    ):
        result = runner.invoke(app, ["mirror", "sync"])

    assert result.exit_code == 0
    assert "Sync completed successfully" in result.output


@pytest.mark.unit
def test_mirror_sync_displays_summary_on_success():
    """Sync command displays summary table with download stats on success."""
    sync_result = {
        "downloaded": 3,
        "skipped": 7,
        "failed": 0,
        "bytes_transferred": 2048,
    }

    def _return_sync_result(coro):
        coro.close()
        return sync_result

    with (
        patch("debcraft.cli.mirror._read_config", return_value=_VALID_CONFIG),
        patch("debcraft.cli.mirror.asyncio.run", side_effect=_return_sync_result),
    ):
        result = runner.invoke(app, ["mirror", "sync"])

    assert result.exit_code == 0
    assert "3" in result.output
    assert "7" in result.output


@pytest.mark.unit
def test_mirror_sync_mirror_error_exits_with_code_1():
    """Sync command exits 1 with structured error on MirrorError during sync."""
    from debcraft.infrastructure.mirror.errors import MirrorError

    def _raise_mirror_error(coro):
        # Close the coroutine to avoid 'never awaited' RuntimeWarning
        coro.close()
        raise MirrorError("Network unreachable")

    with (
        patch("debcraft.cli.mirror._read_config", return_value=_VALID_CONFIG),
        patch(
            "debcraft.cli.mirror.asyncio.run",
            side_effect=_raise_mirror_error,
        ),
    ):
        result = runner.invoke(app, ["mirror", "sync"])

    assert result.exit_code == 1
    assert "Sync failed" in result.output


# ─── Clean command tests ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_mirror_clean_no_repositories_configured():
    """Clean command exits 1 with 'No repositories configured' when config has no repos."""
    with patch("debcraft.cli.mirror._read_config", return_value=_EMPTY_CONFIG):
        result = runner.invoke(app, ["mirror", "clean"])

    assert result.exit_code == 1
    assert "No repositories configured" in result.output


@pytest.mark.unit
def test_mirror_clean_config_error_exits_with_code_1():
    """Clean command exits 1 with structured error when config read fails."""
    with patch(
        "debcraft.cli.mirror._read_config",
        side_effect=MirrorConfigurationError("Bad config"),
    ):
        result = runner.invoke(app, ["mirror", "clean"])

    assert result.exit_code == 1
    assert "Configuration error" in result.output


@pytest.mark.unit
def test_mirror_clean_cache_is_clean_exits_with_code_0():
    """Clean command exits 0 when no unreferenced files are found."""
    with (
        patch("debcraft.cli.mirror._read_config", return_value=_VALID_CONFIG),
        patch("debcraft.cli.mirror._scan_mirror_cache", return_value=[]),
        patch("debcraft.cli.mirror._get_referenced_paths", new_callable=AsyncMock, return_value=set()),
    ):
        result = runner.invoke(app, ["mirror", "clean"])

    assert result.exit_code == 0
    assert "Cache is clean" in result.output


@pytest.mark.unit
def test_mirror_clean_yes_flag_skips_confirmation(tmp_path):
    """Clean command with --yes flag removes files without prompting for confirmation."""
    # Create a fake unreferenced file
    fake_file = tmp_path / "unreferenced.deb"
    fake_file.write_bytes(b"fake package content")

    with (
        patch("debcraft.cli.mirror._read_config", return_value=_VALID_CONFIG),
        patch("debcraft.cli.mirror._scan_mirror_cache", return_value=[fake_file]),
        patch("debcraft.cli.mirror._get_referenced_paths", new_callable=AsyncMock, return_value=set()),
    ):
        result = runner.invoke(app, ["mirror", "clean", "--yes"])

    assert result.exit_code == 0
    assert "Removed" in result.output
    # File should be deleted
    assert not fake_file.exists()


@pytest.mark.unit
def test_mirror_clean_without_yes_flag_aborts_on_no(tmp_path):
    """Clean command without --yes prompts and aborts if user says no."""
    fake_file = tmp_path / "unreferenced.deb"
    fake_file.write_bytes(b"fake package")

    with (
        patch("debcraft.cli.mirror._read_config", return_value=_VALID_CONFIG),
        patch("debcraft.cli.mirror._scan_mirror_cache", return_value=[fake_file]),
        patch("debcraft.cli.mirror._get_referenced_paths", new_callable=AsyncMock, return_value=set()),
    ):
        # Simulate user typing "n" at the confirmation prompt
        result = runner.invoke(app, ["mirror", "clean"], input="n\n")

    assert result.exit_code == 0
    assert "Aborted" in result.output
    # File should NOT be deleted
    assert fake_file.exists()


@pytest.mark.unit
def test_mirror_clean_yes_flag_exits_code_0_on_removal(tmp_path):
    """Clean command exits 0 after successfully removing unreferenced files with --yes."""
    fake_file = tmp_path / "old_pkg.deb"
    fake_file.write_bytes(b"x" * 512)

    with (
        patch("debcraft.cli.mirror._read_config", return_value=_VALID_CONFIG),
        patch("debcraft.cli.mirror._scan_mirror_cache", return_value=[fake_file]),
        patch("debcraft.cli.mirror._get_referenced_paths", new_callable=AsyncMock, return_value=set()),
    ):
        result = runner.invoke(app, ["mirror", "clean", "--yes"])

    assert result.exit_code == 0
    assert "reclaimed" in result.output.lower() or "Removed" in result.output
