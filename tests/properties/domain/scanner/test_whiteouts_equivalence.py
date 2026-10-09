"""Differential equivalence tests for the shared whiteout application.

**Validates: Requirements 5.2, 5.3, 6.6**

Design and the frozen pre-refactor oracles: .agents/tasks/unify-whiteouts/design.md
(revision 4). Extends m6-artifact-scanners Property 7 ("Layer Merge with Whiteouts",
tests/properties/domain/scanner/test_layer_merge.py) with a differential equivalence
check against both pre-refactor implementations.

``DockerScanner._apply_whiteouts`` and ``OCIScanner._apply_whiteouts`` were two
independently-written implementations of the same OCI layer-whiteout semantics.
They were unified into ``debcraft.domain._whiteouts.apply_whiteouts``, whose body
is byte-for-byte equivalent to the legacy Docker implementation on *all* inputs.
The two legacy bodies below are kept frozen as executable oracles: they are the
only durable record of the pre-refactor contract, and they are what fails loudly
if someone later "simplifies" the shared function back into divergence
(``posixpath.dirname`` -> ``rsplit``, ``posixpath.join`` -> an f-string).
"""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from debcraft.domain._whiteouts import OPAQUE_WHITEOUT, WHITEOUT_PREFIX, apply_whiteouts

if TYPE_CHECKING:
    from collections.abc import Sequence

# ---------------------------------------------------------------------------
# FROZEN — pre-refactor reference implementations, do not "fix"
#
# Constants are renamed with a _LEGACY_ prefix on purpose: same-named copies
# would shadow the WHITEOUT_PREFIX imported from debcraft.domain._whiteouts and
# the shadowing would be silent, since the values are identical today.
# ---------------------------------------------------------------------------

_LEGACY_WHITEOUT_PREFIX = ".wh."
_LEGACY_OPAQUE_WHITEOUT = ".wh..wh..opq"


# FROZEN — verbatim from src/debcraft/infrastructure/scanners/docker.py:321-368
# at commit 37033ca (only ``self`` dropped). Do not "fix".
def _legacy_docker_apply_whiteouts(vfs: dict[str, bytes], layer_entries: list[str]) -> None:
    # Build a set of non-whiteout entries from this layer for opaque handling
    current_layer_files: set[str] = set()
    for entry in layer_entries:
        basename = posixpath.basename(entry)
        if not basename.startswith(_LEGACY_WHITEOUT_PREFIX):
            current_layer_files.add(entry)

    for entry in layer_entries:
        basename = posixpath.basename(entry)
        dirname = posixpath.dirname(entry)

        if basename == _LEGACY_OPAQUE_WHITEOUT:
            # Opaque whiteout: remove all entries under this directory
            # that came from lower layers (preserve same-layer entries)
            prefix = dirname + "/" if dirname else ""
            keys_to_remove = [k for k in vfs if k.startswith(prefix) and k not in current_layer_files]
            for key in keys_to_remove:
                del vfs[key]
            # Remove the opaque whiteout marker itself
            vfs.pop(entry, None)

        elif basename.startswith(_LEGACY_WHITEOUT_PREFIX):
            # Regular whiteout: remove the specific file
            target_name = basename[len(_LEGACY_WHITEOUT_PREFIX) :]
            target_path = posixpath.join(dirname, target_name) if dirname else target_name
            vfs.pop(target_path, None)
            # Remove the whiteout marker itself
            vfs.pop(entry, None)


# FROZEN — verbatim from src/debcraft/infrastructure/scanners/oci.py:423-454
# at commit 37033ca (only ``self`` dropped). Keeps its literal ".wh." forms,
# its basename[4:] slice and its dead ``k != entry`` guard. Do not "fix".
def _legacy_oci_apply_whiteouts(vfs: dict[str, bytes], layer_entries: list[str]) -> None:
    for entry in layer_entries:
        basename = entry.rsplit("/", 1)[-1] if "/" in entry else entry
        parent_dir = entry.rsplit("/", 1)[0] if "/" in entry else ""

        if basename == ".wh..wh..opq":
            # Opaque whiteout: remove all entries in this directory
            # from lower layers (entries NOT in current layer_entries)
            prefix = parent_dir + "/" if parent_dir else ""
            keys_to_remove = [k for k in vfs if k.startswith(prefix) and k != entry and k not in layer_entries]
            for key in keys_to_remove:
                del vfs[key]
            # Remove the opaque whiteout marker itself
            vfs.pop(entry, None)

        elif basename.startswith(".wh."):
            # Single-file whiteout: remove the target file
            target_name = basename[4:]  # Strip ".wh." prefix
            target_path = f"{parent_dir}/{target_name}" if parent_dir else target_name
            vfs.pop(target_path, None)
            # Remove the whiteout marker itself
            vfs.pop(entry, None)


