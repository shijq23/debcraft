"""Domain events for the repository mirror infrastructure."""

from dataclasses import dataclass

from debcraft.platform.contracts.events import DomainEvent


@dataclass(frozen=True)
class MirrorSyncStartedEvent(DomainEvent):
    """Published when a synchronization session begins."""

    event_type: str = "mirror.sync.started"
    repository_name: str = ""
    session_id: str = ""
    suites: tuple[str, ...] = ()


@dataclass(frozen=True)
class MirrorSyncCompletedEvent(DomainEvent):
    """Published when synchronization completes successfully."""

    event_type: str = "mirror.sync.completed"
    repository_name: str = ""
    session_id: str = ""
    files_downloaded: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    bytes_transferred: int = 0
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class MirrorSyncFailedEvent(DomainEvent):
    """Published when synchronization fails."""

    event_type: str = "mirror.sync.failed"
    repository_name: str = ""
    session_id: str = ""
    error_message: str = ""
    files_failed: int = 0


@dataclass(frozen=True)
class SnapshotPublishedEvent(DomainEvent):
    """Published when a RepositorySnapshot is published."""

    event_type: str = "mirror.snapshot.published"
    snapshot_id: int = 0
    repository_name: str = ""
    captured_at: str = ""  # ISO format
    verified_file_count: int = 0
    failed_file_count: int = 0
