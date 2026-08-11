"""Value objects for the package intelligence domain layer.

Immutable dataclasses representing parsed metadata from .deb archives,
DEP-5 copyright documents, SPDX license expressions, and license
mapping results. These carry no behavior beyond field access and are
produced by the package intelligence parsers and services.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True)
class DependencyRelation:
    """A single dependency relationship (possibly with alternatives).

    Attributes:
        package: Binary package name of the dependency.
        version_constraint: Optional version constraint (e.g. ">= 2.17").
        alternatives: Alternative packages that can satisfy this dependency.
    """

    package: str
    version_constraint: str | None = None
    alternatives: list[DependencyRelation] = field(default_factory=list)


@dataclass(frozen=True)
class DebParseResult:
    """Complete parse result from a .deb archive.

    Attributes:
        package_name: Binary package name extracted from the control file.
        version: Package version string.
        architecture: Target architecture (e.g. "amd64", "all").
        control_fields: All control file fields as key-value pairs.
        dependencies: Parsed dependency relationships.
        file_listing: List of file paths contained in the archive.
        copyright_text: Raw copyright file content, if present.
    """

    package_name: str
    version: str
    architecture: str
    control_fields: dict[str, str]
    dependencies: list[DependencyRelation]
    file_listing: list[str]
    copyright_text: str | None


@dataclass(frozen=True)
class DEP5Header:
    """Header paragraph of a DEP-5 document.

    Attributes:
        format_url: Format specification URL (required).
        upstream_name: Upstream project name.
        upstream_contact: Upstream contact information.
        source: Upstream source URL.
        comment: Optional comment text.
        extra_fields: Additional unrecognized fields.
    """

    format_url: str
    upstream_name: str | None = None
    upstream_contact: str | None = None
    source: str | None = None
    comment: str | None = None
    extra_fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DEP5FilesParagraph:
    """Files paragraph in a DEP-5 document.

    Attributes:
        files: List of file glob patterns covered by this paragraph.
        copyright: Copyright statement text.
        license_name: Short license identifier.
        license_text: Full license text body, if provided inline.
        comment: Optional comment text.
        extra_fields: Additional unrecognized fields.
    """

    files: list[str]
    copyright: str
    license_name: str
    license_text: str | None = None
    comment: str | None = None
    extra_fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DEP5LicenseParagraph:
    """Standalone License paragraph in a DEP-5 document.

    Attributes:
        license_name: Short license identifier.
        license_text: Full license text body.
        comment: Optional comment text.
        extra_fields: Additional unrecognized fields.
    """

    license_name: str
    license_text: str
    comment: str | None = None
    extra_fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DEP5Document:
    """Structured DEP-5 copyright document.

    Attributes:
        header: The document header paragraph.
        files_paragraphs: List of Files paragraphs.
        license_paragraphs: List of standalone License paragraphs.
    """

    header: DEP5Header
    files_paragraphs: list[DEP5FilesParagraph]
    license_paragraphs: list[DEP5LicenseParagraph]


# --- SPDX AST Nodes ---


@dataclass(frozen=True)
class SimpleNode:
    """Leaf node: a single SPDX license identifier.

    Attributes:
        identifier: The SPDX license identifier string.
        or_later: True when the identifier uses the '+' suffix (e.g. GPL-2.0+).
    """

    identifier: str
    or_later: bool = False


@dataclass(frozen=True)
class WithNode:
    """License WITH exception.

    Attributes:
        license: The base license expression node.
        exception: The SPDX exception identifier.
    """

    license: SPDXNode
    exception: str


@dataclass(frozen=True)
class AndNode:
    """Conjunction of two license expressions.

    Attributes:
        left: Left operand of the AND expression.
        right: Right operand of the AND expression.
    """

    left: SPDXNode
    right: SPDXNode


@dataclass(frozen=True)
class OrNode:
    """Disjunction of two license expressions.

    Attributes:
        left: Left operand of the OR expression.
        right: Right operand of the OR expression.
    """

    left: SPDXNode
    right: SPDXNode


# Union type for all SPDX AST nodes
SPDXNode = SimpleNode | WithNode | AndNode | OrNode


# --- SPDX Tokens ---


class SPDXTokenType(Enum):
    """Token types for SPDX expression lexer."""

    LICENSE_ID = "LICENSE_ID"
    OR_LATER = "OR_LATER"
    AND = "AND"
    OR = "OR"
    WITH = "WITH"
    LPAREN = "LPAREN"
    RPAREN = "RPAREN"
    DOCUMENT_REF = "DOCUMENT_REF"
    LICENSE_REF = "LICENSE_REF"


@dataclass(frozen=True)
class SPDXToken:
    """A typed token from the SPDX expression lexer.

    Attributes:
        type: The token type classification.
        value: The raw string value of the token.
        offset: Zero-based character offset in the original string.
    """

    type: SPDXTokenType
    value: str
    offset: int


# --- License Mapping ---


class MappingAlgorithm(Enum):
    """Algorithms used by the License Mapper."""

    EXACT_SPDX = "ExactSPDX"
    DEBIAN_ALIAS = "DebianAlias"
    NORMALIZED_SPELLING = "NormalizedSpelling"
    SPDX_FULL_NAME = "SPDXFullName"
    FUZZY_SIMILARITY = "FuzzySimilarity"
    LICENSE_TEXT_HASH = "LicenseTextHash"
    UNMAPPED = "Unmapped"


@dataclass(frozen=True)
class LicenseMappingResult:
    """Result of mapping a Debian license identifier to SPDX.

    Attributes:
        spdx_expression: The mapped SPDX expression string.
        confidence: Confidence score from 0 to 100.
        algorithm: The algorithm that produced this mapping.
        rationale: Human-readable explanation of the mapping decision.
    """

    spdx_expression: str
    confidence: int
    algorithm: MappingAlgorithm
    rationale: str


# --- Symlink Resolution ---


@dataclass(frozen=True)
class SymlinkResolutionResult:
    """Result of resolving a copyright symlink.

    Attributes:
        resolved: Whether the symlink was successfully resolved.
        target_path: The resolved target path, if successful.
        owning_package: The package that owns the target file.
        copyright_content: The copyright file content at the target.
        failure_reason: Explanation if resolution failed.
    """

    resolved: bool
    target_path: str | None = None
    owning_package: str | None = None
    copyright_content: str | None = None
    failure_reason: str | None = None
