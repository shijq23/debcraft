"""Unit tests for dpkg status parser edge cases."""

import pytest

from debcraft.domain.scanner.dpkg_parser import parse_dpkg_status

pytestmark = [pytest.mark.unit]


class TestEmptyAndWhitespaceInput:
    """Tests for empty/whitespace-only input (Req 2.6)."""

    def test_empty_string_returns_empty_result(self) -> None:
        """Empty string input yields empty packages list, no error."""
        result = parse_dpkg_status("")
        assert result.packages == []
        assert result.diagnostics == []
        assert result.stanzas == []

    def test_whitespace_only_returns_empty_result(self) -> None:
        """Whitespace-only input yields empty packages list, no error."""
        result = parse_dpkg_status("   \n  \n\t\n  ")
        assert result.packages == []
        assert result.diagnostics == []
        assert result.stanzas == []


class TestMissingRequiredFields:
    """Tests for stanzas missing required fields (Req 2.5)."""

    def test_missing_package_field_skipped_with_diagnostic(self) -> None:
        """Stanza missing Package field is skipped with diagnostic mentioning 'Package'."""
        content = "Version: 1.0\nArchitecture: amd64\nStatus: install ok installed\n"
        result = parse_dpkg_status(content)
        assert result.packages == []
        assert len(result.diagnostics) == 1
        assert "Package" in result.diagnostics[0]

    def test_missing_version_field_skipped_with_diagnostic(self) -> None:
        """Stanza missing Version field is skipped with diagnostic mentioning 'Version'."""
        content = "Package: bash\nArchitecture: amd64\nStatus: install ok installed\n"
        result = parse_dpkg_status(content)
        assert result.packages == []
        assert len(result.diagnostics) == 1
        assert "Version" in result.diagnostics[0]

    def test_missing_both_package_and_version(self) -> None:
        """Stanza missing both Package and Version lists both in diagnostic."""
        content = "Architecture: amd64\nStatus: install ok installed\n"
        result = parse_dpkg_status(content)
        assert result.packages == []
        assert len(result.diagnostics) == 1
        assert "Package" in result.diagnostics[0]
        assert "Version" in result.diagnostics[0]


class TestMissingArchitectureField:
    """Tests for missing Architecture field (Req 2.10)."""

    def test_missing_architecture_included_with_empty_string(self) -> None:
        """Missing Architecture field results in package with architecture=''."""
        content = "Package: bash\nVersion: 5.2-1\nStatus: install ok installed\n"
        result = parse_dpkg_status(content)
        assert len(result.packages) == 1
        assert result.packages[0].architecture == ""
        assert result.packages[0].name == "bash"
        assert result.packages[0].version == "5.2-1"
        assert result.packages[0].status == "installed"


class TestContinuationLines:
    """Tests for continuation line handling (Req 2.4)."""

    def test_space_continuation_appended_to_previous_field(self) -> None:
        """Continuation lines (space prefix) are appended to preceding field."""
        content = (
            "Package: bash\n"
            "Version: 5.2-1\n"
            "Description: GNU Bourne Again SHell\n"
            " An sh-compatible command language interpreter.\n"
            "Architecture: amd64\n"
            "Status: install ok installed\n"
        )
        result = parse_dpkg_status(content)
        assert len(result.packages) == 1
        # Verify the Description field in the stanza is correctly joined
        stanza = result.stanzas[0]
        desc = stanza.get("Description")
        assert desc == "GNU Bourne Again SHell\nAn sh-compatible command language interpreter."

    def test_tab_continuation_handled_correctly(self) -> None:
        """Tab continuation lines are also handled correctly."""
        content = (
            "Package: bash\n"
            "Version: 5.2-1\n"
            "Description: GNU Bourne Again SHell\n"
            "\tAn sh-compatible shell.\n"
            "Architecture: amd64\n"
            "Status: install ok installed\n"
        )
        result = parse_dpkg_status(content)
        assert len(result.packages) == 1
        stanza = result.stanzas[0]
        desc = stanza.get("Description")
        assert desc == "GNU Bourne Again SHell\nAn sh-compatible shell."