# ---------------------------------------------------------------------------
# Strategies
#
# The pool is small and fixed on purpose. max_examples is 5 under the default
# "dev" profile (tests/conftest.py), so random 8-char names would mean marker
# targets essentially never collide with vfs keys and 5 examples of "marker
# misses everything" would prove nothing. "ab" is in the pool so the
# startswith(dirname + "/") sibling guard ("ab/x" next to an "a/" marker) is
# reachable at all.
# ---------------------------------------------------------------------------

_POOL = ("a", "ab", "b", "etc", ".dot", "..data", "f")

st_segment = st.sampled_from(_POOL)


def _entry(directory: str, name: str) -> str:
    """Join with the root carve-out: an empty dir yields a bare basename.

    The carve-out matters for every entry strategy, not just the regular one:
    ``f"{dir}/.wh.{name}"`` with an empty ``dir`` emits ``/.wh.f``, which is
    design.md §3 row 5a — a disjunct-(a) divergence this generator must never
    produce.
    """
    return f"{directory}/{name}" if directory else name


def st_path(min_depth: int = 1, max_depth: int = 4) -> st.SearchStrategy[str]:
    """A slash-joined path of ``min_depth``..``max_depth`` pool segments."""
    return st.lists(st_segment, min_size=min_depth, max_size=max_depth).map("/".join)


st_dir = st.lists(st_segment, min_size=0, max_size=3).map("/".join)  # "" is the root case
st_regular_entry = st.builds(_entry, st_dir, st_segment)
st_whiteout_entry = st.builds(lambda d, n: _entry(d, f"{WHITEOUT_PREFIX}{n}"), st_dir, st_segment)
st_opaque_entry = st.builds(lambda d: _entry(d, OPAQUE_WHITEOUT), st_dir)
st_any_entry = st.one_of(st_regular_entry, st_whiteout_entry, st_opaque_entry)
st_marker_entry = st.one_of(st_whiteout_entry, st_opaque_entry)


@st.composite
def st_entries_and_permutation(draw: st.DrawFn) -> tuple[list[str], list[str]]:
    """One entry list plus a permutation of it, for the P4d ordering check."""
    entries = draw(st.lists(st_any_entry, min_size=0, max_size=6))
    return entries, draw(st.permutations(entries))


def _resolved_target(entry: str) -> str | None:
    """The vfs key a regular ``.wh.`` marker targets, or None if not such a marker."""
    basename = posixpath.basename(entry)
    if basename == OPAQUE_WHITEOUT or not basename.startswith(WHITEOUT_PREFIX):
        return None
    dirname = posixpath.dirname(entry)
    name = basename.removeprefix(WHITEOUT_PREFIX)
    return posixpath.join(dirname, name) if dirname else name


def _marker_targets(layer_entries: Sequence[str]) -> list[str]:
    """Resolved targets of every regular ``.wh.`` marker in the layer."""
    return [t for t in (_resolved_target(e) for e in layer_entries) if t]


@st.composite
def st_vfs(draw: st.DrawFn, layer_entries: list[str]) -> dict[str, bytes]:
    """Any subset of shapes may be empty, so {} is reachable; none is guaranteed."""
    keys: list[str] = []
    targets = _marker_targets(layer_entries)
    if targets:
        keys += draw(st.lists(st.sampled_from(targets), max_size=3))  # exact marker hits
    keys += draw(st.lists(st_path(min_depth=1, max_depth=4), max_size=4))  # deep / sibling shapes
    if layer_entries:
        keys += draw(st.lists(st.sampled_from(layer_entries), max_size=2))  # same-layer keys
    keys += draw(st.lists(st_marker_entry, max_size=2))  # carried-up markers
    return {k: draw(st.binary(max_size=4)) for k in keys if k}


