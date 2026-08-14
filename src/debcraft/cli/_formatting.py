"""Human-readable formatting utilities for CLI output."""


def format_bytes(n: int) -> str:
    """Format a non-negative byte count as a human-readable IEC string.

    Returns values like "0 B", "1.5 KiB", "42.3 MiB", "1.2 GiB".
    """
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KiB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MiB"
    return f"{n / (1024 * 1024 * 1024):.1f} GiB"
