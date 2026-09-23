# ETL Framework Requirements

> **Provenance.** Snapshot as of 2026-09, confirmed by the maintainer. It was
> originally reconstructed from screen photographs of a document in a
> repository that is no longer reachable and, canonical having been frozen,
> (issue #69), is now **frozen and non-authoritative**: this tree is the
> primary lineage, and this document describes what *this* tree implements.
> Where the code and this document disagree, the code is the design and this
> document needs updating — not the other way round.
>
> **Ids.** Requirements here are `ETL-nn`, **formerly `E-nn`**. The `E-nn`
> prefix now refers only to the design-question bank (issue #73), so an old
> citation like "E-05" in an issue comment means the old numbering, and the
> requirement it once named is `ETL-05`. Renamed to end the collision
> recorded in #109.

## Scope

Applies to uqf's `src/etl/` workers and the Python orchestration that starts
and coordinates them.

### The job shapes, on two axes

Two different questions get asked about a pipeline job, and they are not the
same question. Reading them as one taxonomy is why the fleet looks like it
has more kinds than the requirements describe.

**Axis 1 - lifecycle: does it end?** This is the axis the requirements below
are written against, and the distinction to preserve:

- A **bounded historical worker** takes an explicit `[from,to)` request, a
  resumable run specification, and a terminal completion path. This is an
  **enforced contract**: `.qbw` supplies the shell, `.qbfstate.bounded_workers`
  the registry, and `require_contract` the check. `demo_deals_backfill` and
  `demo_events_backfill` are the two instances.
- A **continuous worker** runs a persistent poll-and-cursor loop. This is an
  **established implementation pattern, not a registered shared contract** -
  `.qcont` supplies cursor and freshness primitives and deliberately no
  registry, for the reason `continuous_state.q`'s header gives. **There is no
  instance of one in this tree.** ETL-03 grounds the pattern in
  `src/etl/workers/marketwarehouse_deals.q`, which is bank-internal and
  excluded so `.qcont` is here for the shape rather than for a
  caller. `.qdag.adopt_feeders` reads `.qcont.feeds` and finds it empty.

**Axis 2 - role: what does it do in the dataflow?** This is what
`Pipeline.kind` in `uqf_stack/model/pipelines.py` records, and what decides
a process's proctype, port offset and whether it needs credentials:

| `kind` | Does | Driven by | Shared shell |
|---|---|---|---|
| `feed` | publishes onto the tickerplant, subscribes to nothing | `.timer.repeat` | none - each feed wires itself |
| `etl` | subscribes to tickerplant tables, transforms, republishes | tickerplant subscription | `.qpipe.subscribe_etl` exists; one of five uses it |
| `backfill` | a bounded job: registers, runs a window range, exits | an operator or Airflow, per ETL-15 | `.qbw`, fully |

The two axes are not parallel. Every `backfill` is bounded; `feed` and `etl`
are both long-running but neither is a **continuous worker** in `.qcont`'s
sense - they are timer-driven and subscription-driven respectively, not
poll-and-cursor over an external source.

**Cross-cutting: the source contract.** `.qsrc` is the vendor-facing
generic - one registration per external source carrying its table, target
mapping, required fields and types, row key, time zone and credential
variable (ETL-12). It is orthogonal to both axes: a bounded worker reads
through it today, and a continuous one would too.

### What that means for the generics

They are uneven, and the unevenness is worth knowing before adding a job:

- **Backfill and source** have full, enforced generics. A new one of either
  is a declaration.
- **Continuous** has primitives but no contract, deliberately, and no
  in-tree user.
- **Feed and ETL** have no shell at all. A new feed is a new script that
  wires its own timer and publish path, and a new ETL either uses
  `.qpipe.subscribe_etl` or hand-rolls the same sequence - four of the five
  hand-roll it today.

Whether `.qpipe` should become the ETL shell that `.qbw` is for backfills is
an open design question, recorded in
[pipeline-framework-gaps.md](../architecture/pipeline-framework-gaps.md), not
settled here.

## Bounded lifecycle contract

- **ETL-01** — Register every bounded worker in `.qbfstate.bounded_workers`, and
  implement every method and configuration global named in
  `.qbfstate.bounded_worker_methods` and `.qbfstate.bounded_worker_globals`.
  Call `require_contract` during initialisation, and call the bounded runtime
  only after the publishing handle is resolved. Enforcement is deterministic
  and tested. *Grounded in `src/etl/core/backfill_state.q`,
  `tests/q/test_backfill_state.q`.*

- **ETL-02** — Give every bounded worker an explicit source-tier range, a single
  traversal direction, a run specification, a private state file, and a
  terminal completion path. **A bounded worker must not silently become an
  unbounded tailer.**

- **ETL-03** — Implement continuous workers as long-running poll loops: load a
  local cursor at startup, publish a transformed page, then advance and
  persist the cursor. Do not publish a resume-completion claim merely because
  a continuous cursor advanced. *Grounded in
  `src/etl/workers/marketwarehouse_deals.q`.*

- **ETL-04** — Keep transformation, merge decisions, cursor calculations,
  coverage decisions and query construction deterministic and unit-testable.
  Keep connections, timers, publishing, checkpoint persistence, retry
  reporting and lifecycle orchestration in the worker shell.

- **ETL-05** — Publish a nonempty page before recording the cursor that
  acknowledges it. Treat a failed fetch as having no publication effect and no
  checkpoint effect.

## Data and state

- **ETL-06** — Keep each worker's checkpoint private to that worker. Store the
  full run specification alongside its cursor, and discard saved state when
  the current specification differs. **Never use another worker's checkpoint
  as evidence that a dataset is complete.**

- **ETL-07** — Publish durable cross-process completeness only through the
  append-only `etl_coverage` table, and only after the underlying work is
  complete. Stage a completion event for every completed bounded window,
  *including an empty window*.

- **ETL-08** — Represent every bounded input and coverage interval as half-open
  `[range_from,range_to)`: include the start, exclude the end, and reject
  empty and reversed intervals. Compose adjacent windows only at their common
  boundary.

- **ETL-09** — Require `source_version` when recording a bounded run. Treat it
  as one immutable source-release label, carry it in the run specification and
  the output provenance, and require coverage consumers to filter on it.

- **ETL-10** — Derive continuous coverage from one matching version only. Never
  merge intervals from different versions to satisfy a dependency.

- **ETL-11** — Route local historical reads, including `etl_coverage` admission
  checks, through a gateway addressing both `rdb` and `hdb` targets. **Do not
  bind historical correctness to an rdb-only handle: completion data moves
  after EOD.**

  > **NOT IMPLEMENTED, and deliberately so for now.** The ledger is durable
  > and cross-process as of the persistence layer in `src/etl/core/materialisation.q`
  > — it round-trips to a file beside the checkpoints and every `attach`
  > reloads it — but it is *not* tiered through the tickerplant into `rdb`
  > and `hdb`, and no read here goes via a gateway.
  >
  > The obstacle is specific rather than effort: a tickerplant stream is
  > append-only, and `supersede` **updates** rows to stamp
  > `superseded_at`. Tiering therefore requires supersession to be re-modelled
  > as an *event* that readers compose, which is a redesign of the bitemporal
  > layer rather than a change of storage. Doing it as a side effect of a
  > durability fix would have been the wrong trade.
  >
  > What this costs today: coverage is single-host, and does not migrate at
  > EOD. What it does *not* cost: correctness within a host, which is what
  > ETL-13's skip-what-is-covered actually depends on, and which is now
  > verified across processes by the `q-backfill-process` lane.

- **ETL-12** — Register every external source table, target mapping, required
  field and required type in the centralised source contract. Validate both
  generated fixtures and live external metadata against that same contract.
  *Grounded in `src/etl/core/source_contract.q`,
  `tests/q/test_source_contract.q`.*

## Operational

- **ETL-13** — Make bounded retries idempotent through the range-and-
  `source_version` contract: resume from a compatible private checkpoint when
  available, and query version-specific `etl_coverage` before re-fetching a
  completed table. **Do not assume exactly-once processing** — the framework
  establishes retry-safe publication and coverage skipping, which is a weaker
  and more honest guarantee.

- **ETL-14** — Implement dry-run as diagnostic-only execution: fetch and
  transform as usual, then exit without publishing rows, without publishing
  coverage, and without writing a checkpoint.

- **ETL-15** — Split authority explicitly. q and TorQ own process startup,
  source reads, query failures, checkpoints, run and window counts, and
  coverage events. Airflow owns task ordering, scheduling, retries, timeouts,
  concurrency and alert routing. Exchange **structured worker status** — never
  infer either layer's facts from the other's log text.

- **ETL-16** — Resolve declared TorQ dependencies through `.servers`, fail during
  initialisation when a required connection or coverage precondition is
  unavailable, and register recurring polling through the shared runtime
  helper where the worker participates in the bounded contract.

- **ETL-17** — Regenerate the TorQ configuration on every `bootstrap()` from
  vendored inputs plus uqf-owned additions and overrides. Never hand-edit
  vendored configuration, and never depend on a previously generated file
  still being correct.

## Validation

- **ETL-18** — Add deterministic qUnit coverage for every changed bounded
  lifecycle decision: contract completeness, cursor advancement, run-spec
  invalidation, window boundaries, coverage staging, and version-specific
  coverage admission.

- **ETL-19** — Use `etl_test_doubles` to replace fetch, publish, checkpoint and
  logging adapters when testing stateful control flow. Keep testing
  deterministic business logic directly. **Do not let adapter doubles
  substitute for transform and coverage tests.**

- **ETL-20** — Run the live external metadata smoke test separately, when an
  external schema or adapter changes. The deterministic suite proves local
  behaviour, **not** that a configured external service is reachable or
  compatible. *Grounded in `tests/integration/external_source_meta_smoke.q`.*

- **ETL-21** — Use the suite matching the changed layer: `scripts/test.py q-unit`
  for q behaviour, `scripts/test.py q-backfill-process` for bounded process
  behaviour, and the focused Python suites for orchestration changes.

## Open questions (undecided in the canonical document)

- **ETL-22** — Decide whether continuous workers need a framework-level public
  freshness or coverage contract. Today `etl_coverage` serves bounded
  historical completion while continuous workers keep private cursor state,
  and no shared cross-worker statement of freshness exists — so anything
  consuming continuous output has no sanctioned way to ask how current it is.

- **ETL-23** — Decide whether a future bounded worker may use a different
  idempotency mechanism alongside versioned coverage. The framework
  standardises compatible checkpoints, publish-before-checkpoint ordering, and
  a coverage precheck; it does not define a universal row-level
  idempotency-key interface.

- **ETL-24** — Decide whether the continuous-worker lifecycle should become an
  explicit structural contract. The bounded registry is verified by tests
  while continuous workers remain independently implemented.

## Appendix: reconciliation against an earlier reconstruction

An earlier attempt to derive these requirements from a drift inventory alone
made six checkable assumptions. Recording the outcome here, because the two
failures are instructive about what cannot be inferred from a file listing:

| Assumption | Canonical document | Outcome |
|---|---|---|
| Half-open interval, exclusive upper bound | `[range_from,range_to)`, empty and reversed rejected | Confirmed |
| Publish before checkpoint | Failed fetch has no publication or checkpoint effect | Confirmed |
| Checkpoint durable, storage unspecified | A **private state file**, never read by another worker | Refined |
| q owns status, orchestrator reads it | Authority **split** between q and Airflow; status travels as a file | Refined |
| Coverage **derived** from target data so it self-corrects | Coverage is **recorded** — staged completion events, including empty windows | **Wrong** |
| Re-run leaves target identical (exactly-once) | Explicitly **not** exactly-once: retry-safe publication plus coverage skipping | **Wrong** |
