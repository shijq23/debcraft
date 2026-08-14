"""Unit tests for scanner domain value objects.

Tests frozen behavior (immutability), required fields, default values, and enum membership.
"""

from __future__ import annotations

import dataclasses

import pytest

from debcraft.domain.scanner.values import (
    VALID_PACKAGE_STATUSES,
    Artifact,
    ArtifactType,
    EnrichedPackage,
    IdentifiedPackage,
    PackageEnrichment,
    ScanningStrategy,
    ScanResult,
    detect_artifact_type,
)


@pytest.mark.unit
class TestArtifactTypeEnum:
    """Verify ArtifactType enum membership and values."""

    def test_has_seven_members(self):
        """ArtifactType defines exactly 7 artifact format members."""
        assert len(ArtifactType) == 7

    def test_directory_member(self):
        assert ArtifactType.DIRECTORY.value == "directory"

    def test_docker_member(self):
        assert ArtifactType.DOCKER.value == "docker"

    def test_oci_member(self):
        assert ArtifactType.OCI.value == "oci"

    def test_iso_member(self):
        assert ArtifactType.ISO.value == "iso"

    def test_qcow2_member(self):
        assert ArtifactType.QCOW2.value == "qcow2"

    def test_img_member(self):
        assert ArtifactType.IMG.value == "img"

    def test_ami_member(self):
        assert ArtifactType.AMI.value == "ami"

    def test_access_by_name(self):
        """Enum members can be accessed by name."""
        assert ArtifactType["DIRECTORY"] is ArtifactType.DIRECTORY

    def test_access_by_value(self):
        """Enum members can be accessed by value."""
        assert ArtifactType("docker") is ArtifactType.DOCKER


@pytest.mark.unit
class TestScanningStrategyEnum:
    """Verify ScanningStrategy enum membership and values."""

    def test_has_two_members(self):
        """ScanningStrategy defines exactly 2 members."""
        assert len(ScanningStrategy) == 2

    def test_dpkg_metadata_member(self):
        assert ScanningStrategy.DPKG_METADATA.value == "dpkg_metadata"

    def test_filesystem_analysis_member(self):
        assert ScanningStrategy.FILESYSTEM_ANALYSIS.value == "filesystem_analysis"


@pytest.mark.unit
class TestValidPackageStatuses:
    """Verify VALID_PACKAGE_STATUSES frozenset contents."""

    def test_contains_nine_elements(self):
        assert len(VALID_PACKAGE_STATUSES) == 9

    def test_contains_installed(self):
        assert "installed" in VALID_PACKAGE_STATUSES

    def test_contains_config_files(self):
        assert "config-files" in VALID_PACKAGE_STATUSES

    def test_contains_half_installed(self):
        assert "half-installed" in VALID_PACKAGE_STATUSES

    def test_contains_unpacked(self):
        assert "unpacked" in VALID_PACKAGE_STATUSES

    def test_contains_half_configured(self):
        assert "half-configured" in VALID_PACKAGE_STATUSES

    def test_contains_triggers_awaited(self):
        assert "triggers-awaited" in VALID_PACKAGE_STATUSES

    def test_contains_triggers_pending(self):
        assert "triggers-pending" in VALID_PACKAGE_STATUSES

    def test_contains_not_installed(self):
        assert "not-installed" in VALID_PACKAGE_STATUSES

    def test_contains_inferred(self):
        assert "inferred" in VALID_PACKAGE_STATUSES

    def test_is_frozenset(self):
        assert isinstance(VALID_PACKAGE_STATUSES, frozenset)


