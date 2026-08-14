"""Property-based tests for SBOM enrichment pipeline.

# Feature: sbom-enrichment-pipeline
"""

from __future__ import annotations

import asyncio
import bz2
import gzip
import lzma
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import zstandard
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from typer.testing import CliRunner

from debcraft.cli import app
from debcraft.domain.package_intelligence.errors import DEP5ParseError, PURLGenerationError
from debcraft.domain.package_intelligence.purl_generator import generate_purl
from debcraft.domain.package_intelligence.values import (
    DebParseResult,
    DEP5Document,
    DEP5FilesParagraph,
    DEP5Header,
    LicenseMappingResult,
    MappingAlgorithm,
)
from debcraft.domain.sbom.assembler import ModelAssembler
from debcraft.domain.sbom.values import ExternalReferenceCategory, OutputFormat, WriterResult
from debcraft.domain.scanner.values import EnrichedPackage, IdentifiedPackage, PackageEnrichment
from debcraft.infrastructure.models.base import Base
from debcraft.infrastructure.models.metadata import (
    LicenseExpression,
    PackageInstance,
    Repository,
    RepositorySnapshot,
)
from debcraft.infrastructure.package_intelligence.file_reader import LocalDebFileReader
from debcraft.infrastructure.package_intelligence.iso_file_reader import ISODebFileReader
from debcraft.infrastructure.scanners.deb_extractor import DebExtractor
from debcraft.infrastructure.scanners.enricher import MetadataEnricher

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _MockISOReader:
    """Mock ISOReader that returns pre-loaded file bytes."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self._files = files or {}

    def open(self, path: str) -> None:
        pass

    def list_dir(self, path: str) -> list[str]:
        return []

    def read_file(self, path: str) -> bytes:
        if path in self._files:
            return self._files[path]
        raise FileNotFoundError(f"File not found in mock ISO: {path}")

    def close(self) -> None:
        pass


def _build_ar_archive(members: list[tuple[str, bytes]]) -> bytes:
    """Build a minimal valid ar archive with the given members.

    Args:
        members: List of (name, content) tuples. Each name must be at most
            15 characters (to fit name + '/' in the 16-byte field).

    Returns:
        Raw bytes of a valid ar archive.
    """
    buf = bytearray(b"!<arch>\n")

    for name, content in members:
        # 60-byte header: name(16) + timestamp(12) + owner(6) + group(6)
        #                 + mode(8) + size(10) + magic(2)
        # Name field is exactly 16 bytes; name is stored as "name/" padded with spaces
        name_with_slash = (name + "/")[:16]
        padded_name = f"{name_with_slash:16s}"
        header = (
            f"{padded_name}"
            f"{'0':12s}"  # timestamp
            f"{'0':6s}"  # owner
            f"{'0':6s}"  # group
            f"{'100644':8s}"  # mode
            f"{len(content):<10d}"  # size
            "`\n"  # magic
        )
        buf.extend(header.encode("ascii"))
        buf.extend(content)
        # Pad to even boundary
        if len(content) % 2 != 0:
            buf.extend(b"\n")

    return bytes(buf)


# ---------------------------------------------------------------------------
# Strategies for generating ar archive members
# ---------------------------------------------------------------------------

# Member name strategy: valid ar member names typical for .deb files
_member_name_base = st.sampled_from(
    [
        "debian-binary",
        "control.tar",
        "data.tar",
    ]
)

# Compression suffixes (including no compression).
# ar format: name field is 16 bytes, stored as "name/" padded with spaces.
# So the full member name must be ≤ 15 chars (name + "/" ≤ 16).
# "control.tar" = 11 chars → max suffix = 4 chars → .gz, .xz, .zst, .bz2 OK
# "data.tar" = 8 chars → all suffixes OK including .lzma (13 + "/" = 14)
# "debian-binary" = 13 chars → no suffix (convention)
# We use suffixes that work with "control.tar" (the longest base name with suffix).
_compression_suffix = st.sampled_from(["", ".gz", ".xz", ".zst", ".bz2"])

# Content: arbitrary bytes (but not too large to keep tests fast)
_member_content = st.binary(min_size=0, max_size=512)


def _compress(data: bytes, suffix: str) -> bytes:
    """Compress data according to the given suffix."""
    if suffix == ".gz":
        return gzip.compress(data)
    if suffix == ".xz":
        return lzma.compress(data, format=lzma.FORMAT_XZ)
    if suffix == ".zst":
        cctx = zstandard.ZstdCompressor()
        return cctx.compress(data)
    if suffix == ".bz2":
        return bz2.compress(data)
    if suffix == ".lzma":
        return lzma.compress(data, format=lzma.FORMAT_ALONE)
    return data


@st.composite
def _ar_member(draw: st.DrawFn) -> tuple[str, str, bytes, bytes]:
    """Generate a single ar member with name, compression suffix, raw content, and compressed bytes.

    Returns:
        (full_name, member_prefix, raw_content, compressed_content)
    """
    base_name = draw(_member_name_base)
    suffix = draw(_compression_suffix)
    # debian-binary doesn't typically have a compression suffix
    if base_name == "debian-binary":
        suffix = ""
    full_name = base_name + suffix
    raw_content = draw(_member_content)
    compressed = _compress(raw_content, suffix)
    return full_name, base_name, raw_content, compressed


@st.composite
def _ar_archive_with_target(draw: st.DrawFn) -> tuple[bytes, str, bytes]:
    """Generate a valid ar archive with at least one member and a target to extract.

    Returns:
        (archive_bytes, member_prefix_to_search, expected_decompressed_content)
    """
    # Generate 1-3 members, ensuring unique base names
    num_members = draw(st.integers(min_value=1, max_value=3))
    base_names_used: set[str] = set()
    members: list[tuple[str, bytes]] = []
    target_prefix: str | None = None
    target_content: bytes | None = None

    for _i in range(num_members):
        # Pick a base name not already used
        available_bases = [b for b in ["debian-binary", "control.tar", "data.tar"] if b not in base_names_used]
        if not available_bases:
            break
        base_name = draw(st.sampled_from(available_bases))
        base_names_used.add(base_name)

        suffix = draw(_compression_suffix)
        if base_name == "debian-binary":
            suffix = ""
        full_name = base_name + suffix
        raw_content = draw(_member_content)
        compressed = _compress(raw_content, suffix)
        members.append((full_name, compressed))

        # Use the first member as the target to extract
        if target_prefix is None:
            target_prefix = base_name
            target_content = raw_content

    archive_bytes = _build_ar_archive(members)
    assert target_prefix is not None
    assert target_content is not None
    return archive_bytes, target_prefix, target_content


# ---------------------------------------------------------------------------
# Property 5: ISODebFileReader Ar Member Extraction
# ---------------------------------------------------------------------------

# Feature: sbom-enrichment-pipeline, Property 5: ISODebFileReader Ar Member Extraction


@pytest.mark.property
class TestISODebFileReaderArMemberExtraction:
    """Property 5: ISODebFileReader Ar Member Extraction.

    For any valid ar archive stored within an ISO filesystem,
    ISODebFileReader.read_ar_member SHALL return identical decompressed
    bytes as LocalDebFileReader.read_ar_member would for the same
    archive content on the local filesystem.
    """

    @given(data=_ar_archive_with_target())
    @settings(max_examples=100)
    def test_iso_reader_matches_local_reader(
        self,
        data: tuple[bytes, str, bytes],
        tmp_path_factory: pytest.TempPathFactory,
    ) -> None:
        """ISODebFileReader produces identical output to LocalDebFileReader.

        **Validates: Requirements 8.8**
        """
        archive_bytes, member_prefix, _expected_content = data

        # Write the archive to a temp file for LocalDebFileReader
        tmp_path = tmp_path_factory.mktemp("deb")
        deb_file = tmp_path / "test.deb"
        deb_file.write_bytes(archive_bytes)

        # Read via LocalDebFileReader (filesystem)
        local_reader = LocalDebFileReader()
        local_result = local_reader.read_ar_member(str(deb_file), member_prefix)

        # Read via ISODebFileReader (mock ISO with same bytes)
        iso_path = "pool/main/t/test/test_1.0_amd64.deb"
        mock_iso = _MockISOReader(files={iso_path: archive_bytes})
        iso_reader = ISODebFileReader(mock_iso)
        iso_result = iso_reader.read_ar_member(iso_path, member_prefix)

        # Both readers must produce identical decompressed bytes
        assert iso_result == local_result

    @given(data=_ar_archive_with_target())
    @settings(max_examples=100)
    def test_iso_reader_returns_expected_decompressed_content(
        self,
        data: tuple[bytes, str, bytes],
    ) -> None:
        """ISODebFileReader decompresses to the original raw content.

        **Validates: Requirements 8.8**
        """
        archive_bytes, member_prefix, expected_content = data

        iso_path = "pool/main/t/test/test_1.0_amd64.deb"
        mock_iso = _MockISOReader(files={iso_path: archive_bytes})
        iso_reader = ISODebFileReader(mock_iso)
        result = iso_reader.read_ar_member(iso_path, member_prefix)

        assert result == expected_content

    @given(
        archive_bytes=st.builds(
            _build_ar_archive,
            members=st.just([("debian-binary", b"2.0\n")]),
        )
    )
    @settings(max_examples=100)
    def test_empty_prefix_returns_ar_magic(
        self,
        archive_bytes: bytes,
    ) -> None:
        """Empty prefix returns the ar magic header bytes for both readers.

        **Validates: Requirements 8.8**
        """
        iso_path = "pool/main/t/test/test_1.0_amd64.deb"
        mock_iso = _MockISOReader(files={iso_path: archive_bytes})
        iso_reader = ISODebFileReader(mock_iso)
        iso_result = iso_reader.read_ar_member(iso_path, "")

        assert iso_result == b"!<arch>\n"


# ---------------------------------------------------------------------------
# Strategies for Property 6: DebExtractor
# ---------------------------------------------------------------------------

# Valid Debian package names: lowercase letters, digits, plus, minus, dot (starting with letter)
_deb_package_name = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789+-"),
    min_size=2,
    max_size=30,
).filter(lambda s: s[0].isalpha() and not s.endswith("-") and not s.endswith("+"))

# Valid version strings
_deb_version = st.from_regex(r"[0-9]+\.[0-9]+(\.[0-9]+)?(-[0-9]+)?", fullmatch=True)

# Valid architectures
_deb_architecture = st.sampled_from(["amd64", "i386", "arm64", "armhf", "all"])

# Optional control fields
_optional_field_value = st.text(
    alphabet=st.characters(categories=("L", "N", "P", "Z"), max_codepoint=127),
    min_size=1,
    max_size=80,
).filter(lambda s: s.strip() == s and len(s) > 0)

# DEP5 license names (valid identifiers)
_dep5_license_name = st.sampled_from(
    [
        "GPL-2.0",
        "GPL-3.0+",
        "MIT",
        "Apache-2.0",
        "BSD-3-Clause",
        "LGPL-2.1",
        "MPL-2.0",
    ]
)


@st.composite
def _control_fields(draw: st.DrawFn) -> dict[str, str]:
    """Generate random control fields with required Package, Version, Architecture."""
    name = draw(_deb_package_name)
    version = draw(_deb_version)
    arch = draw(_deb_architecture)

    fields: dict[str, str] = {
        "Package": name,
        "Version": version,
        "Architecture": arch,
    }

    # Optionally add extra control fields
    if draw(st.booleans()):
        fields["Maintainer"] = draw(_optional_field_value)
    if draw(st.booleans()):
        fields["Homepage"] = (
            f"https://{draw(st.text(alphabet='abcdefghijklmnopqrstuvwxyz', min_size=3, max_size=15))}.org"
        )
    if draw(st.booleans()):
        fields["Depends"] = draw(_optional_field_value)
    if draw(st.booleans()):
        fields["Section"] = draw(st.sampled_from(["libs", "utils", "net", "admin", "devel"]))
    if draw(st.booleans()):
        fields["Priority"] = draw(st.sampled_from(["required", "important", "standard", "optional", "extra"]))
    if draw(st.booleans()):
        fields["Description"] = draw(_optional_field_value)
    if draw(st.booleans()):
        fields["Source"] = draw(_deb_package_name)

    return fields


@st.composite
def _dep5_copyright_data(draw: st.DrawFn) -> tuple[str, list[DEP5FilesParagraph]]:
    """Generate valid DEP5 copyright text and the expected parsed files paragraphs.

    Returns:
        (copyright_text, files_paragraphs) where paragraphs contain the
        license_name values that should be mapped.
    """
    num_paragraphs = draw(st.integers(min_value=1, max_value=3))
    paragraphs: list[DEP5FilesParagraph] = []

    for _ in range(num_paragraphs):
        license_name = draw(_dep5_license_name)
        paragraphs.append(
            DEP5FilesParagraph(
                files=["*"],
                copyright="2024 Test Author",
                license_name=license_name,
                license_text=None,
            )
        )

    # Build copyright text (content doesn't matter since we mock the parser)
    copyright_text = "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n\n"
    for para in paragraphs:
        copyright_text += f"Files: *\nCopyright: 2024 Test\nLicense: {para.license_name}\n\n"

    return copyright_text, paragraphs


@st.composite
def _deb_extraction_scenario(draw: st.DrawFn) -> dict:
    """Generate a complete DebExtractor test scenario.

    Returns a dict with:
        - pkg: IdentifiedPackage
        - control_fields: dict of control fields
        - has_copyright: whether DEP5 copyright is present
        - copyright_text: the copyright text (or None)
        - dep5_paragraphs: list of DEP5FilesParagraph (if has_copyright)
    """
    control_fields = draw(_control_fields())
    name = control_fields["Package"]
    version = control_fields["Version"]
    arch = control_fields["Architecture"]

    pkg = IdentifiedPackage(
        name=name,
        version=version,
        architecture=arch,
        status="installed",
    )

    has_copyright = draw(st.booleans())
    copyright_text: str | None = None
    dep5_paragraphs: list[DEP5FilesParagraph] = []

    if has_copyright:
        copyright_text, dep5_paragraphs = draw(_dep5_copyright_data())

    return {
        "pkg": pkg,
        "control_fields": control_fields,
        "has_copyright": has_copyright,
        "copyright_text": copyright_text,
        "dep5_paragraphs": dep5_paragraphs,
    }


# ---------------------------------------------------------------------------
# Property 6: Direct .deb Extraction Produces Valid Enrichment
# ---------------------------------------------------------------------------

# Feature: sbom-enrichment-pipeline, Property 6: Direct .deb Extraction Produces Valid Enrichment


@pytest.mark.property
class TestDebExtractorValidEnrichment:
    """Property 6: Direct .deb Extraction Produces Valid Enrichment.

    For any valid .deb archive containing a control file with Package, Version,
    and Architecture fields, extract_enrichment SHALL produce a PackageEnrichment
    where all control fields present in the .deb are mapped to the corresponding
    enrichment fields, and if a valid DEP5 copyright file is present, the
    license_expressions list is non-empty.
    """

    @given(scenario=_deb_extraction_scenario())
    @settings(max_examples=100)
    def test_extract_enrichment_maps_control_fields_and_licenses(
        self,
        scenario: dict,
    ) -> None:
        """All control fields are mapped to enrichment and DEP5 produces non-empty licenses.

        **Validates: Requirements 8.2, 8.3, 8.5**
        """
        pkg: IdentifiedPackage = scenario["pkg"]
        control_fields: dict[str, str] = scenario["control_fields"]
        has_copyright: bool = scenario["has_copyright"]
        copyright_text: str | None = scenario["copyright_text"]
        dep5_paragraphs: list[DEP5FilesParagraph] = scenario["dep5_paragraphs"]

        # Build the expected .deb path in pool/
        deb_filename = f"{pkg.name}_{pkg.version}_{pkg.architecture}.deb"

        # Determine the pool path prefix
        prefix_dir = pkg.name[:4] if pkg.name.startswith("lib") and len(pkg.name) > 3 else pkg.name[0:1]

        # --- Mock ISOReader ---
        mock_iso_reader = MagicMock()
        mock_iso_reader.list_dir.side_effect = lambda path: {
            "pool": ["main"],
            f"pool/main/{prefix_dir}/{pkg.name}": [deb_filename],
        }.get(path, [])

        # --- Mock DebParser ---
        mock_deb_parser = MagicMock()
        parse_result = DebParseResult(
            package_name=pkg.name,
            version=pkg.version,
            architecture=pkg.architecture,
            control_fields=control_fields,
            dependencies=[],
            file_listing=[],
            copyright_text=copyright_text,
        )
        mock_deb_parser.parse.return_value = parse_result

        # --- Mock DEP5Parser ---
        mock_dep5_parser = MagicMock()
        if has_copyright:
            dep5_doc = DEP5Document(
                header=DEP5Header(format_url="https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/"),
                files_paragraphs=dep5_paragraphs,
                license_paragraphs=[],
            )
            mock_dep5_parser.parse.return_value = dep5_doc
        else:
            # No copyright text → _extract_license_expressions returns [] early
            mock_dep5_parser.parse.side_effect = DEP5ParseError("Not DEP5")

        # --- Mock LicenseMapper ---
        mock_license_mapper = MagicMock()
        mock_license_mapper.map.return_value = LicenseMappingResult(
            spdx_expression="MIT",
            confidence=100,
            algorithm=MappingAlgorithm.EXACT_SPDX,
            rationale="Exact match",
        )

        # --- Execute ---
        extractor = DebExtractor(
            iso_reader=mock_iso_reader,
            deb_parser=mock_deb_parser,
            dep5_parser=mock_dep5_parser,
            license_mapper=mock_license_mapper,
        )
        enrichment = extractor.extract_enrichment(pkg)

        # --- Assertions ---
        # extract_enrichment should produce a result (not None)
        assert enrichment is not None

        # All control fields should be mapped to enrichment
        if "Maintainer" in control_fields:
            assert enrichment.maintainer == control_fields["Maintainer"]
        else:
            assert enrichment.maintainer is None

        if "Homepage" in control_fields:
            assert enrichment.homepage == control_fields["Homepage"]
        else:
            assert enrichment.homepage is None

        if "Depends" in control_fields:
            assert enrichment.depends == control_fields["Depends"]
        else:
            assert enrichment.depends is None

        if "Section" in control_fields:
            assert enrichment.section == control_fields["Section"]
        else:
            assert enrichment.section is None

        if "Priority" in control_fields:
            assert enrichment.priority == control_fields["Priority"]
        else:
            assert enrichment.priority is None

        if "Description" in control_fields:
            assert enrichment.description == control_fields["Description"]
        else:
            assert enrichment.description is None

        if "Source" in control_fields:
            assert enrichment.source_package == control_fields["Source"]
        else:
            assert enrichment.source_package is None

        # PURL should be generated
        assert enrichment.purl is not None
        assert enrichment.purl.startswith("pkg:deb/")

        # License expressions: non-empty when valid DEP5 copyright is present
        if has_copyright and dep5_paragraphs:
            # DEP5 copyright with file paragraphs should produce non-empty license_expressions
            # (each unique license_name gets mapped)
            assert len(enrichment.license_expressions) > 0
            # Each expression should be a (spdx_expression, algorithm) tuple
            for expr, algo in enrichment.license_expressions:
                assert isinstance(expr, str)
                assert len(expr) > 0
                assert isinstance(algo, str)


# ---------------------------------------------------------------------------
# Property 1: Snapshot Resolution Returns Highest Published ID
# ---------------------------------------------------------------------------

# Feature: sbom-enrichment-pipeline, Property 1: Snapshot Resolution Returns Highest Published ID

# Strategies for snapshot records
_snapshot_record = st.tuples(
    st.integers(min_value=1, max_value=100_000),
    st.booleans(),
)

# Lists of snapshot records with unique IDs (DB requires unique primary keys).
_snapshot_list = st.lists(
    _snapshot_record,
    min_size=0,
    max_size=50,
).filter(lambda records: len({r[0] for r in records}) == len(records))


async def _setup_snapshot_db() -> async_sessionmaker[AsyncSession]:
    """Create an in-memory metadata.db with the RepositorySnapshot table."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory


async def _seed_snapshots(
    factory: async_sessionmaker[AsyncSession],
    snapshots: list[tuple[int, bool]],
) -> None:
    """Seed RepositorySnapshot records. Each tuple is (id, published)."""
    async with factory() as session:
        # Create a repository to satisfy the FK constraint
        await session.execute(
            insert(Repository).values(
                id=1,
                name="test-repo",
                base_url="http://example.com",
                suite="bookworm",
                component="main",
            )
        )
        await session.commit()

    async with factory() as session:
        for snap_id, published in snapshots:
            await session.execute(
                insert(RepositorySnapshot).values(
                    id=snap_id,
                    repository_id=1,
                    schema_version=1,
                    captured_at=datetime.now(UTC),
                    published=published,
                )
            )
        await session.commit()


@pytest.mark.property
class TestProperty1SnapshotResolutionReturnsHighestPublishedId:
    """Property 1: Snapshot Resolution Returns Highest Published ID.

    Feature: sbom-enrichment-pipeline, Property 1: Snapshot Resolution Returns Highest Published ID
    """

    @settings(max_examples=100)
    @given(snapshots=_snapshot_list)
    def test_resolve_snapshot_id_returns_highest_published_or_zero(
        self,
        snapshots: list[tuple[int, bool]],
    ) -> None:
        """resolve_snapshot_id returns the highest published ID, or 0 if none published.

        **Validates: Requirements 1.1, 1.3**

        For any list of (id, published) snapshot records:
        - If at least one has published=True, the result is the max id among published records.
        - If none are published (or the list is empty), the result is 0.
        """

        async def _run() -> None:
            from debcraft.cli._sbom_db import resolve_snapshot_id

            factory = await _setup_snapshot_db()
            await _seed_snapshots(factory, snapshots)

            result = await resolve_snapshot_id(session_factory=factory, explicit_id=None)

            # Compute expected value
            published_ids = [snap_id for snap_id, published in snapshots if published]
            expected = max(published_ids) if published_ids else 0

            assert result == expected, (
                f"Expected {expected} but got {result}. Snapshots: {snapshots}, Published IDs: {published_ids}"
            )

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Property 2: Snapshot ID Input Validation
# ---------------------------------------------------------------------------

