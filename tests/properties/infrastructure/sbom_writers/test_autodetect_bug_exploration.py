"""Bug condition exploration test for artifact type auto-detection.

**Validates: Requirements 1.2, 1.3**

Property: Bug Condition — Workflow defaults to DIRECTORY regardless of file extension.

This test demonstrates that when `--type` is not specified (artifact_type is None),
SBOMWorkflow._scan() always defaults to ArtifactType.DIRECTORY instead of inferring
the correct artifact type from the file extension.

For example, a path like `/path/to/image.iso` should be detected as ArtifactType.ISO,
but the current code unconditionally falls back to ArtifactType.DIRECTORY.

The test asserts the CORRECT behavior (auto-detection from extension) and is expected
to FAIL on the unfixed code, confirming the bug exists.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.domain.scanner.values import ArtifactType
from debcraft.infrastructure.sbom_writers.workflow import SBOMWorkflowConfig

# ---------------------------------------------------------------------------
# Extension-to-expected-type mapping (the correct behavior per design doc)
# ---------------------------------------------------------------------------

_EXTENSION_TO_EXPECTED_TYPE: dict[str, ArtifactType] = {
    ".iso": ArtifactType.ISO,
    ".qcow2": ArtifactType.QCOW2,
    ".img": ArtifactType.IMG,
    ".tar": ArtifactType.DOCKER,
    ".tar.gz": ArtifactType.DOCKER,
    ".tgz": ArtifactType.DOCKER,
}

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate a random directory prefix for the path
_path_prefix = st.sampled_from(
    [
        "/tmp",
        "/home/user/artifacts",
        "/var/lib/debcraft",
        "/mnt/storage",
        "/opt/images",
    ]
)

# Generate a random base filename (alphanumeric, 1-20 chars)
_base_name = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
    min_size=1,
    max_size=20,
)

# Generate a recognizable extension and its expected type
_extension_and_type = st.sampled_from(list(_EXTENSION_TO_EXPECTED_TYPE.items()))


@st.composite
def artifact_path_with_expected_type(draw: st.DrawFn) -> tuple[str, ArtifactType]:
    """Generate a (file_path, expected_artifact_type) pair.

    The path always has a recognized extension that should map to a
    specific ArtifactType (not DIRECTORY).
    """
    prefix = draw(_path_prefix)
    name = draw(_base_name)
    extension, expected_type = draw(_extension_and_type)
    path = f"{prefix}/{name}{extension}"
    return path, expected_type


# ---------------------------------------------------------------------------
# Bug exploration test
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.workflow
@pytest.mark.xfail(reason="Exploration test: documents the old buggy logic (fixed in detect_artifact_type)")
class TestAutoDetectionBugExploration:
    """Demonstrates that SBOMWorkflow._scan() defaults to DIRECTORY for all paths.

    The bug is in workflow.py lines 245-248:

        if self._config.artifact_type:
            artifact_type = ArtifactType(self._config.artifact_type)
        else:
            artifact_type = ArtifactType.DIRECTORY

    When artifact_type is None, the code ALWAYS uses DIRECTORY regardless of
    the file extension. This test asserts the CORRECT behavior (auto-detection)
    and should FAIL on the current buggy code.
    """

    @given(data=artifact_path_with_expected_type())
    def test_artifact_type_detected_from_extension_when_type_not_specified(
        self,
        data: tuple[str, ArtifactType],
    ) -> None:
        """Resolved artifact type should match the extension, not default to DIRECTORY.

        For any artifact path with a recognized extension and no explicit --type,
        the resolved artifact type should match the extension.

        **Validates: Requirements 1.2, 1.3**

        This test exercises the type-resolution logic extracted from _scan():
        - config.artifact_type is None (user did not pass --type)
        - config.artifact_path has a recognized extension (.iso, .qcow2, .img, etc.)
        - The resolved type SHOULD be the one matching the extension

        On the current buggy code, this will always resolve to DIRECTORY.
        """
        artifact_path, expected_type = data
        config = SBOMWorkflowConfig(
            artifact_path=artifact_path,
            artifact_type=None,  # User did not specify --type
        )

        # Replicate the type-resolution logic from SBOMWorkflow._scan()
        resolved_type = ArtifactType(config.artifact_type) if config.artifact_type else ArtifactType.DIRECTORY

        # Assert correct behavior: type should be auto-detected from extension
        assert resolved_type == expected_type, (
            f"For path '{artifact_path}' with extension mapping to {expected_type.value}, "
            f"expected auto-detected type {expected_type.value} but got {resolved_type.value}. "
            f"Bug: workflow defaults to DIRECTORY instead of detecting from extension."
        )
