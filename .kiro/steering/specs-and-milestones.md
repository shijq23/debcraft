---
inclusion: manual
---

# Specs, milestones and traceability

## Milestones

M0 through M7 are complete. Each landed as exactly one commit on `main`:

```
12f3ffc Milestone M0 (Engineering Foundation)
bb5f817 Milestone M1 (Platform Kernel)
595a230 Milestone M2 (Storage Layer)
5f617c1 Milestone M3 (Repository Mirror)
2d0d44a Milestone M4 (Repository Indexer)
e5674af Milestone M5 (Package Intelligence)
fc0a776 Milestone M6 (Artifact Scanners)
89038cc Milestone M7 (SBOM Writers)
```

Each has a matching spec directory: `.kiro/specs/m0-engineering-foundation/` … `m7-sbom-writers/`.

Post-M7 work is conventional-commit, one-change-per-commit (`fix:`, `test:`, `chore:`). Do not create new "Milestone" commits unless the user is landing a planned milestone.

## Spec layout

35 spec directories under `.kiro/specs/<name>/`. Every one has `design.md` and `tasks.md`. The third file distinguishes two kinds:

| Third file | Count | Kind |
|------------|-------|------|
| `requirements.md` | 21 | Feature spec |
| `bugfix.md` | 14 | Bug spec |

Feature `requirements.md`: `# Requirements Document` → `## Introduction` → `## Glossary` (defines domain terms used by the ACs) → `### Requirement <N>: <Title>` → `#### Acceptance Criteria` → a numbered list. So AC **2.6** means requirement 2, criterion 6. Criteria are written in SHALL/WHEN/IF-THEN form.

Bug `bugfix.md`: `# Bugfix Requirements Document` → `## Introduction` → `## Bug Analysis` → `### Current Behavior (Defect)` (numbered `1.x`) → `### Expected Behavior (Correct)` (`2.x`) → `### Unchanged Behavior (Regression Prevention)` (`3.x`).

`design.md` carries the component breakdown and a numbered `### Property <N>: <title>` section listing the invariants that get property-based tests.

`tasks.md` is a checklist with nested numbering (`1.`, `1.1`) and `[x]`/`[ ]` state, each leaf ending in a traceability footer:

```
    - _Requirements: 2.1, 2.2, 2.3, 2.6_
```

## Traceability conventions — keep these intact

The chain is `requirements.md` AC number → `tasks.md` `_Requirements:_` footer → test docstring.

- Unit/integration tests cite `AC <req>.<n>` in the docstring: `"""AC 2.6: Parse depends string and generate DEPENDS_ON relationships."""`. Also used in source docstrings (e.g. `assembler.py` methods carry `(AC 2.6)`, `(AC 2.8)`).
- Property test modules tag the design property they cover: `# Feature: <spec-name>, Property <n>: <title>`. Present in 35 of 85 modules under `tests/properties/` — a newer convention, not universal, so absence is not an error.

When you add a test, cite its AC. When you edit one, preserve the existing citation.

## Working with specs

- `.kiro/specs/` is the requirements record. **Do not edit a spec to match the code**; if code and spec disagree, report it.
- A spec's `tasks.md` checkboxes are the progress ledger. Tick them as you land the work.
- Specs are not docs. User-facing documentation goes in `docs/` and must be added to `nav:` in `mkdocs.yml`.
- Prior agent-run findings live under `.agents/` (gitignored, so possibly absent): `.agents/sbom-run/REPORT.md` and `.agents/tasks/<task>/review.md`.
