"""Property-based tests for filesystem analyzer path limit behavior.

**Validates: Requirements 11.6, 11.8**

Property 10: Filesystem Analyzer Path Limit
  When the number of file paths exceeds max_paths, exactly max_paths paths
  are sent to ContentsIndexPort for processing, and a diagnostic message
  mentions the number of skipped paths.
  When the number of file paths is within the limit, all paths are processed
  and no truncation diagnostic is emitted.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.domain.scanner.filesystem_analyzer import analyze_filesystem

# ---------------------------------------------------------------------------
# Fake Ports for recording behavior
# ---------------------------------------------------------------------------


class RecordingContentsIndexPort:
    """Fake ContentsIndexPort that records which paths were sent to it."""

    def __init__(self) -> None:
        self.received_paths: list[str] = []

    async def find_owners(self, file_paths: list[str], snapshot_id: int) -> dict[str, str]:
        """Record paths and return empty mapping (no matches)."""
        self.received_paths = list(file_paths)
        return {}


class StubPackageLookupPort:
    """Stub PackageLookupPort that returns None for all lookups."""

    async def find_by_name(self, package_name: str, snapshot_id: int) -> tuple[str, str, str] | None:
        """Return None (no metadata available)."""
        return None


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate valid-looking filesystem paths
_PATH_COMPONENT = st.text(
    st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters="-_.",
    ),
    min_size=1,
    max_size=20,
)


@st.composite
def st_file_path(draw: st.DrawFn) -> str:
    """Generate a filesystem path like /usr/lib/something."""
    depth = draw(st.integers(min_value=1, max_value=4))
    components = [draw(_PATH_COMPONENT) for _ in range(depth)]
    return "/" + "/".join(components)


@st.composite
def st_file_path_list(draw: st.DrawFn, min_size: int = 1, max_size: int = 200) -> list[str]:
    """Generate a list of file paths."""
    return draw(st.lists(st_file_path(), min_size=min_size, max_size=max_size))


# ---------------------------------------------------------------------------
# Property 10: Filesystem Analyzer Path Limit
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProperty10FilesystemAnalyzerPathLimit:
    """Property 10: Filesystem Analyzer Path Limit.

    Tests that the filesystem analyzer correctly enforces the max_paths limit
    by truncating input paths and reporting diagnostics about skipped paths.
    """

    @settings(max_examples=100)
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_exceeding_max_paths_sends_exactly_max_paths(self, data: st.DataObject) -> None:
        """When len(file_paths) > max_paths, exactly max_paths are processed.

        **Validates: Requirements 11.6**
        """
        # Use a small max_paths to make the test tractable
        max_paths = data.draw(st.integers(min_value=1, max_value=20))
        # Generate more paths than the limit
        extra = data.draw(st.integers(min_value=1, max_value=50))
        total_paths = max_paths + extra
        file_paths = data.draw(st.lists(st_file_path(), min_size=total_paths, max_size=total_paths))

        contents_port = RecordingContentsIndexPort()
        package_port = StubPackageLookupPort()

        await analyze_filesystem(
            file_paths=file_paths,
            contents_port=contents_port,
            package_port=package_port,
            snapshot_id=1,
            max_paths=max_paths,
        )

        # Exactly max_paths paths should have been sent to the port
        assert len(contents_port.received_paths) == max_paths, (
            f"Expected {max_paths} paths sent to ContentsIndexPort, got {len(contents_port.received_paths)}"
        )

    @settings(max_examples=100)
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_exceeding_max_paths_diagnostic_mentions_skipped_count(self, data: st.DataObject) -> None:
        """When len(file_paths) > max_paths, diagnostic mentions skipped count.

        **Validates: Requirements 11.8**
        """
        max_paths = data.draw(st.integers(min_value=1, max_value=20))
        extra = data.draw(st.integers(min_value=1, max_value=50))
        total_paths = max_paths + extra
        file_paths = data.draw(st.lists(st_file_path(), min_size=total_paths, max_size=total_paths))

        contents_port = RecordingContentsIndexPort()
        package_port = StubPackageLookupPort()

        result = await analyze_filesystem(
            file_paths=file_paths,
            contents_port=contents_port,
            package_port=package_port,
            snapshot_id=1,
            max_paths=max_paths,
        )

        skipped_count = total_paths - max_paths

        # There should be a diagnostic about the path limit
        truncation_diagnostics = [d for d in result.diagnostics if str(skipped_count) in d]
        assert len(truncation_diagnostics) >= 1, (
            f"Expected diagnostic mentioning {skipped_count} skipped paths, got diagnostics: {result.diagnostics}"
        )

    @settings(max_examples=100)
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_within_limit_all_paths_processed_no_truncation_diagnostic(self, data: st.DataObject) -> None:
        """When len(file_paths) <= max_paths, all paths processed, no truncation diagnostic.

        **Validates: Requirements 11.6, 11.8**
        """
        max_paths = data.draw(st.integers(min_value=5, max_value=50))
        num_paths = data.draw(st.integers(min_value=1, max_value=max_paths))
        file_paths = data.draw(st.lists(st_file_path(), min_size=num_paths, max_size=num_paths))

        contents_port = RecordingContentsIndexPort()
        package_port = StubPackageLookupPort()

        result = await analyze_filesystem(
            file_paths=file_paths,
            contents_port=contents_port,
            package_port=package_port,
            snapshot_id=1,
            max_paths=max_paths,
        )

        # All paths should have been sent to the port
        assert len(contents_port.received_paths) == num_paths, (
            f"Expected all {num_paths} paths sent to ContentsIndexPort, got {len(contents_port.received_paths)}"
        )

        # No truncation diagnostic should be present
        truncation_diagnostics = [d for d in result.diagnostics if "skipped" in d.lower() or "limit" in d.lower()]
        assert len(truncation_diagnostics) == 0, (
            f"Expected no truncation diagnostics when within limit, got: {truncation_diagnostics}"
        )
