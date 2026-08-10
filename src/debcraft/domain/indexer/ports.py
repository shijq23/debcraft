"""Port interfaces (Protocols) for indexer domain dependencies.

These define the contracts that infrastructure adapters must satisfy,
allowing the domain service to remain decoupled from concrete implementations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from debcraft.domain.indexer.values import FileOwnership, PackageMetadata, SourcePackageMetadata


class IndexingRecordView(Protocol):
    """Read-only view of an indexing record for incremental skip logic."""

    @property
    def indexed_sha256(self) -> str:
        """SHA256 hash of the file content when it was last indexed."""
        ...

    @property
    def parser_version(self) -> int:
        """Parser version used when the file was last indexed."""
        ...


class FileInfo(Protocol):
    """Read-only view of a verified repository file."""

    @property
    def id(self) -> int:
        """Unique identifier of the repository file."""
        ...

    @property
    def url(self) -> str:
        """Remote URL of the repository file."""
        ...

    @property
    def sha256(self) -> str:
        """SHA256 hash of the file content."""
        ...

    @property
    def local_path(self) -> str:
        """Local filesystem path to the cached file."""
        ...

    @property
    def size_bytes(self) -> int:
        """Size of the file in bytes."""
        ...


class FileReader(Protocol):
    """Reads and decompresses cached metadata files."""

    async def read_file(self, local_path: str) -> str:
        """Read and decompress the file at the given local path."""
        ...


class MetadataRepository(Protocol):
    """Persists indexer domain objects."""

    async def find_or_create_repository(
        self,
        *,
        name: str,
        base_url: str,
        suite: str,
        component: str,
    ) -> int:
        """Find or create a repository record, returning its ID."""
        ...

    async def create_snapshot(
        self,
        *,
        repository_id: int,
        schema_version: int,
    ) -> int:
        """Create an unpublished repository snapshot, returning its ID."""
        ...

    async def publish_snapshot(self, snapshot_id: int) -> None:
        """Mark a snapshot as published."""
        ...

    async def add_package_instances(
        self,
        *,
        snapshot_id: int,
        packages: Sequence[PackageMetadata],
        base_url: str,
    ) -> int:
        """Persist binary package instances, returning the count added."""
        ...

    async def add_source_packages(
        self,
        *,
        packages: Sequence[SourcePackageMetadata],
    ) -> int:
        """Persist source packages, returning the count added."""
        ...

    async def replace_file_ownerships(
        self,
        *,
        snapshot_id: int,
        ownerships: Sequence[FileOwnership],
    ) -> int:
        """Replace file ownerships for a snapshot, returning the count added."""
        ...


class MirrorFileRepository(Protocol):
    """Queries/updates repository file states for the indexer."""

    async def get_verified_files(
        self,
        *,
        repository_name: str,
    ) -> Sequence[FileInfo]:
        """Return files eligible for indexing (VERIFIED or INDEXED state) for the given repository."""
        ...

    async def get_indexing_record(
        self,
        file_id: int,
    ) -> IndexingRecordView | None:
        """Return the indexing record for a file, or None if never indexed."""
        ...

    async def mark_indexed(
        self,
        *,
        file_id: int,
        parser_version: int,
        sha256: str,
    ) -> None:
        """Record that a file has been successfully indexed."""
        ...
