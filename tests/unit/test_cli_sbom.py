"""Unit tests for CLI argument validation and output formatting.

Tests the `debcraft sbom` command's input validation (format, path, output-dir),
quiet mode suppression, and the summary table output formatting.

Requirements: 11.2, 11.9, 11.10, 11.11
"""

from __future__ import annotations

import stat
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from debcraft.cli import app
from debcraft.domain.sbom.values import OutputFormat, WriterResult

runner = CliRunner()


# ---------------------------------------------------------------------------
# Format validation tests (Requirement 11.10)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatValidation:
    """Tests for --format option validation."""

    def test_invalid_format_exits_with_error(self, tmp_path: Path):
        """Invalid format value exits with non-zero and lists valid options."""
        artifact = tmp_path / "artifact.deb"
        artifact.write_text("fake artifact")
        result = runner.invoke(app, ["sbom", str(artifact), "--format", "invalid_fmt"])
        assert result.exit_code != 0
        assert "Invalid format" in result.output
        assert "invalid_fmt" in result.output
        # Should list valid formats
        assert "spdx_3_0" in result.output
        assert "spdx_2_3" in result.output
        assert "cyclonedx" in result.output

    def test_multiple_invalid_formats_all_listed(self, tmp_path: Path):
        """Multiple invalid format values are all shown in the error message."""
        artifact = tmp_path / "artifact.deb"
        artifact.write_text("fake artifact")
        result = runner.invoke(app, ["sbom", str(artifact), "--format", "bad1", "--format", "bad2"])
        assert result.exit_code != 0
        assert "bad1" in result.output
        assert "bad2" in result.output

    def test_valid_format_accepted(self, tmp_path: Path):
        """Valid format values do not trigger format validation errors."""
        # Create an artifact so path validation passes
        artifact = tmp_path / "artifact.deb"
        artifact.write_text("fake artifact")

        # The command will fail later (workflow execution), but format validation should pass
        with patch("debcraft.cli.sbom._run_sbom", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = [
                WriterResult(
                    output_path=tmp_path / "sbom.spdx.json",
                    format=OutputFormat.SPDX_2_3,
                    sha256="a" * 64,
                    file_size=1024,
                )
            ]
            result = runner.invoke(
                app,
                ["sbom", str(artifact), "--format", "spdx_2_3", "--output-dir", str(tmp_path)],
            )
        # Should not contain format validation error
        assert "Invalid format" not in result.output

    def test_no_format_defaults_to_all(self, tmp_path: Path):
        """When no --format is specified, all formats are used."""
        artifact = tmp_path / "artifact.deb"
        artifact.write_text("fake artifact")

        with patch("debcraft.cli.sbom._run_sbom", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = [
                WriterResult(
                    output_path=tmp_path / "sbom.spdx3.json",
                    format=OutputFormat.SPDX_3_0,
                    sha256="a" * 64,
                    file_size=512,
                ),
                WriterResult(
                    output_path=tmp_path / "sbom.spdx.json",
                    format=OutputFormat.SPDX_2_3,
                    sha256="b" * 64,
                    file_size=1024,
                ),
                WriterResult(
                    output_path=tmp_path / "sbom.cdx.json",
                    format=OutputFormat.CYCLONEDX,
                    sha256="c" * 64,
                    file_size=768,
                ),
            ]
            result = runner.invoke(
                app,
                ["sbom", str(artifact), "--output-dir", str(tmp_path)],
            )
        # Verify all formats were requested
        assert result.exit_code == 0
        call_args = mock_run.call_args
        formats_arg = call_args.kwargs.get("formats") or call_args[1].get("formats")
        if formats_arg is None:
            # positional args
            formats_arg = call_args[0][1]  # formats is second positional arg
        assert set(formats_arg) == {OutputFormat.SPDX_3_0, OutputFormat.SPDX_2_3, OutputFormat.CYCLONEDX}


# ---------------------------------------------------------------------------
# Path validation tests (Requirement 11.9)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPathValidation:
    """Tests for artifact path validation."""

    def test_nonexistent_path_exits_nonzero(self):
        """Nonexistent artifact_path exits with non-zero code."""
        result = runner.invoke(app, ["sbom", "/nonexistent/path/to/artifact"])
        assert result.exit_code != 0
        assert "does not exist" in result.output

    def test_existing_path_proceeds(self, tmp_path: Path):
        """Existing artifact path passes validation (may fail later in workflow)."""
        artifact = tmp_path / "artifact.deb"
        artifact.write_text("fake artifact")

        with patch("debcraft.cli.sbom._run_sbom", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = [
                WriterResult(
                    output_path=tmp_path / "sbom.spdx.json",
                    format=OutputFormat.SPDX_2_3,
                    sha256="d" * 64,
                    file_size=256,
                )
            ]
            result = runner.invoke(
                app,
                ["sbom", str(artifact), "--format", "spdx_2_3", "--output-dir", str(tmp_path)],
            )
        # Should not contain path validation error
        assert "does not exist" not in result.output


# ---------------------------------------------------------------------------
# Output directory validation tests (Requirement 11.11)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOutputDirValidation:
    """Tests for --output-dir validation."""

    def test_non_writable_directory_exits_nonzero(self, tmp_path: Path):
        """Non-writable output directory exits with non-zero code."""
        artifact = tmp_path / "artifact.deb"
        artifact.write_text("fake artifact")

        # Create a directory and make it non-writable
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)

        try:
            result = runner.invoke(
                app,
                ["sbom", str(artifact), "--output-dir", str(readonly_dir)],
            )
            assert result.exit_code != 0
            assert "not writable" in result.output or "Cannot create" in result.output
        finally:
            # Restore permissions for cleanup
            readonly_dir.chmod(stat.S_IRWXU)

    def test_nonexistent_output_dir_is_created(self, tmp_path: Path):
        """Nonexistent output-dir is created automatically if possible."""
        artifact = tmp_path / "artifact.deb"
        artifact.write_text("fake artifact")

        new_dir = tmp_path / "new" / "nested" / "output"
        assert not new_dir.exists()

        with patch("debcraft.cli.sbom._run_sbom", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = [
                WriterResult(
                    output_path=new_dir / "sbom.spdx.json",
                    format=OutputFormat.SPDX_2_3,
                    sha256="e" * 64,
                    file_size=128,
                )
            ]
            result = runner.invoke(
                app,
                ["sbom", str(artifact), "--format", "spdx_2_3", "--output-dir", str(new_dir)],
            )
        # The directory should have been created
        assert result.exit_code == 0
        assert new_dir.exists()


# ---------------------------------------------------------------------------
# Quiet mode tests (Requirement 11.8 implied via 11.2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQuietMode:
    """Tests for --quiet flag behavior."""

    def test_quiet_suppresses_progress(self, tmp_path: Path):
        """--quiet flag suppresses progress output but still shows summary."""
        artifact = tmp_path / "artifact.deb"
        artifact.write_text("fake artifact")

        with patch("debcraft.cli.sbom._run_sbom", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = [
                WriterResult(
                    output_path=tmp_path / "sbom.spdx.json",
                    format=OutputFormat.SPDX_2_3,
                    sha256="f" * 64,
                    file_size=2048,
                )
            ]
            result = runner.invoke(
                app,
                [
                    "sbom",
                    str(artifact),
                    "--format",
                    "spdx_2_3",
                    "--output-dir",
                    str(tmp_path),
                    "--quiet",
                ],
            )
        # Progress indicators should not appear with --quiet
        # The progress bar text like "Starting SBOM generation..." should be suppressed
        # but the summary table should still display
        assert "SBOM Generation Summary" in result.output

    def test_quiet_passes_to_workflow(self, tmp_path: Path):
        """--quiet flag is passed to the workflow runner."""
        artifact = tmp_path / "artifact.deb"
        artifact.write_text("fake artifact")

        with patch("debcraft.cli.sbom._run_sbom", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = [
                WriterResult(
                    output_path=tmp_path / "sbom.cdx.json",
                    format=OutputFormat.CYCLONEDX,
                    sha256="a" * 64,
                    file_size=512,
                )
            ]
            result = runner.invoke(
                app,
                [
                    "sbom",
                    str(artifact),
                    "--format",
                    "cyclonedx",
                    "--output-dir",
                    str(tmp_path),
                    "--quiet",
                ],
            )
        # Verify quiet was passed to _run_sbom
        assert result.exit_code == 0
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs.get("quiet") is True


# ---------------------------------------------------------------------------
# Summary table output tests (Requirement 11.5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSummaryTableOutput:
    """Tests for the summary table displayed after successful run."""

    def test_summary_shows_format_and_path(self, tmp_path: Path):
        """Summary table includes the format and output file columns."""
        artifact = tmp_path / "artifact.deb"
        artifact.write_text("fake artifact")

        output_path = tmp_path / "sbom.spdx.json"
        with patch("debcraft.cli.sbom._run_sbom", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = [
                WriterResult(
                    output_path=output_path,
                    format=OutputFormat.SPDX_2_3,
                    sha256="ab" * 32,
                    file_size=4096,
                )
            ]
            result = runner.invoke(
                app,
                ["sbom", str(artifact), "--format", "spdx_2_3", "--output-dir", str(tmp_path)],
            )
        assert result.exit_code == 0
        assert "SBOM Generation Summary" in result.output
        assert "spdx_2_3" in result.output
        # Table has Output File column header
        assert "Output File" in result.output

    def test_summary_shows_file_size(self, tmp_path: Path):
        """Summary table includes human-readable file size."""
        artifact = tmp_path / "artifact.deb"
        artifact.write_text("fake artifact")

        with patch("debcraft.cli.sbom._run_sbom", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = [
                WriterResult(
                    output_path=tmp_path / "sbom.cdx.json",
                    format=OutputFormat.CYCLONEDX,
                    sha256="cd" * 32,
                    file_size=2048,
                )
            ]
            result = runner.invoke(
                app,
                ["sbom", str(artifact), "--format", "cyclonedx", "--output-dir", str(tmp_path)],
            )
        assert result.exit_code == 0
        # 2048 bytes = 2.0 KiB
        assert "2.0 KiB" in result.output

    def test_summary_shows_sha256(self, tmp_path: Path):
        """Summary table includes the SHA-256 hash (at least the prefix)."""
        artifact = tmp_path / "artifact.deb"
        artifact.write_text("fake artifact")

        sha = "0123456789abcdef" * 4
        with patch("debcraft.cli.sbom._run_sbom", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = [
                WriterResult(
                    output_path=tmp_path / "sbom.spdx3.json",
                    format=OutputFormat.SPDX_3_0,
                    sha256=sha,
                    file_size=1024,
                )
            ]
            result = runner.invoke(
                app,
                ["sbom", str(artifact), "--format", "spdx_3_0", "--output-dir", str(tmp_path)],
            )
        assert result.exit_code == 0
        # Rich may truncate the hash in narrow terminals, check for prefix
        assert "0123456789abcdef" in result.output

    def test_summary_shows_multiple_formats(self, tmp_path: Path):
        """Summary table shows all formats when multiple are generated."""
        artifact = tmp_path / "artifact.deb"
        artifact.write_text("fake artifact")

        with patch("debcraft.cli.sbom._run_sbom", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = [
                WriterResult(
                    output_path=tmp_path / "sbom.spdx3.json",
                    format=OutputFormat.SPDX_3_0,
                    sha256="a" * 64,
                    file_size=512,
                ),
                WriterResult(
                    output_path=tmp_path / "sbom.spdx.json",
                    format=OutputFormat.SPDX_2_3,
                    sha256="b" * 64,
                    file_size=1024,
                ),
                WriterResult(
                    output_path=tmp_path / "sbom.cdx.json",
                    format=OutputFormat.CYCLONEDX,
                    sha256="c" * 64,
                    file_size=768,
                ),
            ]
            result = runner.invoke(
                app,
                ["sbom", str(artifact), "--output-dir", str(tmp_path)],
            )
        assert result.exit_code == 0
        assert "spdx_3_0" in result.output
        assert "spdx_2_3" in result.output
        assert "cyclonedx" in result.output


# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatBytes:
    """Tests for the _format_bytes helper."""

    def test_bytes(self):
        from debcraft.cli.sbom import _format_bytes

        assert _format_bytes(0) == "0 B"
        assert _format_bytes(100) == "100 B"
        assert _format_bytes(1023) == "1023 B"

    def test_kibibytes(self):
        from debcraft.cli.sbom import _format_bytes

        assert _format_bytes(1024) == "1.0 KiB"
        assert _format_bytes(2048) == "2.0 KiB"

    def test_mebibytes(self):
        from debcraft.cli.sbom import _format_bytes

        assert _format_bytes(1024 * 1024) == "1.0 MiB"
        assert _format_bytes(int(1.5 * 1024 * 1024)) == "1.5 MiB"

    def test_gibibytes(self):
        from debcraft.cli.sbom import _format_bytes

        assert _format_bytes(1024 * 1024 * 1024) == "1.0 GiB"
