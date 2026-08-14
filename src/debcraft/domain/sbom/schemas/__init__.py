"""Bundled JSON schema files for SBOM format validation.

This package contains the official JSON schemas for:
- SPDX 2.3 (from the SPDX specification repository)
- SPDX 3.0 (from the SPDX specification repository)
- CycloneDX 1.5 (from the CycloneDX specification repository)

Schemas are loaded via importlib.resources for offline validation
without requiring network access.
"""

from __future__ import annotations

import importlib.resources
import importlib.resources.abc
import json
from typing import Any

from debcraft.domain.sbom.values import OutputFormat

#: Mapping from output format to the primary schema filename.
_SCHEMA_FILES: dict[OutputFormat, str] = {
    OutputFormat.SPDX_2_3: "spdx-2.3.schema.json",
    OutputFormat.SPDX_3_0: "spdx-3.0.schema.json",
    OutputFormat.CYCLONEDX: "cyclonedx-1.5.schema.json",
}


def get_schema_path(format: OutputFormat) -> importlib.resources.abc.Traversable:  # noqa: A002
    """Return the traversable path to the schema file for the given format.

    Args:
        format: The SBOM output format whose schema is requested.

    Returns:
        A Traversable pointing to the schema JSON file.

    Raises:
        KeyError: If no schema is bundled for the given format.
    """
    filename = _SCHEMA_FILES[format]
    return importlib.resources.files(__package__).joinpath(filename)


def load_schema(format: OutputFormat) -> dict[str, Any]:  # noqa: A002
    """Load and parse the JSON schema for the given format.

    Args:
        format: The SBOM output format whose schema is requested.

    Returns:
        The parsed JSON schema as a dictionary.

    Raises:
        KeyError: If no schema is bundled for the given format.
        json.JSONDecodeError: If the schema file is corrupted.
    """
    path = get_schema_path(format)
    text = path.read_text(encoding="utf-8")
    return json.loads(text)  # type: ignore[no-any-return]
