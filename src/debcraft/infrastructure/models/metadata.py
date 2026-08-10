"""Entity models for metadata.db — authoritative package metadata."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from debcraft.infrastructure.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from debcraft.infrastructure.models.scan import ScanSession


class FileOwnership(Base, TimestampMixin):
    """File-to-package ownership mapping from Contents files."""

    __tablename__ = "file_ownerships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("repository_snapshots.id"),
        nullable=False,
        index=True,
    )
    file_path: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    package_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)

    snapshot: Mapped[RepositorySnapshot] = relationship(back_populates="file_ownerships")


class IndexingRecord(Base):
    """Tracks which repository files have been indexed and with which parser version."""

    __tablename__ = "indexing_records"
    __table_args__ = (UniqueConstraint("repository_file_id", name="uq_indexing_records_file_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repository_file_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    parser_version: Mapped[int] = mapped_column(Integer, nullable=False)
    indexed_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Repository(Base, TimestampMixin):
    """A Debian package repository (e.g. debian bookworm main)."""

    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("name", name="uq_repositories_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    suite: Mapped[str] = mapped_column(String(128), nullable=False)
    component: Mapped[str] = mapped_column(String(128), nullable=False)

    snapshots: Mapped[list[RepositorySnapshot]] = relationship(
        back_populates="repository",
        cascade="all, delete-orphan",
    )


class RepositorySnapshot(Base, TimestampMixin):
    """Immutable point-in-time capture of repository state."""

    __tablename__ = "repository_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repository_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("repositories.id"),
        nullable=False,
        index=True,
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    repository: Mapped[Repository] = relationship(back_populates="snapshots")
    packages: Mapped[list[PackageInstance]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
    )
    scan_sessions: Mapped[list[ScanSession]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
    )
    file_ownerships: Mapped[list[FileOwnership]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
    )


class PackageInstance(Base, TimestampMixin):
    """Binary package identified by name + version + architecture + filename."""

    __tablename__ = "package_instances"
    __table_args__ = (
        UniqueConstraint(
            "package_name",
            "version",
            "architecture",
            "filename",
            name="uq_package_instances_natural_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    package_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    architecture: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    snapshot_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("repository_snapshots.id"),
        nullable=False,
        index=True,
    )

    source_package: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    homepage: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    maintainer: Mapped[str | None] = mapped_column(String(512), nullable=True)
    depends: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    provides: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    section: Mapped[str | None] = mapped_column(String(64), nullable=True)
    priority: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    download_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    snapshot: Mapped[RepositorySnapshot] = relationship(back_populates="packages")
    license_expressions: Mapped[list[LicenseExpression]] = relationship(
        back_populates="package",
        cascade="all, delete-orphan",
    )


class SourcePackage(Base, TimestampMixin):
    """Debian source package."""

    __tablename__ = "source_packages"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_source_packages_natural_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    maintainer: Mapped[str | None] = mapped_column(String(512), nullable=True)
    uploaders: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    section: Mapped[str | None] = mapped_column(String(64), nullable=True)
    homepage: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    build_depends: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    binary_packages: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    snapshot_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("repository_snapshots.id"),
        nullable=True,
    )


class LicenseExpression(Base, TimestampMixin):
    """SPDX license expression linked to a package instance."""

    __tablename__ = "license_expressions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    package_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("package_instances.id"),
        nullable=False,
        index=True,
    )
    expression: Mapped[str] = mapped_column(String(1024), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)

    package: Mapped[PackageInstance] = relationship(back_populates="license_expressions")
