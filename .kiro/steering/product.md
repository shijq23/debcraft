---
inclusion: always
---

# DebCraft

Artifact Intelligence Platform for Debian-based ecosystems (Debian, eLxr). Python 3.13, CLI-first.

It answers "what Debian packages are inside this artifact, and what are they?" and emits that as an SBOM.

Pipeline: **scan** an artifact → **enrich** package metadata from a mirrored/indexed repository → **assemble** an internal SBOM model → **write** SPDX 3.0 / SPDX 2.3 / CycloneDX.

## CLI surface

`uv run debcraft <command>` — commands verified via `debcraft --help`:

| Command | Purpose |
|---------|---------|
| `sbom` | Generate an SBOM for an artifact in one or more formats |
| `mirror` | Repository mirror management subcommands |
| `index` | Repository indexing subcommands |
| `version` | Print version |
| `doctor` | Environment health check |
| `info` | Configuration and environment info |

Scannable artifact types (`ArtifactType` in `src/debcraft/domain/scanner/values.py`):
`directory`, `docker`, `oci`, `iso`, `qcow2`, `img`, `ami`.

Output formats (`OutputFormat` in `src/debcraft/domain/sbom/values.py`):
`spdx_3_0` → `sbom.spdx3.json`, `spdx_2_3` → `sbom.spdx.json`, `cyclonedx` → `sbom.cdx.json`.

## Scope notes

- Enrichment (checksums, PURLs, licenses, `depends`) needs a **published** `RepositorySnapshot` in `metadata.db`, built by `debcraft mirror` + `debcraft index`. `cli/_sbom_db.py::resolve_snapshot_id` uses `--snapshot-id` verbatim if given (no existence check), otherwise picks the highest published snapshot, otherwise falls back to `0`, which **skips enrichment entirely** with only a log warning. A scan-only SBOM is thin — see `#sbom`.
- Development status: milestones M0–M7 are complete. See `#specs-and-milestones`.
- `README.md` is a stub. Real documentation is `docs/` (built with MkDocs) and `.kiro/specs/`.
