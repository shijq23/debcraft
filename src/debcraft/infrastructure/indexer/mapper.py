"""Mapper converting domain value objects to SQLAlchemy ORM models.

This module lives in the infrastructure layer and bridges the domain/infrastructure
boundary by converting frozen dataclasses produced by parsers into mutable
SQLAlchemy model instances ready for persistence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from debcraft.infrastructure.models.metadata import FileOwnership as FileOwnershipModel
from debcraft.infrastructure.models.metadata import PackageInstance, SourcePackage

if TYPE_CHECKING:
    from debcraft.domain.indexer.values import FileOwnership, PackageMetadata, SourcePackageMetadata


class IndexerMapper:
    """Converts domain value objects to SQLAlchemy ORM models."""

    def package_metadata_to_model(
        self,
        metadata: PackageMetadata,
        snapshot_id: int,
        download_url: str,
    ) -> PackageInstance:
        """Map a PackageMetadata domain value object to a PackageInstance ORM model.

        Args:
            metadata: Parsed binary package metadata from the domain layer.
            snapshot_id: ID of the RepositorySnapshot this package belongs to.
            download_url: Computed download URL (base_url + "/" + filename).

        Returns:
            A PackageInstance model instance ready for database insertion.
        """
        return PackageInstance(
            package_name=metadata.package_name,
            version=metadata.version,
            architecture=metadata.architecture,
            filename=metadata.filename,
            sha256=metadata.sha256,
            size_bytes=metadata.size_bytes,
            snapshot_id=snapshot_id,
            source_package=metadata.source_package,
            source_version=metadata.source_version,
            homepage=metadata.homepage,
            maintainer=metadata.maintainer,
            depends=metadata.depends,
            provides=metadata.provides,
            section=metadata.section,
            priority=metadata.priority,
            description=metadata.description,
            download_url=download_url,
        )

    def source_metadata_to_model(
        self,
        metadata: SourcePackageMetadata,
        snapshot_id: int | None = None,
    ) -> SourcePackage:
        """Map a SourcePackageMetadata domain value object to a SourcePackage ORM model.

        List fields (uploaders, binary_packages) are joined into comma-separated
        strings for storage in the database.

        Args:
            metadata: Parsed source package metadata from the domain layer.
            snapshot_id: Optional ID of the RepositorySnapshot this source belongs to.

        Returns:
            A SourcePackage model instance ready for database insertion.
        """
        return SourcePackage(
            name=metadata.name,
            version=metadata.version,
            maintainer=metadata.maintainer,
            uploaders=", ".join(metadata.uploaders) if metadata.uploaders else None,
            section=metadata.section,
            homepage=metadata.homepage,
            build_depends=metadata.build_depends,
            binary_packages=", ".join(metadata.binary_packages) if metadata.binary_packages else None,
            snapshot_id=snapshot_id,
        )

    def file_ownership_to_model(
        self,
        ownership: FileOwnership,
        snapshot_id: int,
    ) -> FileOwnershipModel:
        """Map a FileOwnership domain value object to a FileOwnership ORM model.

        Args:
            ownership: Parsed file-to-package mapping from the domain layer.
            snapshot_id: ID of the RepositorySnapshot this ownership belongs to.

        Returns:
            A FileOwnership model instance ready for database insertion.
        """
        return FileOwnershipModel(
            snapshot_id=snapshot_id,
            file_path=ownership.path,
            package_name=ownership.qualified_package_name,
        )
