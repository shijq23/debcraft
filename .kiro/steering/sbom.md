---
inclusion: auto
name: SBOM subsystem
description: Known behaviour, limitations and gotchas of DebCraft SBOM generation — the debcraft sbom CLI command, the scan/enrich/assemble/write pipeline, SPDX 3.0 / SPDX 2.3 / CycloneDX writers, schema validation, writer diagnostics, and dependency-graph completeness. Use when generating, debugging, validating or modifying SBOM output.
---

# SBOM subsystem: verified behaviour and gotchas

Pipeline, in `src/debcraft/infrastructure/sbom_writers/workflow.py`:
**scan** (`ScannerRegistry` → `ArtifactScanner`) → **enrich** (`MetadataEnricher`, needs a `metadata.db` snapshot) → **assemble** (`domain/sbom/assembler.py::ModelAssembler` → `SBOMDocument`) → **write** (`WriterRegistry` → one file per requested format).

```bash
uv run debcraft sbom <artifact> -f spdx_2_3 -f cyclonedx -o out/ [-t iso] [--snapshot-id N] [-q]
```

Type is auto-detected from the extension by `detect_artifact_type()` when `-t` is omitted; unknown extensions fall back to `DIRECTORY`. Filenames are fixed per format: `sbom.spdx3.json`, `sbom.spdx.json`, `sbom.cdx.json`.

## 1. Writer diagnostics never reach the terminal

`_collect_results` in `src/debcraft/cli/sbom.py` (~lines 529-561) does **not** return the workflow's `WriterResult` objects. It re-stats the output files from disk and builds fresh `WriterResult`s, leaving `diagnostics` at its empty default. `_run_sbom` returns those (~line 651), so `_display_diagnostics` has nothing to print.

Consequence: schema-validation warnings produced by a writer are silently dropped. A `debcraft sbom` run can exit 0 with a clean summary table while the document it just wrote fails schema validation. The writers themselves do populate `diagnostics` correctly — only the CLI path discards them.

To actually see validation state, run the validator out of band:

```bash
uv run python -c "
from debcraft.domain.sbom.validator import SchemaValidator
from debcraft.domain.sbom.values import OutputFormat
v = SchemaValidator()
print(len(v.validate(open('out/sbom.spdx3.json').read(), OutputFormat.SPDX_3_0)))"
```

Note `SchemaValidator.validate` takes a **JSON string**, not a parsed dict — passing a dict raises `TypeError`.

## 2. SPDX 3.0 has exactly 2 known schema-validation errors

Verified against `.agents/sbom-run/sbom.spdx3.json`: SPDX 2.3 → **0** errors, CycloneDX → **0** errors, SPDX 3.0 → **2** errors.

Both are the same root-level finding: `Unevaluated properties are not allowed`, naming `@type`, `creationInfo`, `element`, `name`, `spdxId`.

Cause: the bundled `src/debcraft/domain/sbom/schemas/spdx-3.0.schema.json` is shacl2code-generated. It keys classes on `type` rather than `@type`, and its root sets `unevaluatedProperties: false`. M7 **Req 4.6** mandates the exact shape the writer emits (top-level `SpdxDocument` with `@type` and an inline `element` array), so **Req 4.1 (schema conformance) and Req 4.6 (document shape) are mutually unsatisfiable against the bundled schema**. Req 4.11 says to write the file anyway and record the errors in diagnostics — which the writer does, and which gotcha 1 then swallows.

Prior probing found no cheap fix: renaming `@type`→`type` still leaves 2 errors, and wrapping in `@graph` produces 4,640. Real conformance needs an element-reference rewrite.

**Treat exactly these 2 SPDX 3.0 root errors as baseline. Any other error, in any format, is a regression worth investigating.**

## 3. The dependency graph is enrichment-gated

`depends` is a field on `PackageEnrichment`, not on `IdentifiedPackage`. `ModelAssembler._build_depends_on_relationships` returns immediately when `enriched_pkg.enrichment is None or ...depends is None`.

So a scan-only run (no usable `--snapshot-id`) emits a nearly edge-free dependency graph even though the scanner read the `Depends` lines out of the artifact. On the eLxr ISO run, 39 of 512 components had no `DEPENDS_ON` edge at all; 1294 edges were emitted against 1553 derivable from the artifact's own `Depends` fields.

The same gate applies to `sha256`, `download_url`, `purl` and licenses. An isolated node silently understates blast radius for anyone doing transitive impact analysis — prefer saying "enrichment coverage was N%" over presenting the graph as complete.

## 4. DEPENDS_ON deduplication lives in the assembler only

`_build_depends_on_relationships` keeps a `seen_pairs` set and emits each `(source, target)` once, in first-occurrence order. This matters because a `Depends` line like `libbar (>= 2.0), libbar, libbar (<< 3.0)` collapses to one target name, and CycloneDX 1.5 declares `dependsOn` as `uniqueItems`.

Neither `cyclonedx.py::_build_dependencies` (it sorts `dependsOn` but does not uniquify) nor `spdx3.py` (it derives element ids as `{source}-to-{target}`) has its own guard. Removing the assembler's `seen_pairs` check breaks `tests/unit/domain/sbom/test_assembler.py::TestDependsOnRelationships::test_depends_on_deduplicates_repeated_dependency` and both boundary tests in `tests/unit/infrastructure/test_sbom_writer_relationship_uniqueness.py`.

Separately, two distinct packages that serialize to the same CycloneDX `bom-ref` would also violate `uniqueItems` on `/components` — unfixable by uniquifying `dependsOn`. Not triggered by the eLxr ISO (512 unique names), so it is an open edge case, not a live defect.

## 5. Output is deterministic per-document, not across runs

M7 Req 11.5 / design Property 5 define determinism as serializing the *same* document twice yielding identical bytes. Cross-run digests legitimately differ: `ModelAssembler._build_creation_info` uses `datetime.now(UTC)`, `_generate_namespace` appends a fresh `uuid4`, and the CycloneDX `serialNumber` is a UUID v5 derived from that namespace.

When diffing two runs, blank the volatile fields first:

| Format | Volatile fields |
|--------|-----------------|
| SPDX 2.3 | `documentNamespace`, `creationInfo.created` |
| SPDX 3.0 | top-level `spdxId`, every nested `creationInfo.created` |
| CycloneDX | `serialNumber`, `metadata.timestamp` |

After normalization all three formats compare equal across runs. File sizes match even before normalization.

## Reference material

Verified findings from a full eLxr ISO run live in `.agents/sbom-run/REPORT.md` with the three output documents beside it, and reviewer corrections in `.agents/tasks/task-iso-sbom-run/review.md`. Both paths are gitignored, so they may be absent in a fresh clone.
