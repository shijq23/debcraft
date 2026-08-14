"""Deterministic JSON formatting for SBOM output."""

from __future__ import annotations

import json
from typing import Any


class SBOMPrinter:
    """Format Python dicts into deterministic JSON bytes.

    Produces consistent output suitable for reproducible builds and diffing:
    - 2-space indentation
    - Sorted keys within each object
    - UTF-8 encoding without BOM
    - Trailing newline character
    """

    def print(self, data: dict[str, Any]) -> bytes:
        """Format a Python dict into deterministic JSON bytes.

        Args:
            data: The dictionary to serialize.

        Returns:
            UTF-8 encoded bytes with 2-space indentation, sorted keys,
            no BOM, and a trailing newline.
        """
        json_str = json.dumps(
            data,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        return (json_str + "\n").encode("utf-8")
