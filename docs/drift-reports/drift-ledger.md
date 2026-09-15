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
| D3 | Agent definitions in `.claude/agents/`; canonical uses `.github/agents/*.agent.md`. Drift report disposition: *"Merge manually … side-local workflow rules"* | `open` | Needs a convention decision; one file could serve both |
| D4 | `src/*.q` flat; canonical splits into `foundation/ pricing/ portfolio/ execution/ market_data/ integrations/ examples/` | `blocked` | B-02 (what replaces `init.q`'s load order) and B-03 (do namespaces change with directories) are unanswered. Renaming without those answers would need redoing |
| D5 | `tests/test_*.q` flat with `tests/run_tests.q`; canonical uses `tests/q/test_*.q`, `tests/q/run_unit.q`, `scripts/test.sh` dispatcher | `blocked` | Depends on D4's namespace answer, and on #61 (commit-time budget) for which lanes gate a commit |
| D6 | Generated qDoc HTML committed under `docs/`; canonical regenerates into `build/docs/` | `open` | Mechanical, but needs a decision on what then serves GitHub Pages |
| D7 | Per-package `pyproject.toml`/`uv.lock`; canonical consolidates to one root Python project | `blocked` | Forces `uqf_client`, `torq_orchestrator` and `uqf_frontend` onto one dependency resolution — a real decision, not a move |
| D8 | `docs/torq-demo.md`; canonical supersedes with `docs/guides/torq-demo.md` | `open` | Part of the wider `docs/` taxonomy (guides/architecture/reference/decisions/integrations) |
| D9 | No `src/etl/` at all; canonical has `core/` (14 files) and `workers/` (7) | `blocked` | Reimplementation, not a port. Gated on F-04 and the ETL open questions (#62) |
| D10 | No `python/uqf_airflow_provider/`; canonical has the full package | `blocked` | #55 (F-21) must pick the status mechanism first |
| D11 | `etl_coverage` schema assumed, not verified — `COVERAGE` in `uqf_frontend` rests on it | `blocked` | #60. Canonical-only table; the requirements mention a **partition key** absent from the assumed shape |
| D12 | `python/uqf_frontend/` exists only here: 253 callables of comparison-only drift | `wontfix` | Deliberate. It is the reimplementation the frontend requirements describe; it narrows capability drift while widening file drift |
| D13 | `scripts/torq_pipeline.q` + the three demo pipelines exist only here | `wontfix` | Same reasoning as D12. Canonical has its own `src/etl/workers/`; reconciliation is F-04's job |

## Counters

```
closed   2
open     3
blocked  6
wontfix  2
```

## What closing the next one needs

`D3` and `D6` and `D8` are the only rows actionable without an answer from
outside this tree. Everything else waits on one of: the `src/` namespace
decision (D4, D5), an audience or mechanism decision (D9, D10), or access to
canonical (D11).

So the honest reading is that drift narrowing is **near its ceiling** for
what can be done unilaterally. The remaining six blocked rows are not
effort-limited.