# Feature: sbom-enrichment-pipeline, Property 2: Snapshot ID Input Validation

_MAX_SNAPSHOT_ID = 2_147_483_647

_cli_runner = CliRunner()

# Valid snapshot IDs: positive integers in [1, 2_147_483_647]
_valid_snapshot_ids = st.integers(min_value=1, max_value=_MAX_SNAPSHOT_ID)

# Invalid snapshot IDs: negative integers, zero, floats, non-numeric, empty, overflow
_negative_integers = st.integers(max_value=-1).map(str)
_zero = st.just("0")
_floats = st.floats(allow_nan=False, allow_infinity=False).map(lambda f: f"{f}")
_non_numeric = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz!@#$%^&*()_+-=[]{}|;':\",./<>?"),
    min_size=1,
    max_size=20,
).filter(lambda s: not s.lstrip("-").replace(".", "", 1).isdigit())
_empty_string = st.just("")
_overflow_integers = st.integers(min_value=_MAX_SNAPSHOT_ID + 1, max_value=_MAX_SNAPSHOT_ID * 10).map(str)

_invalid_snapshot_ids = st.one_of(
    _negative_integers,
    _zero,
    _floats,
    _non_numeric,
    _empty_string,
    _overflow_integers,
)


@pytest.mark.property
@settings(max_examples=100)
@given(snapshot_id=_valid_snapshot_ids)
def test_valid_snapshot_ids_accepted(snapshot_id: int) -> None:
    """Valid positive integers in [1, 2_147_483_647] are accepted by --snapshot-id.

    **Validates: Requirements 1.2, 1.5**
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        artifact = tmp_path / "artifact.deb"
        artifact.write_text("fake artifact")

        with patch("debcraft.cli.sbom._run_sbom", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = [
                WriterResult(
                    output_path=tmp_path / "sbom.spdx.json",
                    format=OutputFormat.SPDX_2_3,
                    sha256="a" * 64,
                    file_size=1024,
                )
            ]
            result = _cli_runner.invoke(
                app,
                [
                    "sbom",
                    str(artifact),
                    "--snapshot-id",
                    str(snapshot_id),
                    "--output-dir",
                    str(tmp_path),
                ],
            )

        assert result.exit_code == 0, (
            f"Expected exit code 0 for valid snapshot_id={snapshot_id}, got {result.exit_code}: {result.output}"
        )
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs.get("snapshot_id") == snapshot_id


@pytest.mark.property
@settings(max_examples=100)
@given(snapshot_id=_invalid_snapshot_ids)
def test_invalid_snapshot_ids_rejected(snapshot_id: str) -> None:
    """Invalid inputs (negative, zero, floats, non-numeric, empty, overflow) are rejected with non-zero exit.

    **Validates: Requirements 1.2, 1.5**
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        artifact = tmp_path / "artifact.deb"
        artifact.write_text("fake artifact")

        result = _cli_runner.invoke(
            app,
            [
                "sbom",
                str(artifact),
                "--snapshot-id",
                snapshot_id,
                "--output-dir",
                str(tmp_path),
            ],
        )

        assert result.exit_code != 0, (
            f"Expected non-zero exit code for invalid snapshot_id={snapshot_id!r}, got {result.exit_code}"
        )


