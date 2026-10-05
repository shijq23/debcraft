"""Writer-level regression tests for DEPENDS_ON relationship uniqueness.

The dedupe invariant is enforced in ModelAssembler._build_depends_on_relationships,
but it is the writers that carry the schema obligations: CycloneDX 1.5 puts
``uniqueItems`` on ``dependsOn``, and the SPDX 3.0 writer derives element
``spdxId`` values as ``{source}-to-{target}``, so a duplicated relationship
becomes a duplicated element id. These tests assert the invariant on the written
documents so a regression upstream is caught at the output boundary.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from debcraft.domain.sbom.assembler import ModelAssembler
from debcraft.domain.sbom.values import SBOMDocument
from debcraft.domain.scanner.values import (
    EnrichedPackage,
    IdentifiedPackage,
    PackageEnrichment,
    ScanResult,
)
from debcraft.infrastructure.sbom_writers.cyclonedx import CycloneDXWriter
from debcraft.infrastructure.sbom_writers.spdx3 import SPDX3Writer
from debcraft.platform.contracts.workflow import CancellationToken, WorkflowContext

pytestmark = [pytest.mark.unit]


def _make_context() -> WorkflowContext:
    """Create a mock WorkflowContext with a live cancellation token."""
    ctx = MagicMock(spec=WorkflowContext)
    ctx.cancellation_token = CancellationToken()
    return ctx


@pytest.fixture
def document_with_repeated_depends() -> SBOMDocument:
    """Assemble a document whose Depends field names the same package twice."""
    scan_result = ScanResult(
        packages=[],
        strategy="dpkg-status",
        diagnostics=[],
        duration_seconds=1.0,
        artifact_path="/path/to/artifact.iso",
    )
    app = EnrichedPackage(
        package=IdentifiedPackage(name="app", version="1.0", architecture="amd64", status="installed"),
        enrichment=PackageEnrichment(depends="libbar (>= 2.0), libbar, libbar (<< 3.0)"),
    )
    libbar = EnrichedPackage(
        package=IdentifiedPackage(name="libbar", version="2.0", architecture="amd64", status="installed"),
    )
    return ModelAssembler().assemble(scan_result, [app, libbar])


class TestDependsOnUniquenessInWrittenOutput:
    """AC 2.6 regression guard: no duplicate DEPENDS_ON reaches the writers."""

    @pytest.mark.asyncio
    async def test_cyclonedx_depends_on_has_no_duplicates(
        self, document_with_repeated_depends: SBOMDocument, tmp_path: Path
    ) -> None:
        output = tmp_path / "sbom.cdx.json"
        await CycloneDXWriter().write(document_with_repeated_depends, output, _make_context())
        data = json.loads(output.read_text(encoding="utf-8"))
        for dependency in data["dependencies"]:
            depends_on = dependency["dependsOn"]
            assert len(depends_on) == len(set(depends_on)), f"duplicate dependsOn for ref {dependency['ref']}"

    @pytest.mark.asyncio
    async def test_spdx3_element_spdx_ids_are_unique(
        self, document_with_repeated_depends: SBOMDocument, tmp_path: Path
    ) -> None:
        output = tmp_path / "sbom.spdx3.json"
        await SPDX3Writer().write(document_with_repeated_depends, output, _make_context())
        data = json.loads(output.read_text(encoding="utf-8"))
        spdx_ids = [element["spdxId"] for element in data["element"]]
        assert len(spdx_ids) == len(set(spdx_ids))
