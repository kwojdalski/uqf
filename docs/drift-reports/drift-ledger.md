# Drift ledger — CLOSED 2026-09-16

> **This ledger is closed.** Decisions A-02 and A-03 (issue #69, 2026-09-16)
> made this tree the **primary lineage** and declared the canonical Bitbucket
> tree frozen. A drift ledger measures distance from something you are
> converging toward; there is now nothing to converge toward. The table below
> is kept as the record of what was reconciled and why — a history, not a
> to-do list — and no row will be added to it.
>
> What replaces it: capability gaps are tracked as ordinary GitHub issues, and
> design decisions live in `docs/decisions.md`, derived from the question
> bank by `scripts/build_decision_log.py`.



One row per known divergence between this tree and the canonical Bitbucket
`uqf`. The point is that **narrowing is measurable**: a divergence moves to
`closed` only when it is verifiably gone from this side.

Canonical is not reachable from here, so "closed" means *this tree now
matches what the requirements and drift reports say canonical does* — not
that a merge happened. Sources are `docs/drift-reports/`,
`docs/migrations/`, `docs/etl-framework-requirements.md` and
`docs/frontend-requirements.md`.

Design decisions do **not** belong in this table. They live in
`docs/decisions.md`, derived from the GitHub question bank by
`scripts/build_decision_log.py`. Keeping them apart avoids a real collision:
this ledger numbers its rows `D1`, `D2`, ... while the bank dash-numbers its
backfill questions, so `D8` here is not `D-08` there.

One row per known divergence between this tree and the canonical Bitbucket
`uqf`. The point is that **narrowing is measurable**: a divergence moves to
`closed` only when it is verifiably gone from this side.

Canonical is not reachable from here, so "closed" means *this tree now
matches what the requirements and drift reports say canonical does* — not
that a merge happened. Sources are `docs/drift-reports/`,
`docs/migrations/`, `docs/etl-framework-requirements.md` and
`docs/frontend-requirements.md`.

Design decisions do **not** belong in this table. They live in
`docs/decisions.md`, derived from the GitHub question bank by
`scripts/build_decision_log.py`. Keeping them apart avoids a real collision:
this ledger numbers its rows `D1`, `D2`, ... while the bank dash-numbers its
backfill questions, so `D8` here is not `D-08` there.

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
| D2 | Lint gate scoped to one package; 43 of 47 tracked `.py` files ungated | `closed` | `python3 scripts/check_hook_scopes.py` exits 0 reporting **49/49** tracked `.py` files covered by each of the lint, test and type gates (43/43 when closed; the count grew, and a type gate was added). Verified adversarially on 2026-09-15: narrowing any hook's scope makes it exit 1 and name the ungated files by directory |
| D3 | Agent definitions in `.claude/agents/`; canonical has `.github/agents/*.agent.md` | `wontfix` | **False equivalence.** `.agent.md` is GitHub Copilot's custom-agent format; `.claude/agents/*.md` is Claude Code's, with `tools:`/`model:` frontmatter. Different tools, not one artifact in two places — which is why the report dispositions them "side-local workflow rules". Converging them would break both. See D14 for the real gap |
| D4 | `src/*.q` flat; canonical splits into `foundation/ pricing/ portfolio/ execution/ market_data/ integrations/ examples/` | `closed` | Split adopted with **namespaces unchanged** (B-03 answered), so no call site or test assertion moved — only `init.q`'s 13 load lines and 124 path references. `init.q` and README now record that the directories are organisational, since the graph has a real `pricing/` ↔ `execution/` cycle |
| D5 | `tests/test_*.q` flat; canonical uses `tests/q/test_*.q` | `closed` | 14 files moved, runner retargeted, 19 doc references fixed. `nsList` untouched (keys on namespaces, not paths). The `scripts/test.sh` dispatcher was deliberately deferred here — a one-option dispatcher is worse than none — and **built in #88**, once E-21's three lanes made it earn its place |
| D6 | Generated qDoc HTML committed under `docs/`; canonical regenerates into `build/docs/` | `closed` | Output retargeted to `build/docs/`, 15 generated files removed from git, `build/` ignored. **Also defused a `rm -rf docs` in `gen-docs.sh`** that would have deleted all 13 hand-written documents. GitHub Pages is not configured (404), so nothing was serving from `docs/` |
| D7 | Per-package `pyproject.toml`/`uv.lock`; canonical consolidates to one root Python project | `closed` | uv **workspace** at the repo root: one lockfile, one `.venv` (1.1 GB → 383 MB), shared ruff/pytest config, ruff pinned to pre-commit's own version. Members keep their names and the `torq-demo` script. Safe by inspection — zero cross-package imports, every shared constraint an open lower bound |
| D8 | `docs/torq-demo.md`; canonical supersedes with `docs/guides/torq-demo.md` | `wontfix` | **Closed by A-03.** Canonical's layout is no longer a target; this tree's docs taxonomy is decided here on its own merits (J-05 remains a live question in #78, about *this* tree's docs, not about matching another's) |
| D9 | No `src/etl/` at all; canonical has `core/` (14 files) and `workers/` (7) | `open` | **Unblocked and in progress.** Reimplementation from the requirements, not a port. `src/etl/core/` now holds 4 of canonical's 14: `backfill_state.q` (#85, E-01..E-05), `coverage.q` (#86, E-06..E-11), `worker_config.q` and `worker_runtime.q` (#87, E-13..E-17). #88 adds `tests/lib/etl_test_doubles.q`, the `q-backfill-process` and `smoke` lanes and `scripts/test.sh` (E-18..E-21). `src/etl/core/source_contract.q` adds E-12, `src/etl/sources/demo_deals.q` a generic analogue source (A-04), and `src/etl/workers/demo_deals_backfill.q` the first real bounded worker. Seven of canonical's `core/` 14 — the last two being `continuous_state.q` (E-03's poll-and-cursor pattern) and `coercion.q` (E-05's shared text-to-type layer) — one source, one worker. The file COUNT will not converge — this tree is its own lineage per F-04, so only capability drift is meaningful. **E-05 is now answered** (#106), so a worker over a real source is no longer blocked on the coercion trap list; it is blocked only on having a real source to point at, which A-04 rules out for this public tree. |
| D10 | No `python/uqf_airflow_provider/`; canonical has the full package | `open` | **Unblocked: #55 is closed** (`gh issue view 55` → CLOSED), and the status-file mechanism it chose is built on both sides. `python/uqf_airflow_provider/` holds a status reader, an E-15 translator and a lazily-imported sensor. Proved by `uv run pytest -q` (322 passing, 14 this package's) and by `import uqf_airflow_provider.sensor` succeeding with **Airflow not installed** — deliberately not a dependency (F-22/F-23), so the demo needs no Airflow. What remains is DAG-level work that only runs inside a real Airflow environment |
| D11 | **The one row that survives the closure, re-scoped:** `etl_coverage` schema assumed, not verified. Now **16 files** rest on the assumed shape (8 source, 8 test), up from `queries.py`/`catalog.py` alone — and `.qcov.require_schema`, the guard meant to refuse a wrong-shaped ledger, is **defined and tested but called from no live path** | `blocked` | #60, still open with no `meta` output posted back. `scripts/verify_coverage_schema.q` and `.qcov.require_schema` are built and tested, but only a machine that can reach the real ledger can discharge it: `QHOME=~/.kx ~/.kx/bin/q scripts/verify_coverage_schema.q -target host:port`. The requirements mention a **partition key** absent from the assumed shape, so the untested direction reports a gap-ridden range as complete |
| D12 | `python/uqf_frontend/` exists only here: 253 callables of comparison-only drift | `wontfix` | Deliberate. It is the reimplementation the frontend requirements describe; it narrows capability drift while widening file drift |
| D13 | `scripts/torq_pipeline.q` + the three demo pipelines exist only here | `wontfix` | Same reasoning as D12. Canonical has its own `src/etl/workers/`; reconciliation is F-04's job |
| D14 | `AGENTS.md` present in canonical, absent here | `wontfix` | **Closed by A-03.** With canonical frozen there is no `AGENTS.md` to learn the contents of. This tree has `CLAUDE.md` and `.claude/`; whether it also wants an `AGENTS.md` for other tools is a question about this tree (#90), not a divergence |

## Counters

```
closed   6     D1, D2, D4, D5, D6, D7
open     2     D9, D10       (capability work continuing as ordinary issues)
blocked  1     D11           (#60 — a fact about this tree's own ledger)
wontfix  5     D3, D8, D12, D13, D14
```

Eight design decisions were taken on 2026-09-15, which unblocked D4 and D5
(both since closed) and closed D7. See
`~/.claude/plans/sprightly-brewing-catmull.md` for the full record; the two
that bear on this ledger:

- **`src/` adopts domain directories, namespaces stay unchanged.** The split
  is organisational only: the dependency graph has a genuine cycle
  (`pricing/forwards.q` ↔ `execution/execution.q`) and `market_data/dqchecks.q`
  reaches into three candidate groups, so no directory layering is implied.
- **Python consolidates to one root project**, closing D7 above.

## How it ended

The ledger was opened when this tree was understood as a reimplementation of
an unreachable canonical repository, and every row measured a distance from
that target. On 2026-09-16 the maintainer settled the two questions the whole
exercise had been resting on (issue #69):

- **A-03 — this tree is the primary lineage.** Decisions made here are
  authoritative; canonical is provenance, never authority.
- **A-02 — canonical is frozen.** Nothing new will be re-described from it.

With no target, "drift" stops being a meaningful quantity, and the honest
thing to do with a ledger that measures it is to close it rather than let it
decay into a list of things that are simply *different*. Three rows were
re-dispositioned on that basis: D8 and D14 were "blocked" on learning what
canonical contained, which no longer matters; D11 survives because it is not
about canonical at all — it is a fact about this tree's own `etl_coverage`
table (#60) that only the maintainer's machine can supply.

What the ledger got right while it was open, and is worth keeping:

- **A row is only closed by a check that can be re-run.** D2 was re-verified
  adversarially long after closing, and its evidence was found stale (43/43
  had become 49/49, then 55/55, then 57/57). The mechanism held; the number
  in the cell had not. That is the difference between a claim and a check.
- **Some drift was a false equivalence** (D3: two tools' agent formats), some
  was a destructive bug in disguise (D6: a `rm -rf docs`), and some was
  premature (D8: a file move masquerading as structure). The ledger's value
  was in making those distinctions, not in making the count go down.
- **The narrative must not outlive the table.** This section was wrong twice
  before it was right, both times because prose was edited without the
  table. Counters are recomputed from the table by hand, every time.

Capability gaps are now ordinary GitHub issues. Design decisions live in
`docs/decisions.md`, derived from the question bank by
`scripts/build_decision_log.py` and reconciled into the issue bodies by
`scripts/reconcile_question_bodies.py` — which together answer the question
this ledger could not: not "how far are we from canonical" but "what have we
decided, and where is it recorded".