# ---------------------------------------------------------------------------
# Property 4: ModelAssembler Enrichment-to-SBOMPackage Mapping
# ---------------------------------------------------------------------------

# Feature: sbom-enrichment-pipeline, Property 4: ModelAssembler Enrichment-to-SBOMPackage Mapping

# Strategy for package identity fields (non-empty, no whitespace-only)
_pkg_name = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789+-."),
    min_size=1,
    max_size=30,
).filter(lambda s: s[0].isalnum())

_pkg_version = st.from_regex(r"[0-9]+\.[0-9]+(\.[0-9]+)?(-[0-9]+)?", fullmatch=True)

_pkg_architecture = st.sampled_from(["amd64", "i386", "arm64", "armhf", "all", ""])

# Optional string enrichment field: either None or a non-empty string value.
_optional_str = st.one_of(
    st.none(),
    st.text(
        alphabet=st.characters(categories=("L", "N", "P"), max_codepoint=127),
        min_size=1,
        max_size=60,
    ).filter(lambda s: len(s) > 0),
)

# Optional PURL: either None or a plausible pkg:deb PURL string.
_optional_purl = st.one_of(
    st.none(),
    st.builds(
        lambda name, ver: f"pkg:deb/debian/{name}@{ver}",
        _pkg_name,
        _pkg_version,
    ),
)

