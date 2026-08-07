"""Command-line interface for DebCraft."""

import os
import platform
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from debcraft.version import VERSION

app = typer.Typer(name="debcraft", help="DebCraft - Artifact Intelligence Platform")
console = Console()

from debcraft.cli.mirror import mirror_app  # noqa: E402

app.add_typer(mirror_app, name="mirror")


@dataclass
class DoctorCheck:
    """Result of a single doctor check."""

    name: str
    passed: bool
    message: str
    details: str | None = None


@dataclass
class EnvironmentInfo:
    """Environment information for the info command."""

    version: str
    python_version: str
    python_path: Path
    platform: str
    architecture: str
    package_location: Path
    venv_path: Path | None


def _check_python_version() -> DoctorCheck:
    """Check that Python version meets the minimum requirement."""
    major = sys.version_info.major
    minor = sys.version_info.minor
    current = f"{major}.{minor}.{sys.version_info.micro}"

    if major >= 3 and minor >= 13:
        return DoctorCheck(
            name="Python version",
            passed=True,
            message=f"Python {current} >= 3.13",
        )
    return DoctorCheck(
        name="Python version",
        passed=False,
        message=f"Python {current} < 3.13 (minimum required)",
        details=f"Current: {current}. Please upgrade to Python 3.13 or higher.",
    )


def _check_writable_temp_dir() -> DoctorCheck:
    """Check that the system temp directory is writable."""
    temp_dir = Path(tempfile.gettempdir())
    if os.access(temp_dir, os.W_OK):
        return DoctorCheck(
            name="Writable temp directory",
            passed=True,
            message=f"Temp directory is writable: {temp_dir}",
        )
    return DoctorCheck(
        name="Writable temp directory",
        passed=False,
        message=f"Temp directory is not writable: {temp_dir}",
        details="Ensure your system temp directory has write permissions.",
    )


def _check_writable_current_dir() -> DoctorCheck:
    """Check that the current working directory is writable."""
    current_dir = Path.cwd()
    if os.access(current_dir, os.W_OK):
        return DoctorCheck(
            name="Writable current directory",
            passed=True,
            message=f"Current directory is writable: {current_dir}",
        )
    return DoctorCheck(
        name="Writable current directory",
        passed=False,
        message=f"Current directory is not writable: {current_dir}",
        details="Ensure you have write permissions in the current directory.",
    )


def _gather_environment_info() -> EnvironmentInfo:
    """Gather environment information for display."""
    python_path = Path(sys.executable)
    package_location = Path(__file__).resolve().parent.parent

    venv_path: Path | None = None
    venv_env = os.environ.get("VIRTUAL_ENV")
    if venv_env:
        venv_path = Path(venv_env)

    return EnvironmentInfo(
        version=VERSION,
        python_version=sys.version,
        python_path=python_path,
        platform=platform.platform(),
        architecture=platform.machine(),
        package_location=package_location,
        venv_path=venv_path,
    )


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """DebCraft - Artifact Intelligence Platform."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@app.command()
def version() -> None:
    """Display the current DebCraft version."""
    console.print(f"debcraft {VERSION}")


@app.command()
def doctor() -> None:
    """Check environment health and report status."""
    checks: list[DoctorCheck] = [
        _check_python_version(),
        _check_writable_temp_dir(),
        _check_writable_current_dir(),
    ]

    table = Table(title="DebCraft Doctor")
    table.add_column("Status", style="bold", width=6)
    table.add_column("Check")
    table.add_column("Message")

    for check in checks:
        status = "[green]PASS[/green]" if check.passed else "[red]FAIL[/red]"
        message = check.message
        if check.details and not check.passed:
            message += f"\n  {check.details}"
        table.add_row(status, check.name, message)

    console.print(table)

    all_passed = all(check.passed for check in checks)
    if all_passed:
        console.print("\n[green]All checks passed.[/green]")
    else:
        console.print("\n[red]Some checks failed.[/red]")
        raise SystemExit(1)


@app.command()
def info() -> None:
    """Display configuration and environment information."""
    env_info = _gather_environment_info()

    table = Table(title="DebCraft Environment Info")
    table.add_column("Property", style="bold")
    table.add_column("Value")

    table.add_row("Version", env_info.version)
    table.add_row("Python version", env_info.python_version)
    table.add_row("Python path", str(env_info.python_path))
    table.add_row("Platform", env_info.platform)
    table.add_row("Architecture", env_info.architecture)
    table.add_row("Package location", str(env_info.package_location))
    table.add_row("Virtual environment", str(env_info.venv_path) if env_info.venv_path else "None")

    console.print(table)