@st.composite
def st_scenario(draw: st.DrawFn) -> tuple[list[str], list[str], dict[str, bytes]]:
    """An entry list, a permutation of it, and a vfs seeded from it."""
    entries, permuted = draw(st_entries_and_permutation())
    return entries, permuted, draw(st_vfs(entries))


def _assert_non_divergent_alphabet(layer_entries: Sequence[str], vfs: dict[str, bytes]) -> None:
    """Pin the generator to the non-divergent side of design.md §2.2.

    A leading ``/`` is disjunct (a) and a doubled slash before a basename is
    disjunct (b); either one makes the two oracles disagree by design (§4), so
    P2 and P3 would fail for a reason that has nothing to do with the refactor.
    Widening ``_POOL`` or ``_entry`` trips this instead.
    """
    for e in layer_entries:
        assert not e.startswith("/") and "//" not in e, e
    for k in vfs:
        assert not k.startswith("/") and "//" not in k, k


@pytest.mark.property
@pytest.mark.unit
class TestWhiteoutApplicationEquivalence:
    """The shared ``apply_whiteouts`` agrees with both frozen legacy oracles."""

    @given(scenario=st_scenario())
    def test_p1_agrees_with_docker_oracle(self, scenario: tuple[list[str], list[str], dict[str, bytes]]) -> None:
        """P1: identical to the legacy Docker body, keys and bytes.

        The primary equivalence: the unified body is legacy Docker's path
        arithmetic verbatim, and Docker is the chosen-correct side of every
        divergence in design.md §3.
        """
        layer_entries, _, vfs = scenario
        _assert_non_divergent_alphabet(layer_entries, vfs)

        actual = dict(vfs)
        expected = dict(vfs)
        apply_whiteouts(actual, layer_entries)
        _legacy_docker_apply_whiteouts(expected, layer_entries)

        assert actual == expected

    @given(scenario=st_scenario())
    def test_p2_agrees_with_oci_oracle(self, scenario: tuple[list[str], list[str], dict[str, bytes]]) -> None:
        """P2: identical to the legacy OCI body on the non-divergent alphabet.

        Valid *because* the generator excludes leading and doubled slashes. The
        one deliberate behaviour change is exactly there — see design.md §4 and
        the ``row2``/``row3``/``row4`` cases below, which assert the new result.
        """
        layer_entries, _, vfs = scenario
        _assert_non_divergent_alphabet(layer_entries, vfs)

        actual = dict(vfs)
        expected = dict(vfs)
        apply_whiteouts(actual, layer_entries)
        _legacy_oci_apply_whiteouts(expected, layer_entries)

        assert actual == expected

    @given(scenario=st_scenario())
    def test_p3_oracles_agree_with_each_other(self, scenario: tuple[list[str], list[str], dict[str, bytes]]) -> None:
        """P3: the two legacy bodies agree, making design.md §2's result a standing test."""
        layer_entries, _, vfs = scenario
        _assert_non_divergent_alphabet(layer_entries, vfs)

        docker = dict(vfs)
        oci = dict(vfs)
        _legacy_docker_apply_whiteouts(docker, layer_entries)
        _legacy_oci_apply_whiteouts(oci, layer_entries)

        assert docker == oci

    @given(scenario=st_scenario())
    def test_p4a_markers_in_this_layer_are_gone(self, scenario: tuple[list[str], list[str], dict[str, bytes]]) -> None:
        """P4a: every ``.wh.`` marker listed in ``layer_entries`` is removed.

        layer.md "Whiteouts": "Once a whiteout is applied, the whiteout itself
        MUST also be hidden." Scoped to ``layer_entries`` deliberately — a
        marker carried up from a *lower* layer is untouched, which the
        ``C7-carried-up-marker-survives`` case pins.
        """
        layer_entries, _, vfs = scenario
        _assert_non_divergent_alphabet(layer_entries, vfs)

        apply_whiteouts(vfs, layer_entries)

        for entry in layer_entries:
            if posixpath.basename(entry).startswith(WHITEOUT_PREFIX):
                assert entry not in vfs

    @given(scenario=st_scenario())
    def test_p4b_never_gains_a_key_and_bytes_are_preserved(
        self, scenario: tuple[list[str], list[str], dict[str, bytes]]
    ) -> None:
        """P4b: the result is a sub-map of the input — no new keys, no rewritten bytes."""
        layer_entries, _, vfs = scenario
        _assert_non_divergent_alphabet(layer_entries, vfs)

        before = dict(vfs)
        apply_whiteouts(vfs, layer_entries)

        assert set(vfs) <= set(before)
        for key, value in vfs.items():
            assert value == before[key]

    @given(scenario=st_scenario())
    def test_p4c_same_layer_entries_survive_an_opaque_marker(
        self, scenario: tuple[list[str], list[str], dict[str, bytes]]
    ) -> None:
        """P4c: same-layer keys survive an opaque wipe, with the D2 carve-out.

        The proviso is deviation D2 (layer.md "Whiteouts": files in the same
        layer as a whiteout can only be hidden by *subsequent* layers), which
        this refactor deliberately preserves: a regular ``.wh.`` marker in the
        same layer still destroys its same-layer target. Follow-up F3.
        **Remove the ``targets`` exclusion when F3 lands** — that is how this
        invariant becomes the proof that F3 worked.
        """
        layer_entries, _, vfs = scenario
        _assert_non_divergent_alphabet(layer_entries, vfs)

        before = dict(vfs)
        targets = set(_marker_targets(layer_entries))
        apply_whiteouts(vfs, layer_entries)

        for entry in layer_entries:
            is_marker = posixpath.basename(entry).startswith(WHITEOUT_PREFIX)
            if not is_marker and entry in before and entry not in targets:
                assert entry in vfs

    @given(scenario=st_scenario())
    def test_p4d_result_is_independent_of_entry_order(
        self, scenario: tuple[list[str], list[str], dict[str, bytes]]
    ) -> None:
        """P4d: permuting ``layer_entries`` does not change the result.

        Structural, not cited: every operation is a deletion whose predicate
        depends only on key names and on ``frozenset(layer_entries)``, which is
        order-free, so the union of removals is order-invariant.
        """
        layer_entries, permuted, vfs = scenario
        _assert_non_divergent_alphabet(layer_entries, vfs)

        first = dict(vfs)
        second = dict(vfs)
        apply_whiteouts(first, layer_entries)
        apply_whiteouts(second, permuted)

        assert first == second


