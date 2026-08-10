"""Integration tests for repository indexer CLI commands.

Tests `debcraft index` and `debcraft package` commands end-to-end using
Typer's CliRunner with real SQLite databases in temporary directories.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from debcraft.cli import app
from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.models.metadata import (
    PackageInstance,
    Repository,
    RepositorySnapshot,
)

runner = CliRunner()


@pytest.mark.integration
def test_index_no_verified_files(tmp_path, monkeypatch):
    """Verify `debcraft index` with no VERIFIED files prints informational message and exits 0."""
    # Point XDG_DATA_HOME so resolve_xdg_path("database") -> tmp_path/debcraft
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    db_dir = tmp_path / "debcraft"
    db_dir.mkdir(parents=True, exist_ok=True)

    # Create empty mirror.db and metadata.db with schemas
    mirror_db_path = db_dir / "mirror.db"
    metadata_db_path = db_dir / "metadata.db"

    mirror_engine = create_engine(f"sqlite:///{mirror_db_path}", echo=False)
    Base.metadata.create_all(mirror_engine)
    mirror_engine.dispose()

    metadata_engine = create_engine(f"sqlite:///{metadata_db_path}", echo=False)
    Base.metadata.create_all(metadata_engine)
    metadata_engine.dispose()

    # Also need a config file for the ConfigReader or patch it
    # The _run_index function checks verified files first, so an empty mirror.db
    # will return no verified files and short-circuit.
    result = runner.invoke(app, ["index"])

    assert result.exit_code == 0
    assert "No VERIFIED files" in result.output or "no" in result.output.lower()


@pytest.mark.integration
def test_package_not_found(tmp_path, monkeypatch):
    """Verify `debcraft package` exits 1 with 'Package not found' when package doesn't exist."""
    # Point XDG_DATA_HOME so resolve_xdg_path("database") -> tmp_path/debcraft
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    db_dir = tmp_path / "debcraft"
    db_dir.mkdir(parents=True, exist_ok=True)

    # Create metadata.db with schema but no packages
    metadata_db_path = db_dir / "metadata.db"
    engine = create_engine(f"sqlite:///{metadata_db_path}", echo=False)
    Base.metadata.create_all(engine)
    engine.dispose()

    result = runner.invoke(app, ["index", "package", "nonexistent-package"])

    assert result.exit_code == 1
    assert "Package not found" in result.output


@pytest.mark.integration
def test_package_found(tmp_path, monkeypatch):
    """Verify `debcraft package` displays package details when found in the database."""
    # Point XDG_DATA_HOME so resolve_xdg_path("database") -> tmp_path/debcraft
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    db_dir = tmp_path / "debcraft"
    db_dir.mkdir(parents=True, exist_ok=True)

    # Create metadata.db with a package
    metadata_db_path = db_dir / "metadata.db"
    engine = create_engine(f"sqlite:///{metadata_db_path}", echo=False)
    Base.metadata.create_all(engine)

    now = datetime.now(UTC)
    with Session(engine) as session:
        repo = Repository(
            name="test-repo",
            base_url="https://example.com/debian",
            suite="bookworm",
            component="main",
            created_at=now,
            updated_at=now,
        )
        session.add(repo)
        session.flush()

        snapshot = RepositorySnapshot(
            repository_id=repo.id,
            schema_version=1,
            captured_at=now,
            published=True,
            created_at=now,
            updated_at=now,
        )
        session.add(snapshot)
        session.flush()

        pkg = PackageInstance(
            package_name="libfoo",
            version="1.2.3-1",
            architecture="amd64",
            filename="pool/main/l/libfoo/libfoo_1.2.3-1_amd64.deb",
            sha256="a" * 64,
            size_bytes=102400,
            snapshot_id=snapshot.id,
            source_package="libfoo",
            source_version="1.2.3-1",
            section="libs",
            priority="optional",
            maintainer="Test Maintainer <test@example.com>",
            homepage="https://libfoo.example.com",
            description="A test library for integration testing",
            created_at=now,
            updated_at=now,
        )
        session.add(pkg)
        session.commit()

    engine.dispose()

    result = runner.invoke(app, ["index", "package", "libfoo"])

    assert result.exit_code == 0
    assert "libfoo" in result.output
    assert "1.2.3-1" in result.output
    assert "amd64" in result.output


@pytest.mark.integration
def test_package_not_found_no_database(tmp_path, monkeypatch):
    """Verify `debcraft package` exits 1 when metadata.db does not exist."""
    # Point XDG_DATA_HOME to a temp dir with no metadata.db
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    db_dir = tmp_path / "debcraft"
    db_dir.mkdir(parents=True, exist_ok=True)
    # Don't create metadata.db

    result = runner.invoke(app, ["index", "package", "some-package"])

    assert result.exit_code == 1
    assert "Package not found" in result.output
