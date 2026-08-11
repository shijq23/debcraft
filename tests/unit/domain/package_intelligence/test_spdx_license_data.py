"""Unit tests for the SPDX license data loader."""

from __future__ import annotations

import pytest

from debcraft.domain.package_intelligence.spdx_license_data import (
    SPDXLicenseData,
    SPDXLicenseEntry,
    load_spdx_license_data,
)


@pytest.mark.unit
@pytest.mark.package
class TestSPDXLicenseEntry:
    """Tests for the SPDXLicenseEntry dataclass."""

    def test_basic_creation(self):
        entry = SPDXLicenseEntry(license_id="MIT", name="MIT License")
        assert entry.license_id == "MIT"
        assert entry.name == "MIT License"
        assert entry.is_deprecated is False

    def test_deprecated_entry(self):
        entry = SPDXLicenseEntry(license_id="GPL-2.0", name="GNU General Public License v2.0 only", is_deprecated=True)
        assert entry.is_deprecated is True

    def test_frozen(self):
        entry = SPDXLicenseEntry(license_id="MIT", name="MIT License")
        with pytest.raises(AttributeError):
            entry.license_id = "Apache-2.0"  # type: ignore[misc]


@pytest.mark.unit
@pytest.mark.package
class TestSPDXLicenseData:
    """Tests for the SPDXLicenseData container."""

    def test_get_by_id_exact_case(self):
        data = load_spdx_license_data()
        entry = data.get_by_id("MIT")
        assert entry is not None
        assert entry.license_id == "MIT"
        assert entry.name == "MIT License"

    def test_get_by_id_case_insensitive(self):
        data = load_spdx_license_data()
        entry = data.get_by_id("mit")
        assert entry is not None
        assert entry.license_id == "MIT"

    def test_get_by_id_not_found(self):
        data = load_spdx_license_data()
        assert data.get_by_id("NonExistent-License-XYZ") is None

    def test_get_by_name_exact_case(self):
        data = load_spdx_license_data()
        entry = data.get_by_name("Apache License 2.0")
        assert entry is not None
        assert entry.license_id == "Apache-2.0"

    def test_get_by_name_case_insensitive(self):
        data = load_spdx_license_data()
        entry = data.get_by_name("apache license 2.0")
        assert entry is not None
        assert entry.license_id == "Apache-2.0"

    def test_get_by_name_not_found(self):
        data = load_spdx_license_data()
        assert data.get_by_name("Not A Real License Name") is None

    def test_identifiers_property(self):
        data = load_spdx_license_data()
        ids = data.identifiers
        assert "MIT" in ids
        assert "Apache-2.0" in ids
        assert "GPL-2.0-only" in ids
        assert len(ids) >= 100


@pytest.mark.unit
@pytest.mark.package
class TestLoadSPDXLicenseData:
    """Tests for the load_spdx_license_data function."""

    def test_loads_successfully(self):
        data = load_spdx_license_data()
        assert isinstance(data, SPDXLicenseData)
        assert data.version == "3.25"
        assert len(data.licenses) >= 100

    def test_all_entries_are_spdx_license_entry(self):
        data = load_spdx_license_data()
        for entry in data.licenses:
            assert isinstance(entry, SPDXLicenseEntry)
            assert entry.license_id
            assert entry.name

    def test_includes_common_licenses(self):
        data = load_spdx_license_data()
        common_ids = ["MIT", "Apache-2.0", "GPL-2.0-only", "GPL-3.0-only", "BSD-2-Clause", "BSD-3-Clause", "ISC"]
        for license_id in common_ids:
            assert data.get_by_id(license_id) is not None, f"Missing common license: {license_id}"

    def test_includes_deprecated_identifiers(self):
        data = load_spdx_license_data()
        deprecated = data.get_by_id("GPL-2.0")
        assert deprecated is not None
        assert deprecated.is_deprecated is True
