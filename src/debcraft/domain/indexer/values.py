"""Value objects for the indexer domain layer.

Immutable dataclasses representing parsed metadata from Debian
repository index files. These carry no behavior beyond field access
and are produced by the indexer parsers for persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PackageMetadata:
    """Full binary package metadata extracted from a Packages file.

    Attributes:
        package_name: Binary package name (e.g. "libfoo-dev").
        version: Package version string.
        architecture: Target architecture (e.g. "amd64", "all").
        filename: Path to the .deb file relative to repository root.
        sha256: Hex-encoded SHA256 digest of the .deb file.
        size_bytes: Size of the .deb file in bytes.
        source_package: Source package name (inferred from package name if Source absent).
        source_version: Source version (inferred from binary version if Source has no parens).
        homepage: Upstream project URL, if declared.
        maintainer: Package maintainer identity.
        depends: Runtime dependencies as declared in the Depends field.
        provides: Virtual packages provided by this package.
        section: Archive section (e.g. "libs", "devel").
        priority: Package priority (e.g. "optional", "required").
        description: Short description of the package.
    """

    package_name: str
    version: str
    architecture: str
    filename: str
    sha256: str
    size_bytes: int
    source_package: str
    source_version: str
    homepage: str | None = None
    maintainer: str | None = None
    depends: str | None = None
    provides: str | None = None
    section: str | None = None
    priority: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class SourcePackageMetadata:
    """Source package metadata extracted from a Sources file.

    Attributes:
        name: Source package name.
        version: Source package version string.
        maintainer: Package maintainer identity.
        uploaders: List of additional uploaders.
        section: Archive section.
        homepage: Upstream project URL.
        build_depends: Build-time dependencies as declared.
        binary_packages: List of binary package names built from this source.
    """

    name: str
    version: str
    maintainer: str | None = None
    uploaders: list[str] = field(default_factory=list)
    section: str | None = None
    homepage: str | None = None
    build_depends: str | None = None
    binary_packages: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FileOwnership:
    """Mapping from a filesystem path to the owning package.

    Attributes:
        path: Filesystem path as listed in the Contents file.
        qualified_package_name: Section-qualified package name (e.g. "libs/libfoo").
    """

    path: str
    qualified_package_name: str


@dataclass(frozen=True)
class RepositoryIdentity:
    """Repository-level metadata from a Release file.

    Attributes:
        suite: Repository suite (e.g. "bookworm", "jammy").
        codename: Repository codename, if declared.
        origin: Repository origin (e.g. "Debian", "Ubuntu").
        label: Repository label.
        architectures: List of supported architectures.
        components: List of repository components (e.g. "main", "contrib").
        date: Release date string as declared in the file.
    """

    suite: str
    codename: str | None = None
    origin: str | None = None
    label: str | None = None
    architectures: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    date: str | None = None


@dataclass(frozen=True)
class IndexResult:
    """Summary of an indexing run for one repository.

    Attributes:
        repository_name: Name of the repository that was indexed.
        snapshot_id: ID of the RepositorySnapshot created for this run.
        packages_indexed: Number of binary packages successfully indexed.
        source_packages_indexed: Number of source packages successfully indexed.
        file_ownerships_indexed: Number of file ownership records created.
        files_skipped: Number of files skipped due to incremental indexing.
        success: Whether the indexing run completed without fatal errors.
        error: Error description if the run failed, None otherwise.
    """

    repository_name: str
    snapshot_id: int
    packages_indexed: int
    source_packages_indexed: int
    file_ownerships_indexed: int
    files_skipped: int
    success: bool
    error: str | None = None
