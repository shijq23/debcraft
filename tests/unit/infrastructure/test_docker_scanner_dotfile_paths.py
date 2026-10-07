"""Regression tests for dotfile-rooted paths in Docker layers.

Tar member names were previously normalized with ``member.name.lstrip("./")``,
which strips a *set of characters* rather than a prefix. Any path whose first
component began with a dot was mangled (``./.dockerenv`` -> ``dockerenv``), so
the virtual filesystem was keyed by a path that does not exist in the image and
a ``.wh.`` whiteout marker for such a file never matched its target.

These tests drive the real scanner path (``_process_docker_layers``, which runs
``_merge_layer`` and ``_apply_whiteouts``) rather than the normalization helper
in isolation, so they demonstrate the user-visible impact.
"""

from __future__ import annotations

import io
import json
import tarfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from debcraft.infrastructure.scanners.docker import DockerScanner

pytestmark = [pytest.mark.unit]


def _make_workflow_context() -> MagicMock:
    """Create a mock WorkflowContext with cancellation disabled."""
    context = MagicMock()
    context.cancellation_token.is_cancelled = False
    context.progress.report = MagicMock()
    return context


def _make_scanner() -> DockerScanner:
    """Create a DockerScanner with mock ports (unused by layer merging)."""
    contents_port = AsyncMock()
    package_port = AsyncMock()
    return DockerScanner(contents_port=contents_port, package_port=package_port)


def _add_bytes_to_tar(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    """Add raw bytes as a file entry in a tarfile."""
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _add_dir_to_tar(tar: tarfile.TarFile, name: str) -> None:
    """Add a directory entry in a tarfile."""
    info = tarfile.TarInfo(name=name)
    info.type = tarfile.DIRTYPE
    tar.addfile(info)


def _create_layer_tar(files: dict[str, bytes], dirs: tuple[str, ...] = ()) -> bytes:
    """Create a layer tar (inner tar) from path -> content plus directory entries."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as layer_tar:
        for dir_name in dirs:
            _add_dir_to_tar(layer_tar, dir_name)
        for path, content in files.items():
            _add_bytes_to_tar(layer_tar, path, content)
    return buf.getvalue()


def _create_docker_tarball(tmp_path, layers: list[bytes]) -> tuple[str, list[str]]:
    """Create a Docker image tarball in docker save format.

    Args:
        tmp_path: pytest tmp_path fixture for file creation.
        layers: Pre-built layer tar payloads, bottom layer first.

    Returns:
        Tuple of (tarball path, ordered layer member paths).
    """
    tarball_path = str(tmp_path / "image.tar")
    layer_paths: list[str] = []

    with tarfile.open(tarball_path, "w") as outer_tar:
        for index, layer_data in enumerate(layers):
            layer_name = f"layer{index}/layer.tar"
            _add_bytes_to_tar(outer_tar, layer_name, layer_data)
            layer_paths.append(layer_name)
        manifest = json.dumps([{"Layers": layer_paths}]).encode("utf-8")
        _add_bytes_to_tar(outer_tar, "manifest.json", manifest)

    return tarball_path, layer_paths


class TestDotfileRootedLayerPaths:
    """Dot-prefixed first components survive layer merging and whiteouts."""

    def test_dotfile_whiteout_removes_its_target(self, tmp_path) -> None:
        """``.wh..dockerenv`` in an upper layer removes ``.dockerenv``."""
        lower = _create_layer_tar(
            {
                "./.dockerenv": b"docker-marker",
                "./.config/app.conf": b"conf",
                "./..data/secret": b"secret",
                "./usr/bin/foo": b"elf",
            }
        )
        upper = _create_layer_tar({"./.wh..dockerenv": b""})
        tarball_path, layer_paths = _create_docker_tarball(tmp_path, [lower, upper])

        scanner = _make_scanner()
        with tarfile.open(tarball_path, "r") as outer_tar:
            vfs, diagnostics, cancelled = scanner._process_docker_layers(
                outer_tar,
                layer_paths,
                _make_workflow_context(),
            )

        assert not cancelled
        assert diagnostics == []
        # The whiteout now matches its dotfile target and deletes it.
        assert ".dockerenv" not in vfs
        assert ".wh..dockerenv" not in vfs
        # Dot-prefixed components are preserved verbatim.
        assert vfs[".config/app.conf"] == b"conf"
        assert vfs["..data/secret"] == b"secret"
        assert vfs["usr/bin/foo"] == b"elf"
        # None of the keys the old lstrip("./") produced may appear.
        for mangled in ("dockerenv", "config/app.conf", "data/secret", "wh..dockerenv"):
            assert mangled not in vfs

    def test_dotfile_entries_survive_without_whiteout(self, tmp_path) -> None:
        """Without a marker, dotfile-rooted files keep their dotted keys."""
        layer = _create_layer_tar({"./.bashrc": b"rc", ".config/app.conf": b"conf"})
        tarball_path, layer_paths = _create_docker_tarball(tmp_path, [layer])

        scanner = _make_scanner()
        with tarfile.open(tarball_path, "r") as outer_tar:
            vfs, _, _ = scanner._process_docker_layers(
                outer_tar,
                layer_paths,
                _make_workflow_context(),
            )

        assert set(vfs) == {".bashrc", ".config/app.conf"}


class TestArchiveRootMember:
    """The archive root member is skipped rather than mangled into ``''``."""

    def test_bare_dot_member_is_skipped(self, tmp_path) -> None:
        """A bare ``.`` directory member yields no vfs key.

        Under the old normalization ``"."`` collapsed to ``""`` and was dropped
        by the ``if not name`` guard. It now normalizes to ``"."``, so the guard
        skips the root member explicitly.
        """
        layer = _create_layer_tar({"./usr/bin/foo": b"elf"}, dirs=(".", "./"))
        tarball_path, layer_paths = _create_docker_tarball(tmp_path, [layer])

        scanner = _make_scanner()
        with tarfile.open(tarball_path, "r") as outer_tar:
            vfs, _, _ = scanner._process_docker_layers(
                outer_tar,
                layer_paths,
                _make_workflow_context(),
            )

        assert set(vfs) == {"usr/bin/foo"}
        assert "." not in vfs
        assert "" not in vfs

    def test_bare_dot_member_is_absent_from_layer_entries(self) -> None:
        """``_merge_layer`` does not report the root member as an entry."""
        layer_bytes = _create_layer_tar({"./usr/bin/foo": b"elf"}, dirs=(".", "./"))
        scanner = _make_scanner()
        vfs: dict[str, bytes] = {}

        with tarfile.open(fileobj=io.BytesIO(layer_bytes), mode="r") as layer_tar:
            entries = scanner._merge_layer(vfs, layer_tar)

        assert entries == ["usr/bin/foo"]
