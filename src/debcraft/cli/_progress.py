"""Shared progress bar factory for CLI modules."""

from __future__ import annotations

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

console = Console()


def create_progress_bar(*, disabled: bool = False) -> Progress:
    """Create a Rich Progress instance with standard debcraft column configuration.

    Columns: SpinnerColumn, TextColumn (description), BarColumn,
    TextColumn (percentage), TimeElapsedColumn.

    Args:
        disabled: If True, disables progress output. Defaults to False.

    Returns:
        A configured Progress instance bound to the module-level console.
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
        disable=disabled,
    )
