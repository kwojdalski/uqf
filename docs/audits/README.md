# Audit log

One row per audit run. Three agents write here — `causality-auditor`,
`docstring-example-verifier` and `naming-cohesion-auditor` — and each of them
is instructed to **read this file before starting** so a run builds on what a
previous run cleared instead of re-deriving it.

That handoff is the reason this file exists. Until it did, all three agents
read a missing index, found nothing, and silently started from zero every
time; `extension-brainstormer` mines this directory for findings that need new
capability and likewise found nothing.

## Column contract

Append one row per run, newest at the bottom. Every column is mandatory —
an empty cell makes the row unusable to the next run.

| Column | What goes in it |
|---|---|
| `Date` | `YYYY-MM-DD`, the run date |
| `Agent` | `causality` / `docstring-eg` / `naming-cohesion` |
| `Scope` | what the run actually covered — a module, an area, or `all`. Scope, not intent: if the run only got through four modules, the scope is those four |
| `Report` | link to the run file, `[2026-09-16](2026-09-16-causality-execution.md)` |
| `Findings` | severity split, e.g. `1 CRIT, 3 MED` — or `0` |
| `Cleared` | what the run verified clean, so the next run can skip it |
| `Not checked` | what the run did **not** reach. Never leave this blank; the whole point is that an unchecked vector must not read as a cleared one |

The `Cleared` / `Not checked` split is load-bearing. All three agents already
refuse to conflate the two in their inline reports, and a row that drops the
distinction turns this index into a source of false confidence — which is
worse than having no index.

## Run files

`YYYY-MM-DD-<agent>-<scope>.md`, e.g. `2026-09-16-naming-cohesion-etl.md`.
A second run on the same day appends a `## Run <timestamp>` section to the
existing file rather than overwriting it.

A report here is a **copy** of what the agent reported inline, not a
replacement for it, and none of these agents may edit `src/**` or `tests/**` —
they propose, the user applies.

## Index

| Date | Agent | Scope | Report | Findings | Cleared | Not checked |
|---|---|---|---|---|---|---|
| 2026-09-16 | causality | Execution metrics and rolling OFI | [Report](2026-09-16-causality-execution.md) | 0 | Signed buy/sell metrics; execution as-of sorting/boundaries and nulls; expanding VWAP and rolling OFI prefixes | Forwards/chain as-of paths; other rolling functions; downstream null consumers; integrations excluded |
| 2026-09-16 | docstring-eg | All 13 execution examples; 8 executable assertions | [Report](2026-09-16-docstring-eg-execution.md) | 0 STALE/THROWS; 1 test candidate; 7 already covered | All 8 executable assertions match, dictionary compared per key | 5 execution lines have no executable assertion; 138 other in-scope examples unexecuted; 6 integrations examples excluded |
| 2026-09-16 | naming-cohesion | 11 execution definitions; selected layout and three prior rename seeds | [Report](2026-09-16-naming-cohesion-execution-seeds.md) | 0 | Execution families and test/loader pairing; namespace, convexity and timestamp-order seeds already fixed | Whole-tree layout/collisions; other module bodies and call graphs; integrations convention/layout |
