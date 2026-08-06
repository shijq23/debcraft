"""Unit tests for DebCraft CLI commands."""

from unittest.mock import patch

import pytest
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