# license_expressions: possibly empty list of (expression, source) tuples.
_license_expressions = st.lists(
    st.tuples(
        st.text(
            alphabet=st.sampled_from("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-+ ()"),
            min_size=1,
            max_size=30,
        ).filter(lambda s: len(s.strip()) > 0),
        st.sampled_from(["dep5", "exact_spdx", "heuristic", "fallback"]),
    ),
    min_size=0,
    max_size=4,
)


@st.composite
def _identified_package(draw: st.DrawFn) -> IdentifiedPackage:
    """Generate a valid IdentifiedPackage."""
    return IdentifiedPackage(
        name=draw(_pkg_name),
        version=draw(_pkg_version),
        architecture=draw(_pkg_architecture),
        status="installed",
    )


@st.composite
def _package_enrichment(draw: st.DrawFn) -> PackageEnrichment:
    """Generate a PackageEnrichment with varying (some None) field combinations."""
    return PackageEnrichment(
        source_package=draw(_optional_str),
        maintainer=draw(_optional_str),
        homepage=draw(_optional_str),
        depends=draw(_optional_str),
        section=draw(_optional_str),
        priority=draw(_optional_str),
        description=draw(_optional_str),
        sha256=draw(st.one_of(st.none(), st.text(alphabet="0123456789abcdef", min_size=64, max_size=64))),
        download_url=draw(_optional_str),
        purl=draw(_optional_purl),
        license_expressions=draw(_license_expressions),
    )


