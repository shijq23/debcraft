"""Entity models for metadata.db — authoritative package metadata."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from debcraft.infrastructure.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from debcraft.infrastructure.models.scan import ScanSession


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
