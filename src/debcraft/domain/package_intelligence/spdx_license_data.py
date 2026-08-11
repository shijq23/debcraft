"""SPDX license data loader for the package intelligence domain layer.

Loads and provides access to the embedded SPDX license list data,
enabling case-insensitive lookups by identifier and full name.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SPDXLicenseEntry:
    """A single SPDX license entry.

    Attributes:
        license_id: The canonical SPDX license identifier (e.g. "MIT").
        name: The full human-readable license name (e.g. "MIT License").
        is_deprecated: Whether this identifier has been deprecated by SPDX.
    """

    license_id: str
    name: str
    is_deprecated: bool = False


@dataclass(frozen=True)
class SPDXLicenseData:
    """Container for the complete SPDX license list.

    Provides efficient case-insensitive lookups by license identifier
    and by full license name.

    Attributes:
        version: The SPDX license list version string.
        licenses: The complete list of SPDX license entries.
    """

    version: str
    licenses: list[SPDXLicenseEntry]
    _by_id: dict[str, SPDXLicenseEntry] = field(default_factory=dict, repr=False, compare=False)
    _by_name: dict[str, SPDXLicenseEntry] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Build lookup indexes after initialization."""
        by_id = self._by_id
        by_name = self._by_name
        # Use object.__setattr__ because frozen dataclass doesn't allow normal assignment
        # but we're mutating the mutable default_factory dicts in-place
        for entry in self.licenses:
            by_id[entry.license_id.lower()] = entry
            by_name[entry.name.lower()] = entry

    def get_by_id(self, license_id: str) -> SPDXLicenseEntry | None:
        """Look up a license entry by SPDX identifier (case-insensitive).

        Args:
            license_id: The SPDX license identifier to look up.

        Returns:
            The matching SPDXLicenseEntry, or None if not found.
        """
        return self._by_id.get(license_id.lower())

    def get_by_name(self, name: str) -> SPDXLicenseEntry | None:
        """Look up a license entry by full name (case-insensitive).

        Args:
            name: The full license name to look up.

        Returns:
            The matching SPDXLicenseEntry, or None if not found.
        """
        return self._by_name.get(name.lower())

    @property
    def identifiers(self) -> list[str]:
        """Return all license identifiers in canonical case."""
        return [entry.license_id for entry in self.licenses]


_DATA_DIR = Path(__file__).parent / "data"


def load_spdx_license_data() -> SPDXLicenseData:
    """Load the embedded SPDX license data from the bundled JSON file.

    Reads and parses the spdx_licenses.json file located in the data/
    subdirectory of this package. The returned SPDXLicenseData object
    provides efficient case-insensitive lookups by identifier and name.

    Returns:
        An SPDXLicenseData instance containing all bundled SPDX licenses.

    Raises:
        FileNotFoundError: If the spdx_licenses.json file is missing.
        json.JSONDecodeError: If the JSON file is malformed.
    """
    json_path = _DATA_DIR / "spdx_licenses.json"
    raw = json.loads(json_path.read_text(encoding="utf-8"))

    entries = [
        SPDXLicenseEntry(
            license_id=lic["licenseId"],
            name=lic["name"],
            is_deprecated=lic.get("isDeprecatedLicenseId", False),
        )
        for lic in raw["licenses"]
    ]

    return SPDXLicenseData(
        version=raw["licenseListVersion"],
        licenses=entries,
    )