class TestMultilineFields:
    """Tests for multiline Description field preservation (Req 2.4)."""

    def test_multiline_description_preserved(self) -> None:
        """Multiline Description field is preserved correctly."""
        content = (
            "Package: bash\n"
            "Version: 5.2-1\n"
            "Architecture: amd64\n"
            "Status: install ok installed\n"
            "Description: GNU Bourne Again SHell\n"
            " Bash is an sh-compatible command language interpreter that\n"
            " executes commands read from the standard input or from a file.\n"
            " .\n"
            " Bash also incorporates useful features from the Korn and C shells.\n"
        )
        result = parse_dpkg_status(content)
        assert len(result.packages) == 1
        stanza = result.stanzas[0]
        desc = stanza.get("Description")
        expected_desc = (
            "GNU Bourne Again SHell\n"
            "Bash is an sh-compatible command language interpreter that\n"
            "executes commands read from the standard input or from a file.\n"
            ".\n"
            "Bash also incorporates useful features from the Korn and C shells."
        )
        assert desc == expected_desc


class TestStatusClassification:
    """Tests for status field classification (Req 2.2, 2.7, 2.9)."""

    def test_config_files_status_included(self) -> None:
        """Status 'install ok config-files' results in status='config-files' (Req 2.7)."""
        content = "Package: libfoo\nVersion: 1.2.3-4\nArchitecture: amd64\nStatus: install ok config-files\n"
        result = parse_dpkg_status(content)
        assert len(result.packages) == 1
        assert result.packages[0].status == "config-files"
        assert result.packages[0].name == "libfoo"

    def test_deinstall_status_excluded_no_diagnostic(self) -> None:
        """Status 'deinstall ok installed' is excluded without diagnostic."""
        content = "Package: removed-pkg\nVersion: 1.0-1\nArchitecture: amd64\nStatus: deinstall ok installed\n"
        result = parse_dpkg_status(content)
        assert result.packages == []
        assert result.diagnostics == []

    def test_purge_status_excluded_no_diagnostic(self) -> None:
        """Status 'purge ok installed' is excluded without diagnostic."""
        content = "Package: purged-pkg\nVersion: 2.0-1\nArchitecture: amd64\nStatus: purge ok installed\n"
        result = parse_dpkg_status(content)
        assert result.packages == []
        assert result.diagnostics == []

    def test_half_installed_excluded_with_diagnostic(self) -> None:
        """Status 'install ok half-installed' is excluded with diagnostic about unrecognized state (Req 2.9)."""
        content = "Package: broken-pkg\nVersion: 1.0-1\nArchitecture: amd64\nStatus: install ok half-installed\n"
        result = parse_dpkg_status(content)
        assert result.packages == []
        assert len(result.diagnostics) == 1
        assert "half-installed" in result.diagnostics[0]


class TestMalformedStatusField:
    """Tests for malformed Status field."""

    def test_status_field_with_less_than_three_parts(self) -> None:
        """Stanza with malformed Status field (less than 3 parts) is skipped with diagnostic."""
        content = "Package: bad-status\nVersion: 1.0-1\nArchitecture: amd64\nStatus: install\n"
        result = parse_dpkg_status(content)
        assert result.packages == []
        assert len(result.diagnostics) == 1
        assert "malformed" in result.diagnostics[0].lower() or "Status" in result.diagnostics[0]


class TestMultipleStanzasMixed:
    """Tests for multiple stanzas with mix of valid/invalid."""

    def test_mixed_valid_invalid_stanzas(self) -> None:
        """Multiple stanzas with mix of valid/invalid: only valid packages returned."""
        content = (
            "Package: valid-pkg\n"
            "Version: 1.0-1\n"
            "Architecture: amd64\n"
            "Status: install ok installed\n"
            "\n"
            "Version: 2.0-1\n"
            "Architecture: amd64\n"
            "Status: install ok installed\n"
            "\n"
            "Package: deinstalled-pkg\n"
            "Version: 3.0-1\n"
            "Architecture: amd64\n"
            "Status: deinstall ok installed\n"
            "\n"
            "Package: another-valid\n"
            "Version: 4.0-1\n"
            "Architecture: arm64\n"
            "Status: hold ok installed\n"
        )
        result = parse_dpkg_status(content)
        # Only valid-pkg and another-valid should be returned
        assert len(result.packages) == 2
        assert result.packages[0].name == "valid-pkg"
        assert result.packages[0].status == "installed"
        assert result.packages[1].name == "another-valid"
        assert result.packages[1].status == "installed"
        # The second stanza (missing Package) should have a diagnostic
        assert len(result.diagnostics) == 1
        assert "Package" in result.diagnostics[0]
