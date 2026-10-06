---
inclusion: always
---

# Structure and layer rules

## Layer dependency rules — ENFORCED

Only these rules are machine-checked. Each is enforced by one or both of two mechanisms — `import-linter` contracts in `pyproject.toml` (`[tool.importlinter]`) and AST-scanning tests in `tests/architecture/`. The last two rows are architecture-test-only.

| Rule | import-linter contract | Architecture test |
|------|------------------------|-------------------|
| `debcraft.domain` must not import `debcraft.infrastructure` | `Domain independence` (forbidden) | `test_architecture.py::test_domain_does_not_import_infrastructure` |
| `debcraft.platform.contracts` must not import `infrastructure`, `plugins`, or `platform.kernel` | `Contracts purity` (forbidden) | `test_architecture.py::test_contracts_have_no_implementation_dependencies`, `test_platform_architecture.py::TestContractPurity` (3 tests) |
| Plugins under `debcraft.plugins` must not cross-import sibling plugins | `Plugin isolation` (independence) | `test_architecture.py::test_plugins_do_not_cross_import` |
| No mutable module-level globals (`list`/`dict`/`set`/mutable calls) in `domain`, `platform`, `infrastructure`, `plugins` unless `Final`-annotated, ALL_CAPS, or `__all__` | — not covered | `test_architecture.py::test_no_mutable_module_level_global_state`; stricter variant for `platform/kernel` in `test_platform_architecture.py::TestNoMutableGlobalState` (also allows leading-underscore names) |
| Every ABC in `platform/contracts` has a concrete subclass in `platform/kernel` or `infrastructure` | — not covered | `test_platform_architecture.py::TestABCImplementationMapping` (exempts `Workflow`) |

Check both:

```bash
uv run lint-imports          # 3 contracts, all KEPT
uv run pytest -m architecture # 17 tests
```

## What is NOT enforced

Do not assume a layering rule beyond the table above. The actual import graph today:

- `domain` **does** import `debcraft.platform.contracts` (`events`, `workflow`) and `debcraft.platform.kernel.errors` (every domain `errors.py` subclasses `PlatformError`). Domain is independent of *infrastructure*, not of *platform*.
- `infrastructure` imports `platform.contracts` and `platform.kernel.errors` freely.
- `platform` imports **nothing** from `domain`.
- `cli` imports `infrastructure` directly (`cli/sbom.py`, `cli/index.py`, `cli/mirror.py`, `cli/_sbom_db.py`).

`docs/architecture/index.md` carries a "May Depend On" table that contradicts all four of these points. Trust this file and the enforcement commands over that table.

## Where things live

```
src/debcraft/
  domain/            # pure logic: values, ports (Protocols), errors, services
    scanner/         #   ports.py = ArtifactScanner, ContentsIndexPort, PackageLookupPort, GuestfsInspector
    sbom/            #   ports.py = SBOMWriter; assembler.py, validator.py, schemas/ (bundled JSON schemas)
    indexer/  mirror/  package_intelligence/
  platform/
    contracts/       # ABCs + plain value/enum types, no Protocols (container, workflow, events, logging, persistence, storage, policies, resources, configuration)
    kernel/          # DI container, workflow engine, lifecycle, config, errors (PlatformError)
    sdk/             # STUB — intended public API for plugin authors; only a docstring __init__.py today
  infrastructure/    # adapters: scanners/, sbom_writers/, database/ (+migrations/), storage/, repositories/, models/, mirror/, indexer/, package_intelligence/
  plugins/           # EMPTY today (only a docstring __init__.py)
  cli/               # Typer app; sbom.py, mirror.py, index.py, _formatting.py, _progress.py, _storage.py, _sbom_db.py
```

### Adding a file

- New business rule / value object / port → `domain/<subdomain>/`. Keep infrastructure imports out.
- New ABC or Protocol for the platform → `platform/contracts/`. It must stay import-pure *and* gain a concrete implementation in `kernel/` or `infrastructure/`, or `TestABCImplementationMapping` fails.
- New adapter (DB, filesystem, network, format writer) → `infrastructure/<area>/`.
- New scanner or SBOM writer → `infrastructure/scanners/` or `infrastructure/sbom_writers/`. See `#plugins`.
- New CLI command → `src/debcraft/cli/`, registered on the Typer app in `cli/__init__.py`.
- Do **not** add to `src/debcraft/plugins/` without reason; the `plugins` package is unused and the isolation contract over it is currently vacuous.

## Code conventions

Derived from `pyproject.toml` (`[tool.ruff]`), `.editorconfig`, and `.pre-commit-config.yaml`:

- Line length 120. Target `py313`. 4-space indent, LF endings, UTF-8, final newline.
- Google-style docstrings (`[tool.ruff.lint.pydocstyle] convention = "google"`), required on public modules/classes/functions — pydocstyle `D` is enabled; only `D100` and `D104` are ignored.
- Full type annotations: ruff `ANN`, plus `basedpyright` (CI) and `mypy --strict` (`make lint` only).
- `from __future__ import annotations` is the prevailing first import in `src/`; imports used only in annotations go under `if TYPE_CHECKING:` (ruff `TCH` enforces this).
- **Domain ports are `typing.Protocol`** (structural, no inheritance required) in `domain/*/ports.py`. **Platform contracts are `ABC`** with `@abstractmethod` — 8 of the 10 modules in `platform/contracts/` use `ABC` and none use `Protocol` (the exceptions: `policies.py` is plain dataclasses, `__init__.py` declares nothing). Pick the right one: a new platform contract ABC must gain an implementation or `TestABCImplementationMapping` fails; a Protocol carries no such obligation.
- Value objects are `@dataclass(frozen=True)` with validation in `__post_init__`.
- Domain errors subclass `PlatformError` from `platform/kernel/errors.py`.
- `pathlib.Path` for paths, not `os.path` (stated in `CONTRIBUTING.md`; prevailing in `src/`).