@st.composite
def _enriched_package(draw: st.DrawFn) -> EnrichedPackage:
    """Generate an EnrichedPackage with a non-None PackageEnrichment."""
    return EnrichedPackage(
        package=draw(_identified_package()),
        enrichment=draw(_package_enrichment()),
    )


@pytest.mark.property
class TestProperty4ModelAssemblerEnrichmentMapping:
    """Property 4: ModelAssembler Enrichment-to-SBOMPackage Mapping.

    Feature: sbom-enrichment-pipeline, Property 4: ModelAssembler Enrichment-to-SBOMPackage Mapping

    For any EnrichedPackage with a non-None PackageEnrichment,
    ModelAssembler._build_single_package SHALL produce an SBOMPackage where:
    download_location equals enrichment.download_url (if non-None),
    supplier equals enrichment.maintainer (if non-None),
    package_url equals enrichment.purl (if non-None),
    concluded_license and declared_license both equal
    enrichment.license_expressions[0][0] (if the list is non-empty),
    and all None enrichment fields result in None in the corresponding
    SBOMPackage fields.
    """

    @settings(max_examples=100)
    @given(enriched_pkg=_enriched_package())
    def test_enrichment_fields_map_to_sbom_package(
        self,
        enriched_pkg: EnrichedPackage,
    ) -> None:
        """Enrichment fields are correctly mapped; None fields stay None.

        **Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 7.6**
        """
        assembler = ModelAssembler()
        enrichment = enriched_pkg.enrichment
        assert enrichment is not None  # invariant of the generator

        sbom_pkg = assembler._build_single_package(enriched_pkg, "SPDXRef-Package-1")

        # Req 7.2: download_location equals download_url (or None if download_url is None)
        assert sbom_pkg.download_location == enrichment.download_url

        # Req 7.3: supplier equals maintainer (or None if maintainer is None)
        assert sbom_pkg.supplier == enrichment.maintainer

        # Req 7.4: package_url equals purl (or None if purl is None)
        assert sbom_pkg.package_url == enrichment.purl

        # Req 7.1, 7.6: concluded_license and declared_license both equal
        # license_expressions[0][0] when the list is non-empty; both None otherwise.
        if enrichment.license_expressions:
            expected_license = enrichment.license_expressions[0][0]
            assert sbom_pkg.concluded_license == expected_license
            assert sbom_pkg.declared_license == expected_license
            # Req 7.6: single expression is not duplicated
            assert sbom_pkg.concluded_license == sbom_pkg.declared_license
        else:
            # Req 7.5: empty license_expressions → None
            assert sbom_pkg.concluded_license is None
            assert sbom_pkg.declared_license is None

        # Req 7.4: a non-None purl also produces a PACKAGE_MANAGER external reference
        if enrichment.purl is not None:
            purl_refs = [
                ref
                for ref in sbom_pkg.external_references
                if ref.category == ExternalReferenceCategory.PACKAGE_MANAGER and ref.url == enrichment.purl
            ]
            assert len(purl_refs) == 1
        else:
            assert all(
                ref.category != ExternalReferenceCategory.PACKAGE_MANAGER for ref in sbom_pkg.external_references
            )

    @settings(max_examples=100)
    @given(pkg=_identified_package())
    def test_none_enrichment_leaves_all_fields_none(
        self,
        pkg: IdentifiedPackage,
    ) -> None:
        """When enrichment is None, all enrichment-derived SBOMPackage fields are None.

        **Validates: Requirements 7.5**
        """
        assembler = ModelAssembler()
        enriched_pkg = EnrichedPackage(package=pkg, enrichment=None)

        sbom_pkg = assembler._build_single_package(enriched_pkg, "SPDXRef-Package-1")

        assert sbom_pkg.download_location is None
        assert sbom_pkg.supplier is None
        assert sbom_pkg.package_url is None
        assert sbom_pkg.concluded_license is None
        assert sbom_pkg.declared_license is None
        assert sbom_pkg.external_references == []
        assert sbom_pkg.checksums == []


