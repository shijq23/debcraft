---
inclusion: always
---

# Tech stack and canonical commands

Python `>=3.13` (`.python-version` pins `3.13`; local interpreter is 3.13.9). Package manager is **uv** — never call `pip` or a bare `python`/`pytest`; always `uv run <tool>`.

Runtime deps (`[project] dependencies`, all 10): typer + rich (CLI), sqlalchemy + aiosqlite + greenlet (async SQLite), aiohttp (HTTP), jsonschema (SBOM schema validation), pycdlib (ISO), PySquashfsImage (squashfs), zstandard.

## Canonical targets

`Makefile` and `justfile` define the same seven targets with the same commands — neither overrides the other, pick either. No `install` target exists; use `uv sync` directly. `make test` is green on a clean tree because it runs the unit-only default plus architecture; it does **not** cover `-m integration`, which has a known baseline failure (see `#testing`).

| Task | Command |
|------|---------|
| Install / sync deps | `uv sync` (CI uses `uv sync --locked`) |
| Test | `make test` → `uv run pytest` then `uv run pytest -m architecture` |
| Lint + typecheck + arch (full gate) | `make lint` |
| Build wheel/sdist | `make build` → `rm -rf dist/ && uv build` |
| Docs | `make docs` → `uv run mkdocs build --strict` |
| Clean caches | `make clean` |
| Run mirror / index | `make mirror` / `make index` |

`make lint` runs these in order — all verified passing on current `main`:

```bash
uv run ruff format --check .   # "N files already formatted" (N varies: ruff walks untracked dirs too)
uv run ruff check --fix .      # All checks passed
uv run basedpyright            # 0 errors, 0 warnings, 0 notes
uv run mypy                    # Success: no issues found in 170 source files
uv run lint-imports            # Contracts: 3 kept, 0 broken
uv run pylint src/             # 10.00/10 (gate: fail-under = 9.90)
uv run pre-commit run --all-files  # all hooks Passed
```

Note `ruff check --fix` mutates files. Use `uv run ruff check .` when you only want to inspect.

`pre-commit` hooks (`.pre-commit-config.yaml`) are a strict subset of the above: `ruff-format`, `ruff --fix`, `check-yaml`, `check-toml`, `end-of-file-fixer`, `trailing-whitespace`. Passing `make lint` implies passing the hooks.

## Type checking is doubled up

Both run, both must pass, and their scopes differ:

- `basedpyright` — `include = ["src"]`, `typeCheckingMode = "standard"`.
- `mypy` — `packages = ["debcraft"]`, `strict = true`. Tests relax `disallow_untyped_defs`. `pycdlib` / `PySquashfsImage` have `ignore_missing_imports`.

`mypy` prints `note: unused section(s): module = ['PySquashfsImage.*', 'tests.*']` on a clean run. That note is expected, not a failure.

## CI

`.github/workflows/ci.yml` runs on a 3-OS matrix (ubuntu, windows, macos), steps in order:

`uv sync --locked` → `ruff format --check src/ tests/` → `ruff check src/ tests/` → `basedpyright src/` → build test fixtures (**Linux only**: installs `genisoimage`/`squashfs-tools`, runs `make -C fixtures images`) → `uv run pytest` with `HYPOTHESIS_PROFILE=ci` → `lint-imports` → `pytest -m architecture`.

`.github/workflows/static.yml` runs `mkdocs build --strict`.

`.gitlab/.gitlab-ci.yml` is a second, live pipeline (58 lines, `python:3.13` image, `pip install uv && uv sync --locked` setup) with three **stages** — `lint`, `typecheck`, `test` — holding six **jobs**: `format` and `lint` in the `lint` stage (`ruff format --check src/ tests/`, `ruff check src/ tests/`), `typecheck` (`basedpyright src/`), then `unit-tests` (`uv run pytest` with `HYPOTHESIS_PROFILE=ci`), `import-linter` (`lint-imports`) and `architecture-tests` (`pytest -m architecture`) in the `test` stage.

It is single-OS and has **no equivalent of GitHub's Linux-only fixture-build step**, so tests needing generated binary fixtures behave differently between the two. Changing a CI gate means editing **both** pipeline files.

Neither CI runs mypy or pylint; `make lint` does. Keep both green.

## Documentation

MkDocs Material, config in `mkdocs.yml`, sources in `docs/`. `mkdocstrings` renders API pages from Google-style docstrings, so a malformed docstring can break `mkdocs build --strict`. Any new page must be added to the `nav:` in `mkdocs.yml`.

`docs/adr/` contains only `index.md` and `template.md` — **no ADRs have been written**. Do not cite ADRs as authority; there are none.

Build output goes to `site/` (removed by `make clean`).
