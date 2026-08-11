"""Package intelligence domain bounded context.

Provides domain logic for extracting metadata, licensing information,
dependency relationships, and generating Package URLs from .deb archives.
"""

# --- Value Objects ---
# --- Parsers ---
from debcraft.domain.package_intelligence.deb_parser import DebParser
from debcraft.domain.package_intelligence.dep5_parser import DEP5Parser

# --- Printers / Serializers ---
from debcraft.domain.package_intelligence.dep5_printer import DEP5Printer

# --- Pure Functions ---
from debcraft.domain.package_intelligence.download_location import (
    resolve_download_location,
)

# --- Errors ---
from debcraft.domain.package_intelligence.errors import (
    DebParseError,
    DEP5ParseError,
    DependencyParseError,
    PURLGenerationError,
    SPDXParseError,
    SPDXTokenizeError,
)

# --- Services ---
from debcraft.domain.package_intelligence.license_mapper import LicenseMapper

# --- Port Protocols ---
from debcraft.domain.package_intelligence.ports import (
    ContentsLookupPort,
    DebFileReader,
    ParseCachePort,
)
from debcraft.domain.package_intelligence.purl_generator import generate_purl
from debcraft.domain.package_intelligence.spdx_parser import SPDXExpressionParser
from debcraft.domain.package_intelligence.spdx_printer import SPDXPrinter
from debcraft.domain.package_intelligence.spdx_tokenizer import SPDXTokenizer
from debcraft.domain.package_intelligence.symlink_resolver import SymlinkResolver
from debcraft.domain.package_intelligence.values import (
    AndNode,
    DebParseResult,
    DEP5Document,
    DEP5FilesParagraph,
    DEP5Header,
    DEP5LicenseParagraph,
    DependencyRelation,
    LicenseMappingResult,
    MappingAlgorithm,
    OrNode,
    SimpleNode,
    SPDXNode,
    SPDXToken,
    SPDXTokenType,
    SymlinkResolutionResult,
    WithNode,
)

__all__ = [
    # Value Objects
    "AndNode",
    # Port Protocols
    "ContentsLookupPort",
    "DEP5Document",
    "DEP5FilesParagraph",
    "DEP5Header",
    "DEP5LicenseParagraph",
    "DEP5ParseError",
    "DEP5Parser",
    # Printers / Serializers
    "DEP5Printer",
    "DebFileReader",
    # Errors
    "DebParseError",
    "DebParseResult",
    # Parsers
    "DebParser",
    "DependencyParseError",
    "DependencyRelation",
    # Services
    "LicenseMapper",
    "LicenseMappingResult",
    "MappingAlgorithm",
    "OrNode",
    "PURLGenerationError",
    "ParseCachePort",
    "SPDXExpressionParser",
    "SPDXNode",
    "SPDXParseError",
    "SPDXPrinter",
    "SPDXToken",
    "SPDXTokenType",
    "SPDXTokenizeError",
    "SPDXTokenizer",
    "SimpleNode",
    "SymlinkResolutionResult",
    "SymlinkResolver",
    "WithNode",
    # Pure Functions
    "generate_purl",
    "resolve_download_location",
]
