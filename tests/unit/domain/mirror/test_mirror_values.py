"""Unit tests for domain/mirror/values.py value objects."""

import pytest

from debcraft.domain.mirror.values import DownloadResult, FileEntry, SyncDecision


@pytest.mark.unit
@pytest.mark.mirror
class TestFileEntry:
    """Tests for the FileEntry frozen dataclass."""

    def test_construction(self):
        fe = FileEntry(relative_path="pool/main/a.deb", sha256="abcd1234", size_bytes=2048)
        assert fe.relative_path == "pool/main/a.deb"
        assert fe.sha256 == "abcd1234"
        assert fe.size_bytes == 2048

    def test_frozen(self):
        fe = FileEntry(relative_path="x", sha256="y", size_bytes=0)
        with pytest.raises(AttributeError):
            fe.relative_path = "other"  # type: ignore[misc]

    def test_equality(self):
        a = FileEntry(relative_path="p", sha256="h", size_bytes=1)
        b = FileEntry(relative_path="p", sha256="h", size_bytes=1)
        assert a == b

    def test_inequality(self):
        a = FileEntry(relative_path="p", sha256="h", size_bytes=1)
        b = FileEntry(relative_path="p", sha256="h", size_bytes=2)
        assert a != b


@pytest.mark.unit
@pytest.mark.mirror
class TestSyncDecision:
    """Tests for the SyncDecision frozen dataclass."""

    def test_download_decision(self):
        fe = FileEntry(relative_path="f", sha256="h", size_bytes=10)
        sd = SyncDecision(file_entry=fe, action="download", reason="not cached")
        assert sd.action == "download"
        assert sd.reason == "not cached"

    def test_skip_decision(self):
        fe = FileEntry(relative_path="f", sha256="h", size_bytes=10)
        sd = SyncDecision(file_entry=fe, action="skip", reason="checksum matches")
        assert sd.action == "skip"

    def test_verify_decision(self):
        fe = FileEntry(relative_path="f", sha256="h", size_bytes=10)
        sd = SyncDecision(file_entry=fe, action="verify", reason="needs verification")
        assert sd.action == "verify"

    def test_frozen(self):
        fe = FileEntry(relative_path="f", sha256="h", size_bytes=10)
        sd = SyncDecision(file_entry=fe, action="skip", reason="r")
        with pytest.raises(AttributeError):
            sd.action = "download"  # type: ignore[misc]


@pytest.mark.unit
@pytest.mark.mirror
class TestDownloadResult:
    """Tests for the DownloadResult frozen dataclass."""

    def test_success_result(self):
        dr = DownloadResult(
            url="https://example.com/file.deb",
            success=True,
            sha256_verified=True,
            bytes_transferred=4096,
        )
        assert dr.success is True
        assert dr.sha256_verified is True
        assert dr.error is None
        assert dr.retry_count == 0

    def test_failure_result(self):
        dr = DownloadResult(
            url="https://example.com/file.deb",
            success=False,
            sha256_verified=False,
            bytes_transferred=0,
            error="Connection refused",
            retry_count=3,
        )
        assert dr.success is False
        assert dr.error == "Connection refused"
        assert dr.retry_count == 3

    def test_frozen(self):
        dr = DownloadResult(url="u", success=True, sha256_verified=True, bytes_transferred=0)
        with pytest.raises(AttributeError):
            dr.success = False  # type: ignore[misc]
