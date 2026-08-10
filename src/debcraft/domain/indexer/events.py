"""Domain events for the repository indexer lifecycle.

Published by the IndexerService to notify other components of indexing
progress and outcomes.
"""

from dataclasses import dataclass

from debcraft.platform.contracts.events import DomainEvent


@dataclass(frozen=True)
class IndexingStarted(DomainEvent):
    """Published when indexing begins for a repository."""

    event_type: str = "indexing.started"
    repository_name: str = ""
    snapshot_id: int = 0


@dataclass(frozen=True)
class IndexingCompleted(DomainEvent):
    """Published when indexing completes successfully for a repository."""

    event_type: str = "indexing.completed"
    repository_name: str = ""
    snapshot_id: int = 0
    packages_indexed: int = 0


@dataclass(frozen=True)
class IndexingFailed(DomainEvent):
    """Published when indexing fails for a repository."""

    event_type: str = "indexing.failed"
    repository_name: str = ""
    snapshot_id: int = 0
    error: str = ""
