"""Entity models for scan sessions and SBOM documents in metadata.db."""

from __future__ import annotations

import enum
from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from debcraft.infrastructure.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from debcraft.infrastructure.models.metadata import RepositorySnapshot


class ScanState(enum.Enum):
    """Lifecycle states for a scan session."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ScanSession(Base, TimestampMixin):
    """A complete analysis run linked to a repository snapshot."""

    __tablename__ = "scan_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("repository_snapshots.id"),
        nullable=False,
        index=True,
    )
    state: Mapped[ScanState] = mapped_column(
        Enum(ScanState),
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    snapshot: Mapped[RepositorySnapshot] = relationship(back_populates="scan_sessions")
    sbom_documents: Mapped[list[SBOMDocument]] = relationship(
        back_populates="scan_session",
        cascade="all, delete-orphan",
    )


class SBOMDocument(Base, TimestampMixin):
    """A generated Software Bill of Materials document."""

    __tablename__ = "sbom_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_session_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("scan_sessions.id"),
        nullable=False,
        index=True,
    )
    format: Mapped[str] = mapped_column(String(32), nullable=False)
    content_path: Mapped[str] = mapped_column(String(4096), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    scan_session: Mapped[ScanSession] = relationship(back_populates="sbom_documents")
