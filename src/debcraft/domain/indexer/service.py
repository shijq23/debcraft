"""Domain service orchestrating the repository indexing workflow.

The IndexerService coordinates parsing of cached repository metadata files,
incremental skip logic, persistence of domain objects, and event publishing.
All infrastructure dependencies are received via constructor injection.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from debcraft.domain.indexer.contents_parser import ContentsParser
from debcraft.domain.indexer.events import (
    IndexingCompleted,
    IndexingFailed,
    IndexingStarted,
)
from debcraft.domain.indexer.packages_parser import PackagesParser
from debcraft.domain.indexer.release_metadata_parser import ReleaseMetadataParser
from debcraft.domain.indexer.sources_parser import SourcesParser
from debcraft.domain.indexer.values import IndexResult

if TYPE_CHECKING:
    from debcraft.domain.indexer.ports import (
        FileReader,
        IndexingRecordView,
        MetadataRepository,
        MirrorFileRepository,
    )
    from debcraft.platform.contracts.events import EventBus

logger = logging.getLogger(__name__)

# Schema version for snapshots created by this service
_SCHEMA_VERSION = 3


def _infer_file_type(url_or_path: str) -> str:
    """Determine the file type from a URL or filesystem path.

    Classification is based on the filename (last non-empty path segment)
    after stripping compression extensions (.gz, .xz, .bz2).

    Returns one of: "packages", "sources", "contents", "release", or "unknown".
    """
    # Extract the filename: last non-empty segment after splitting on "/"
    segments = url_or_path.split("/")
    filename = ""
    for seg in reversed(segments):
        if seg:
            filename = seg
            break

    if not filename:
        return "unknown"

    # Strip compression extension
    base = filename
    for ext in (".gz", ".xz", ".bz2"):
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
            break

    # Match on the lowercased base filename
    lowered = base.lower()
    if lowered.startswith("packages"):
        return "packages"
    if lowered.startswith("sources"):
        return "sources"
    if lowered.startswith("contents"):
        return "contents"
    if lowered == "release" or lowered == "inrelease":
        return "release"
    return "unknown"


def _compute_download_url(base_url: str, filename: str) -> str:
    """Join repository base URL with a package filename to produce a download URL.

    Ensures exactly one slash separator between base URL and filename.
    """
    return base_url.rstrip("/") + "/" + filename.lstrip("/")


class IndexerService:
    """Orchestrates the indexing workflow for a repository.

    Receives all dependencies via constructor injection:
    - file_reader: reads and decompresses cached files
    - metadata_repository: persists domain objects
    - mirror_file_repository: queries/updates RepositoryFile states
    - event_bus: publishes lifecycle events
    - logger: structured logging
    """

    def __init__(
        self,
        file_reader: FileReader,
        metadata_repository: MetadataRepository,
        mirror_file_repository: MirrorFileRepository,
        event_bus: EventBus,
        indexer_logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the IndexerService with injected dependencies.

        Args:
            file_reader: Reads and decompresses cached metadata files.
            metadata_repository: Persists domain objects to metadata.db.
            mirror_file_repository: Queries/updates RepositoryFile states.
            event_bus: Publishes domain lifecycle events.
            indexer_logger: Optional logger instance; defaults to module logger.
        """
        self._file_reader = file_reader
        self._metadata_repository = metadata_repository
        self._mirror_file_repository = mirror_file_repository
        self._event_bus = event_bus
        self._logger = indexer_logger or logger

        # Parsers are pure domain objects, instantiated directly
        self._packages_parser = PackagesParser()
        self._sources_parser = SourcesParser()
        self._contents_parser = ContentsParser()
        self._release_metadata_parser = ReleaseMetadataParser()

    async def index_repository(
        self,
        repository_name: str,
        base_url: str,
        suite: str,
        component: str,
    ) -> IndexResult:
        """Index a single repository: parse cached files and persist metadata.

        Workflow:
        1. Find or create a Repository record
        2. Create a new unpublished RepositorySnapshot
        3. Publish IndexingStarted event
        4. Get verified files from mirror DB
        5. Sort files deterministically by (repository_name, file_type, file_path)
        6. For each file: check incremental skip, read, parse, persist, mark indexed
        7. Publish snapshot (set published=True)
        8. Publish IndexingCompleted event
        9. Return IndexResult summary

        Args:
            repository_name: Name of the repository to index.
            base_url: Base URL of the repository (for computing download URLs).
            suite: Repository suite (e.g. "bookworm").
            component: Repository component (e.g. "main").

        Returns:
            IndexResult summarizing the indexing run.
        """
        snapshot_id = 0
        packages_indexed = 0
        source_packages_indexed = 0
        file_ownerships_indexed = 0
        files_skipped = 0

        try:
            # Step 1: Find or create repository
            repository_id = await self._metadata_repository.find_or_create_repository(
                name=repository_name,
                base_url=base_url,
                suite=suite,
                component=component,
            )

            # Step 2: Create unpublished snapshot
            snapshot_id = await self._metadata_repository.create_snapshot(
                repository_id=repository_id,
                schema_version=_SCHEMA_VERSION,
            )

            # Step 3: Publish IndexingStarted event
            await self._event_bus.publish(
                IndexingStarted(
                    repository_name=repository_name,
                    snapshot_id=snapshot_id,
                )
            )

            # Step 4: Get verified files from mirror DB
            verified_files = await self._mirror_file_repository.get_verified_files(repository_name=repository_name)

            if not verified_files:
                self._logger.info(
                    "No verified files to index for repository %s",
                    repository_name,
                )
                # Still publish the snapshot (empty but valid)
                await self._metadata_repository.publish_snapshot(snapshot_id)
                await self._event_bus.publish(
                    IndexingCompleted(
                        repository_name=repository_name,
                        snapshot_id=snapshot_id,
                        packages_indexed=0,
                    )
                )
                return IndexResult(
                    repository_name=repository_name,
                    snapshot_id=snapshot_id,
                    packages_indexed=0,
                    source_packages_indexed=0,
                    file_ownerships_indexed=0,
                    files_skipped=0,
                    success=True,
                )

            # Step 5: Sort files deterministically
            sorted_files = sorted(
                verified_files,
                key=lambda f: (
                    repository_name,
                    _infer_file_type(f.url),
                    f.url,
                ),
            )

            # Step 6: Process each file
            for file_info in sorted_files:
                try:
                    file_type = _infer_file_type(file_info.url)

                    # Step 6a: Incremental indexing check
                    parser_version = self._get_parser_version(file_type)
                    indexing_record = await self._mirror_file_repository.get_indexing_record(file_info.id)

                    if self._should_skip(indexing_record, file_info.sha256, parser_version):
                        files_skipped += 1
                        self._logger.debug(
                            "Skipping already-indexed file: %s",
                            file_info.url,
                        )
                        continue

                    # Skip unknown file types before any I/O
                    if file_type == "unknown":
                        self._logger.debug("Skipping unknown file type: %s", file_info.url)
                        continue

                    # Step 6b: Read file content
                    content = await self._file_reader.read_file(file_info.local_path)

                    # Steps 6c-6e: Parse and persist based on file type
                    if file_type == "packages":
                        packages = self._packages_parser.parse(content)
                        # Compute download URLs
                        packages_with_urls = []
                        for pkg in packages:
                            download_url = _compute_download_url(base_url, pkg.filename)
                            packages_with_urls.append((pkg, download_url))

                        count = await self._metadata_repository.add_package_instances(
                            snapshot_id=snapshot_id,
                            packages=packages,
                            base_url=base_url,
                        )
                        packages_indexed += count

                    elif file_type == "sources":
                        source_packages = self._sources_parser.parse(content)
                        count = await self._metadata_repository.add_source_packages(
                            packages=source_packages,
                        )
                        source_packages_indexed += count

                    elif file_type == "contents":
                        ownerships = self._contents_parser.parse(content)
                        count = await self._metadata_repository.replace_file_ownerships(
                            snapshot_id=snapshot_id,
                            ownerships=ownerships,
                        )
                        file_ownerships_indexed += count

                    elif file_type == "release":
                        # Release files provide repository identity metadata
                        # but don't produce persistable records themselves
                        self._release_metadata_parser.parse(content)
                        self._logger.debug("Parsed Release metadata from: %s", file_info.url)

                    # Step 6f: Mark file as indexed
                    await self._mirror_file_repository.mark_indexed(
                        file_id=file_info.id,
                        parser_version=parser_version,
                        sha256=file_info.sha256,
                    )

                except Exception:
                    self._logger.exception("Error processing file: %s", file_info.url)
                    # Allow other files to proceed
                    continue

            # Step 7: Publish snapshot
            await self._metadata_repository.publish_snapshot(snapshot_id)

            # Step 8: Publish IndexingCompleted event
            await self._event_bus.publish(
                IndexingCompleted(
                    repository_name=repository_name,
                    snapshot_id=snapshot_id,
                    packages_indexed=packages_indexed,
                )
            )

            self._logger.info(
                "Indexing completed for %s: %d packages, %d source packages, %d file ownerships, %d files skipped",
                repository_name,
                packages_indexed,
                source_packages_indexed,
                file_ownerships_indexed,
                files_skipped,
            )

        except Exception as exc:
            error_msg = f"Indexing failed for {repository_name}: {exc}"
            self._logger.exception(error_msg)

            # Publish failure event
            await self._event_bus.publish(
                IndexingFailed(
                    repository_name=repository_name,
                    snapshot_id=snapshot_id,
                    error=str(exc),
                )
            )

            return IndexResult(
                repository_name=repository_name,
                snapshot_id=snapshot_id,
                packages_indexed=packages_indexed,
                source_packages_indexed=source_packages_indexed,
                file_ownerships_indexed=file_ownerships_indexed,
                files_skipped=files_skipped,
                success=False,
                error=str(exc),
            )

        # Step 9: Return IndexResult
        return IndexResult(
            repository_name=repository_name,
            snapshot_id=snapshot_id,
            packages_indexed=packages_indexed,
            source_packages_indexed=source_packages_indexed,
            file_ownerships_indexed=file_ownerships_indexed,
            files_skipped=files_skipped,
            success=True,
        )

    def _get_parser_version(self, file_type: str) -> int:
        """Get the parser version for a given file type.

        Args:
            file_type: One of "packages", "sources", "contents", "release".

        Returns:
            The PARSER_VERSION constant from the appropriate parser.
        """
        version_map = {
            "packages": self._packages_parser.PARSER_VERSION,
            "sources": self._sources_parser.PARSER_VERSION,
            "contents": self._contents_parser.PARSER_VERSION,
        }
        # Release files and unknown types use version 1 as default
        return version_map.get(file_type, 1)

    def _should_skip(
        self,
        indexing_record: IndexingRecordView | None,
        current_sha256: str,
        current_parser_version: int,
    ) -> bool:
        """Determine whether a file should be skipped (incremental indexing).

        A file is skipped if and only if:
        - An indexing record exists for it
        - The recorded SHA256 matches the current file SHA256
        - The recorded parser version matches the current parser version

        Args:
            indexing_record: The existing indexing record, or None if never indexed.
            current_sha256: The current SHA256 of the cached file.
            current_parser_version: The current parser version.

        Returns:
            True if the file should be skipped, False otherwise.
        """
        if indexing_record is None:
            return False

        return (
            indexing_record.indexed_sha256 == current_sha256
            and indexing_record.parser_version == current_parser_version
        )
