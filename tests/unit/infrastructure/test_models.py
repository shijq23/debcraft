"""Unit tests for entity models.

Verifies correct __tablename__ values, column definitions, unique constraints,
and enum values for all infrastructure entity models.
"""

from __future__ import annotations

import pytest
from sqlalchemy import UniqueConstraint

from debcraft.infrastructure.models.cache import (
    CachedEnrichment,
    ChecksumCache,
    NormalizedLicense,
    ParsedDep5,
)
from debcraft.infrastructure.models.metadata import (
    LicenseExpression,
    PackageInstance,
    Repository,
    RepositorySnapshot,
    SourcePackage,
)
from debcraft.infrastructure.models.mirror import RepositoryFile, RepositoryFileState
from debcraft.infrastructure.models.scan import SBOMDocument, ScanSession, ScanState


@pytest.mark.unit
@pytest.mark.storage
class TestTableNames:
    """Verify correct __tablename__ values for all entity models."""

    def test_repository_file_tablename(self) -> None:
        assert RepositoryFile.__tablename__ == "repository_files"

    def test_repository_tablename(self) -> None:
        assert Repository.__tablename__ == "repositories"

    def test_repository_snapshot_tablename(self) -> None:
        assert RepositorySnapshot.__tablename__ == "repository_snapshots"

    def test_package_instance_tablename(self) -> None:
        assert PackageInstance.__tablename__ == "package_instances"

    def test_source_package_tablename(self) -> None:
        assert SourcePackage.__tablename__ == "source_packages"

    def test_license_expression_tablename(self) -> None:
        assert LicenseExpression.__tablename__ == "license_expressions"

    def test_scan_session_tablename(self) -> None:
        assert ScanSession.__tablename__ == "scan_sessions"

    def test_sbom_document_tablename(self) -> None:
        assert SBOMDocument.__tablename__ == "sbom_documents"

    def test_parsed_dep5_tablename(self) -> None:
        assert ParsedDep5.__tablename__ == "parsed_dep5"

    def test_normalized_license_tablename(self) -> None:
        assert NormalizedLicense.__tablename__ == "normalized_licenses"

    def test_checksum_cache_tablename(self) -> None:
        assert ChecksumCache.__tablename__ == "checksum_cache"


@pytest.mark.unit
@pytest.mark.storage
class TestRepositoryFileStateEnum:
    """Verify RepositoryFileState has exactly 7 values with correct names."""

    def test_has_seven_values(self) -> None:
        assert len(RepositoryFileState) == 7

    def test_discovered_value(self) -> None:
        assert RepositoryFileState.DISCOVERED.value == "DISCOVERED"

    def test_queued_value(self) -> None:
        assert RepositoryFileState.QUEUED.value == "QUEUED"

    def test_downloading_value(self) -> None:
        assert RepositoryFileState.DOWNLOADING.value == "DOWNLOADING"

    def test_downloaded_value(self) -> None:
        assert RepositoryFileState.DOWNLOADED.value == "DOWNLOADED"

    def test_verified_value(self) -> None:
        assert RepositoryFileState.VERIFIED.value == "VERIFIED"

    def test_indexed_value(self) -> None:
        assert RepositoryFileState.INDEXED.value == "INDEXED"

    def test_failed_value(self) -> None:
        assert RepositoryFileState.FAILED.value == "FAILED"

    def test_all_expected_values_present(self) -> None:
        expected = {"DISCOVERED", "QUEUED", "DOWNLOADING", "DOWNLOADED", "VERIFIED", "INDEXED", "FAILED"}
        actual = {member.value for member in RepositoryFileState}
        assert actual == expected


@pytest.mark.unit
@pytest.mark.storage
class TestScanStateEnum:
    """Verify ScanState has exactly 4 values with correct names."""

    def test_has_four_values(self) -> None:
        assert len(ScanState) == 4

    def test_pending_value(self) -> None:
        assert ScanState.PENDING.value == "PENDING"

    def test_running_value(self) -> None:
        assert ScanState.RUNNING.value == "RUNNING"

    def test_completed_value(self) -> None:
        assert ScanState.COMPLETED.value == "COMPLETED"

    def test_failed_value(self) -> None:
        assert ScanState.FAILED.value == "FAILED"

    def test_all_expected_values_present(self) -> None:
        expected = {"PENDING", "RUNNING", "COMPLETED", "FAILED"}
        actual = {member.value for member in ScanState}
        assert actual == expected


@pytest.mark.unit
@pytest.mark.storage
class TestUniqueConstraints:
    """Verify unique constraints are present in SQLAlchemy table args."""

    def test_package_instance_has_natural_key_constraint(self) -> None:
        """PackageInstance has unique constraint on (package_name, version, architecture, filename)."""
        constraints = [arg for arg in PackageInstance.__table_args__ if isinstance(arg, UniqueConstraint)]
        assert len(constraints) == 1
        constraint = constraints[0]
        column_names = {col.name for col in constraint.columns}
        assert column_names == {"package_name", "version", "architecture", "filename"}

    def test_source_package_has_natural_key_constraint(self) -> None:
        """SourcePackage has unique constraint on (name, version)."""
        constraints = [arg for arg in SourcePackage.__table_args__ if isinstance(arg, UniqueConstraint)]
        assert len(constraints) == 1
        constraint = constraints[0]
        column_names = {col.name for col in constraint.columns}
        assert column_names == {"name", "version"}

    def test_repository_has_name_unique_constraint(self) -> None:
        """Repository has unique constraint on (name)."""
        constraints = [arg for arg in Repository.__table_args__ if isinstance(arg, UniqueConstraint)]
        assert len(constraints) == 1
        constraint = constraints[0]
        column_names = {col.name for col in constraint.columns}
        assert column_names == {"name"}