# ---------------------------------------------------------------------------
# Deterministic companion cases.
#
# These carry the real coverage under the 5-example "dev" profile and pin every
# shape st_vfs only makes *possible*. Every expected value was derived by
# executing the frozen legacy Docker oracle (design.md Appendix B and D), never
# by reading apply_whiteouts. Do not repair a failing row by pasting in what the
# implementation produced.
# ---------------------------------------------------------------------------

_CASES: list[tuple[list[str], list[str], list[str]]] = [
    # (layer_entries, vfs keys before, expected sorted keys after)
    # design.md §3 rows — the three behaviour-change rows name the old OCI
    # result in their case id, so `pytest -v` shows the change.
    (["a//.wh.f"], ["a/f"], []),
    (["a//.wh.f"], ["a//f"], ["a//f"]),
    (
        ["a//.wh..wh..opq"],
        ["a/f", "a/b/f", "a/b/c/f", "a/.dot", "ab/x"],
        ["ab/x"],
    ),
    (["/.wh.f"], ["f"], ["f"]),
    (["/.wh..wh..opq"], ["f", "a/f"], ["a/f", "f"]),
    (["//.wh..wh..opq"], ["//f"], ["//f"]),
    (["//.wh.f"], ["//f", "f"], ["f"]),
    (["///.wh.f"], ["///f"], []),
    (["a/.wh..wh..opq", "a/new"], ["a/old", "a/new"], ["a/new"]),
    (["a/.wh..wh..opqX"], ["a/.wh..opqX", "a/f"], ["a/f"]),
    (["a/.wh..wh..opq"], ["ab/x", "a/f"], ["ab/x"]),
    # Companion cases C1-C7 (design.md Appendix D).
    ([".wh..wh..opq"], ["f", "a/f"], []),
    ([".wh..wh..opq", "new"], ["f", "a/f", "new"], ["new"]),
    (
        ["a/.wh..wh..opq", "a/b/.wh..wh..opq"],
        ["a/f", "a/b/f", "a/b/.wh..wh..opq"],
        [],
    ),
    (
        ["a/b/.wh..wh..opq", "a/.wh..wh..opq"],
        ["a/f", "a/b/f", "a/b/.wh..wh..opq"],
        [],
    ),
    (["a/.wh.f", "a/.wh..wh..opq"], ["a/f", "a/g"], []),
    (["a/.wh..wh..opq", "a/.wh.f"], ["a/f", "a/g"], []),
    (["a/.wh..wh..opq"], ["a/b/c/f", "ab/x"], ["ab/x"]),
    (["a/.wh.missing"], ["a/f", "a/g"], ["a/f", "a/g"]),
    (["a/g"], ["a/.wh.f", "a/g"], ["a/.wh.f", "a/g"]),
    # Inert entries: an empty basename matches neither branch (design.md §2.4).
    (["a/"], ["a/f"], ["a/f"]),
    (["a//"], ["a/f"], ["a/f"]),
    # Shared deviations D1-D3, preserved. See design.md §5; follow-ups F2-F4.
    (["a/.wh.b"], ["a/b", "a/b/c", "a/keep"], ["a/b/c", "a/keep"]),
    (["a/f", "a/.wh.f"], ["a/f"], []),
    (["a/.wh."], ["a/", "a/f"], ["a/f"]),
    ([".wh."], ["", "f"], ["f"]),
    (["a/b/.wh."], ["a/b/", "a/b/f"], ["a/b/f"]),
    (["a/.wh."], ["a/f"], ["a/f"]),
]

