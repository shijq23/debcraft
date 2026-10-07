"""Shared normalization for tar archive member names.

Tar member names are **not** filesystem paths: they are always forward-slash
separated regardless of the host platform. They are therefore handled with
plain string operations (or ``posixpath``), never ``pathlib`` or ``os.path``
— the latter is ``ntpath`` on Windows and would produce backslash-joined
values that never match a key derived from a tar member.

Used by the Docker layer merger and the .deb control/data tar readers, which
all need the same leading-prefix normalization.
"""

from __future__ import annotations


def normalize_tar_member_name(name: str) -> str:
    """Normalize a tar member name by removing a leading ``./`` or ``/`` prefix.

    Tar writers emit members as ``./usr/bin/foo``, ``usr/bin/foo`` or (rarely)
    ``/usr/bin/foo``. This collapses all three spellings to ``usr/bin/foo``.

    Why not ``name.lstrip("./")``:
        ``str.lstrip`` takes a **set of characters**, not a prefix. It removes
        every leading ``.`` and ``/`` in any combination, so any path whose
        first component begins with a dot is silently mangled:
        ``"./.dockerenv"`` becomes ``"dockerenv"``, ``".config/app.conf"``
        becomes ``"config/app.conf"`` and ``"./..data/x"`` becomes ``"data/x"``.
        For the Docker scanner that produced virtual-filesystem keys for paths
        that do not exist in the image and broke ``.wh.`` whiteout matching
        against dotfile-rooted entries. ``removeprefix`` is exact, and the
        trailing ``lstrip("/")`` keeps the existing absolute-path handling
        (where a single-character argument makes set- and prefix-stripping
        coincide, which is the intent for repeated leading slashes).

    This is prefix normalization, not canonicalization:
        * The ``./`` prefix is removed **once**. ``"././usr/bin/foo"`` becomes
          ``"./usr/bin/foo"``. No tar writer in the pipeline emits a repeated
          prefix, and looping would be a step toward canonicalization.
        * ``..`` segments are **not** resolved. ``posixpath.normpath`` is
          deliberately avoided: it would change path-traversal behaviour on
          hostile archives and would turn ``"./"`` into ``"."``.
        * The archive root member ``"."`` is returned unchanged as ``"."``.
          Callers that must not treat the root directory as a file skip it
          themselves rather than relying on it normalizing to an empty string.

    Args:
        name: A raw tar member name (forward-slash separated).

    Returns:
        The member name with a single leading ``./`` prefix removed and any
        leading ``/`` characters stripped.
    """
    return name.removeprefix("./").lstrip("/")