@pytest.mark.unit
class TestArtifact:
    """Verify Artifact frozen dataclass behavior."""

    def test_construction_with_required_fields(self):
        """Artifact can be created with type and path."""
        artifact = Artifact(type=ArtifactType.DIRECTORY, path="/some/path")
        assert artifact.type is ArtifactType.DIRECTORY
        assert artifact.path == "/some/path"

    def test_default_options_is_empty_dict(self):
        """Options defaults to an empty dict when not provided."""
        artifact = Artifact(type=ArtifactType.DOCKER, path="/img.tar")
        assert artifact.options == {}

    def test_options_can_be_provided(self):
        """Options can be set explicitly."""
        opts = {"tag": "latest"}
        artifact = Artifact(type=ArtifactType.OCI, path="/oci", options=opts)
        assert artifact.options == {"tag": "latest"}

    def test_frozen_cannot_set_attribute(self):
        """Attempting to modify a frozen Artifact raises FrozenInstanceError."""
        artifact = Artifact(type=ArtifactType.DIRECTORY, path="/root")
        with pytest.raises(dataclasses.FrozenInstanceError):
            artifact.path = "/other"  # type: ignore[misc]

    def test_missing_type_raises_type_error(self):
        """Omitting required field 'type' raises TypeError."""
        with pytest.raises(TypeError):
            Artifact(path="/some/path")  # type: ignore[call-arg]

    def test_missing_path_raises_type_error(self):
        """Omitting required field 'path' raises TypeError."""
        with pytest.raises(TypeError):
            Artifact(type=ArtifactType.ISO)  # type: ignore[call-arg]


@pytest.mark.unit
class TestIdentifiedPackage:
    """Verify IdentifiedPackage frozen dataclass behavior."""

    def test_construction_with_all_required_fields(self):
        pkg = IdentifiedPackage(name="libc6", version="2.36-9", architecture="amd64", status="installed")
        assert pkg.name == "libc6"
        assert pkg.version == "2.36-9"
        assert pkg.architecture == "amd64"
        assert pkg.status == "installed"

    def test_frozen_cannot_set_attribute(self):
        pkg = IdentifiedPackage(name="bash", version="5.2-2", architecture="amd64", status="installed")
        with pytest.raises(dataclasses.FrozenInstanceError):
            pkg.name = "zsh"  # type: ignore[misc]

    def test_missing_name_raises_type_error(self):
        with pytest.raises(TypeError):
            IdentifiedPackage(version="1.0", architecture="amd64", status="installed")  # type: ignore[call-arg]

    def test_missing_version_raises_type_error(self):
        with pytest.raises(TypeError):
            IdentifiedPackage(name="pkg", architecture="amd64", status="installed")  # type: ignore[call-arg]

    def test_missing_architecture_raises_type_error(self):
        with pytest.raises(TypeError):
            IdentifiedPackage(name="pkg", version="1.0", status="installed")  # type: ignore[call-arg]

    def test_missing_status_raises_type_error(self):
        with pytest.raises(TypeError):
            IdentifiedPackage(name="pkg", version="1.0", architecture="amd64")  # type: ignore[call-arg]


@pytest.mark.unit
class TestPackageEnrichment:
    """Verify PackageEnrichment frozen dataclass behavior."""

    def test_construction_with_no_arguments(self):
        """All fields are optional with None/empty defaults."""
        enrichment = PackageEnrichment()
        assert enrichment.source_package is None
        assert enrichment.maintainer is None
        assert enrichment.homepage is None
        assert enrichment.depends is None
        assert enrichment.section is None
        assert enrichment.priority is None
        assert enrichment.description is None
        assert enrichment.sha256 is None
        assert enrichment.download_url is None
        assert enrichment.purl is None
        assert enrichment.license_expressions == []
        assert enrichment.local_deb_path is None

    def test_construction_with_some_fields(self):
        enrichment = PackageEnrichment(
            source_package="glibc",
            maintainer="Debian Glibc Team",
            purl="pkg:deb/debian/libc6@2.36-9",
        )
        assert enrichment.source_package == "glibc"
        assert enrichment.maintainer == "Debian Glibc Team"
        assert enrichment.purl == "pkg:deb/debian/libc6@2.36-9"

    def test_frozen_cannot_set_attribute(self):
        enrichment = PackageEnrichment(source_package="foo")
        with pytest.raises(dataclasses.FrozenInstanceError):
            enrichment.source_package = "bar"  # type: ignore[misc]

    def test_license_expressions_default_is_empty_list(self):
        enrichment = PackageEnrichment()
        assert enrichment.license_expressions == []


