"""Property-based tests for Model Assembler field mapping.

# Feature: sbom-writers, Property 3: Model assembler field mapping correctness

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.10**

Property 3: Model assembler field mapping correctness.
For any ScanResult containing one or more EnrichedPackage entries, the
ModelAssembler SHALL produce an SBOMDocument where:
(a) there is exactly one SBOMPackage per input EnrichedPackage with name,
    version, and description correctly mapped
(b) packages with non-null purl have correct package_url and a
    PACKAGE_MANAGER external reference
(c) packages with non-null sha256 have a SHA256 checksum
(d) packages with license_expressions have concluded_license set
(e) a DESCRIBES relationship exists from root to each component
(f) DEPENDS_ON relationships are generated for dependencies matching
    other packages
(g) the document namespace matches the format
    `https://debcraft.io/spdxdocs/<16-hex>-<uuid4>`
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.domain.sbom.assembler import ModelAssembler
from debcraft.domain.sbom.values import (
    ChecksumAlgorithm,
    ExternalReferenceCategory,
    RelationshipType,
)
from debcraft.domain.scanner.values import (
    EnrichedPackage,
    IdentifiedPackage,
    PackageEnrichment,
    ScanResult,
)

from .strategies import sha256_hashes

# ---------------------------------------------------------------------------
# Hypothesis strategies for scanner domain objects
# ---------------------------------------------------------------------------

# Non-empty package names (alphanumeric + hyphen, like real Debian packages)
_package_names: st.SearchStrategy[str] = st.from_regex(r"[a-z][a-z0-9\-]{0,30}[a-z0-9]", fullmatch=True)

# Package versions (Debian-style: epoch:upstream-revision or simpler)
_package_versions: st.SearchStrategy[str] = st.from_regex(
    r"[0-9]{1,3}\.[0-9]{1,3}(\.[0-9]{1,3})?(-[0-9]{1,3})?", fullmatch=True
)

# Architectures
_architectures: st.SearchStrategy[str] = st.sampled_from(["amd64", "arm64", "i386", "armhf", "all"])

# Package statuses
_statuses: st.SearchStrategy[str] = st.sampled_from(["installed", "config-files", "half-installed"])

# PURL strings (simplified but representative)
_purls: st.SearchStrategy[str] = _package_names.flatmap(
    lambda name: _package_versions.map(lambda ver: f"pkg:deb/debian/{name}@{ver}")
)

# License expressions (SPDX-style)
_license_expressions: st.SearchStrategy[list[tuple[str, str]]] = st.lists(
    st.tuples(
        st.sampled_from(["MIT", "Apache-2.0", "GPL-2.0-only", "BSD-3-Clause"]),
        st.sampled_from(["file-scan", "debian-copyright", "heuristic"]),
    ),
    min_size=1,
    max_size=3,
)

# Depends strings (comma-separated, possibly with version constraints)
_depends_strings: st.SearchStrategy[str] = st.lists(
    _package_names.flatmap(
        lambda name: st.one_of(
            st.just(name),
            st.just(f"{name} (>= 1.0)"),
            st.just(f"{name} (>> 0.5)"),
        )
    ),
    min_size=1,
    max_size=5,
).map(", ".join)


def _identified_packages() -> st.SearchStrategy[IdentifiedPackage]:
    """Generate valid IdentifiedPackage instances."""
    return st.builds(
        IdentifiedPackage,
        name=_package_names,
        version=_package_versions,
        architecture=_architectures,
        status=_statuses,
    )


def _package_enrichments(
    *,
    with_purl: st.SearchStrategy[str | None] | None = None,
    with_sha256: st.SearchStrategy[str | None] | None = None,
    with_license: st.SearchStrategy[list[tuple[str, str]]] | None = None,
    with_depends: st.SearchStrategy[str | None] | None = None,
) -> st.SearchStrategy[PackageEnrichment]:
    """Generate PackageEnrichment with configurable optional fields."""
    return st.builds(
        PackageEnrichment,
        source_package=st.none() | _package_names,
        maintainer=st.none() | st.just("Maintainer <maint@example.org>"),
        homepage=st.none() | st.just("https://example.org"),
        depends=with_depends if with_depends is not None else (st.none() | _depends_strings),
        section=st.none() | st.sampled_from(["libs", "utils", "devel"]),
        priority=st.none() | st.sampled_from(["optional", "required"]),
        description=st.none() | st.text(min_size=1, max_size=50),
        sha256=with_sha256 if with_sha256 is not None else (st.none() | sha256_hashes),
        download_url=st.none() | st.just("https://deb.debian.org/pool/main/test.deb"),
        purl=with_purl if with_purl is not None else (st.none() | _purls),
        license_expressions=with_license if with_license is not None else (st.just([]) | _license_expressions),
    )


def _enriched_packages(
    *,
    enrichment_strategy: st.SearchStrategy[PackageEnrichment | None] | None = None,
) -> st.SearchStrategy[EnrichedPackage]:
    """Generate EnrichedPackage instances."""
    enrichment = enrichment_strategy if enrichment_strategy is not None else (st.none() | _package_enrichments())
    return st.builds(
        EnrichedPackage,
        package=_identified_packages(),
        enrichment=enrichment,
    )


def _scan_results_with_packages(
    min_packages: int = 1,
    max_packages: int = 5,
) -> st.SearchStrategy[tuple[ScanResult, list[EnrichedPackage]]]:
    """Generate a ScanResult and matching list of EnrichedPackages."""
    return st.lists(
        _enriched_packages(),
        min_size=min_packages,
        max_size=max_packages,
    ).flatmap(
        lambda pkgs: st.tuples(
            st.builds(
                ScanResult,
                packages=st.just([ep.package for ep in pkgs]),
                strategy=st.just("dpkg_metadata"),
                diagnostics=st.just([]),
                duration_seconds=st.just(0.5),
                artifact_path=st.from_regex(r"/[a-z]{1,10}/[a-z]{1,10}\.[a-z]{2,4}", fullmatch=True),
                enriched_packages=st.just(pkgs),
            ),
            st.just(pkgs),
        )
    )


# ---------------------------------------------------------------------------
# Namespace pattern
# ---------------------------------------------------------------------------

_NAMESPACE_PATTERN = re.compile(
    r"^https://debcraft\.io/spdxdocs/"
    r"[0-9a-f]{16}-"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


# ---------------------------------------------------------------------------
# Property 3: Model assembler field mapping correctness
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.spdx
class TestProperty3AssemblerFieldMappingCorrectness:
    """Property 3: Model assembler field mapping correctness.

    For any ScanResult containing one or more EnrichedPackage entries, the
    ModelAssembler SHALL produce an SBOMDocument that correctly maps all
    enriched package fields to the internal SBOM model.
    """

    # --- (a) One SBOMPackage per EnrichedPackage with correct field mapping ---

    @settings(max_examples=100)
    @given(data=_scan_results_with_packages(min_packages=1, max_packages=5))
    def test_one_package_per_enriched_package_with_correct_mapping(
        self, data: tuple[ScanResult, list[EnrichedPackage]]
    ) -> None:
        """Each EnrichedPackage produces exactly one SBOMPackage with correct name, version, and description."""
        scan_result, enriched_packages = data
        assembler = ModelAssembler()
        doc = assembler.assemble(scan_result, enriched_packages)

        # Exactly one SBOMPackage per input EnrichedPackage
        assert len(doc.packages) == len(enriched_packages)

        for ep, sbom_pkg in zip(enriched_packages, doc.packages, strict=False):
            # Name mapped from IdentifiedPackage.name
            assert sbom_pkg.name == ep.package.name
            # Version mapped from IdentifiedPackage.version
            assert sbom_pkg.version == ep.package.version
            # Description mapped from architecture
            if ep.package.architecture:
                assert sbom_pkg.description == f"Architecture: {ep.package.architecture}"

    # --- (b) Non-null purl → package_url + PACKAGE_MANAGER external ref ---

    @settings(max_examples=100)
    @given(
        data=st.lists(
            _enriched_packages(
                enrichment_strategy=_package_enrichments(
                    with_purl=_purls,
                ).map(lambda e: e)  # ensure enrichment is not None
            ),
            min_size=1,
            max_size=3,
        ).flatmap(
            lambda pkgs: st.tuples(
                st.builds(
                    ScanResult,
                    packages=st.just([ep.package for ep in pkgs]),
                    strategy=st.just("dpkg_metadata"),
                    diagnostics=st.just([]),
                    duration_seconds=st.just(0.5),
                    artifact_path=st.from_regex(r"/[a-z]{1,10}/[a-z]{1,10}\.[a-z]{2,4}", fullmatch=True),
                    enriched_packages=st.just(pkgs),
                ),
                st.just(pkgs),
            )
        )
    )
    def test_purl_maps_to_package_url_and_external_reference(
        self, data: tuple[ScanResult, list[EnrichedPackage]]
    ) -> None:
        """Packages with non-null purl have correct package_url and a PACKAGE_MANAGER external reference."""
        scan_result, enriched_packages = data
        assembler = ModelAssembler()
        doc = assembler.assemble(scan_result, enriched_packages)

        for ep, sbom_pkg in zip(enriched_packages, doc.packages, strict=False):
            if ep.enrichment is not None and ep.enrichment.purl is not None:
                assert sbom_pkg.package_url == ep.enrichment.purl
                # Should have at least one PACKAGE_MANAGER external reference
                pm_refs = [
                    ref
                    for ref in sbom_pkg.external_references
                    if ref.category == ExternalReferenceCategory.PACKAGE_MANAGER
                ]
                assert len(pm_refs) >= 1
                assert any(ref.url == ep.enrichment.purl for ref in pm_refs)

    # --- (c) Non-null sha256 → SHA256 checksum ---

    @settings(max_examples=100)
    @given(
        data=st.lists(
            _enriched_packages(
                enrichment_strategy=_package_enrichments(
                    with_sha256=sha256_hashes,
                ).map(lambda e: e)
            ),
            min_size=1,
            max_size=3,
        ).flatmap(
            lambda pkgs: st.tuples(
                st.builds(
                    ScanResult,
                    packages=st.just([ep.package for ep in pkgs]),
                    strategy=st.just("dpkg_metadata"),
                    diagnostics=st.just([]),
                    duration_seconds=st.just(0.5),
                    artifact_path=st.from_regex(r"/[a-z]{1,10}/[a-z]{1,10}\.[a-z]{2,4}", fullmatch=True),
                    enriched_packages=st.just(pkgs),
                ),
                st.just(pkgs),
            )
        )
    )
    def test_sha256_maps_to_checksum(self, data: tuple[ScanResult, list[EnrichedPackage]]) -> None:
        """Packages with non-null sha256 have a SHA256 checksum entry."""
        scan_result, enriched_packages = data
        assembler = ModelAssembler()
        doc = assembler.assemble(scan_result, enriched_packages)

        for ep, sbom_pkg in zip(enriched_packages, doc.packages, strict=False):
            if ep.enrichment is not None and ep.enrichment.sha256 is not None:
                sha256_checksums = [c for c in sbom_pkg.checksums if c.algorithm == ChecksumAlgorithm.SHA256]
                assert len(sha256_checksums) >= 1
                assert any(c.value == ep.enrichment.sha256 for c in sha256_checksums)

    # --- (d) license_expressions → concluded_license set ---

    @settings(max_examples=100)
    @given(
        data=st.lists(
            _enriched_packages(
                enrichment_strategy=_package_enrichments(
                    with_license=_license_expressions,
                ).map(lambda e: e)
            ),
            min_size=1,
            max_size=3,
        ).flatmap(
            lambda pkgs: st.tuples(
                st.builds(
                    ScanResult,
                    packages=st.just([ep.package for ep in pkgs]),
                    strategy=st.just("dpkg_metadata"),
                    diagnostics=st.just([]),
                    duration_seconds=st.just(0.5),
                    artifact_path=st.from_regex(r"/[a-z]{1,10}/[a-z]{1,10}\.[a-z]{2,4}", fullmatch=True),
                    enriched_packages=st.just(pkgs),
                ),
                st.just(pkgs),
            )
        )
    )
    def test_license_expressions_map_to_concluded_license(self, data: tuple[ScanResult, list[EnrichedPackage]]) -> None:
        """Packages with license_expressions have concluded_license set to the first expression's SPDX string."""
        scan_result, enriched_packages = data
        assembler = ModelAssembler()
        doc = assembler.assemble(scan_result, enriched_packages)

        for ep, sbom_pkg in zip(enriched_packages, doc.packages, strict=False):
            if ep.enrichment is not None and ep.enrichment.license_expressions:
                expected_license = ep.enrichment.license_expressions[0][0]
                assert sbom_pkg.concluded_license == expected_license

    # --- (e) DESCRIBES relationship from root to each component ---

    @settings(max_examples=100)
    @given(data=_scan_results_with_packages(min_packages=1, max_packages=5))
    def test_describes_relationship_from_root_to_each_component(
        self, data: tuple[ScanResult, list[EnrichedPackage]]
    ) -> None:
        """A DESCRIBES relationship exists from root package to each component package."""
        scan_result, enriched_packages = data
        assembler = ModelAssembler()
        doc = assembler.assemble(scan_result, enriched_packages)

        root_id = doc.root_package.spdx_id
        describes_rels = [
            rel
            for rel in doc.relationships
            if rel.relationship_type == RelationshipType.DESCRIBES and rel.source_id == root_id
        ]

        # One DESCRIBES per component package
        component_ids = {pkg.spdx_id for pkg in doc.packages}
        describes_target_ids = {rel.target_id for rel in describes_rels}

        assert component_ids == describes_target_ids

    # --- (f) DEPENDS_ON relationships for matching dependencies ---

    @settings(max_examples=100)
    @given(data=st.just(None).flatmap(lambda _: _build_depends_on_test_data()))
    def test_depends_on_relationships_for_matching_packages(
        self, data: tuple[ScanResult, list[EnrichedPackage]]
    ) -> None:
        """DEPENDS_ON relationships are generated for dependencies that match other packages in the document."""
        scan_result, enriched_packages = data
        assembler = ModelAssembler()
        doc = assembler.assemble(scan_result, enriched_packages)

        # Build name→spdx_id lookup from doc
        name_to_spdx_id: dict[str, str] = {}
        for pkg in doc.packages:
            name_to_spdx_id[pkg.name] = pkg.spdx_id

        depends_on_rels = [rel for rel in doc.relationships if rel.relationship_type == RelationshipType.DEPENDS_ON]

        # For each enriched package that has depends, verify matching
        # dependencies produce DEPENDS_ON relationships
        for ep, sbom_pkg in zip(enriched_packages, doc.packages, strict=False):
            if ep.enrichment is None or ep.enrichment.depends is None:
                continue

            dep_specs = [d.strip() for d in ep.enrichment.depends.split(",")]
            for dep_spec in dep_specs:
                if not dep_spec:
                    continue
                dep_name = dep_spec.split("(")[0].strip()
                dep_name = dep_name.split("|")[0].strip()

                if dep_name and dep_name in name_to_spdx_id:
                    target_id = name_to_spdx_id[dep_name]
                    # Should have a DEPENDS_ON from this package to the dep
                    matching = [
                        r for r in depends_on_rels if r.source_id == sbom_pkg.spdx_id and r.target_id == target_id
                    ]
                    assert len(matching) >= 1, f"Missing DEPENDS_ON from {sbom_pkg.name} to {dep_name}"

    # --- (g) Document namespace format ---

    @settings(max_examples=100)
    @given(data=_scan_results_with_packages(min_packages=1, max_packages=3))
    def test_document_namespace_format(self, data: tuple[ScanResult, list[EnrichedPackage]]) -> None:
        """Document namespace matches format https://debcraft.io/spdxdocs/<16-hex>-<uuid4>."""
        scan_result, enriched_packages = data
        assembler = ModelAssembler()
        doc = assembler.assemble(scan_result, enriched_packages)

        assert _NAMESPACE_PATTERN.match(doc.namespace), f"Namespace does not match expected format: {doc.namespace}"