# ---------------------------------------------------------------------------
# Property 3: PackageInstance to PackageEnrichment Field Preservation
# ---------------------------------------------------------------------------

# Feature: sbom-enrichment-pipeline, Property 3: PackageInstance to PackageEnrichment Field Preservation

# Strategies for generating PackageInstance-like data

_pi_package_name = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789+-"),
    min_size=2,
    max_size=30,
).filter(lambda s: s[0].isalpha() and s.strip() == s)

_pi_version = st.from_regex(r"[0-9]+\.[0-9]+(\.[0-9]+)?(-[0-9]+)?", fullmatch=True)

_pi_architecture = st.sampled_from(["amd64", "i386", "arm64", "armhf", "all"])

_pi_optional_str = st.one_of(
    st.none(),
    st.text(
        alphabet=st.characters(categories=("L", "N", "P", "Z"), max_codepoint=127),
        min_size=1,
        max_size=80,
    ).filter(lambda s: s.strip() == s and len(s) > 0),
)

_pi_sha256 = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)

_license_expr_pair = st.tuples(
    st.text(
        alphabet=st.sampled_from("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-+ ()"),
        min_size=1,
        max_size=40,
    ).filter(lambda s: len(s.strip()) > 0),
    st.sampled_from(["dep5", "exact_spdx", "heuristic", "fallback"]),
)

_license_expr_list = st.lists(_license_expr_pair, min_size=0, max_size=5)


@st.composite
def _package_instance_scenario(draw: st.DrawFn) -> dict:
    """Generate a PackageInstance with associated LicenseExpression records.

    Returns a dict with:
        - package_name: str
        - version: str
        - architecture: str
        - sha256: str
        - source_package: str | None
        - maintainer: str | None
        - homepage: str | None
        - depends: str | None
        - section: str | None
        - priority: str | None
        - description: str | None
        - download_url: str | None
        - license_expressions: list of (expression, source) tuples
        - snapshot_id: int
    """
    name = draw(_pi_package_name)
    version = draw(_pi_version)
    arch = draw(_pi_architecture)
    sha256 = draw(_pi_sha256)

    source_package = draw(_pi_optional_str)
    maintainer = draw(_pi_optional_str)
    homepage = draw(_pi_optional_str)
    depends = draw(_pi_optional_str)
    section = draw(_pi_optional_str)
    priority = draw(_pi_optional_str)
    description = draw(_pi_optional_str)
    download_url = draw(_pi_optional_str)
    license_expressions = draw(_license_expr_list)
    snapshot_id = draw(st.integers(min_value=1, max_value=1000))

    return {
        "package_name": name,
        "version": version,
        "architecture": arch,
        "sha256": sha256,
        "source_package": source_package,
        "maintainer": maintainer,
        "homepage": homepage,
        "depends": depends,
        "section": section,
        "priority": priority,
        "description": description,
        "download_url": download_url,
        "license_expressions": license_expressions,
        "snapshot_id": snapshot_id,
    }


