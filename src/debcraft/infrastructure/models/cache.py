"""Entity models for cache.db — recomputable derived data."""

from sqlalchemy import Boolean, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from debcraft.infrastructure.models.base import Base, TimestampMixin


class ParsedDep5(Base, TimestampMixin):
    """Cached DEP-5 AST keyed by source SHA256."""

    __tablename__ = "parsed_dep5"
    __table_args__ = (Index("ix_parsed_dep5_source_sha256", "source_sha256", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    parsed_ast: Mapped[str] = mapped_column(Text, nullable=False)
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class NormalizedLicense(Base, TimestampMixin):
    """Normalized SPDX expression keyed by raw expression string."""

    __tablename__ = "normalized_licenses"
    __table_args__ = (Index("ix_normalized_licenses_raw_expression", "raw_expression", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_expression: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    normalized_expression: Mapped[str] = mapped_column(String(1024), nullable=False)
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ChecksumCache(Base, TimestampMixin):
    """Pre-computed SHA256 for expensive-to-hash content blobs."""

    __tablename__ = "checksum_cache"
    __table_args__ = (Index("ix_checksum_cache_content_sha256", "content_sha256", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    computed_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ParsedDebPackage(Base, TimestampMixin):
    """Cached .deb parse result keyed by file SHA256 and parser version."""

    __tablename__ = "parsed_deb_packages"
    __table_args__ = (
        UniqueConstraint("sha256", "parser_version", name="uq_parsed_deb_sha256_version"),
        Index("ix_parsed_deb_sha256", "sha256"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[int] = mapped_column(Integer, nullable=False)
    control_metadata: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    dependencies: Mapped[str] = mapped_column(Text, nullable=False)  # JSON array
    copyright_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_listing: Mapped[str] = mapped_column(Text, nullable=False)  # JSON array