@pytest.mark.unit
@pytest.mark.storage
class TestColumnDefinitions:
    """Verify key column definitions exist on entity models."""

    def test_repository_file_has_required_columns(self) -> None:
        columns = {col.name for col in RepositoryFile.__table__.columns}
        expected = {
            "id",
            "url",
            "sha256",
            "size_bytes",
            "state",
            "retry_count",
            "local_path",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(columns)

    def test_package_instance_has_required_columns(self) -> None:
        columns = {col.name for col in PackageInstance.__table__.columns}
        expected = {
            "id",
            "package_name",
            "version",
            "architecture",
            "filename",
            "sha256",
            "size_bytes",
            "snapshot_id",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(columns)

    def test_source_package_has_required_columns(self) -> None:
        columns = {col.name for col in SourcePackage.__table__.columns}
        expected = {"id", "name", "version", "maintainer", "created_at", "updated_at"}
        assert expected.issubset(columns)

    def test_repository_has_required_columns(self) -> None:
        columns = {col.name for col in Repository.__table__.columns}
        expected = {"id", "name", "base_url", "suite", "component", "created_at", "updated_at"}
        assert expected.issubset(columns)

    def test_repository_snapshot_has_required_columns(self) -> None:
        columns = {col.name for col in RepositorySnapshot.__table__.columns}
        expected = {"id", "repository_id", "schema_version", "captured_at", "published", "created_at", "updated_at"}
        assert expected.issubset(columns)

    def test_license_expression_has_required_columns(self) -> None:
        columns = {col.name for col in LicenseExpression.__table__.columns}
        expected = {"id", "package_id", "expression", "source", "created_at", "updated_at"}
        assert expected.issubset(columns)

    def test_scan_session_has_required_columns(self) -> None:
        columns = {col.name for col in ScanSession.__table__.columns}
        expected = {"id", "snapshot_id", "state", "started_at", "completed_at", "created_at", "updated_at"}
        assert expected.issubset(columns)

    def test_sbom_document_has_required_columns(self) -> None:
        columns = {col.name for col in SBOMDocument.__table__.columns}
        expected = {"id", "scan_session_id", "format", "content_path", "sha256", "created_at", "updated_at"}
        assert expected.issubset(columns)

    def test_parsed_dep5_has_required_columns(self) -> None:
        columns = {col.name for col in ParsedDep5.__table__.columns}
        expected = {"id", "source_sha256", "parsed_ast", "valid", "created_at", "updated_at"}
        assert expected.issubset(columns)

    def test_normalized_license_has_required_columns(self) -> None:
        columns = {col.name for col in NormalizedLicense.__table__.columns}
        expected = {"id", "raw_expression", "normalized_expression", "valid", "created_at", "updated_at"}
        assert expected.issubset(columns)

    def test_checksum_cache_has_required_columns(self) -> None:
        columns = {col.name for col in ChecksumCache.__table__.columns}
        expected = {"id", "content_sha256", "computed_hash", "valid", "created_at", "updated_at"}
        assert expected.issubset(columns)

    def test_cached_enrichment_has_required_columns(self) -> None:
        columns = {col.name for col in CachedEnrichment.__table__.columns}
        expected = {
            "id",
            "package_name",
            "version",
            "architecture",
            "snapshot_id",
            "source_package",
            "maintainer",
            "homepage",
            "depends",
            "section",
            "priority",
            "description",
            "sha256",
            "download_url",
            "purl",
            "license_expressions_json",
            "local_deb_path",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(columns)

    def test_cached_enrichment_has_unique_constraint(self) -> None:
        constraints = [c for c in CachedEnrichment.__table__.constraints if isinstance(c, UniqueConstraint)]
        assert len(constraints) == 1
        col_names = {col.name for col in constraints[0].columns}
        assert col_names == {"package_name", "version", "architecture", "snapshot_id"}

    def test_cached_enrichment_has_pkg_ver_index(self) -> None:
        from sqlalchemy import Index as SaIndex

        index_names = [arg.name for arg in CachedEnrichment.__table_args__ if isinstance(arg, SaIndex)]
        assert "ix_cached_enrichment_pkg_ver" in index_names

    def test_repository_file_id_is_primary_key(self) -> None:
        id_col = RepositoryFile.__table__.columns["id"]
        assert id_col.primary_key

    def test_package_instance_id_is_primary_key(self) -> None:
        id_col = PackageInstance.__table__.columns["id"]
        assert id_col.primary_key

    def test_repository_file_sha256_is_indexed(self) -> None:
        """RepositoryFile sha256 column has an index defined."""
        # Index is in __table_args__ as an explicit Index object
        from sqlalchemy import Index as SaIndex

        index_names = [arg.name for arg in RepositoryFile.__table_args__ if isinstance(arg, SaIndex)]
        assert "ix_repository_files_sha256" in index_names

    def test_package_instance_sha256_is_indexed(self) -> None:
        """PackageInstance sha256 column is indexed via mapped_column."""
        sha256_col = PackageInstance.__table__.columns["sha256"]
        assert sha256_col.index is True

    def test_sbom_document_sha256_is_indexed(self) -> None:
        """SBOMDocument sha256 column is indexed via mapped_column."""
        sha256_col = SBOMDocument.__table__.columns["sha256"]
        assert sha256_col.index is True
