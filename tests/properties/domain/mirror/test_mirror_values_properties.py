"""Property-based tests for mirror domain value objects.

**Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 3.1, 3.2, 3.3,
4.1, 4.2, 4.3, 4.4, 11.1, 11.2, 11.3, 11.4, 11.5**

Property 1: Value Object Immutability.
For any generated value object instance (FileEntry, SyncDecision, or
DownloadResult), attempting to assign a new value to any of its fields
SHALL raise an AttributeError.

Property 2: Value Object Equality Reflexivity.
For any generated value object instance with arbitrary valid field values
including both None and non-None optional fields, the instance SHALL
equal itself under the == operator.

Property 3: Value Object Equality Symmetry.
For any set of field values used to construct two distinct value object
instances of the same type, if a == b then b == a (both evaluate to True).

Property 4: Value Object Inequality on Differing Fields.
For any pair of value object instances of the same type where exactly one
field differs and all other fields are identical, a != b SHALL hold.
"""

from __future__ import annotations

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from debcraft.domain.mirror.values import (
    DownloadResult,
    FileEntry,
    SyncDecision,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


def _file_entry() -> st.SearchStrategy[FileEntry]:
    """Generate an arbitrary FileEntry instance."""
    return st.builds(
        FileEntry,
        relative_path=st.text(),
        sha256=st.text(),
        size_bytes=st.integers(min_value=0),
    )


def _sync_decision() -> st.SearchStrategy[SyncDecision]:
    """Generate an arbitrary SyncDecision instance."""
    return st.builds(
        SyncDecision,
        file_entry=_file_entry(),
        action=st.sampled_from(["download", "skip", "verify"]),
        reason=st.text(),
    )


def _download_result() -> st.SearchStrategy[DownloadResult]:
    """Generate an arbitrary DownloadResult instance."""
    return st.builds(
        DownloadResult,
        url=st.text(),
        success=st.booleans(),
        sha256_verified=st.booleans(),
        bytes_transferred=st.integers(min_value=0),
        error=st.none() | st.text(),
        retry_count=st.integers(min_value=0),
        status_code=st.none() | st.integers(),
        response_headers=st.none() | st.dictionaries(st.text(), st.text()),
    )


# ---------------------------------------------------------------------------
# Property 1: Value Object Immutability
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty1ValueObjectImmutability:
    """Property 1: Value Object Immutability.

    For any generated value object instance, attempting to assign a new
    value to any of its fields SHALL raise an AttributeError.
    """

    @given(entry=_file_entry())
    def test_file_entry_fields_are_immutable(self, entry: FileEntry) -> None:
        """FileEntry field assignment raises AttributeError."""
        with pytest.raises(AttributeError):
            entry.relative_path = "new"  # type: ignore[misc]
        with pytest.raises(AttributeError):
            entry.sha256 = "new"  # type: ignore[misc]
        with pytest.raises(AttributeError):
            entry.size_bytes = 999  # type: ignore[misc]

    @given(decision=_sync_decision())
    def test_sync_decision_fields_are_immutable(self, decision: SyncDecision) -> None:
        """SyncDecision field assignment raises AttributeError."""
        with pytest.raises(AttributeError):
            decision.file_entry = FileEntry("x", "y", 0)  # type: ignore[misc]
        with pytest.raises(AttributeError):
            decision.action = "skip"  # type: ignore[misc]
        with pytest.raises(AttributeError):
            decision.reason = "new"  # type: ignore[misc]

    @given(result=_download_result())
    def test_download_result_fields_are_immutable(self, result: DownloadResult) -> None:
        """DownloadResult field assignment raises AttributeError."""
        with pytest.raises(AttributeError):
            result.url = "new"  # type: ignore[misc]
        with pytest.raises(AttributeError):
            result.success = True  # type: ignore[misc]
        with pytest.raises(AttributeError):
            result.sha256_verified = True  # type: ignore[misc]
        with pytest.raises(AttributeError):
            result.bytes_transferred = 0  # type: ignore[misc]
        with pytest.raises(AttributeError):
            result.error = "err"  # type: ignore[misc]
        with pytest.raises(AttributeError):
            result.retry_count = 1  # type: ignore[misc]
        with pytest.raises(AttributeError):
            result.status_code = 200  # type: ignore[misc]
        with pytest.raises(AttributeError):
            result.response_headers = {}  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Property 2: Value Object Equality Reflexivity
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty2EqualityReflexivity:
    """Property 2: Value Object Equality Reflexivity.

    For any generated value object instance, the instance SHALL equal
    itself under the == operator.
    """

    @given(entry=_file_entry())
    def test_file_entry_equals_itself(self, entry: FileEntry) -> None:
        """FileEntry instance equals itself."""
        assert entry == entry

    @given(decision=_sync_decision())
    def test_sync_decision_equals_itself(self, decision: SyncDecision) -> None:
        """SyncDecision instance equals itself."""
        assert decision == decision

    @given(result=_download_result())
    def test_download_result_equals_itself(self, result: DownloadResult) -> None:
        """DownloadResult instance equals itself."""
        assert result == result


# ---------------------------------------------------------------------------
# Property 3: Value Object Equality Symmetry
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty3EqualitySymmetry:
    """Property 3: Value Object Equality Symmetry.

    For any set of field values used to construct two distinct instances,
    a == b and b == a both evaluate to True.
    """

    @given(
        relative_path=st.text(),
        sha256=st.text(),
        size_bytes=st.integers(min_value=0),
    )
    def test_file_entry_equality_is_symmetric(self, relative_path: str, sha256: str, size_bytes: int) -> None:
        """Two FileEntry instances with same fields are equal both ways."""
        a = FileEntry(relative_path, sha256, size_bytes)
        b = FileEntry(relative_path, sha256, size_bytes)
        assert a == b
        assert b == a

    @given(
        file_entry=_file_entry(),
        action=st.sampled_from(["download", "skip", "verify"]),
        reason=st.text(),
    )
    def test_sync_decision_equality_is_symmetric(self, file_entry: FileEntry, action: str, reason: str) -> None:
        """Two SyncDecision instances with same fields are equal both ways."""
        a = SyncDecision(file_entry, action, reason)
        b = SyncDecision(file_entry, action, reason)
        assert a == b
        assert b == a

    @given(
        url=st.text(),
        success=st.booleans(),
        sha256_verified=st.booleans(),
        bytes_transferred=st.integers(min_value=0),
        error=st.none() | st.text(),
        retry_count=st.integers(min_value=0),
        status_code=st.none() | st.integers(),
        response_headers=st.none() | st.dictionaries(st.text(), st.text()),
    )
    def test_download_result_equality_is_symmetric(
        self,
        url: str,
        success: bool,
        sha256_verified: bool,
        bytes_transferred: int,
        error: str | None,
        retry_count: int,
        status_code: int | None,
        response_headers: dict[str, str] | None,
    ) -> None:
        """Two DownloadResult instances with same fields are equal both ways."""
        a = DownloadResult(
            url,
            success,
            sha256_verified,
            bytes_transferred,
            error,
            retry_count,
            status_code,
            response_headers,
        )
        b = DownloadResult(
            url,
            success,
            sha256_verified,
            bytes_transferred,
            error,
            retry_count,
            status_code,
            response_headers,
        )
        assert a == b
        assert b == a


# ---------------------------------------------------------------------------
# Property 4: Value Object Inequality on Differing Fields
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty4InequalityOnDifferingFields:
    """Property 4: Value Object Inequality on Differing Fields.

    For any pair of instances where exactly one field differs,
    a != b SHALL hold.
    """

    # --- FileEntry ---

    @given(
        entry=_file_entry(),
        other_path=st.text(),
    )
    def test_file_entry_differs_on_relative_path(self, entry: FileEntry, other_path: str) -> None:
        """FileEntry instances with different relative_path are not equal."""
        assume(other_path != entry.relative_path)
        other = FileEntry(other_path, entry.sha256, entry.size_bytes)
        assert entry != other

    @given(
        entry=_file_entry(),
        other_sha=st.text(),
    )
    def test_file_entry_differs_on_sha256(self, entry: FileEntry, other_sha: str) -> None:
        """FileEntry instances with different sha256 are not equal."""
        assume(other_sha != entry.sha256)
        other = FileEntry(entry.relative_path, other_sha, entry.size_bytes)
        assert entry != other

    @given(
        entry=_file_entry(),
        other_size=st.integers(min_value=0),
    )
    def test_file_entry_differs_on_size_bytes(self, entry: FileEntry, other_size: int) -> None:
        """FileEntry instances with different size_bytes are not equal."""
        assume(other_size != entry.size_bytes)
        other = FileEntry(entry.relative_path, entry.sha256, other_size)
        assert entry != other

    # --- SyncDecision ---

    @given(
        decision=_sync_decision(),
        other_entry=_file_entry(),
    )
    def test_sync_decision_differs_on_file_entry(self, decision: SyncDecision, other_entry: FileEntry) -> None:
        """SyncDecision instances with different file_entry are not equal."""
        assume(other_entry != decision.file_entry)
        other = SyncDecision(other_entry, decision.action, decision.reason)
        assert decision != other

    @given(
        decision=_sync_decision(),
        other_action=st.sampled_from(["download", "skip", "verify"]),
    )
    def test_sync_decision_differs_on_action(self, decision: SyncDecision, other_action: str) -> None:
        """SyncDecision instances with different action are not equal."""
        assume(other_action != decision.action)
        other = SyncDecision(decision.file_entry, other_action, decision.reason)
        assert decision != other

    @given(
        decision=_sync_decision(),
        other_reason=st.text(),
    )
    def test_sync_decision_differs_on_reason(self, decision: SyncDecision, other_reason: str) -> None:
        """SyncDecision instances with different reason are not equal."""
        assume(other_reason != decision.reason)
        other = SyncDecision(decision.file_entry, decision.action, other_reason)
        assert decision != other

    # --- DownloadResult ---

    @given(
        result=_download_result(),
        other_url=st.text(),
    )
    def test_download_result_differs_on_url(self, result: DownloadResult, other_url: str) -> None:
        """DownloadResult instances with different url are not equal."""
        assume(other_url != result.url)
        other = DownloadResult(
            other_url,
            result.success,
            result.sha256_verified,
            result.bytes_transferred,
            result.error,
            result.retry_count,
            result.status_code,
            result.response_headers,
        )
        assert result != other

    @given(result=_download_result())
    def test_download_result_differs_on_success(self, result: DownloadResult) -> None:
        """DownloadResult instances with different success are not equal."""
        other = DownloadResult(
            result.url,
            not result.success,
            result.sha256_verified,
            result.bytes_transferred,
            result.error,
            result.retry_count,
            result.status_code,
            result.response_headers,
        )
        assert result != other

    @given(result=_download_result())
    def test_download_result_differs_on_sha256_verified(self, result: DownloadResult) -> None:
        """DownloadResult instances with different sha256_verified are not equal."""
        other = DownloadResult(
            result.url,
            result.success,
            not result.sha256_verified,
            result.bytes_transferred,
            result.error,
            result.retry_count,
            result.status_code,
            result.response_headers,
        )
        assert result != other

    @given(
        result=_download_result(),
        other_bytes=st.integers(min_value=0),
    )
    def test_download_result_differs_on_bytes_transferred(self, result: DownloadResult, other_bytes: int) -> None:
        """DownloadResult instances with different bytes_transferred are not equal."""
        assume(other_bytes != result.bytes_transferred)
        other = DownloadResult(
            result.url,
            result.success,
            result.sha256_verified,
            other_bytes,
            result.error,
            result.retry_count,
            result.status_code,
            result.response_headers,
        )
        assert result != other

    @given(
        result=_download_result(),
        other_error=st.none() | st.text(),
    )
    def test_download_result_differs_on_error(self, result: DownloadResult, other_error: str | None) -> None:
        """DownloadResult instances with different error are not equal."""
        assume(other_error != result.error)
        other = DownloadResult(
            result.url,
            result.success,
            result.sha256_verified,
            result.bytes_transferred,
            other_error,
            result.retry_count,
            result.status_code,
            result.response_headers,
        )
        assert result != other

    @given(
        result=_download_result(),
        other_retry=st.integers(min_value=0),
    )
    def test_download_result_differs_on_retry_count(self, result: DownloadResult, other_retry: int) -> None:
        """DownloadResult instances with different retry_count are not equal."""
        assume(other_retry != result.retry_count)
        other = DownloadResult(
            result.url,
            result.success,
            result.sha256_verified,
            result.bytes_transferred,
            result.error,
            other_retry,
            result.status_code,
            result.response_headers,
        )
        assert result != other

    @given(
        result=_download_result(),
        other_status=st.none() | st.integers(),
    )
    def test_download_result_differs_on_status_code(self, result: DownloadResult, other_status: int | None) -> None:
        """DownloadResult instances with different status_code are not equal."""
        assume(other_status != result.status_code)
        other = DownloadResult(
            result.url,
            result.success,
            result.sha256_verified,
            result.bytes_transferred,
            result.error,
            result.retry_count,
            other_status,
            result.response_headers,
        )
        assert result != other

    @given(
        result=_download_result(),
        other_headers=st.none() | st.dictionaries(st.text(), st.text()),
    )
    def test_download_result_differs_on_response_headers(
        self, result: DownloadResult, other_headers: dict[str, str] | None
    ) -> None:
        """DownloadResult instances with different response_headers are not equal."""
        assume(other_headers != result.response_headers)
        other = DownloadResult(
            result.url,
            result.success,
            result.sha256_verified,
            result.bytes_transferred,
            result.error,
            result.retry_count,
            result.status_code,
            other_headers,
        )
        assert result != other
