"""Entity models for mirror.db — repository file tracking."""

import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from debcraft.infrastructure.models.base import Base, TimestampMixin


class RepositoryFileState(enum.Enum):
    """Lifecycle states for a file discovered in a remote Debian repository."""

    DISCOVERED = "DISCOVERED"
    QUEUED = "QUEUED"
    DOWNLOADING = "DOWNLOADING"
    DOWNLOADED = "DOWNLOADED"
    VERIFIED = "VERIFIED"
    INDEXED = "INDEXED"
    FAILED = "FAILED"


class RepositoryFile(Base, TimestampMixin):
    """A file discovered in a remote Debian repository."""

    __tablename__ = "repository_files"
    __table_args__ = (
        Index("ix_repository_files_url", "url", unique=True),
        Index("ix_repository_files_sha256", "sha256"),
        Index("ix_repository_files_state", "state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[RepositoryFileState] = mapped_column(Enum(RepositoryFileState), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    local_path: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(128), nullable=True)


class SyncSession(Base, TimestampMixin):
    """Tracks a synchronization session for observability and resumption."""

    __tablename__ = "sync_sessions"
    __table_args__ = (
        Index("ix_sync_sessions_session_id", "session_id", unique=True),
        Index("ix_sync_sessions_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    repository_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # running, completed, failed, cancelled
    files_downloaded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bytes_transferred: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