async def _setup_metadata_db_with_package(scenario: dict) -> async_sessionmaker[AsyncSession]:
    """Create an in-memory metadata.db and seed with the given PackageInstance and LicenseExpressions."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        # Create a repository to satisfy the FK constraint
        await session.execute(
            insert(Repository).values(
                id=1,
                name="test-repo",
                base_url="http://example.com",
                suite="bookworm",
                component="main",
            )
        )
        await session.commit()

    async with factory() as session:
        # Create a snapshot
        await session.execute(
            insert(RepositorySnapshot).values(
                id=scenario["snapshot_id"],
                repository_id=1,
                schema_version=1,
                captured_at=datetime.now(UTC),
                published=True,
            )
        )
        await session.commit()

    async with factory() as session:
        # Insert the PackageInstance
        await session.execute(
            insert(PackageInstance).values(
                id=1,
                package_name=scenario["package_name"],
                version=scenario["version"],
                architecture=scenario["architecture"],
                filename=f"{scenario['package_name']}_{scenario['version']}_{scenario['architecture']}.deb",
                sha256=scenario["sha256"],
                size_bytes=1024,
                snapshot_id=scenario["snapshot_id"],
                source_package=scenario["source_package"],
                maintainer=scenario["maintainer"],
                homepage=scenario["homepage"],
                depends=scenario["depends"],
                section=scenario["section"],
                priority=scenario["priority"],
                description=scenario["description"],
                download_url=scenario["download_url"],
            )
        )
        await session.commit()

    # Insert license expressions
    if scenario["license_expressions"]:
        async with factory() as session:
            for idx, (expression, source) in enumerate(scenario["license_expressions"]):
                await session.execute(
                    insert(LicenseExpression).values(
                        id=idx + 1,
                        package_id=1,
                        expression=expression,
                        source=source,
                    )
                )
            await session.commit()

    return factory


@pytest.mark.property
class TestProperty3PackageInstanceToPackageEnrichmentFieldPreservation:
    """Property 3: PackageInstance to PackageEnrichment Field Preservation.

    Feature: sbom-enrichment-pipeline, Property 3: PackageInstance to PackageEnrichment Field Preservation

    For any PackageInstance record with associated LicenseExpression records,
    the MetadataEnricher's _query_metadata_db method SHALL produce a
    PackageEnrichment where:
    - Every non-None field from the PackageInstance appears unchanged
      in the corresponding PackageEnrichment field
    - The license_expressions list contains all (expression, source) pairs
      from the associated LicenseExpression records
    - The purl field equals generate_purl(package_name, version, architecture)
      (or None if generation fails)
    """

    @settings(max_examples=100)
    @given(scenario=_package_instance_scenario())
    def test_field_preservation_and_purl_generation(
        self,
        scenario: dict,
    ) -> None:
        """All non-None PackageInstance fields are preserved in PackageEnrichment.

        **Validates: Requirements 3.1, 3.3, 4.1, 4.4**
        """

        async def _run() -> None:
            factory = await _setup_metadata_db_with_package(scenario)

            # Create a mock cache adapter that always misses and accepts stores
            mock_cache = AsyncMock()
            mock_cache.get.return_value = None
            mock_cache.store.return_value = None

            enricher = MetadataEnricher(
                cache_adapter=mock_cache,
                metadata_session_factory=factory,
            )

            pkg = IdentifiedPackage(
                name=scenario["package_name"],
                version=scenario["version"],
                architecture=scenario["architecture"],
                status="installed",
            )

            enrichment = await enricher._query_metadata_db(pkg, scenario["snapshot_id"])

            # Enrichment should not be None (we seeded matching data)
            assert enrichment is not None

            # Field preservation: every non-None field from PackageInstance
            # appears unchanged in the corresponding PackageEnrichment field
            field_map = {
                "source_package": scenario["source_package"],
                "maintainer": scenario["maintainer"],
                "homepage": scenario["homepage"],
                "depends": scenario["depends"],
                "section": scenario["section"],
                "priority": scenario["priority"],
                "description": scenario["description"],
                "sha256": scenario["sha256"],
                "download_url": scenario["download_url"],
            }
            for field_name, expected_value in field_map.items():
                actual_value = getattr(enrichment, field_name)
                assert actual_value == expected_value, (
                    f"Field {field_name}: expected {expected_value!r}, got {actual_value!r}"
                )

            # License expressions: all (expression, source) pairs preserved
            expected_licenses = set(scenario["license_expressions"])
            actual_licenses = set(enrichment.license_expressions)
            assert actual_licenses == expected_licenses, (
                f"License expressions mismatch.\nExpected: {expected_licenses}\nActual: {actual_licenses}"
            )

            # PURL: equals generate_purl(name, version, arch) or None
            try:
                expected_purl = generate_purl(
                    scenario["package_name"],
                    scenario["version"],
                    scenario["architecture"],
                )
            except PURLGenerationError:
                expected_purl = None

            assert enrichment.purl == expected_purl, (
                f"PURL mismatch: expected {expected_purl!r}, got {enrichment.purl!r}"
            )

        asyncio.run(_run())
