# uqf Frontend Requirements

> **Provenance — read before relying on this file.** This document was
> reconstructed from screen photographs of the canonical
> `frontend-requirements.md` in the Bitbucket `uqf` repository, which is not
> reachable from this tree. The quoted constraints are faithful; some
> file-path citations may be imprecise at photograph resolution, and anything
> below the fold of those photographs is absent entirely. It is **not** the
> canonical document. The build sequence in the final section is *new* — it
> does not come from the canonical document at all.

## Purpose and users

Scopes a general-purpose React (or React-like) single-page application on top
of uqf's TorQ stack. Two audiences were equally plausible candidates; F-19 has
since answered **both** — see [Decisions](#decisions).

- **Ops-monitoring audience** — operators who need process health, ETL
  coverage, and backfill/Airflow status for the running demo or a
  production-shaped deployment.
- **Desk-facing analytics audience** — users who need to query and filter
  published domain tables (deals, order books, quotes, markouts, liquidity,
  ledger) through the gateway.

Every functional requirement is tagged `[Ops]`, `[Desk]` or `[Both]`. No
personas, SLAs or compliance requirements beyond what is stated here are
assumed.

## Ops-monitoring

- **F-01** `[Ops]` — **Process fleet health**: up or down, pid, port and group
  membership for every process in `process.csv`. Today this exists *only* as
  the `torq-demo summary` CLI, which shells out to `torq.sh` and inspects
  local OS processes. Exposing it to a browser is **new backend work**.

- **F-02** `[Ops]` — **Gateway query queue**: pending and running queries with
  id, time, client, query text, error type, status and submit time, from
  `.gw.getqueue[]` on the gateway itself.

- **F-03** `[Ops]` — **Backend connection status**: which RDB and HDB handles
  the gateway currently has registered and their up state, from `.gw.servers`
  and `.gw.clients`. Read on the gateway, so this still respects the
  gateway-only query boundary. *Grounded in
  `lib/torq/code/processes/gateway.q`.*

- **F-04** `[Ops]` — **Per-process query log**: recent query timing, status and
  errors from `.usage.usage`. Exists per-process; **no fleet-wide rollup
  exists**, so a single cross-process view has to fan out and merge.
  *Grounded in `lib/torq/code/handlers/logusage.q`.*

- **F-05** `[Ops]` — **ETL coverage**: completeness of published datasets by
  dataset, partition key and time range, from `etl_coverage` — already proven
  queryable through the gateway.

- **F-06** `[Ops]` — **Backfill and Airflow task status**: lifecycle state,
  window, cursor, row count and error for a bounded worker invocation.
  Written by q as a status file and read directly off disk by the Airflow
  operators. **It is not queryable through the gateway or any existing API.**

## Desk-facing analytics

- **F-07** `[Desk]` — **Browse and filter published domain tables**: deals,
  order books, quotes, markouts, book liquidity and the general ledger, by
  symbol, time range and domain-specific dimensions. Schemas are defined in
  the orchestrator and generated into `database.q`; the producing pipeline is
  documented per table.

- **F-08** `[Desk]` — **Historical versus current-session split**: HDB
  completed partitions and RDB today's session, both reachable through the
  gateway, matching the existing `torq-demo query` endpoint.

- **F-09** `[Desk]` — **Coverage-aware querying**: before querying a bounded
  dataset, confirm the required inputs are covered — matching
  `plan-markout-coverage` against `etl_coverage`. Per **E-09** this check must
  filter on `source_version`.

## Non-functional

- **F-10** — The gateway path has **no push or subscribe mechanism to a
  browser client**. Every view is poll-only. This is the single most
  shape-determining constraint in the document.

- **F-11** — Respect the gateway's own per-query timeout, and tolerate
  variable latency: HDB historical queries are expected to be slower than RDB
  current-session ones.

- **F-12** — Surface the gateway's EOD reload window as a known transient
  state, not a hard failure.

- **F-13** — Capture any usage or error history the frontend needs to retain
  beyond `flushtime` (default one day) before it is pruned. `.usage.usage`
  rows are flushed to disk and dropped from memory, so a long-retention view
  is a capture pipeline, not a query.

- **F-14** — Any server-side API layer must hold real q credentials **only on
  the server, never in the browser**, and must never construct a query from
  unvalidated client input. Raw string concatenation of untrusted input into
  a gateway exec call is injection-equivalent.

- **F-15** — There is no per-browser-user auth scheme in this repo. TorQ's
  access list is the only demonstrated access control, and the demo ships a
  single shared credential.

## Architecture recommendation

- **F-16** — Build a thin server-side Python backend-for-frontend in front of
  the gateway, reusing the kola-based IPC pattern already proven twice in this
  repo (`uqf_client`, and the orchestrator's own `query()`), rather than
  Grafana or the vendored access-layer bridge. Not a new IPC mechanism.

- **F-17** — Three vendored options exist in `lib/torq` but are wired into
  nothing: the Grafana JSON datasource adapter, the generic `dataaccess.q`
  access layer, and its REST bridge. A repository-wide search confirms none is
  loaded by any process in the generated `process.csv`, and none is referenced
  under `src/`, `python/` or `docs/`. Enabling any is **backend work
  equivalent to writing a small bespoke API layer**, and none of them closes
  the F-01 or F-06 gaps.

- **F-18** — Grafana stays plausible only if the ops audience is chosen alone
  and fixed, non-interactive panels suffice. Its fixed-panel model fits the
  desk audience's user-driven filtering and drill-down poorly.

## Access paths

Every requirement above maps to an existing access pattern, or is flagged as
requiring new backend work.

| Requirement | Access path | Status |
|---|---|---|
| Domain table query / filter | Gateway sync or async exec, reusing the orchestrator's `query()` and the `uqf_client` pattern | Exists today |
| ETL coverage | Same gateway path, against `etl_coverage` | Exists today |
| Gateway queue / backend connections | Direct connection to the gateway, reading its own `.gw.getqueue[]`, `.gw.servers`, `.gw.clients` | Exists today |
| Per-process query and error log | Direct connection to each process's own `.usage.usage` | Exists per-process; no fleet-wide rollup |
| Process fleet up/down | `torq-demo summary`, which shells to `torq.sh` and inspects local OS processes | CLI-only; **new backend work** |
| Airflow / backfill task status | Status file on disk, or Airflow's own REST API for task-instance state | File-based only; **new work either way** |

## Decisions

All five of this document's open questions were answered on 2026-09-15, each on
its own issue, and each issue is closed. Each bullet keeps the question before
the arrow, because the reason it was worth asking is the reason the answer costs
what it does.
Machine-derived index: [`docs/decisions.md`](decisions.md).

- **F-19 — which audience is in scope?** → **both**, ops-monitoring *and*
  desk-facing analytics ([#53]). Because B0's query layer was built
  audience-agnostic, roughly half of what exists already serves each: the
  `/query` `/catalog` `/coverage` surface for desk, `/ops/*` plus the
  usage-capture pipeline for ops. The cost of "both" is carrying both.

- **F-20 — auth model for the API layer?** → **one service credential, with
  authorisation enforced in the API layer** ([#54]), and built:
  `python/uqf_frontend/src/uqf_frontend/authz.py` is the single seam every
  request passes through. Two things it states rather than papers over — the
  identity is *claimed* (an `x-uqf-user` header anyone can set), not
  authenticated; and `allow_all` is the correct policy on a single-host demo
  with one credential, not a placeholder. What carries the real security weight
  is F-14: credentials stay server-side and no client input reaches query text.

- **F-21 — how does Airflow/backfill status reach the frontend?** → **read q's
  own status files** ([#55]), merged in #68. `.qpipe.write_status` writes,
  `uqf_frontend/status.py` reads, and `test_status.py` parses the q source to
  keep the two field sets in step. The format is defined here rather than
  inherited — there is no Airflow provider in this tree to be compatible with
  (F-04) — and per E-15 the files carry only q's own facts.

- **F-22 — target deployment?** → **local demo, single host** ([#56]). The API
  layer runs beside the `torq-demo` stack on one machine, which is what makes
  F-21's plain local path work with no shared volume. B3's fleet health was
  unconstrained by this (liveness is an IPC probe either way), and the existing
  10k row cap and 30s gateway timeout were sized for it.

- **F-23 — hosting model for the API layer and the React app?** → **answered
  together with F-22: one host** ([#57]). This gives F-13's usage-capture
  pipeline a home — it ships as `UsageCapture.capture_once()`, and a timer in
  the API process or a cron entry both work. That matters more than it sounds:
  `.usage.flushtime` defaults to **three hours** (measured, not the one day the
  requirements state), so unscheduled capture means history older than three
  hours is simply gone. The React app is still unwritten and deliberately not
  under `python/`.

The B-phase gates below still name the question they were gated on (B3 on F-22,
B4 on F-21, B5 on F-20); those gates are now open rather than blocked.

[#53]: ../../issues/53
[#54]: ../../issues/54
[#55]: ../../issues/55
[#56]: ../../issues/56
[#57]: ../../issues/57

---

## Build sequence (not from the canonical document)

Everything below is a proposal, ordered so that work reachable through a
proven access path ships before anything needing new q-side work or an
undecided answer. Each phase ends at a gate that can actually be run.

### B0 — Backend-for-frontend skeleton · buildable now

- **Delivers** F-16, F-14.
- **Work**: a thin Python API layer in front of the gateway, reusing the
  existing kola IPC pattern. Parameterised query construction with validation
  at the boundary from day one. Credentials server-side only.
- **Gate**: a validated, parameterised query reaches the gateway and returns;
  a hostile input string is rejected before construction.
- **Why here**: every frontend requirement except F-01 and F-06 routes through
  this layer, and F-14's injection constraint is far cheaper to build in than
  to retrofit.

### B1 — Desk analytics over proven paths · buildable now

- **Delivers** F-07, F-08, F-09, F-11, F-12.
- **Work**: domain-table browse and filter endpoints; the HDB/RDB split
  surfaced explicitly rather than hidden behind one spinner; coverage-aware
  pre-checks filtered by `source_version`; EOD window rendered as transient.
- **Gate**: a markout query for a date range refuses to run when coverage for
  that range and source version is absent, and says so.
- **Why here**: entirely existing access paths, so it ships without a single
  q-side change.

### B2 — Ops views over proven paths · buildable now

- **Delivers** F-02, F-03, F-04, F-05, F-10, F-13.
- **Work**: gateway queue and backend connection state; per-process usage
  fanned out and merged into the rollup that does not exist server-side;
  coverage view; poll cadences chosen per view — seconds for queue and
  connection state, longer for coverage and analytics.
- **Gate**: usage rows are captured into durable storage before `flushtime`
  prunes them, verified by advancing a fake clock past the window.
- **Why here**: F-13 is the trap — a retention view looks like a query and is
  actually a capture pipeline. Built later, the history is already gone.

### B3 — Process fleet health · gated on F-22

- **Delivers** F-01.
- **Work**: an HTTP surface over what `torq-demo summary` does today, without
  shelling out per request.
- **Gate**: killing one process is reflected in the fleet view within one poll
  interval.
- **Why gated**: local-only makes OS process inspection sufficient; a
  production-shaped deployment puts processes elsewhere and needs a different
  mechanism entirely.

### B4 — Airflow and backfill status · gated on F-21

- **Delivers** F-06.
- **Work**: either read the q-written status files from a shared volume, or
  query Airflow's REST API for task-instance state.
- **Gate**: a partially failed bounded run shows its window, cursor and error
  in the UI.
- **Why gated**: **E-15** constrains it — exchange structured status, never
  parse the other layer's log text. The status filename carries a process
  instance id, so a shared-volume reader must know instance identity.

### B5 — Auth · gated on F-20

- **Delivers** F-15, F-20.
- **Work**: either per-user q credentials mapped to access-list entries, or
  one service credential with authorisation enforced in the API layer.
- **Gate**: two users with different entitlements get different result sets
  for one query.
- **Why last**: F-14 already keeps credentials off the browser, so B0–B4 are
  safe to build under the single shared credential without foreclosing either
  answer.
