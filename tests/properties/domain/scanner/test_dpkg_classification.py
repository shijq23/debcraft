"""Property-based tests for dpkg parser classification correctness.

# Feature: m6-artifact-scanners, Property 2: dpkg Parser Classification Correctness

**Validates: Requirements 2.1, 2.2, 2.3, 2.7, 2.9**
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.domain.scanner.dpkg_parser import parse_dpkg_status

# ===========================================================================
# Strategies: dpkg stanza text generation
# ===========================================================================

# Valid characters for Debian package names (lowercase alphanum, plus ., +, -)
_PKG_NAME_START = "abcdefghijklmnopqrstuvwxyz"
_PKG_NAME_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789.+-"

# Valid characters for version strings
_VERSION_CHARS = "0123456789.+-~:"

# Valid architectures
_ARCHITECTURES = ["amd64", "i386", "arm64", "armhf", "all", "any", "s390x", "ppc64el"]


@st.composite
def st_package_name(draw: st.DrawFn) -> str:
    """Generate a valid Debian package name."""
    first = draw(st.sampled_from(list(_PKG_NAME_START)))
    rest = draw(st.text(alphabet=_PKG_NAME_CHARS, min_size=1, max_size=30))
    return first + rest


@st.composite
def st_version(draw: st.DrawFn) -> str:
    """Generate a valid Debian version string."""
    epoch = draw(st.sampled_from(["", "1:", "2:"]))
    upstream = draw(st.text(alphabet="0123456789.", min_size=1, max_size=10))
    revision = draw(st.sampled_from(["", "-1", "-2", "-1ubuntu1"]))
    return epoch + upstream + revision


@st.composite
def st_architecture(draw: st.DrawFn) -> str:
    """Generate a valid architecture string."""
    return draw(st.sampled_from(_ARCHITECTURES))


def _build_stanza(
    package: str,
    version: str,
    architecture: str,
    status: str,
) -> str:
    """Build a dpkg status stanza text from fields."""
    lines = [
        f"Package: {package}",
        f"Status: {status}",
        "Priority: optional",
        "Section: libs",
        "Installed-Size: 1234",
        f"Architecture: {architecture}",
        f"Version: {version}",
        "Description: A test package",
    ]
    return "\n".join(lines)


# ===========================================================================
# Property 2: dpkg Parser Classification Correctness
# ===========================================================================


@pytest.mark.unit
class TestProperty2DpkgClassificationCorrectness:
    """Property 2: dpkg Parser Classification Correctness.

    For any dpkg status stanza with various Status field combinations,
    the parser SHALL correctly classify packages:
    - "install ok installed" → included with status "installed"
    - "hold ok installed" → included with status "installed"
    - "install ok config-files" → included with status "config-files"
    - "deinstall" as desired action → excluded
    - "purge" as desired action → excluded
    - "install"/"hold" with unrecognized state → excluded with diagnostic

    **Validates: Requirements 2.1, 2.2, 2.3, 2.7, 2.9**
    """

    @settings(max_examples=100)
    @given(
        package=st_package_name(),
        version=st_version(),
        architecture=st_architecture(),
    )
    def test_install_ok_installed_produces_status_installed(
        self,
        package: str,
        version: str,
        architecture: str,
    ) -> None:
        """Stanzas with 'install ok installed' are always included with status 'installed'."""
        stanza_text = _build_stanza(package, version, architecture, "install ok installed")
        result = parse_dpkg_status(stanza_text)

        assert len(result.packages) == 1, f"Expected 1 package, got {len(result.packages)}.\nStanza:\n{stanza_text}"
        pkg = result.packages[0]
        assert pkg.name == package
        assert pkg.version == version
        assert pkg.architecture == architecture
        assert pkg.status == "installed"

    @settings(max_examples=100)
    @given(
        package=st_package_name(),
        version=st_version(),
        architecture=st_architecture(),
    )
    def test_hold_ok_installed_produces_status_installed(
        self,
        package: str,
        version: str,
        architecture: str,
    ) -> None:
        """Stanzas with 'hold ok installed' are always included with status 'installed'."""
        stanza_text = _build_stanza(package, version, architecture, "hold ok installed")
        result = parse_dpkg_status(stanza_text)

        assert len(result.packages) == 1, f"Expected 1 package, got {len(result.packages)}.\nStanza:\n{stanza_text}"
        pkg = result.packages[0]
        assert pkg.name == package
        assert pkg.version == version
        assert pkg.architecture == architecture
        assert pkg.status == "installed"

    @settings(max_examples=100)
    @given(
        package=st_package_name(),
        version=st_version(),
        architecture=st_architecture(),
    )
    def test_install_ok_config_files_produces_status_config_files(
        self,
        package: str,
        version: str,
        architecture: str,
    ) -> None:
        """Stanzas with 'install ok config-files' are included with status 'config-files'."""
        stanza_text = _build_stanza(package, version, architecture, "install ok config-files")
        result = parse_dpkg_status(stanza_text)

        assert len(result.packages) == 1, f"Expected 1 package, got {len(result.packages)}.\nStanza:\n{stanza_text}"
        pkg = result.packages[0]
        assert pkg.name == package
        assert pkg.version == version
        assert pkg.architecture == architecture
        assert pkg.status == "config-files"

    @settings(max_examples=100)
    @given(
        package=st_package_name(),
        version=st_version(),
        architecture=st_architecture(),
        ok_state=st.sampled_from(["ok", "reinstreq", "hold"]),
        current_state=st.sampled_from(
            [
                "installed",
                "config-files",
                "half-installed",
                "unpacked",
                "not-installed",
                "triggers-pending",
            ]
        ),
    )
    def test_deinstall_always_excluded(
        self,
        package: str,
        version: str,
        architecture: str,
        ok_state: str,
        current_state: str,
    ) -> None:
        """Stanzas with 'deinstall' as desired action are never in result."""
        status_field = f"deinstall {ok_state} {current_state}"
        stanza_text = _build_stanza(package, version, architecture, status_field)
        result = parse_dpkg_status(stanza_text)

        assert len(result.packages) == 0, (
            f"Expected 0 packages for deinstall, got {len(result.packages)}.\n"
            f"Status: '{status_field}'\n"
            f"Stanza:\n{stanza_text}"
        )

    @settings(max_examples=100)
    @given(
        package=st_package_name(),
        version=st_version(),
        architecture=st_architecture(),
        ok_state=st.sampled_from(["ok", "reinstreq", "hold"]),
        current_state=st.sampled_from(
            [
                "installed",
                "config-files",
                "half-installed",
                "unpacked",
                "not-installed",
                "triggers-pending",
            ]
        ),
    )
    def test_purge_always_excluded(
        self,
        package: str,
        version: str,
        architecture: str,
        ok_state: str,
        current_state: str,
    ) -> None:
        """Stanzas with 'purge' as desired action are never in result."""
        status_field = f"purge {ok_state} {current_state}"
        stanza_text = _build_stanza(package, version, architecture, status_field)
        result = parse_dpkg_status(stanza_text)

        assert len(result.packages) == 0, (
            f"Expected 0 packages for purge, got {len(result.packages)}.\n"
            f"Status: '{status_field}'\n"
            f"Stanza:\n{stanza_text}"
        )

    @settings(max_examples=100)
    @given(
        package=st_package_name(),
        version=st_version(),
        architecture=st_architecture(),
        desired_action=st.sampled_from(["install", "hold"]),
        unrecognized_state=st.sampled_from(
            [
                "half-installed",
                "unpacked",
                "half-configured",
                "triggers-awaited",
                "triggers-pending",
                "not-installed",
            ]
        ),
    )
    def test_install_hold_with_unrecognized_state_excluded_with_diagnostic(
        self,
        package: str,
        version: str,
        architecture: str,
        desired_action: str,
        unrecognized_state: str,
    ) -> None:
        """Stanzas with install/hold but unrecognized current state are excluded with diagnostic."""
        status_field = f"{desired_action} ok {unrecognized_state}"
        stanza_text = _build_stanza(package, version, architecture, status_field)
        result = parse_dpkg_status(stanza_text)

        assert len(result.packages) == 0, (
            f"Expected 0 packages for unrecognized state '{unrecognized_state}', "
            f"got {len(result.packages)}.\n"
            f"Status: '{status_field}'\n"
            f"Stanza:\n{stanza_text}"
        )
        # Should have a diagnostic about the unrecognized state
        assert len(result.diagnostics) >= 1, (
            f"Expected at least 1 diagnostic for unrecognized state, got {len(result.diagnostics)}.\n"
            f"Status: '{status_field}'"
        )
        # The diagnostic should mention the unrecognized state
        diag_text = " ".join(result.diagnostics)
        assert unrecognized_state in diag_text, (
            f"Diagnostic should mention '{unrecognized_state}', got: {result.diagnostics}"
        )
