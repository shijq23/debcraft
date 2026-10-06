# DebCraft

Artifact Intelligence Platform for Debian-based ecosystems (Debian, eLxr).

DebCraft answers one question: *what Debian packages are inside this artifact, and what are they?*
It scans an artifact, enriches the packages it finds against a mirrored and indexed repository, and
emits the result as an SBOM in SPDX 3.0, SPDX 2.3, or CycloneDX form.

```
scan  ->  enrich  ->  assemble  ->  write
```

## Status

Pre-release (version `0.1.0`). Milestones M0 through M7 are complete — every `tasks.md` from
`.kiro/specs/m0-engineering-foundation` through `.kiro/specs/m7-sbom-writers` is fully checked off,
covering the engineering foundation, platform kernel, storage layer, repository mirror, indexer,
package intelligence, artifact scanners, and SBOM writers. Not published to PyPI; install from a
clone.

## Requirements

- Python 3.13 or newer
- [uv](https://docs.astral.sh/uv/) for dependency management. All commands run through `uv run`.
- Contributors who want to build the binary test fixtures also need `genisoimage` and
  `squashfs-tools` (`make -C fixtures images`). CI builds these on Linux only.

Ten runtime dependencies: `typer`, `rich`, `sqlalchemy`, `aiosqlite`, `greenlet`, `aiohttp`,
`jsonschema`, `pycdlib`, `PySquashfsImage`, `zstandard`.

## Install and quickstart

```bash
uv sync
uv run debcraft doctor
```

Scan a root filesystem directory and write two SBOM formats:

```bash
uv run debcraft sbom /path/to/rootfs -f spdx_2_3 -f cyclonedx -o out/
```

For enriched output (checksums, PURLs, licenses, dependency edges), first mirror and index a
repository so a published snapshot exists, then generate:

```bash
uv run debcraft mirror sync      # download repository metadata, mark files VERIFIED
uv run debcraft index            # parse the verified files, publish a snapshot
uv run debcraft sbom ./image.iso -f spdx_3_0 -o out/
```

With no `mirrors.toml` on disk, `mirror sync` uses a built-in default: the eLxr repository at
`https://mirror.elxr.dev/elxr`, suite `aria`, component `main`, architectures `amd64` and `arm64`.
To override, write `~/.config/debcraft/mirrors.toml` (`$XDG_CONFIG_HOME` is honored). It takes one
`[settings]` table (`download_timeout`, `max_connections_per_repo`, `max_total_connections`,
`rate_limit_rps`, `rate_limit_burst`) and one `[[repository]]` entry per repository:

```toml
[[repository]]
name = "elxr"
base_url = "https://mirror.elxr.dev/elxr"
suites = ["aria"]
components = ["main"]
architectures = ["amd64", "arm64"]
```

## Commands

| Command | Purpose |
|---------|---------|
| `sbom <artifact_path>` | Generate an SBOM in one or more formats |
| `mirror sync` | Download and verify the configured repositories |
| `mirror verify` / `status` / `list` / `clean` | Re-check checksums, report state, list repos, prune the cache |
| `index` | Index repositories with verified files in the mirror cache |
| `index package <name>` | Show the latest indexed metadata for one package |
| `version` | Print the version |
| `doctor` | Environment health check |
| `info` | Configuration and environment info |

`debcraft sbom` options: `--format/-f` (repeatable), `--output-dir/-o` (default `.`), `--type/-t`,
`--snapshot-id`, `--quiet/-q`. `--verbose/-v` is a global flag and goes before the subcommand
(`uv run debcraft -v sbom ...`). `index` accepts `--repository/-r` to narrow to one repository, and
`mirror clean` accepts `--yes/-y` to skip its confirmation prompt.

## Artifact types and output formats

Valid `--type` values: `directory` (extracted root filesystem), `docker` (image tarball), `oci`
(image layout), `iso` (ISO 9660, including squashfs payloads), `qcow2`, `img` (raw disk image), and
`ami`. When `--type` is omitted the type is inferred from the path: any real directory, plus the
extensions `.iso`, `.qcow2`, `.img`, `.tar`, `.tar.gz`, `.tgz`, `.oci`, and `.ami`. Anything
unrecognized falls back to `directory`.

| `--format` | Output file |
|------------|-------------|
| `spdx_3_0` | `sbom.spdx3.json` |
| `spdx_2_3` | `sbom.spdx.json` |
| `cyclonedx` | `sbom.cdx.json` |

Omitting `--format` writes all three.

## Enrichment needs a published snapshot

A scan on its own identifies package names, versions, and architectures. Checksums, download URLs,
PURLs, licenses, and `DEPENDS_ON` dependency edges all come from the enrichment step, which needs a
published `RepositorySnapshot` in `metadata.db` — the artifact of `debcraft mirror sync` followed by
`debcraft index`.

Snapshot resolution (`src/debcraft/cli/_sbom_db.py::resolve_snapshot_id`) works like this:

1. `--snapshot-id N` is used verbatim, with no existence check.
2. Otherwise the highest published snapshot in `metadata.db` wins.
3. If there is none — or `metadata.db` does not exist — the snapshot ID falls back to `0`, which
   **skips enrichment entirely**. The only signal is a log warning, visible with `-v`.

The run still succeeds and still writes valid-looking files, so an un-enriched SBOM is easy to
mistake for a complete one. It will be thin: sparse licensing, no PURLs, and a nearly edge-free
dependency graph. Check for a snapshot before trusting the output.

## Data locations

Resolved per the XDG Base Directory spec, with macOS and Windows fallbacks. On Linux: config in
`~/.config/debcraft/`, databases (`mirror.db`, `metadata.db`) in `~/.local/share/debcraft/`, the
mirror cache in `~/.cache/debcraft/mirror/`, and the enrichment cache at
`~/.cache/debcraft/cache/cache.db`.

## Architecture

Four layers. `domain/` holds pure logic — frozen value objects, `Protocol` ports, errors, services.
`platform/` holds the ABC contracts plus a kernel with the DI container, workflow engine, lifecycle,
and config. `infrastructure/` holds the adapters that satisfy the domain ports. `cli/` is the Typer
application.

```
src/debcraft/
  domain/           scanner, sbom, indexer, mirror, package_intelligence
  platform/
    contracts/      ABCs and plain value types
    kernel/         DI container, workflow engine, lifecycle, config, PlatformError
    sdk/            stub; intended public API for plugin authors
  infrastructure/   scanners, sbom_writers, database (+migrations), storage,
                    repositories, models, mirror, indexer, package_intelligence
  plugins/          empty today
  cli/              Typer app
```

Three layering rules are machine-enforced, by `import-linter` contracts in `pyproject.toml` and by
AST-scanning tests under `tests/architecture/`:

- `debcraft.domain` must not import `debcraft.infrastructure`.
- `debcraft.platform.contracts` must not import `infrastructure`, `plugins`, or `platform.kernel`.
- Packages under `debcraft.plugins` must not cross-import siblings.

```bash
uv run lint-imports            # 3 contracts, all kept
uv run pytest -m architecture  # 17 tests
```

Nothing beyond those three is enforced. In particular, `domain` does import `platform.contracts` and
`platform.kernel.errors`, and `cli` imports `infrastructure` directly.

## Development

`Makefile` and `justfile` define the same seven targets with the same commands, so either works.

```bash
uv sync        # install/sync dependencies (CI uses uv sync --locked)
make test      # uv run pytest, then uv run pytest -m architecture
make lint      # full gate, see below
make build     # rm -rf dist/ && uv build
make docs      # uv run mkdocs build --strict
make clean     # remove caches and build output
```

`make lint` runs, in order: `ruff format --check`, `ruff check --fix`, `basedpyright`, `mypy`,
`lint-imports`, `pylint src/`, and `pre-commit run --all-files`. Note `ruff check --fix` mutates
files; use `uv run ruff check .` to inspect without changing anything.

Two type checkers run and both must pass: `basedpyright` over `src` in standard mode, and
`mypy --strict` over the `debcraft` package. Neither CI pipeline runs `mypy` or `pylint` — only
`make lint` does, so run it locally before pushing.

`make test` covers the unit and architecture markers. It does not run `-m integration`, which has a
known baseline failure.

CI lives in `.github/workflows/ci.yml` (ubuntu, windows, macos; the fixture build step is Linux
only), `.github/workflows/static.yml` (docs build), and `.gitlab/.gitlab-ci.yml` (a second live
single-OS pipeline with no fixture-build step). Changing a CI gate means editing both pipelines.

## Documentation

The real documentation is MkDocs Material under `docs/` — architecture notes, user guides per
artifact type, developer guides, and an mkdocstrings API reference. Build it with `make docs`;
output lands in `site/`. Feature specs with requirements, design, and task lists live in
`.kiro/specs/`.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution workflow and [SECURITY.md](SECURITY.md)
for reporting vulnerabilities. License terms are in [LICENSE](LICENSE).