@pytest.mark.unit
class TestEnrichedPackage:
    """Verify EnrichedPackage frozen dataclass behavior."""

    def _make_package(self):
        return IdentifiedPackage(name="libc6", version="2.36-9", architecture="amd64", status="installed")

    def test_construction_with_package_only(self):
        """EnrichedPackage can be created with just a package (enrichment defaults to None)."""
        pkg = self._make_package()
        enriched = EnrichedPackage(package=pkg)
        assert enriched.package is pkg
        assert enriched.enrichment is None

    def test_construction_with_enrichment(self):
        pkg = self._make_package()
        enrichment = PackageEnrichment(source_package="glibc")
        enriched = EnrichedPackage(package=pkg, enrichment=enrichment)
        assert enriched.enrichment is enrichment

    def test_frozen_cannot_set_attribute(self):
        enriched = EnrichedPackage(package=self._make_package())
        with pytest.raises(dataclasses.FrozenInstanceError):
            enriched.enrichment = PackageEnrichment()  # type: ignore[misc]

    def test_missing_package_raises_type_error(self):
        with pytest.raises(TypeError):
            EnrichedPackage()  # type: ignore[call-arg]


@pytest.mark.unit
class TestScanResult:
    """Verify ScanResult frozen dataclass behavior."""

    def test_construction_with_required_fields(self):
        result = ScanResult(
            packages=[],
            strategy="dpkg_metadata",
            diagnostics=[],
            duration_seconds=1.5,
            artifact_path="/some/path",
        )
        assert result.packages == []
        assert result.strategy == "dpkg_metadata"
        assert result.diagnostics == []
        assert result.duration_seconds == 1.5
        assert result.artifact_path == "/some/path"

    def test_default_enriched_packages_is_empty_list(self):
        result = ScanResult(
            packages=[],
            strategy="dpkg_metadata",
            diagnostics=[],
            duration_seconds=0.0,
            artifact_path="/path",
        )
        assert result.enriched_packages == []

    def test_enriched_packages_can_be_provided(self):
        pkg = IdentifiedPackage(name="bash", version="5.2", architecture="amd64", status="installed")
        enriched = EnrichedPackage(package=pkg)
        result = ScanResult(
            packages=[pkg],
            strategy="dpkg_metadata",
            diagnostics=[],
            duration_seconds=2.0,
            artifact_path="/dir",
            enriched_packages=[enriched],
        )
        assert result.enriched_packages == [enriched]

    def test_frozen_cannot_set_attribute(self):
        result = ScanResult(
            packages=[],
            strategy="dpkg_metadata",
            diagnostics=[],
            duration_seconds=0.0,
            artifact_path="/p",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.artifact_path = "/other"  # type: ignore[misc]

    def test_missing_packages_raises_type_error(self):
        with pytest.raises(TypeError):
            ScanResult(  # type: ignore[call-arg]
                strategy="dpkg_metadata",
                diagnostics=[],
                duration_seconds=0.0,
                artifact_path="/p",
            )

    def test_missing_strategy_raises_type_error(self):
        with pytest.raises(TypeError):
            ScanResult(  # type: ignore[call-arg]
                packages=[],
                diagnostics=[],
                duration_seconds=0.0,
                artifact_path="/p",
            )

    def test_missing_diagnostics_raises_type_error(self):
        with pytest.raises(TypeError):
            ScanResult(  # type: ignore[call-arg]
                packages=[],
                strategy="dpkg_metadata",
                duration_seconds=0.0,
                artifact_path="/p",
            )

    def test_missing_duration_seconds_raises_type_error(self):
        with pytest.raises(TypeError):
            ScanResult(  # type: ignore[call-arg]
                packages=[],
                strategy="dpkg_metadata",
                diagnostics=[],
                artifact_path="/p",
            )

    def test_missing_artifact_path_raises_type_error(self):
        with pytest.raises(TypeError):
            ScanResult(  # type: ignore[call-arg]
                packages=[],
                strategy="dpkg_metadata",
                diagnostics=[],
                duration_seconds=0.0,
            )


@pytest.mark.unit
class TestDetectArtifactType:
    """Verify detect_artifact_type maps extensions and directories correctly."""

    # --- Known single extensions ---

    def test_iso_extension(self):
        """A .iso file is detected as ArtifactType.ISO."""
        assert detect_artifact_type("/path/to/image.iso") == ArtifactType.ISO

    def test_qcow2_extension(self):
        """A .qcow2 file is detected as ArtifactType.QCOW2."""
        assert detect_artifact_type("/path/to/disk.qcow2") == ArtifactType.QCOW2

    def test_img_extension(self):
        """A .img file is detected as ArtifactType.IMG."""
        assert detect_artifact_type("/path/to/disk.img") == ArtifactType.IMG

    def test_tar_extension(self):
        """A .tar file is detected as ArtifactType.DOCKER."""
        assert detect_artifact_type("/path/to/container.tar") == ArtifactType.DOCKER

    def test_tgz_extension(self):
        """A .tgz file is detected as ArtifactType.DOCKER."""
        assert detect_artifact_type("/path/to/container.tgz") == ArtifactType.DOCKER

    def test_oci_extension(self):
        """A .oci file is detected as ArtifactType.OCI."""
        assert detect_artifact_type("/path/to/bundle.oci") == ArtifactType.OCI

    def test_ami_extension(self):
        """A .ami file is detected as ArtifactType.AMI."""
        assert detect_artifact_type("/path/to/machine.ami") == ArtifactType.AMI

    # --- Compound extensions ---

    def test_tar_gz_compound_extension(self):
        """A .tar.gz file is detected as ArtifactType.DOCKER."""
        assert detect_artifact_type("/path/to/container.tar.gz") == ArtifactType.DOCKER

    def test_tar_gz_with_name_prefix(self):
        """A file like archive.tar.gz is detected as DOCKER."""
        assert detect_artifact_type("my-image.tar.gz") == ArtifactType.DOCKER

    # --- Directory detection ---

    def test_directory_path(self, tmp_path):
        """An actual directory is detected as ArtifactType.DIRECTORY."""
        assert detect_artifact_type(str(tmp_path)) == ArtifactType.DIRECTORY

    # --- Fallback for unknown extensions ---

    def test_unknown_extension_falls_back_to_directory(self):
        """An unrecognized extension falls back to ArtifactType.DIRECTORY."""
        assert detect_artifact_type("/path/to/file.xyz") == ArtifactType.DIRECTORY

    def test_no_extension_falls_back_to_directory(self):
        """A path with no extension (and not an existing directory) falls back to DIRECTORY."""
        assert detect_artifact_type("/nonexistent/path/noext") == ArtifactType.DIRECTORY

    # --- Case insensitivity ---

    def test_uppercase_iso_extension(self):
        """Extension matching is case-insensitive (.ISO maps to ISO)."""
        assert detect_artifact_type("/path/to/IMAGE.ISO") == ArtifactType.ISO

    def test_mixed_case_qcow2_extension(self):
        """Extension matching is case-insensitive (.Qcow2 maps to QCOW2)."""
        assert detect_artifact_type("/path/to/disk.Qcow2") == ArtifactType.QCOW2

    def test_uppercase_tar_gz_extension(self):
        """Compound extension matching is case-insensitive (.TAR.GZ maps to DOCKER)."""
        assert detect_artifact_type("/path/to/image.TAR.GZ") == ArtifactType.DOCKER
