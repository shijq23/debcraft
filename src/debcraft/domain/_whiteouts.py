"""Shared application of OCI layer whiteout semantics.

A *whiteout* is an empty file whose name marks a path for deletion when a
layer changeset is applied on top of the layers below it. Two forms exist,
specified by the OCI image spec ``layer.md`` (sections "Whiteouts" and
"Opaque Whiteout"):

* ``.wh.<name>`` — the prefix ``.wh.`` plus the basename of the path to
  delete, hiding ``<name>`` in the same directory.
* ``.wh..wh..opq`` — hides *all* children of the containing directory,
  including sub-directories, other resources and all descendants.

Whiteouts apply only to lower layers: a path written by the *same* layer that
carries the marker is not hidden by it. Once applied, the marker itself must
also be hidden.

Tar member names are **not** filesystem paths: they are always forward-slash
separated regardless of the host platform. All path arithmetic here therefore
uses ``posixpath``, never ``os.path`` — the latter resolves to ``ntpath`` on
Windows and would compute backslash-joined targets that never match a virtual
filesystem key derived from a tar member name.

Callers are expected to have passed member names through
``debcraft.domain._archive_paths.normalize_tar_member_name`` first.
"""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

WHITEOUT_PREFIX: Final = ".wh."
OPAQUE_WHITEOUT: Final = ".wh..wh..opq"


def apply_whiteouts(vfs: dict[str, bytes], layer_entries: Sequence[str]) -> None:
    """Apply one layer's whiteout markers to a merged virtual filesystem.

    Mutates ``vfs`` in place. Entries of ``layer_entries`` shield themselves
    from an opaque wipe in the same layer, implementing the same-layer rule of
    ``layer.md`` ("Whiteouts"); every marker is removed from ``vfs`` as it is
    applied, implementing the same section's "once a whiteout is applied, the
    whiteout itself MUST also be hidden".

    The opaque-marker test must precede the prefix test: ``.wh..wh..opq``
    itself starts with ``.wh.``.

    Args:
        vfs: Virtual filesystem, keyed by normalized tar member name, mapping
            to file content. Modified in place.
        layer_entries: All normalized member names in the layer being applied,
            whiteout markers included. Not modified.
    """
    shielded = frozenset(layer_entries)

    for entry in layer_entries:
        basename = posixpath.basename(entry)
        dirname = posixpath.dirname(entry)

        if basename == OPAQUE_WHITEOUT:
            prefix = dirname + "/" if dirname else ""
            doomed = [key for key in vfs if key.startswith(prefix) and key not in shielded]
            for key in doomed:
                del vfs[key]
            vfs.pop(entry, None)

        elif basename.startswith(WHITEOUT_PREFIX):
            target_name = basename.removeprefix(WHITEOUT_PREFIX)
            target_path = posixpath.join(dirname, target_name) if dirname else target_name
            vfs.pop(target_path, None)
            vfs.pop(entry, None)
