"""Tests verifying documentation content meets spec requirements.

Validates that user guides, developer guides, navigation, and API reference
pages contain required content as specified in the documentation expansion
requirements (1.1–9.4).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DOCS_DIR = Path("docs")
PROJECT_ROOT = Path(".")


class _PermissiveLoader(yaml.SafeLoader):
    """YAML loader that ignores Python-specific tags (e.g. !!python/name:...)."""


# Register a constructor that returns None for any unrecognized tag
_PermissiveLoader.add_multi_constructor(
    "tag:yaml.org,2002:python/",
    lambda loader, suffix, node: None,
)


def _read_doc(relative_path: str) -> str:
    """Read a documentation file and return its content as a string."""
    path = DOCS_DIR / relative_path
    assert path.exists(), f"Documentation file not found: {path}"
    return path.read_text(encoding="utf-8")


# ===========================================================================
# User Guide — ISO (Requirements 1.1–1.5)
# ===========================================================================


@pytest.mark.unit
class TestISOUserGuide:
    """Tests for docs/user/iso.md content."""

    @pytest.fixture(autouse=True)
    def _load_content(self) -> None:
        self.content = _read_doc("user/iso.md")

    def test_iso_user_guide_cli_example(self) -> None:
        """ISO user guide contains `debcraft sbom` CLI example."""
        assert "debcraft sbom" in self.content

    def test_iso_user_guide_prerequisites(self) -> None:
        """ISO user guide mentions no root privileges required."""
        content_lower = self.content.lower()
        assert "no root" in content_lower or "root" in content_lower
        # Verify it specifically states root is not required
        assert "no root privileges" in content_lower

    def test_iso_user_guide_fixture_reference(self) -> None:
        """ISO user guide references build-iso.sh fixture script."""
        assert "build-iso.sh" in self.content

    def test_iso_user_guide_error_behavior(self) -> None:
        """ISO user guide documents error behavior."""
        content_lower = self.content.lower()
        assert "error" in content_lower or "does not exist" in content_lower


# ===========================================================================
# User Guide — Docker (Requirements 2.1–2.6)
# ===========================================================================


@pytest.mark.unit
class TestDockerUserGuide:
    """Tests for docs/user/docker.md content."""

    @pytest.fixture(autouse=True)
    def _load_content(self) -> None:
        self.content = _read_doc("user/docker.md")

    def test_docker_user_guide_cli_example(self) -> None:
        """Docker user guide contains --type docker CLI example."""
        assert "--type docker" in self.content

    def test_docker_user_guide_docker_save(self) -> None:
        """Docker user guide contains docker save workflow."""
        assert "docker save" in self.content

    def test_docker_user_guide_whiteout_semantics(self) -> None:
        """Docker user guide documents whiteout semantics."""
        assert ".wh." in self.content
        assert ".wh..wh..opq" in self.content

    def test_docker_user_guide_output_format(self) -> None:
        """Docker user guide describes output table format."""
        content_lower = self.content.lower()
        assert "package name" in content_lower or "package" in content_lower
        assert "version" in content_lower
        assert "architecture" in content_lower

    def test_docker_user_guide_fixture_reference(self) -> None:
        """Docker user guide references build-docker.sh fixture script."""
        assert "build-docker.sh" in self.content

    def test_docker_user_guide_error_behavior(self) -> None:
        """Docker user guide documents error for missing tarball."""
        content_lower = self.content.lower()
        # Should document what happens when tarball is missing/invalid
        assert "not found" in content_lower or "missing" in content_lower or "error" in content_lower


# ===========================================================================
# User Guide — IMG (Requirements 3.1–3.5)
# ===========================================================================


@pytest.mark.unit
class TestIMGUserGuide:
    """Tests for docs/user/img.md content."""

    @pytest.fixture(autouse=True)
    def _load_content(self) -> None:
        self.content = _read_doc("user/img.md")

    def test_img_user_guide_cli_examples(self) -> None:
        """IMG user guide has at least two CLI examples."""
        # Count occurrences of debcraft sbom invocations
        cli_count = self.content.count("debcraft sbom")
        assert cli_count >= 2, f"Expected at least 2 CLI examples, found {cli_count}"

    def test_img_user_guide_guestfs_dependency(self) -> None:
        """IMG user guide mentions python3-guestfs dependency."""
        assert "python3-guestfs" in self.content

    def test_img_user_guide_debcraft_doctor(self) -> None:
        """IMG user guide mentions debcraft doctor for verification."""
        assert "debcraft doctor" in self.content

    def test_img_user_guide_filesystem_fallback(self) -> None:
        """IMG user guide explains filesystem fallback."""
        content_lower = self.content.lower()
        assert "fallback" in content_lower

    def test_img_user_guide_fixture_reference(self) -> None:
        """IMG user guide references build-img.sh fixture script."""
        assert "build-img.sh" in self.content


# ===========================================================================
# User Guide — QCOW2 (Requirements 4.1–4.6)
# ===========================================================================


@pytest.mark.unit
class TestQCOW2UserGuide:
    """Tests for docs/user/qcow2.md content."""

    @pytest.fixture(autouse=True)
    def _load_content(self) -> None:
        self.content = _read_doc("user/qcow2.md")

    def test_qcow2_user_guide_cli_example(self) -> None:
        """QCOW2 user guide contains --type qcow2 CLI example."""
        assert "--type qcow2" in self.content

    def test_qcow2_user_guide_libguestfs(self) -> None:
        """QCOW2 user guide mentions python3-guestfs requirement."""
        assert "python3-guestfs" in self.content

    def test_qcow2_user_guide_vs_img_guidance(self) -> None:
        """QCOW2 user guide compares QCOW2 vs IMG or mentions QFI."""
        # Should contain QFI magic header reference or QCOW2 vs IMG comparison
        assert "QFI" in self.content or ("QCOW2" in self.content and "IMG" in self.content)

    def test_qcow2_user_guide_fixture_reference(self) -> None:
        """QCOW2 user guide references build-qcow2.sh fixture script."""
        assert "build-qcow2.sh" in self.content

    def test_qcow2_user_guide_diagnostic(self) -> None:
        """QCOW2 user guide documents unavailable guestfs diagnostic."""
        content_lower = self.content.lower()
        # Should describe behavior when guestfs is unavailable
        assert "not available" in content_lower or "not installed" in content_lower or "unavailable" in content_lower


# ===========================================================================
# Developer Guide — Docker Scanner (Requirements 5.1–5.7)
# ===========================================================================


@pytest.mark.unit
class TestDockerDevGuide:
    """Tests for docs/developer/docker-scanner.md content."""

    @pytest.fixture(autouse=True)
    def _load_content(self) -> None:
        self.content = _read_doc("developer/docker-scanner.md")

    def test_docker_dev_guide_sections(self) -> None:
        """Docker dev guide has required sections."""
        required_sections = [
            "Introduction",
            "Prerequisites",
            "How It Works",
            "Code Example",
            "Running",
            "Extending",
        ]
        for section in required_sections:
            assert section in self.content, f"Missing section: {section}"

    def test_docker_dev_guide_code_sample(self) -> None:
        """Docker dev guide has DockerScanner import and scan() call."""
        assert "DockerScanner" in self.content
        assert "scan(" in self.content or "scan()" in self.content or "scanner.scan" in self.content

    def test_docker_dev_guide_mermaid(self) -> None:
        """Docker dev guide has a mermaid diagram."""
        assert "```mermaid" in self.content or "mermaid" in self.content

    def test_docker_dev_guide_three_stages(self) -> None:
        """Docker dev guide mentions manifest, layer, and dpkg stages."""
        content_lower = self.content.lower()
        assert "manifest" in content_lower
        assert "layer" in content_lower
        assert "dpkg" in content_lower


# ===========================================================================
# Developer Guide — Disk Image Scanner (Requirements 6.1–6.7)
# ===========================================================================


@pytest.mark.unit
class TestDiskImageDevGuide:
    """Tests for docs/developer/disk-image-scanner.md content."""

    @pytest.fixture(autouse=True)
    def _load_content(self) -> None:
        self.content = _read_doc("developer/disk-image-scanner.md")

    def test_disk_image_dev_guide_sections(self) -> None:
        """Disk image dev guide has IMGScanner and QCOW2Scanner sections."""
        assert "IMGScanner" in self.content
        assert "QCOW2Scanner" in self.content

    def test_disk_image_dev_guide_code_sample(self) -> None:
        """Disk image dev guide has GuestfsInspector import."""
        assert "GuestfsInspector" in self.content

    def test_disk_image_dev_guide_mermaid(self) -> None:
        """Disk image dev guide has mermaid diagram."""
        assert "```mermaid" in self.content or "mermaid" in self.content

    def test_disk_image_dev_guide_fallback(self) -> None:
        """Disk image dev guide describes fallback strategy."""
        content_lower = self.content.lower()
        assert "fallback" in content_lower


# ===========================================================================
# Developer Guide — Writing a Scanner (Requirements 7.1–7.6)
# ===========================================================================


@pytest.mark.unit
class TestWritingScannerGuide:
    """Tests for docs/developer/writing-a-scanner.md content."""

    @pytest.fixture(autouse=True)
    def _load_content(self) -> None:
        self.content = _read_doc("developer/writing-a-scanner.md")

    def test_writing_scanner_protocol(self) -> None:
        """Writing-a-scanner guide documents ArtifactScanner protocol."""
        assert "ArtifactScanner" in self.content

    def test_writing_scanner_code_sample(self) -> None:
        """Writing-a-scanner guide has skeleton scanner code."""
        # Should have an async scan method in the code sample
        assert "async def scan" in self.content
        assert "ScanResult" in self.content

    def test_writing_scanner_entry_point(self) -> None:
        """Writing-a-scanner guide has pyproject.toml entry points."""
        assert "pyproject.toml" in self.content
        assert "debcraft.scanners" in self.content

    def test_writing_scanner_workflow_context(self) -> None:
        """Writing-a-scanner guide explains cancellation_token and progress.report."""
        assert "cancellation_token" in self.content
        assert "progress.report" in self.content or "progress_reporter" in self.content

    def test_writing_scanner_value_objects(self) -> None:
        """Writing-a-scanner guide documents Artifact and ScanResult."""
        assert "Artifact" in self.content
        assert "ScanResult" in self.content


# ===========================================================================
# Navigation Tests (Requirements 9.1–9.4)
# ===========================================================================


@pytest.mark.unit
class TestNavigation:
    """Tests for mkdocs.yml navigation structure."""

    @pytest.fixture(autouse=True)
    def _load_config(self) -> None:
        config_path = PROJECT_ROOT / "mkdocs.yml"
        assert config_path.exists(), "mkdocs.yml not found"
        self.config = yaml.load(config_path.read_text(encoding="utf-8"), Loader=_PermissiveLoader)  # noqa: S506
        self.nav = self.config["nav"]

    def _find_nav_section(self, section_name: str) -> list | None:
        """Find a top-level navigation section by name."""
        for entry in self.nav:
            if isinstance(entry, dict) and section_name in entry:
                return entry[section_name]
        return None

    def _flatten_nav_paths(self, nav_items: list) -> list[str]:
        """Recursively extract all file paths from nav items."""
        paths = []
        for item in nav_items:
            if isinstance(item, str):
                paths.append(item)
            elif isinstance(item, dict):
                for value in item.values():
                    if isinstance(value, str):
                        paths.append(value)
                    elif isinstance(value, list):
                        paths.extend(self._flatten_nav_paths(value))
        return paths

    def test_nav_includes_user_guides(self) -> None:
        """Navigation includes iso, docker, img, qcow2 under User Guide."""
        user_guide = self._find_nav_section("User Guide")
        assert user_guide is not None, "User Guide section not found in nav"

        user_guide_str = str(user_guide).lower()
        assert "iso" in user_guide_str, "ISO not found in User Guide nav"
        assert "docker" in user_guide_str, "Docker not found in User Guide nav"
        assert "img" in user_guide_str, "IMG not found in User Guide nav"
        assert "qcow2" in user_guide_str, "QCOW2 not found in User Guide nav"

    def test_nav_includes_developer_guides(self) -> None:
        """Navigation includes docker-scanner, disk-image-scanner, writing-a-scanner under Developer Guide."""
        dev_guide = self._find_nav_section("Developer Guide")
        assert dev_guide is not None, "Developer Guide section not found in nav"

        dev_guide_str = str(dev_guide).lower()
        assert "docker-scanner" in dev_guide_str, "docker-scanner not found in Developer Guide nav"
        assert "disk-image-scanner" in dev_guide_str, "disk-image-scanner not found in Developer Guide nav"
        assert "writing-a-scanner" in dev_guide_str, "writing-a-scanner not found in Developer Guide nav"

    def test_nav_includes_api_reference(self) -> None:
        """Navigation includes an API Reference section with contracts, scanner-ports, scanner-values."""
        api_ref = self._find_nav_section("API Reference")
        assert api_ref is not None, "API Reference section not found in nav"

        api_ref_str = str(api_ref).lower()
        assert "contracts" in api_ref_str, "contracts not found in API Reference nav"
        assert "scanner-ports" in api_ref_str, "scanner-ports not found in API Reference nav"
        assert "scanner-values" in api_ref_str, "scanner-values not found in API Reference nav"

    def test_nav_entries_resolve_to_files(self) -> None:
        """Every file path in mkdocs.yml nav corresponds to existing file in docs/."""
        all_paths = self._flatten_nav_paths(self.nav)
        missing = []
        for rel_path in all_paths:
            full_path = DOCS_DIR / rel_path
            if not full_path.exists():
                missing.append(rel_path)
        assert not missing, f"Navigation entries with missing files: {missing}"


# ===========================================================================
# API Reference Tests (Requirements 8.1–8.6)
# ===========================================================================


@pytest.mark.unit
class TestAPIReference:
    """Tests for API reference page mkdocstrings directives."""

    def test_api_contracts_mkdocstrings_directive(self) -> None:
        """API contracts page contains ::: debcraft.platform.contracts directive."""
        content = _read_doc("api/contracts.md")
        assert "::: debcraft.platform.contracts" in content

    def test_api_scanner_ports_directive(self) -> None:
        """API scanner-ports page contains ::: debcraft.domain.scanner.ports directive."""
        content = _read_doc("api/scanner-ports.md")
        assert "::: debcraft.domain.scanner.ports" in content

    def test_api_scanner_values_directive(self) -> None:
        """API scanner-values page contains ::: debcraft.domain.scanner.values directive."""
        content = _read_doc("api/scanner-values.md")
        assert "::: debcraft.domain.scanner.values" in content
