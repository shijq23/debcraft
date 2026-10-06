---
inclusion: fileMatch
fileMatchPattern: 'src/debcraft/infrastructure/**/*'
---

# Scanners and SBOM writers

Scope note: this file covers `infrastructure/scanners/` and `infrastructure/sbom_writers/` only. The match pattern spans all of `infrastructure/**` because a single glob cannot name two sibling subtrees; if you are editing `database/`, `storage/`, `models/`, `repositories/`, `mirror/`, `indexer/` or `package_intelligence/`, none of the below applies.

Both are infrastructure adapters satisfying a `Protocol` port declared in the domain layer. **But they are wired into the running system in two different ways, and this is the single most important thing to get right.**

## The asymmetry: writers use entry points, scanners do not

`pyproject.toml` declares both groups:

```toml
[project.entry-points."debcraft.scanners"]
directory = "...directory:DirectoryScanner"   # + docker, oci, iso, qcow2, img, ami
[project.entry-points."debcraft.sbom_writers"]
spdx_3_0 = "...spdx3:SPDX3Writer"             # + spdx_2_3, cyclonedx
```

Both groups name **classes**. The two registries treat that differently:

- `WriterRegistry._load_entry_point` calls `loaded()` to instantiate. Entry-point discovery **works**: `load_from_entry_points()` registers all 3 writers, zero diagnostics.
- `ScannerRegistry._load_entry_point` calls `inspect.isclass(loaded)` and **rejects classes** with a diagnostic, because scanners need constructor-injected ports that the registry cannot resolve. `load_from_entry_points()` registers **0 scanners** and emits 7 diagnostics of the form `Entry point 'iso' returned a class (ISOScanner), not an instance. Use registry.register() with a pre-built instance instead.`

Verify any time:

```bash
uv run python -c "
from debcraft.infrastructure.scanners.registry import ScannerRegistry
r = ScannerRegistry(); r.load_from_entry_points()
print(r.registered_types, len(r.diagnostics))"
# -> [] 7
```

This is deliberate, not a bug. It was designed in `.kiro/specs/sbom-scanner-registry-class-and-autodetect/` (see `bugfix.md` 1.1/2.1 and `design.md`) to make the failure explicit instead of storing an uncallable class. The scanner entry points remain as declarative metadata only.

**Scanners are actually registered in `src/debcraft/infrastructure/scanners/bootstrap.py`**, which resolves ports from the DI container and calls `registry.register(ArtifactType.X, XScanner(...))` explicitly. Adding a scanner means editing `_register_scanners()` there. Adding only an entry point does nothing.

`src/debcraft/infrastructure/sbom_writers/` has no bootstrap; `cli/sbom.py` constructs a `WriterRegistry`, calls `load_from_entry_points()`, and registers it into the scope.

## Adding an SBOM writer

1. Add the format to `OutputFormat` in `src/debcraft/domain/sbom/values.py`. The entry-point name **must equal the enum value** or the registry rejects it (`does not map to a valid OutputFormat enum value`).
2. Create `src/debcraft/infrastructure/sbom_writers/<fmt>.py` with a class satisfying `SBOMWriter` (`src/debcraft/domain/sbom/ports.py`):

```python
async def write(self, document: SBOMDocument, output_path: Path, context: WorkflowContext) -> WriterResult
```

   The `__init__` must take **no arguments** — the registry calls `loaded()` with none. Existing writers build their `SBOMPrinter` and `SchemaValidator` there.
3. Delegate the write to `write_with_cancellation` in `_write_utils.py`. It does the pre-write cancellation check, writes via `write_sbom_output`, re-checks cancellation and `unlink`s on cancel, then builds the `WriterResult`. Do not hand-roll this sequence.
4. Pass schema-validation findings as the `diagnostics` list — `WriterResult.diagnostics` (max 1000 entries).
5. Raise `OutputPathError`, `WriterCancellationError`, or `DocumentValidationError` from `domain/sbom/errors.py` as documented on the port.
6. Add a filename mapping in `_get_output_filename` in `src/debcraft/cli/sbom.py` (otherwise it falls back to `sbom.<value>.json`).
7. Register the entry point in `pyproject.toml`, then **`uv sync`** (see below).

Writers do **not** report progress. Progress is reported once per stage by `sbom_writers/workflow.py` (0/25/50/75/100). Writers only check `context.cancellation_token`.

## Adding a scanner

1. Add the type to `ArtifactType` in `src/debcraft/domain/scanner/values.py`, and an extension mapping in `_EXTENSION_MAP` so `detect_artifact_type()` can infer it (it falls back to `DIRECTORY` for unknown extensions, and returns `DIRECTORY` for any real directory path).
2. Create `src/debcraft/infrastructure/scanners/<type>.py` with a class satisfying `ArtifactScanner` (`src/debcraft/domain/scanner/ports.py`):

```python
async def scan(self, artifact: Artifact, context: WorkflowContext) -> ScanResult
```

   Constructor dependencies are fine and expected (ports, readers).
3. **Register it in `bootstrap.py::_register_scanners`.** Required ports come from `container.resolve(...)`; optional ones via the `ServiceNotFoundError`-catching helper `_resolve_optional_deps`, which allows graceful degradation (e.g. `ISOScanner` is skipped entirely when readers are absent; `QCOW2Scanner`/`IMGScanner` accept `None` for `guestfs_inspector`).
4. Optionally expose an `int` `priority` attribute. Higher wins for the same `ArtifactType`; ties keep the incumbent. No built-in scanner sets one today, so all are priority 0.
5. Add the entry point to `pyproject.toml` for consistency with the existing seven, understanding it is metadata only.

Error handling contract: raise `ArtifactAccessError` only when the artifact is wholly inaccessible. For missing optional deps or unparseable content, return a `ScanResult` with empty/partial `packages` and an explanation in `diagnostics` so the workflow continues.

Both registries' `_validate_protocol` only checks that `inspect.iscoroutinefunction(obj.scan/write)` is true. A sync method is silently rejected with a diagnostic, not an exception — so a non-async method surfaces as "plugin missing", not as a type error.

## Operational gotcha: entry points need a reinstall

`importlib.metadata.entry_points()` reads installed distribution metadata, not `pyproject.toml` on disk. A newly declared entry point is invisible until the package is reinstalled into the environment:

```bash
uv sync          # re-resolves and reinstalls the editable project
```

Symptom if you skip it: `UnsupportedFormatError` / `UnsupportedArtifactTypeError` naming only the previously registered values, despite `pyproject.toml` looking correct.

## Existing docs are stale here

`docs/developer/writing-a-scanner.md` is a good reference for the value objects, `WorkflowContext`, and error-handling patterns. Its "Entry-Point Registration" section is **wrong**: it claims that after `pip install -e .` "debcraft will automatically discover and load your scanner." It will not — see the asymmetry above. That section also omits `oci` and `ami` from the built-in list, and its test snippet uses the `WorkflowContext` constructor kwargs (`progress_reporter=`, `resource_manager=`) while the table lists the attribute names (`progress`, `resources`); both are correct in their own context, which reads as a contradiction.
