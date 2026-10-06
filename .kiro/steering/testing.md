---
inclusion: fileMatch
fileMatchPattern: 'tests/**/*'
---

# Testing conventions

pytest 9 with `--strict-markers`. Config lives in `pyproject.toml` under `[tool.pytest.ini_options]`.

**Default `addopts` is `-m 'unit and not slow' --strict-markers -vv`.** A bare `uv run pytest` therefore runs *only* unit tests. Current baseline on `main`: **2022 passed, 112 deselected, 5 xfailed** in ~60s.

Because `--strict-markers` is on, any `@pytest.mark.X` not listed in the `markers` array in `pyproject.toml` is a hard error. Add the marker to `pyproject.toml` before using a new one.

## Integration baseline

`uv run pytest -m integration` → **67 passed, 0 failed** (~13s). The marker spans three locations: 48 in `tests/integration/`, 13 in `tests/docs/`, 6 in `tests/unit/test_debian_test_repos.py`.

Those last 6 used to fail with `FileNotFoundError: .../tests/fixtures/create-package.sh` — fixed in `1d58d44`. The cause is worth knowing, because the trap is easy to re-introduce: tests in `tests/unit/` must resolve the repo root as `Path(__file__).parent.parent.parent` (`tests/unit/` → `tests/` → root). Using `.parent.parent` lands on `tests/`, whose `fixtures/` directory is empty; the real generators live in the repo-root `fixtures/`.

If you add a test that shells out to a fixture script, anchor it the same way and give it the `integration` marker.

## Directory layout and what each tier means

| Path | Contents |
|------|----------|
| `tests/unit/` | Bulk of the suite. Mirrors `src/` layout (`domain/`, `infrastructure/`, `platform/`). |
| `tests/properties/` | 85 Hypothesis test modules, mirroring `src/` (`domain/`, `infrastructure/`). |
| `tests/architecture/` | 17 layer-boundary tests. See `#structure`. |
| `tests/integration/` | 48 integration tests; subdirs `scanner/`, `package_intelligence/`. May need external resources. |
| `tests/docs/` | Documentation content + fixture-script checks (13 integration, rest unit). |
| `tests/fixtures/` | **Empty.** Real fixture generators are in the repo-root `fixtures/`. |
| `tests/contract/`, `tests/e2e/`, `tests/benchmark/`, `tests/regression/` | **Empty** (only `__init__.py`). Their markers are registered but unused — zero tests collect. |

### Property tests are not a separate tier at runtime

Most modules under `tests/properties/` are marked `@pytest.mark.unit` (≈175 occurrences) and only 34 tests carry `@pytest.mark.property`. So **property tests run in the default `uv run pytest`** (430 of 458 collect there). `-m property` selects only a small subset — it is not "all the property tests".

`.hypothesis/` at the repo root is Hypothesis' example/cache database. It is generated; do not hand-edit.

## Hypothesis profiles

Registered in `tests/conftest.py`, selected by the `HYPOTHESIS_PROFILE` env var:

- `dev` (**default**) — `max_examples=5`, suppresses the `too_slow` health check. Fast, shallow.
- `ci` — `max_examples=100`. What `.github/workflows/ci.yml` sets.

Reproduce CI thoroughness locally with `HYPOTHESIS_PROFILE=ci uv run pytest`.

## Session-scoped autouse fixtures in `tests/conftest.py`

These apply to every test, and they are why tests do not touch real user state:

- `_isolate_xdg_paths` — redirects `XDG_DATA_HOME` / `XDG_CACHE_HOME` / `XDG_CONFIG_HOME` into a temp tree, so nothing writes to `~/.local/share/debcraft/`.
- `no_http_requests` — monkeypatches `aiohttp.TCPConnector._create_connection` to raise on any host outside `localhost`/`127.0.0.1`/`::1`. A test that needs network must target localhost or mock the client. The error reads `Test tried to make a real HTTP request to ...`.

Also available: `monkeypatch_session` (session-scoped `MonkeyPatch`), `tmp_working_dir` (wraps `tmp_path`).

## Running a focused subset

```bash
uv run pytest                                  # default: unit and not slow
uv run pytest -m architecture                  # 17 arch tests
uv run pytest -m "unit or architecture or integration"  # the three real tiers together
uv run pytest tests/unit/domain/sbom/           # a directory
uv run pytest tests/unit/domain/sbom/test_assembler.py::TestDependsOnRelationships
uv run pytest -m "spdx"                        # a feature marker
uv run pytest --cov=debcraft --cov-report=html  # coverage (branch=true)
HYPOTHESIS_PROFILE=ci uv run pytest            # thorough property runs
```

Overriding the marker filter requires passing your own `-m`, since `addopts` already supplies one.

## Marker vocabulary

Full list is in `pyproject.toml`. Grouped as: **type** (`unit`, `integration`, `contract`, `architecture`, `benchmark`, `regression`, `e2e`, `property`), **feature** (`repository`, `package`, `dep5`, `license`, `spdx`, `cyclonedx`, `docker`, `oci`, `iso`, `qcow2`, `ami`, `mirror`, `workflow`, `plugin`, `storage`, `database`), **environment** (`sqlite`, `network`, `filesystem`, `aws`, `container`, `cross_platform`, `linux_only`, `windows_only`, `macos_only`), **speed** (`slow`, `serial`, `parallel`).

Give every test a type marker — without `unit` (or another selected type) it silently never runs. Stack feature markers on top as applicable.

## Style in tests

`[tool.ruff.lint.per-file-ignores]` relaxes `tests/**/*.py`: `S101` (assert), `S108`, `S603`, `S608`, `D101`/`D102`/`D103`/`D107` (docstrings), `ANN` (annotations), `TCH`, `N806`, `RUF002`, `RUF003`. mypy also relaxes `disallow_untyped_defs` for `tests.*`. Line length 120 still applies, and ruff format still runs.

Async tests use `@pytest.mark.asyncio` (pytest-asyncio in **strict** mode).

Two filtered warnings in `pyproject.toml` are known-benign upstream issues (Typer/Click `_run_sync` coroutine, aiosqlite worker-thread race). Do not chase them.

Prefer generating binary fixtures via the scripts in `fixtures/` (`build-iso.sh`, `build-squashfs.sh`, `create-package.sh`, `create-repo.sh`, or `make -C fixtures images`) over committing binaries.

## Requirement traceability

Test docstrings cite acceptance criteria as `AC <req>.<n>` (e.g. `"""AC 2.6: ..."""`), matching the numbered requirements in `.kiro/specs/<spec>/requirements.md`. Property modules additionally tag `# Feature: <spec-name>, Property <n>: <title>` (present in 35 of 85 modules — a newer convention, not universal). Preserve these tags when editing; add them to new tests. See `#specs-and-milestones`.
