"""Property-based tests for CLI registration correctness.

**Validates: Requirements 4.2, 4.3, 5.3, 5.4, 5.5, 6.2, 6.3, 6.4, 7.2**

Property 1: Format validation accepts only valid formats.
For any string value, the SBOM format validation function accepts the string
if and only if it is a member of the supported format set {spdx_3_0, spdx_2_3, cyclonedx}.
Invalid strings are rejected with a typer.Exit, and the valid set remains unchanged.

Property 2: Help accessibility for all registered commands.
For any command path in the set of registered commands (top-level commands,
sub-app commands, and sub-app sub-commands), invoking that command path with
--help SHALL exit with code 0 and SHALL NOT produce import errors or tracebacks
in the output.

Property 3: Entry point resolution for all declared plugins.
For any entry point declared in the debcraft.scanners or debcraft.sbom_writers
groups in pyproject.toml, loading the entry point via importlib.metadata.entry_points()
and calling .load() SHALL resolve to an importable class without raising ImportError
or AttributeError.
"""

from __future__ import annotations

import importlib.metadata

import pytest
import typer
from hypothesis import given, settings
from hypothesis import strategies as st
from typer.testing import CliRunner

from debcraft.cli import app
from debcraft.cli.sbom import _validate_formats
from debcraft.domain.sbom.values import OutputFormat

# The complete set of valid format strings
VALID_FORMATS = {"spdx_3_0", "spdx_2_3", "cyclonedx"}


@pytest.mark.unit
class TestFormatValidationProperty:
    """Property 1: Format validation accepts only valid formats.

    For any arbitrary string, _validate_formats accepts it if and only if
    it belongs to the supported format set {spdx_3_0, spdx_2_3, cyclonedx}.
    """

    @settings(max_examples=100)
    @given(value=st.text())
    def test_format_validation_accepts_only_valid_formats(self, value: str) -> None:
        """**Validates: Requirements 4.2, 4.3**.

        For any arbitrary string, the validation function either:
        - Accepts it (returns OutputFormat list) if it's in the valid set
        - Rejects it (raises typer.Exit with code 1) if it's not in the valid set
        """
        if value in VALID_FORMATS:
            # Valid format strings should be accepted and return the corresponding enum
            result = _validate_formats([value])
            assert len(result) == 1
            assert isinstance(result[0], OutputFormat)
            assert result[0].value == value
        else:
            # Invalid format strings should be rejected with typer.Exit(code=1)
            with pytest.raises(typer.Exit) as exc_info:
                _validate_formats([value])
            assert exc_info.value.exit_code == 1


# ---------------------------------------------------------------------------
# Property 2: Help accessibility for all registered commands
# ---------------------------------------------------------------------------

# All valid command paths for the debcraft CLI
ALL_COMMAND_PATHS: list[list[str]] = [
    ["version"],
    ["doctor"],
    ["info"],
    ["sbom"],
    ["mirror", "sync"],
    ["mirror", "verify"],
    ["mirror", "status"],
    ["mirror", "list"],
    ["mirror", "clean"],
    ["index", "package"],
]

runner = CliRunner()


@pytest.mark.unit
class TestHelpAccessibilityProperty:
    """Property 2: Help accessibility for all registered commands.

    For any command path in the set of registered commands, invoking that
    command path with --help SHALL exit with code 0 and SHALL NOT produce
    import errors or tracebacks in the output.
    """

    @settings(max_examples=100)
    @given(command_path=st.sampled_from(ALL_COMMAND_PATHS))
    def test_all_commands_produce_help(self, command_path: list[str]) -> None:
        """**Validates: Requirements 5.3, 5.4, 5.5, 7.2**.

        For any registered command path, invoking with --help exits 0
        with no tracebacks or import errors.
        """
        result = runner.invoke(app, [*command_path, "--help"])

        assert result.exit_code == 0, (
            f"Command {command_path} --help exited with code {result.exit_code}.\nOutput: {result.output}"
        )
        assert "Traceback" not in result.output, (
            f"Command {command_path} --help produced a traceback.\nOutput: {result.output}"
        )
        assert "ImportError" not in result.output, (
            f"Command {command_path} --help produced an ImportError.\nOutput: {result.output}"
        )
        assert "AttributeError" not in result.output, (
            f"Command {command_path} --help produced an AttributeError.\nOutput: {result.output}"
        )


# ---------------------------------------------------------------------------
# Property 3: Entry point resolution for all declared plugins
# ---------------------------------------------------------------------------

# Combined list of all entry point names from scanners and writers groups
ALL_ENTRY_POINTS: list[tuple[str, str]] = [
    ("debcraft.scanners", "directory"),
    ("debcraft.scanners", "docker"),
    ("debcraft.scanners", "oci"),
    ("debcraft.scanners", "iso"),
    ("debcraft.scanners", "qcow2"),
    ("debcraft.scanners", "img"),
    ("debcraft.scanners", "ami"),
    ("debcraft.sbom_writers", "spdx_3_0"),
    ("debcraft.sbom_writers", "spdx_2_3"),
    ("debcraft.sbom_writers", "cyclonedx"),
]


@pytest.mark.unit
class TestEntryPointResolutionProperty:
    """Property 3: Entry point resolution for all declared plugins.

    For any entry point declared in the debcraft.scanners or debcraft.sbom_writers
    groups, loading the entry point and calling .load() SHALL resolve to an
    importable class without raising ImportError or AttributeError.
    """

    @settings(max_examples=100)
    @given(entry_point_info=st.sampled_from(ALL_ENTRY_POINTS))
    def test_all_entry_points_resolve(self, entry_point_info: tuple[str, str]) -> None:
        """**Validates: Requirements 6.2, 6.3, 6.4**.

        For any declared plugin entry point, loading it resolves to an
        importable class without ImportError or AttributeError.
        """
        group, name = entry_point_info
        eps = importlib.metadata.entry_points(group=group)
        matching = [ep for ep in eps if ep.name == name]

        assert len(matching) == 1, (
            f"Expected exactly one entry point '{name}' in group '{group}', found {len(matching)}"
        )

        # .load() should succeed without ImportError or AttributeError
        loaded = matching[0].load()
        assert loaded is not None, f"Entry point '{name}' in group '{group}' loaded as None"
