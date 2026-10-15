"""Remove build artifacts and tool caches.

Cross-platform replacement for the ``rm -rf`` / ``find`` pipeline the ``clean``
target used to shell out to, so ``just clean`` and ``make clean`` behave the
same on Linux, macOS and Windows.

Run it with ``uv run python scripts/clean.py`` for a full clean, or pass
specific paths (``uv run python scripts/clean.py dist``) to remove only those.
Only the standard library is used, so it works even when the project
environment is half-built.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

# Paths removed outright, relative to the repository root.
TARGETS: Final[tuple[str, ...]] = (
    "dist",
    "site",
    "htmlcov",
    ".coverage",
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
    ".import_linter_cache",
)

# Directory names removed wherever they appear in the tree.
PATTERNS: Final[tuple[str, ...]] = ("__pycache__", "*.egg-info")

# Directories never descended into while searching for PATTERNS.
PRUNED: Final[frozenset[str]] = frozenset({".git", ".venv", ".hypothesis", "node_modules"})


def remove(path: Path) -> bool:
    """Delete ``path``, whether it is a directory, a file or absent.

    Args:
        path: Filesystem path to delete.

    Returns:
        True if something was deleted, False if the path did not exist.
    """
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
        return True
    if path.is_file() or path.is_symlink():
        path.unlink(missing_ok=True)
        return True
    return False


def find_cruft(root: Path) -> Iterator[Path]:
    """Yield directories under ``root`` whose name matches ``PATTERNS``.

    Args:
        root: Directory to search recursively.

    Yields:
        Matching directories. Matches are not descended into, and neither are
        the directories named in ``PRUNED``.
    """
    for parent, dirnames, _filenames in root.walk(top_down=True):
        matched = [name for name in dirnames if any(fnmatch(name, pattern) for pattern in PATTERNS)]
        dirnames[:] = [name for name in dirnames if name not in PRUNED and name not in matched]
        for name in matched:
            yield parent / name


def resolve_in_root(root: Path, target: str) -> Path:
    """Resolve ``target`` against ``root``, refusing anything outside it.

    Args:
        root: Repository root that deletions are confined to.
        target: Path given on the command line.

    Returns:
        The resolved path.

    Raises:
        ValueError: If the path escapes ``root``, or is ``root`` itself.
    """
    path = (root / target).resolve()
    if path == root or root not in path.parents:
        msg = f"refusing to remove {target!r}: outside the repository root"
        raise ValueError(msg)
    return path


def main(argv: Sequence[str] | None = None) -> int:
    """Clean the repository and report how many paths were removed.

    Args:
        argv: Command-line arguments. Defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description="Remove build artifacts and tool caches.")
    parser.add_argument(
        "targets",
        nargs="*",
        metavar="PATH",
        help="Paths to remove, relative to the repository root. Defaults to a full clean.",
    )
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    try:
        candidates = (
            [resolve_in_root(root, target) for target in args.targets]
            if args.targets
            else [root / target for target in TARGETS] + list(find_cruft(root))
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    removed = [path for path in candidates if remove(path)]
    for path in removed:
        print(f"removed {path.relative_to(root).as_posix()}")
    print(f"clean: removed {len(removed)} path(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
