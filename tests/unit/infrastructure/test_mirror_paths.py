"""Unit tests for infrastructure/mirror/paths.py path derivation utilities."""

import os
import stat
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from debcraft.infrastructure.mirror.paths import (
    _FILE_MODE,
    derive_file_path,
    derive_mirror_root,
    set_file_mode,
)

_skip_windows_permissions = pytest.mark.skipif(
    sys.platform == "win32", reason="NTFS does not support Unix file permissions"
)


@pytest.fixture
def storage_engine():
    mock = MagicMock()
    mock.get_path.return_value = Path("/home/user/.cache/debcraft/mirror")
    return mock


@pytest.mark.unit
@pytest.mark.mirror
class TestDeriveMirrorRoot:
    """Tests for derive_mirror_root path derivation from base URL."""

    def test_derives_path_with_hostname_and_url_path(self, storage_engine):
        """Base URL with path produces {mirror_base}/{hostname}/{url_path}."""
        result = derive_mirror_root(storage_engine, "https://mirror.elxr.dev/elxr")
        assert result == Path("/home/user/.cache/debcraft/mirror/mirror.elxr.dev/elxr")

    def test_derives_path_hostname_only(self, storage_engine):
        """Base URL without path produces {mirror_base}/{hostname}."""
        result = derive_mirror_root(storage_engine, "https://mirror.elxr.dev")
        assert result == Path("/home/user/.cache/debcraft/mirror/mirror.elxr.dev")

    def test_derives_path_with_trailing_slash(self, storage_engine):
        """Trailing slash in URL path is stripped."""
        result = derive_mirror_root(storage_engine, "https://mirror.elxr.dev/elxr/")
        assert result == Path("/home/user/.cache/debcraft/mirror/mirror.elxr.dev/elxr")

    def test_separate_directories_per_repository(self, storage_engine):
        """Different base URLs produce different root paths."""
        root1 = derive_mirror_root(storage_engine, "https://mirror.elxr.dev/elxr")
        root2 = derive_mirror_root(storage_engine, "https://deb.debian.org/debian")
        assert root1 != root2
        assert root1 == Path("/home/user/.cache/debcraft/mirror/mirror.elxr.dev/elxr")
        assert root2 == Path("/home/user/.cache/debcraft/mirror/deb.debian.org/debian")

    def test_derives_path_with_nested_url_path(self, storage_engine):
        """Multi-segment URL path is preserved."""
        result = derive_mirror_root(storage_engine, "https://example.com/repos/debian/main")
        assert result == Path("/home/user/.cache/debcraft/mirror/example.com/repos/debian/main")

    def test_calls_storage_engine_with_mirror_purpose(self, storage_engine):
        """Verifies get_path is called with 'mirror' purpose."""
        derive_mirror_root(storage_engine, "https://example.com/repo")
        storage_engine.get_path.assert_called_once_with("mirror")

    def test_handles_http_url(self, storage_engine):
        """HTTP URLs (not just HTTPS) are handled correctly."""
        result = derive_mirror_root(storage_engine, "http://archive.ubuntu.com/ubuntu")
        assert result == Path("/home/user/.cache/debcraft/mirror/archive.ubuntu.com/ubuntu")

    def test_handles_url_with_no_hostname(self, storage_engine):
        """Malformed URL without hostname uses 'unknown' fallback."""
        result = derive_mirror_root(storage_engine, "file:///local/path")
        # urlparse("file:///local/path").hostname is None
        assert "unknown" in str(result)


@pytest.mark.unit
@pytest.mark.mirror
class TestDeriveFilePath:
    """Tests for derive_file_path relative path construction."""

    def test_preserves_dists_relative_path(self):
        """Dists-relative path is appended to mirror root."""
        root = Path("/home/user/.cache/debcraft/mirror/mirror.elxr.dev/elxr")
        result = derive_file_path(root, "dists/elxr3/InRelease")
        assert result == root / "dists" / "elxr3" / "InRelease"

    def test_preserves_pool_relative_path(self):
        """Pool-relative path with deep nesting is preserved."""
        root = Path("/home/user/.cache/debcraft/mirror/mirror.elxr.dev/elxr")
        result = derive_file_path(root, "pool/main/l/libssl3/libssl3_3.0.2-0ubuntu1_amd64.deb")
        expected = root / "pool" / "main" / "l" / "libssl3" / "libssl3_3.0.2-0ubuntu1_amd64.deb"
        assert result == expected

    def test_strips_leading_slash(self):
        """Leading slash in relative path does not cause absolute path."""
        root = Path("/mirror/root")
        result = derive_file_path(root, "/dists/suite/Release")
        assert result == root / "dists" / "suite" / "Release"

    def test_preserves_binary_index_path(self):
        """Component/architecture index paths are preserved."""
        root = Path("/mirror/root")
        result = derive_file_path(root, "dists/elxr3/main/binary-amd64/Packages.gz")
        expected = root / "dists" / "elxr3" / "main" / "binary-amd64" / "Packages.gz"
        assert result == expected

    def test_simple_filename(self):
        """Single filename without directory structure."""
        root = Path("/mirror/root")
        result = derive_file_path(root, "Release")
        assert result == root / "Release"


@pytest.mark.unit
@pytest.mark.mirror
class TestSetFileMode:
    """Tests for set_file_mode permission setting."""

    @_skip_windows_permissions
    def test_sets_0o644_mode(self, tmp_path):
        """File mode is set to 0o644 (rw-r--r--)."""
        test_file = tmp_path / "test.deb"
        test_file.write_bytes(b"content")
        # Start with restrictive permissions
        os.chmod(test_file, 0o600)

        set_file_mode(test_file)

        mode = test_file.stat().st_mode & 0o777
        assert mode == 0o644

    def test_file_mode_constant(self):
        """_FILE_MODE constant equals 0o644."""
        assert _FILE_MODE == 0o644

    @_skip_windows_permissions
    def test_mode_allows_owner_read_write(self, tmp_path):
        """Owner has read and write permissions."""
        test_file = tmp_path / "test.deb"
        test_file.write_bytes(b"content")
        set_file_mode(test_file)

        mode = test_file.stat().st_mode
        assert mode & stat.S_IRUSR  # owner read
        assert mode & stat.S_IWUSR  # owner write

    @_skip_windows_permissions
    def test_mode_allows_group_read(self, tmp_path):
        """Group has read permission."""
        test_file = tmp_path / "test.deb"
        test_file.write_bytes(b"content")
        set_file_mode(test_file)

        mode = test_file.stat().st_mode
        assert mode & stat.S_IRGRP  # group read

    @_skip_windows_permissions
    def test_mode_allows_other_read(self, tmp_path):
        """Others have read permission."""
        test_file = tmp_path / "test.deb"
        test_file.write_bytes(b"content")
        set_file_mode(test_file)

        mode = test_file.stat().st_mode
        assert mode & stat.S_IROTH  # other read