_CASE_IDS = [
    "row2-oci-kept-a-slash-f",
    "row3-oci-deleted-a-double-slash-f",
    "row4-oci-kept-all-under-a",
    "row5a-unreachable-leading-slash",
    "row5b-unreachable-root-opaque",
    "row5c-unreachable-depth2-opaque",
    "row5d1-nondivergent-depth2",
    "row5d2-nondivergent-depth3",
    "row8-same-layer-survives-opaque",
    "row11-near-miss-is-regular-whiteout",
    "sibling-guard-ab-not-under-a",
    "C1-root-opaque-no-same-layer",
    "C2-root-opaque-with-same-layer",
    "C3-nested-opaque-outer-first",
    "C3-nested-opaque-inner-first",
    "C4-regular-and-opaque-same-dir",
    "C4-regular-and-opaque-reversed",
    "C5-deep-descendant-under-opaque",
    "C6-absent-target-is-noop",
    "C7-carried-up-marker-survives",
    "inert-trailing-slash",
    "inert-double-slash",
    "D1-directory-whiteout-keeps-descendants",
    "D2-same-layer-file-wrongly-hidden",
    "D3-bare-wh-pops-dir-key",
    "D3-bare-wh-pops-root-key",
    "D3-bare-wh-deep",
    "D3-bare-wh-inert-normal-vfs",
]


@pytest.mark.unit
class TestWhiteoutDeterministicCases:
    """Fixed examples pinning every shape the Hypothesis generator only samples."""

    @pytest.mark.parametrize(("layer_entries", "vfs_keys", "expected"), _CASES, ids=_CASE_IDS)
    def test_case_matches_frozen_expectation(
        self, layer_entries: list[str], vfs_keys: list[str], expected: list[str]
    ) -> None:
        """The shared implementation reproduces the independently-derived result."""
        before = {key: f"content-of-{key}".encode() for key in vfs_keys}
        vfs = dict(before)

        apply_whiteouts(vfs, layer_entries)

        assert sorted(vfs) == expected
        for key in vfs:
            assert vfs[key] == before[key]

    @pytest.mark.parametrize(("layer_entries", "vfs_keys", "expected"), _CASES, ids=_CASE_IDS)
    def test_case_matches_frozen_docker_oracle(
        self, layer_entries: list[str], vfs_keys: list[str], expected: list[str]
    ) -> None:
        """The same literals hold for the legacy Docker oracle they were derived from.

        This is what makes the table non-circular: the expected values are a
        property of the frozen pre-refactor contract, not of the new code.
        """
        vfs = {key: f"content-of-{key}".encode() for key in vfs_keys}

        _legacy_docker_apply_whiteouts(vfs, layer_entries)

        assert sorted(vfs) == expected
