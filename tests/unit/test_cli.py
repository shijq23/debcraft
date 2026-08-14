"""Unit tests for DebCraft CLI commands."""

import importlib.metadata
import logging
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from debcraft.cli import app
from debcraft.version import VERSION

runner = CliRunner()


@pytest.mark.unit
def test_version_displays_current_version():
    """The version command outputs the current version string."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert VERSION in result.output


@pytest.mark.unit
def test_doctor_all_checks_pass():
    """The doctor command reports PASS for all checks in a healthy environment."""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "PASS" in result.output
    assert "All checks passed" in result.output


@pytest.mark.unit
def test_doctor_reports_python_version_failure():
    """The doctor command reports FAIL when Python version is below 3.13."""
    mock_version = type(
        "version_info",
        (),
        {
            "major": 3,
            "minor": 12,
            "micro": 0,
        },
    )()

    with patch("debcraft.cli.sys.version_info", mock_version):
        result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "FAIL" in result.output
    assert "3.12.0" in result.output
    assert "Some checks failed" in result.output


@pytest.mark.unit
def test_info_displays_environment_details():
    """The info command outputs key environment information."""
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0
    assert VERSION in result.output
    assert "Python version" in result.output
    assert "Platform" in result.output
    assert "Architecture" in result.output
    assert "Package location" in result.output


@pytest.mark.unit
def test_mirror_help_lists_subcommands():
    """The mirror sub-app help lists all expected sub-commands."""
    result = runner.invoke(app, ["mirror", "--help"])
    assert result.exit_code == 0
    for subcommand in ("sync", "verify", "status", "list", "clean"):
        assert subcommand in result.output, f"Expected '{subcommand}' in mirror --help output"


@pytest.mark.unit
def test_index_help_lists_subcommands():
    """The index sub-app help lists the package sub-command."""
    result = runner.invoke(app, ["index", "--help"])
    assert result.exit_code == 0
    assert "package" in result.output


@pytest.mark.unit
def test_sbom_help_shows_arguments():
    """The sbom command help shows its arguments and options."""
    result = runner.invoke(app, ["sbom", "--help"])
    assert result.exit_code == 0
    # Should show the artifact_path argument
    assert "artifact_path" in result.output.lower() or "artifact-path" in result.output.lower()
    # Should show key options
    assert "--format" in result.output
    assert "--output-dir" in result.output


@pytest.mark.unit
def test_help_lists_all_top_level_commands():
    """The --help output lists all top-level commands: version, doctor, info, sbom.

    Validates: Requirements 5.1
    """
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("version", "doctor", "info", "sbom"):
        assert command in result.output, f"Expected '{command}' in help output"


@pytest.mark.unit
def test_help_lists_all_sub_apps():
    """The --help output lists all sub-apps: mirror, index.

    Validates: Requirements 5.2
    """
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for sub_app in ("mirror", "index"):
        assert sub_app in result.output, f"Expected '{sub_app}' in help output"


@pytest.mark.unit
def test_help_exits_with_code_zero():
    """The --help invocation exits with code 0 without import errors.

    Validates: Requirements 5.3
    """
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Traceback" not in result.output
    assert "ImportError" not in result.output


@pytest.mark.unit
def test_bare_invocation_shows_help():
    """Bare invocation (no subcommand) displays top-level help text.

    Validates: Requirements 7.3
    """
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    # Should show help text with commands listed
    for command in ("version", "doctor", "info", "sbom"):
        assert command in result.output, f"Expected '{command}' in bare invocation output"
    for sub_app in ("mirror", "index"):
        assert sub_app in result.output, f"Expected '{sub_app}' in bare invocation output"


@pytest.mark.unit
def test_verbose_with_no_subcommand_shows_help():
    """Passing --verbose with no subcommand still displays help text and exits 0.

    Validates: Requirements 7.4
    """
    result = runner.invoke(app, ["--verbose"])
    assert result.exit_code == 0
    # Should still show help text
    for command in ("version", "doctor", "info", "sbom"):
        assert command in result.output, f"Expected '{command}' in --verbose output"
    for sub_app in ("mirror", "index"):
        assert sub_app in result.output, f"Expected '{sub_app}' in --verbose output"


@pytest.mark.unit
def test_verbose_sets_debug_level():
    """Passing --verbose sets the debcraft logger to DEBUG level."""
    logger = logging.getLogger("debcraft")
    # Reset to a non-DEBUG level before invocation
    logger.setLevel(logging.WARNING)

    result = runner.invoke(app, ["--verbose", "version"])
    assert result.exit_code == 0
    assert logger.level == logging.DEBUG


# --- Entry Point Resolution Tests (Requirements 6.1, 6.2, 6.3, 6.4, 6.5) ---

EXPECTED_SCANNER_ENTRY_POINTS = {"directory", "docker", "oci", "iso", "qcow2", "img", "ami"}
EXPECTED_WRITER_ENTRY_POINTS = {"spdx_3_0", "spdx_2_3", "cyclonedx"}


@pytest.mark.unit
def test_scanner_entry_points_resolve():
    """All declared scanner entry points load without error."""
    eps = importlib.metadata.entry_points(group="debcraft.scanners")
    loaded_names = set()
    for ep in eps:
        obj = ep.load()
        assert obj is not None, f"Scanner entry point '{ep.name}' loaded as None"
        loaded_names.add(ep.name)

    assert EXPECTED_SCANNER_ENTRY_POINTS.issubset(loaded_names), (
        f"Missing scanner entry points: {EXPECTED_SCANNER_ENTRY_POINTS - loaded_names}"
    )


@pytest.mark.unit
def test_sbom_writer_entry_points_resolve():
    """All declared SBOM writer entry points load without error."""
    eps = importlib.metadata.entry_points(group="debcraft.sbom_writers")
    loaded_names = set()
    for ep in eps:
        obj = ep.load()
        assert obj is not None, f"SBOM writer entry point '{ep.name}' loaded as None"
        loaded_names.add(ep.name)

    assert EXPECTED_WRITER_ENTRY_POINTS.issubset(loaded_names), (
        f"Missing SBOM writer entry points: {EXPECTED_WRITER_ENTRY_POINTS - loaded_names}"
    )


@pytest.mark.unit
def test_cli_app_is_typer_instance():
    """The debcraft.cli:app entry point resolves to a typer.Typer instance."""
    eps = importlib.metadata.entry_points(group="console_scripts")
    debcraft_eps = [ep for ep in eps if ep.name == "debcraft"]
    assert len(debcraft_eps) == 1, "Expected exactly one 'debcraft' console_scripts entry point"

    loaded_app = debcraft_eps[0].load()
    assert isinstance(loaded_app, typer.Typer), (
        f"Expected debcraft.cli:app to be a typer.Typer instance, got {type(loaded_app)}"
    )


# --- Mirror sync/clean with no repositories configured (Requirement 2.8) ---


@pytest.mark.unit
def test_mirror_sync_no_repos_exits_nonzero():
    """Mirror sync with no configured repositories exits with non-zero code and error message.

    Validates: Requirements 2.8
    """
    from debcraft.domain.mirror.config import MirrorConfig

    empty_config = MirrorConfig(repositories=[])

    with patch("debcraft.cli.mirror._read_config", return_value=empty_config):
        result = runner.invoke(app, ["mirror", "sync"])

    assert result.exit_code != 0, f"Expected non-zero exit code, got {result.exit_code}"
    assert "no repositories configured" in result.output.lower(), (
        f"Expected error about no repositories configured, got: {result.output}"
    )


@pytest.mark.unit
def test_mirror_clean_no_repos_exits_nonzero():
    """Mirror clean with no configured repositories exits with non-zero code and error message.

    Validates: Requirements 2.8
    """
    from debcraft.domain.mirror.config import MirrorConfig

    empty_config = MirrorConfig(repositories=[])

    with patch("debcraft.cli.mirror._read_config", return_value=empty_config):
        result = runner.invoke(app, ["mirror", "clean"])

    assert result.exit_code != 0, f"Expected non-zero exit code, got {result.exit_code}"
    assert "no repositories configured" in result.output.lower(), (
        f"Expected error about no repositories configured, got: {result.output}"
    )


# --- SBOM Command Error Handling Tests (Requirements 4.3, 4.4, 4.7) ---


@pytest.mark.unit
def test_sbom_nonexistent_path_exits_nonzero():
    """SBOM command with a nonexistent artifact path exits non-zero with error message.

    Validates: Requirement 4.7
    """
    result = runner.invoke(app, ["sbom", "/nonexistent/path"])
    assert result.exit_code != 0
    assert "does not exist" in result.output or "Error" in result.output


@pytest.mark.unit
def test_sbom_invalid_format_exits_nonzero():
    """SBOM command with an invalid format exits non-zero and lists valid formats.

    Validates: Requirement 4.3
    """
    result = runner.invoke(app, ["sbom", "/tmp", "--format", "invalid_format"])
    assert result.exit_code != 0
    assert "invalid_format" in result.output
    # Should list valid formats
    assert "spdx_3_0" in result.output
    assert "spdx_2_3" in result.output
    assert "cyclonedx" in result.output


@pytest.mark.unit
def test_sbom_no_format_defaults_to_all():
    """SBOM command with no --format option does not crash on format validation.

    When no format is specified, the command should default to all formats.
    We mock the workflow execution since it requires a real artifact, but
    verify that format validation passes without error.

    Validates: Requirement 4.4
    """
    with patch("debcraft.cli.sbom._validate_artifact_path"), patch("debcraft.cli.sbom.asyncio.run", return_value=[]):
        result = runner.invoke(app, ["sbom", "/tmp"])
    # The command may fail because no SBOM files were generated (mocked),
    # but it should NOT fail due to format validation.
    # Check that no format validation error occurred
    assert "Invalid format" not in result.output