# ---------------------------------------------------------------------------
# Helper strategy for DEPENDS_ON test (f)
# ---------------------------------------------------------------------------


@st.composite
def _build_depends_on_test_data(
    draw: st.DrawFn,
) -> tuple[ScanResult, list[EnrichedPackage]]:
    """Build a set of packages where at least one has a depends field referencing another package in the set."""
    # Generate 2-4 base packages with distinct names
    num_packages = draw(st.integers(min_value=2, max_value=4))
    names = draw(
        st.lists(
            _package_names,
            min_size=num_packages,
            max_size=num_packages,
            unique=True,
        )
    )

    packages: list[EnrichedPackage] = []
    for i, name in enumerate(names):
        version = draw(_package_versions)
        arch = draw(_architectures)
        status = draw(_statuses)
        identified = IdentifiedPackage(name=name, version=version, architecture=arch, status=status)

        # For the first package, make it depend on at least one other
        if i == 0 and len(names) > 1:
            # Pick a random dep target from remaining names
            dep_target = draw(st.sampled_from(names[1:]))
            depends_str = f"{dep_target} (>= 1.0)"
            enrichment = PackageEnrichment(depends=depends_str)
        else:
            enrichment = draw(st.none() | _package_enrichments(with_depends=st.none()))

        packages.append(EnrichedPackage(package=identified, enrichment=enrichment))

    artifact_path = draw(st.from_regex(r"/[a-z]{1,10}/[a-z]{1,10}\.[a-z]{2,4}", fullmatch=True))
    scan_result = ScanResult(
        packages=[ep.package for ep in packages],
        strategy="dpkg_metadata",
        diagnostics=[],
        duration_seconds=0.5,
        artifact_path=artifact_path,
        enriched_packages=packages,
    )

    return scan_result, packages
