"""Extracts enrichment metadata directly from .deb files in an ISO.

Orchestrates the extraction of package metadata from .deb archives
within an ISO's pool/ directory structure. Used as a fallback when
no RepositorySnapshot is available (snapshot_id == 0) to provide
real enrichment data (license, maintainer, download URL, PURL) instead
of NOASSERTION placeholders.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from debcraft.domain.package_intelligence.errors import (
    DebParseError,
    DEP5ParseError,
    PURLGenerationError,
)
from debcraft.domain.package_intelligence.purl_generator import generate_purl
from debcraft.domain.package_intelligence.values import MappingAlgorithm
from debcraft.domain.scanner.values import PackageEnrichment

if TYPE_CHECKING:
    from debcraft.domain.package_intelligence.deb_parser import DebParser
    from debcraft.domain.package_intelligence.dep5_parser import DEP5Parser
    from debcraft.domain.package_intelligence.license_mapper import LicenseMapper
    from debcraft.domain.package_intelligence.values import DEP5Document
    from debcraft.domain.scanner.values import IdentifiedPackage
    from debcraft.infrastructure.scanners.iso import ISOReader

logger = logging.getLogger(__name__)

#: Components typically found in a Debian pool directory.
_POOL_COMPONENTS = ("main", "contrib", "non-free", "non-free-firmware")


class DebExtractor:
    """Extracts enrichment metadata directly from .deb files in an ISO.

    Walks the ISO's pool/ directory to locate .deb files matching an
    identified package by name, version, and architecture. Parses the
    .deb control file and copyright to produce a PackageEnrichment.
    """

    def __init__(
        self,
        iso_reader: ISOReader,
        deb_parser: DebParser,
        dep5_parser: DEP5Parser,
        license_mapper: LicenseMapper,
    ) -> None:
        """Initialize DebExtractor with required dependencies.

        Args:
            iso_reader: Reader for ISO 9660 filesystem access.
            deb_parser: Parser for .deb binary archives.
            dep5_parser: Parser for DEP-5 machine-readable copyright files.
            license_mapper: Mapper from Debian license identifiers to SPDX expressions.
        """
        self._iso_reader = iso_reader
        self._deb_parser = deb_parser
        self._dep5_parser = dep5_parser
        self._license_mapper = license_mapper

    def extract_enrichment(self, pkg: IdentifiedPackage) -> PackageEnrichment | None:
        """Attempt to extract enrichment from a .deb in the ISO's pool/ directory.

        Discovers the .deb file path within the ISO pool structure, parses it
        to extract control fields and copyright, then maps license identifiers
        to SPDX expressions.

        Args:
            pkg: The identified package to look up in the ISO pool.

        Returns:
            A PackageEnrichment with extracted metadata, or None if the .deb
            cannot be found or parsed.
        """
        deb_path = self._discover_deb_path(pkg)
        if deb_path is None:
            logger.debug(
                "No .deb file found in ISO pool for %s %s (%s)",
                pkg.name,
                pkg.version,
                pkg.architecture,
            )
            return None

        try:
            parse_result = self._deb_parser.parse(deb_path)
        except DebParseError as exc:
            logger.warning(
                "Failed to parse .deb at '%s' for %s %s (%s): %s",
                deb_path,
                pkg.name,
                pkg.version,
                pkg.architecture,
                exc,
            )
            return None
        except (OSError, ValueError, KeyError, UnicodeDecodeError) as exc:
            logger.warning(
                "Unexpected error parsing .deb at '%s': %s",
                deb_path,
                exc,
            )
            return None

        # Extract control fields
        control = parse_result.control_fields
        maintainer = control.get("Maintainer")
        homepage = control.get("Homepage")
        depends = control.get("Depends")
        section = control.get("Section")
        priority = control.get("Priority")
        description = control.get("Description")
        source_package = control.get("Source")

        # Process copyright for license expressions
        license_expressions = self._extract_license_expressions(parse_result.copyright_text)

        # Generate PURL
        purl = self._generate_purl(pkg)

        return PackageEnrichment(
            source_package=source_package,
            maintainer=maintainer,
            homepage=homepage,
            depends=depends,
            section=section,
            priority=priority,
            description=description,
            sha256=None,
            download_url=None,
            purl=purl,
            license_expressions=license_expressions,
        )

    def _discover_deb_path(self, pkg: IdentifiedPackage) -> str | None:
        """Discover the .deb file path within the ISO pool structure.

        Walks pool/ → component → letter/lib prefix → package directory
        to find a .deb matching {name}_{version}_{arch}.deb.

        Args:
            pkg: The identified package to locate.

        Returns:
            The ISO-relative path to the .deb file, or None if not found.
        """
        # Check if pool/ exists
        try:
            pool_entries = self._iso_reader.list_dir("pool")
        except (FileNotFoundError, OSError):
            return None

        # Determine which components to check
        components = [c for c in pool_entries if c in _POOL_COMPONENTS]
        if not components:
            # Fall back to whatever directories are present in pool/
            components = pool_entries

        # Determine the letter/prefix directory for this package
        prefix_dir = self._get_prefix_dir(pkg.name)

        # Build the expected .deb filename
        expected_filename = f"{pkg.name}_{pkg.version}_{pkg.architecture}.deb"

        for component in components:
            pkg_dir = f"pool/{component}/{prefix_dir}/{pkg.name}"
            try:
                entries = self._iso_reader.list_dir(pkg_dir)
            except (FileNotFoundError, OSError):
                continue

            # Exact match first
            if expected_filename in entries:
                return f"{pkg_dir}/{expected_filename}"

            # Fuzzy match: look for files starting with the package name
            # and containing the version (versions may have epoch stripped in filename)
            for entry in entries:
                if entry.endswith(".deb") and entry.startswith(f"{pkg.name}_"):
                    # Check if version matches (handle epoch stripping)
                    version_in_filename = self._strip_epoch(pkg.version)
                    candidate_filename = f"{pkg.name}_{version_in_filename}_{pkg.architecture}.deb"
                    if entry == candidate_filename:
                        return f"{pkg_dir}/{entry}"

        return None

    def _get_prefix_dir(self, package_name: str) -> str:
        """Determine the pool prefix directory for a package.

        Debian pool directory convention:
        - lib* packages → first 4 chars (e.g., 'libc' for 'libc6')
        - Other packages → first char (e.g., 'n' for 'nginx')

        Args:
            package_name: The package name.

        Returns:
            The prefix directory component (e.g., 'l', 'libc').
        """
        if package_name.startswith("lib") and len(package_name) > 3:
            return package_name[:4]
        return package_name[0:1]

    def _strip_epoch(self, version: str) -> str:
        """Strip the epoch prefix from a Debian version string.

        Debian filenames do not include the epoch in the version portion.
        E.g., "1:2.40-1" → "2.40-1"

        Args:
            version: The version string, possibly with epoch.

        Returns:
            The version without epoch.
        """
        colon_pos = version.find(":")
        if colon_pos >= 0:
            return version[colon_pos + 1 :]
        return version

    def _extract_license_expressions(self, copyright_text: str | None) -> list[tuple[str, str]]:
        """Extract SPDX license expressions from copyright text.

        Tries DEP5 parsing first; if that fails, attempts free-form text
        mapping via the LicenseMapper.

        Args:
            copyright_text: Raw copyright file content, or None.

        Returns:
            List of (spdx_expression, source) tuples.
        """
        if not copyright_text:
            return []

        # Try DEP5 parsing
        try:
            dep5_doc = self._dep5_parser.parse(copyright_text)
            return self._map_dep5_licenses(dep5_doc)
        except DEP5ParseError:
            pass

        # Fall back to free-form text mapping
        return self._map_freeform_license(copyright_text)

    def _map_dep5_licenses(self, dep5_doc: DEP5Document) -> list[tuple[str, str]]:
        """Map DEP5 document license names to SPDX expressions.

        Collects unique license names from Files paragraphs and maps
        each to an SPDX expression via the LicenseMapper.

        Args:
            dep5_doc: Parsed DEP5 document.

        Returns:
            List of (spdx_expression, algorithm_source) tuples.
        """
        seen: set[str] = set()
        expressions: list[tuple[str, str]] = []

        for files_para in dep5_doc.files_paragraphs:
            license_name = files_para.license_name
            if not license_name or license_name in seen:
                continue
            seen.add(license_name)

            result = self._license_mapper.map(license_name, files_para.license_text)
            expressions.append((result.spdx_expression, result.algorithm.value))

        return expressions

    def _map_freeform_license(self, copyright_text: str) -> list[tuple[str, str]]:
        """Attempt to map free-form copyright text to an SPDX expression.

        Extracts the first non-empty line or the text up to a reasonable
        length as a license identifier hint, then passes it to the mapper.

        Args:
            copyright_text: Raw copyright text.

        Returns:
            List with a single (spdx_expression, algorithm_source) tuple,
            or empty list if no mapping could be determined.
        """
        # Use first non-empty line as identifier hint
        identifier = ""
        for line in copyright_text.splitlines():
            stripped = line.strip()
            if stripped:
                identifier = stripped
                break

        if not identifier:
            return []

        result = self._license_mapper.map(identifier, copyright_text)

        # Only include if mapper found something useful (not unmapped)
        if result.algorithm == MappingAlgorithm.UNMAPPED:
            return []

        return [(result.spdx_expression, result.algorithm.value)]

    def _generate_purl(self, pkg: IdentifiedPackage) -> str | None:
        """Generate a PURL for the package.

        Args:
            pkg: The identified package.

        Returns:
            PURL string, or None if generation fails.
        """
        try:
            return generate_purl(pkg.name, pkg.version, pkg.architecture)
        except PURLGenerationError:
            logger.debug(
                "PURL generation failed for %s %s (%s)",
                pkg.name,
                pkg.version,
                pkg.architecture,
            )
            return None
