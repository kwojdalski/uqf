# Drift ledger

One row per known divergence between this tree and the canonical Bitbucket
`uqf`. The point is that **narrowing is measurable**: a divergence moves to
`closed` only when it is verifiably gone from this side.

Canonical is not reachable from here, so "closed" means *this tree now
matches what the requirements and drift reports say canonical does* — not
that a merge happened. Sources are `docs/drift-reports/`,
`docs/migrations/`, `docs/etl-framework-requirements.md` and
`docs/frontend-requirements.md`.

## Status vocabulary

| Status | Meaning |
|---|---|
| `closed` | verifiably matched on this side, with the check that proves it |
| `open` | actionable here now, nothing blocking |
| `blocked` | needs an answer or access this tree does not have |
| `wontfix` | deliberately divergent; reason recorded |

## Ledger

| # | Divergence | Status | Closed by / blocked on |
|---|---|---|---|
| D1 | `python/uqf-client/` hyphenated; canonical uses `python/uqf_client/`. The drift report names this a **blocking packaging conflict** | `closed` | Directory renamed, 7 live references updated, venv rebuilt. Verified: `check_hook_scopes.py` 43/43, all four suites green |
| D2 | Lint gate scoped to one package; 43 of 47 tracked `.py` files ungated | `closed` | PR #52 — scoped by intent (`\.py$` less `^lib/`), plus `scripts/check_hook_scopes.py` as a standing gate |
| D3 | Agent definitions in `.claude/agents/`; canonical has `.github/agents/*.agent.md` | `wontfix` | **False equivalence.** `.agent.md` is GitHub Copilot's custom-agent format; `.claude/agents/*.md` is Claude Code's, with `tools:`/`model:` frontmatter. Different tools, not one artifact in two places — which is why the report dispositions them "side-local workflow rules". Converging them would break both. See D14 for the real gap |
| D4 | `src/*.q` flat; canonical splits into `foundation/ pricing/ portfolio/ execution/ market_data/ integrations/ examples/` | `closed` | Split adopted with **namespaces unchanged** (B-03 answered), so no call site or test assertion moved — only `init.q`'s 13 load lines and 124 path references. `init.q` and README now record that the directories are organisational, since the graph has a real `pricing/` ↔ `execution/` cycle |
| D5 | `tests/test_*.q` flat; canonical uses `tests/q/test_*.q` | `closed` | 14 files moved, runner retargeted, 19 doc references fixed. `nsList` untouched (keys on namespaces, not paths). Canonical's `run_unit.q` + `scripts/test.sh` dispatcher deliberately **not** built: one q lane makes a one-option dispatcher premature |
| D6 | Generated qDoc HTML committed under `docs/`; canonical regenerates into `build/docs/` | `closed` | Output retargeted to `build/docs/`, 15 generated files removed from git, `build/` ignored. **Also defused a `rm -rf docs` in `gen-docs.sh`** that would have deleted all 13 hand-written documents. GitHub Pages is not configured (404), so nothing was serving from `docs/` |
| D7 | Per-package `pyproject.toml`/`uv.lock`; canonical consolidates to one root Python project | `closed` | uv **workspace** at the repo root: one lockfile, one `.venv` (1.1 GB → 383 MB), shared ruff/pytest config, ruff pinned to pre-commit's own version. Members keep their names and the `torq-demo` script. Safe by inspection — zero cross-package imports, every shared constraint an open lower bound |
| D8 | `docs/torq-demo.md`; canonical supersedes with `docs/guides/torq-demo.md` | `blocked` | Deliberately deferred. Moving it alone creates a single-file `docs/guides/` and costs ~24 reference edits, for no benefit until the taxonomy is settled — J-05: what belongs in `guides/` vs `architecture/` vs `reference/` vs `decisions/` vs `integrations/` |
| D9 | No `src/etl/` at all; canonical has `core/` (14 files) and `workers/` (7) | `blocked` | Reimplementation, not a port. Gated on F-04 and the ETL open questions (#62) |
| D10 | No `python/uqf_airflow_provider/`; canonical has the full package | `blocked` | #55 (F-21) must pick the status mechanism first |
| D11 | `etl_coverage` schema assumed, not verified — `COVERAGE` in `uqf_frontend` rests on it | `blocked` | #60. Canonical-only table; the requirements mention a **partition key** absent from the assumed shape |
| D12 | `python/uqf_frontend/` exists only here: 253 callables of comparison-only drift | `wontfix` | Deliberate. It is the reimplementation the frontend requirements describe; it narrows capability drift while widening file drift |
| D13 | `scripts/torq_pipeline.q` + the three demo pipelines exist only here | `wontfix` | Same reasoning as D12. Canonical has its own `src/etl/workers/`; reconciliation is F-04's job |
| D14 | `AGENTS.md` present in canonical, absent here | `blocked` | The genuine gap D3 was masking. Needs to know what it contains and how it relates to `CLAUDE.md`, which this tree has instead — O-03 in the question bank |

## Counters

```
closed   6     D1, D2, D4, D5, D6, D7
open     0
blocked  5     D8 D9 D10 D11 D14
wontfix  3     D3, D12, D13
```

Eight design decisions were taken on 2026-09-15, which moved D4 and D5 from
`blocked` to `open` and closed D7. See
`~/.claude/plans/sprightly-brewing-catmull.md` for the full record; the two
that bear on this ledger:

- **`src/` adopts domain directories, namespaces stay unchanged.** The split
  is organisational only: the dependency graph has a genuine cycle
  (`pricing/forwards.q` ↔ `execution/execution.q`) and `market_data/dqchecks.q`
  reaches into three candidate groups, so no directory layering is implied.
- **Python consolidates to one root project**, closing D7 above.

## The ceiling has been reached

**`open` is zero again, and this time nothing structural is left.** Six rows
are closed. The five that remain each need something this tree cannot
produce:

| Waiting on | Rows |
|---|---|
| A mechanism or audience decision — #55, #60, F-04 | D9, D10, D11 |
| A docs taxonomy decision — J-05 | D8 |
| A packaging decision — one root Python project | D7 |
| Knowing what a canonical file contains — O-03 | D14 |

Three rows were closed by doing the work; three are deliberately divergent
with reasons recorded. Of the three that looked actionable when this ledger
was written, only one survived inspection:

- **D6 was worth more than expected** — it defused a destructive `rm -rf`.
- **D3 was a false equivalence** — two different tools' agent formats.
- **D8 was premature** — a file move masquerading as a structural change.

That is the useful output of a ledger: it stops "narrow the drift" from
meaning "make the number go down".
