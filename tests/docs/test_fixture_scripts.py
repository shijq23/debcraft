"""Tests for fixture script validation.

Validates that fixture scripts produce correct output at expected paths,
meet size constraints, have correct internal structure, are idempotent,
and fail gracefully when required tools are missing.

Validates: Requirements 11.2, 11.3, 11.4, 11.5, 11.6, 11.8, 11.9
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Return the project root directory."""
    # Walk up from this test file to find the project root (contains pyproject.toml)
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("Could not find project root (no pyproject.toml found)")


@pytest.fixture(scope="session")
def fixtures_dir(project_root: Path) -> Path:
    """Return the fixtures directory."""
    return project_root / "fixtures"


@pytest.fixture(scope="session")
def images_dir(fixtures_dir: Path) -> Path:
    """Return the fixtures/images directory."""
    return fixtures_dir / "images"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _run_fixture_script(script_path: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run a fixture script and return the completed process."""
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run(
        ["bash", str(script_path)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        env=run_env,
    )


def _file_sha256(path: Path) -> str:
    """Compute the SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_restricted_path(exclude_tool: str) -> str:
    """Create a PATH containing a temp directory with symlinks to common tools, excluding the target.

    Creates a temporary directory, symlinks essential tools (bash, env, etc.) into it,
    but omits the specified tool. This ensures `command -v <tool>` fails in the script.
    """
    # Create a temp directory to hold our restricted tool set
    restricted_dir = tempfile.mkdtemp(prefix="restricted-path-")

    # Collect all unique real directories from PATH
    current_path = os.environ.get("PATH", "")
    path_dirs = []
    seen_real = set()
    for d in current_path.split(os.pathsep):
        if not d:
            continue
        try:
            real = str(Path(d).resolve())
        except (OSError, ValueError):
            continue
        if real not in seen_real:
            seen_real.add(real)
            path_dirs.append(real)

    # Find all instances of the excluded tool (resolve symlinks)
    excluded_realpaths = set()
    for d in path_dirs:
        candidate = Path(d) / exclude_tool
        if candidate.exists():
            excluded_realpaths.add(str(candidate.resolve()))

    # Symlink all executables from PATH dirs into our restricted dir, except the excluded tool
    for d in path_dirs:
        dir_path = Path(d)
        if not dir_path.is_dir():
            continue
        try:
            entries = list(dir_path.iterdir())
        except PermissionError:
            continue
        for entry in entries:
            if entry.name == exclude_tool:
                continue
            # Skip if it resolves to the excluded tool
            try:
                if str(entry.resolve()) in excluded_realpaths:
                    continue
            except (OSError, ValueError):
                continue
            dest = Path(restricted_dir) / entry.name
            if not dest.exists():
                with contextlib.suppress(OSError):
                    dest.symlink_to(entry)

    return restricted_dir


# ---------------------------------------------------------------------------
# Marks for tool availability
# ---------------------------------------------------------------------------

has_mkfs_ext4 = pytest.mark.skipif(
    shutil.which("mkfs.ext4") is None,
    reason="mkfs.ext4 not available (install e2fsprogs)",
)

has_debugfs = pytest.mark.skipif(
    shutil.which("debugfs") is None,
    reason="debugfs not available (install e2fsprogs)",
)

has_qemu_img = pytest.mark.skipif(
    shutil.which("qemu-img") is None,
    reason="qemu-img not available (install qemu-utils)",
)


# ---------------------------------------------------------------------------
# Output path tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFixtureOutputPaths:
    """Test that each fixture produces output at the expected path."""

    def test_docker_fixture_produces_output(self, fixtures_dir: Path, images_dir: Path) -> None:
        """Run build-docker.sh and verify test.tar exists."""
        script = fixtures_dir / "build-docker.sh"
        result = _run_fixture_script(script)
        assert result.returncode == 0, f"build-docker.sh failed: {result.stderr}"
        assert (images_dir / "test.tar").exists(), "fixtures/images/test.tar not created"

    @has_mkfs_ext4
    @has_debugfs
    def test_img_fixture_produces_output(self, fixtures_dir: Path, images_dir: Path) -> None:
        """Run build-img.sh and verify test.img exists."""
        script = fixtures_dir / "build-img.sh"
        result = _run_fixture_script(script)
        assert result.returncode == 0, f"build-img.sh failed: {result.stderr}"
        assert (images_dir / "test.img").exists(), "fixtures/images/test.img not created"

    @has_mkfs_ext4
    @has_debugfs
    @has_qemu_img
    def test_qcow2_fixture_produces_output(self, fixtures_dir: Path, images_dir: Path) -> None:
        """Run build-qcow2.sh and verify test.qcow2 exists."""
        script = fixtures_dir / "build-qcow2.sh"
        result = _run_fixture_script(script)
        assert result.returncode == 0, f"build-qcow2.sh failed: {result.stderr}"
        assert (images_dir / "test.qcow2").exists(), "fixtures/images/test.qcow2 not created"


# ---------------------------------------------------------------------------
# Size limit tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFixtureSizeLimits:
    """Test that fixture outputs meet size constraints."""

    def test_docker_fixture_size_limit(self, fixtures_dir: Path, images_dir: Path) -> None:
        """fixtures/images/test.tar must not exceed 100 KB."""
        script = fixtures_dir / "build-docker.sh"
        _run_fixture_script(script)
        tar_path = images_dir / "test.tar"
        assert tar_path.exists(), "test.tar not found"
        size = tar_path.stat().st_size
        assert size <= 102400, f"test.tar is {size} bytes, exceeds 100 KB limit"

    @has_mkfs_ext4
    @has_debugfs
    def test_img_fixture_size_limit(self, fixtures_dir: Path, images_dir: Path) -> None:
        """fixtures/images/test.img must not exceed 4 MB."""
        script = fixtures_dir / "build-img.sh"
        _run_fixture_script(script)
        img_path = images_dir / "test.img"
        assert img_path.exists(), "test.img not found"
        size = img_path.stat().st_size
        assert size <= 4194304, f"test.img is {size} bytes, exceeds 4 MB limit"

    @has_mkfs_ext4
    @has_debugfs
    @has_qemu_img
    def test_qcow2_fixture_size_limit(self, fixtures_dir: Path, images_dir: Path) -> None:
        """fixtures/images/test.qcow2 must not exceed 100 KB."""
        script = fixtures_dir / "build-qcow2.sh"
        _run_fixture_script(script)
        qcow2_path = images_dir / "test.qcow2"
        assert qcow2_path.exists(), "test.qcow2 not found"
        size = qcow2_path.stat().st_size
        assert size <= 102400, f"test.qcow2 is {size} bytes, exceeds 100 KB limit"


# ---------------------------------------------------------------------------
# Docker fixture structure test
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDockerFixtureStructure:
    """Test Docker fixture internal structure."""

    def test_docker_fixture_structure(self, fixtures_dir: Path, images_dir: Path) -> None:
        """Docker tarball must contain manifest.json and at least one layer.tar."""
        script = fixtures_dir / "build-docker.sh"
        _run_fixture_script(script)
        tar_path = images_dir / "test.tar"
        assert tar_path.exists(), "test.tar not found"

        with tarfile.open(tar_path, "r") as tf:
            members = tf.getnames()

        # Must contain manifest.json
        assert "manifest.json" in members, f"manifest.json not found in tarball. Members: {members}"

        # Must contain at least one layer.tar (could be at root or in a subdirectory)
        layer_tar_found = any(name == "layer.tar" or name.endswith("/layer.tar") for name in members)
        assert layer_tar_found, f"No layer.tar found in tarball. Members: {members}"


# ---------------------------------------------------------------------------
# Idempotency tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFixtureIdempotency:
    """Test that running fixture scripts twice produces identical output."""

    def test_docker_fixture_idempotent(self, fixtures_dir: Path, images_dir: Path) -> None:
        """Running build-docker.sh twice produces byte-identical output."""
        script = fixtures_dir / "build-docker.sh"
        tar_path = images_dir / "test.tar"

        # First run
        result1 = _run_fixture_script(script)
        assert result1.returncode == 0, f"First run failed: {result1.stderr}"
        hash1 = _file_sha256(tar_path)

        # Second run
        result2 = _run_fixture_script(script)
        assert result2.returncode == 0, f"Second run failed: {result2.stderr}"
        hash2 = _file_sha256(tar_path)

        assert hash1 == hash2, "build-docker.sh is not idempotent: output differs between runs"

    @has_mkfs_ext4
    @has_debugfs
    def test_img_fixture_idempotent(self, fixtures_dir: Path, images_dir: Path) -> None:
        """Running build-img.sh twice produces byte-identical output."""
        script = fixtures_dir / "build-img.sh"
        img_path = images_dir / "test.img"

        # First run
        result1 = _run_fixture_script(script)
        assert result1.returncode == 0, f"First run failed: {result1.stderr}"
        hash1 = _file_sha256(img_path)

        # Second run
        result2 = _run_fixture_script(script)
        assert result2.returncode == 0, f"Second run failed: {result2.stderr}"
        hash2 = _file_sha256(img_path)

        assert hash1 == hash2, "build-img.sh is not idempotent: output differs between runs"

    @has_mkfs_ext4
    @has_debugfs
    @has_qemu_img
    def test_qcow2_fixture_idempotent(self, fixtures_dir: Path, images_dir: Path) -> None:
        """Running build-qcow2.sh twice produces byte-identical output."""
        script = fixtures_dir / "build-qcow2.sh"
        qcow2_path = images_dir / "test.qcow2"

        # First run
        result1 = _run_fixture_script(script)
        assert result1.returncode == 0, f"First run failed: {result1.stderr}"
        hash1 = _file_sha256(qcow2_path)

        # Second run
        result2 = _run_fixture_script(script)
        assert result2.returncode == 0, f"Second run failed: {result2.stderr}"
        hash2 = _file_sha256(qcow2_path)

        assert hash1 == hash2, "build-qcow2.sh is not idempotent: output differs between runs"


# ---------------------------------------------------------------------------
# Graceful failure on missing tools
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFixtureMissingToolErrors:
    """Test that fixture scripts fail gracefully when required tools are missing."""

    def test_docker_fixture_missing_tool_error(self, fixtures_dir: Path) -> None:
        """build-docker.sh exits non-zero and mentions missing tool when tar is unavailable."""
        script = fixtures_dir / "build-docker.sh"
        restricted_dir = _make_restricted_path("tar")
        try:
            result = _run_fixture_script(script, env={"PATH": restricted_dir})
            assert result.returncode != 0, "build-docker.sh should fail when tar is missing"
            assert "tar" in result.stderr.lower(), f"stderr should mention 'tar', got: {result.stderr}"
        finally:
            shutil.rmtree(restricted_dir, ignore_errors=True)

    @has_mkfs_ext4
    @has_debugfs
    def test_img_fixture_missing_tool_error(self, fixtures_dir: Path) -> None:
        """build-img.sh exits non-zero and mentions missing tool when mkfs.ext4 is unavailable."""
        script = fixtures_dir / "build-img.sh"
        restricted_dir = _make_restricted_path("mkfs.ext4")
        try:
            result = _run_fixture_script(script, env={"PATH": restricted_dir})
            assert result.returncode != 0, "build-img.sh should fail when mkfs.ext4 is missing"
            assert "mkfs.ext4" in result.stderr.lower(), f"stderr should mention 'mkfs.ext4', got: {result.stderr}"
        finally:
            shutil.rmtree(restricted_dir, ignore_errors=True)

    @has_mkfs_ext4
    @has_debugfs
    @has_qemu_img
    def test_qcow2_fixture_missing_tool_error(self, fixtures_dir: Path) -> None:
        """build-qcow2.sh exits non-zero and mentions missing tool when qemu-img is unavailable."""
        script = fixtures_dir / "build-qcow2.sh"
        restricted_dir = _make_restricted_path("qemu-img")
        try:
            result = _run_fixture_script(script, env={"PATH": restricted_dir})
            assert result.returncode != 0, "build-qcow2.sh should fail when qemu-img is missing"
            assert "qemu-img" in result.stderr.lower(), f"stderr should mention 'qemu-img', got: {result.stderr}"
        finally:
            shutil.rmtree(restricted_dir, ignore_errors=True)
