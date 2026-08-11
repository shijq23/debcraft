"""Port interfaces (Protocols) for package intelligence domain dependencies.

These define the contracts that infrastructure adapters must satisfy,
allowing the domain service to remain decoupled from concrete implementations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from debcraft.domain.package_intelligence.values import DebParseResult


class DebFileReader(Protocol):
    """Reads and decompresses .deb archive members."""

    def read_ar_member(self, deb_path: str, member_prefix: str) -> bytes:
        """Read and return raw bytes of an ar archive member matching the given prefix."""
        ...

    def compute_sha256(self, file_path: str) -> str:
        """Compute and return the SHA256 hex digest of the file at the given path."""
        ...


class ParseCachePort(Protocol):
    """Permanent parse cache keyed by SHA256."""

    async def get(self, sha256: str, parser_version: int) -> DebParseResult | None:
        """Retrieve a cached parse result matching the SHA256 and parser version, or None."""
        ...

    async def store(self, sha256: str, parser_version: int, result: DebParseResult) -> None:
        """Store a parse result in the cache keyed by SHA256 and parser version."""
        ...


class ContentsLookupPort(Protocol):
    """Queries file ownership from Contents index data."""

    def find_owner(self, file_path: str) -> str | None:
        """Return the qualified package name owning the given file path, or None."""
        ...

    def get_copyright_content(self, package_name: str) -> str | None:
        """Return the copyright text for a package, or None if not available."""
        ...
