# ETL Framework Requirements

> **Provenance — read before relying on this file.** This document was
> reconstructed from screen photographs of the canonical
> `etl-framework-requirements.md` in the Bitbucket `uqf` repository, which is
> not reachable from this tree. The quoted constraints are faithful; some
> file-path citations may be imprecise at photograph resolution, and anything
> below the fold of those photographs is absent entirely. It is **not** the
> canonical document and must not be treated as authoritative where the two
> disagree. If the canonical repository ever becomes reachable, reconcile
> against it rather than merging this file forward.

## Scope

Applies to uqf's `src/etl/` workers and the Python orchestration that starts
and coordinates them.

Preserve the distinction between two worker shapes:

- A **bounded historical worker** takes an explicit `[from,to)` request, a
  resumable run specification, and a terminal completion path. This is an
  **enforced contract**.
- A **continuous worker** runs a persistent poll-and-cursor loop. This is an
  **established implementation pattern, not a registered shared contract.**

## Bounded lifecycle contract

- **E-01** — Register every bounded worker in `.qbfstate.bounded_workers`, and
  implement every method and configuration global named in
  `.qbfstate.bounded_worker_methods` and `.qbfstate.bounded_worker_globals`.
  Call `require_contract` during initialisation, and call the bounded runtime
  only after the publishing handle is resolved. Enforcement is deterministic
  and tested. *Grounded in `src/etl/core/backfill_state.q`,
  `tests/q/test_backfill_state.q`.*

- **E-02** — Give every bounded worker an explicit source-tier range, a single
  traversal direction, a run specification, a private state file, and a
  terminal completion path. **A bounded worker must not silently become an
  unbounded tailer.**

- **E-03** — Implement continuous workers as long-running poll loops: load a
  local cursor at startup, publish a transformed page, then advance and
  persist the cursor. Do not publish a resume-completion claim merely because
  a continuous cursor advanced. *Grounded in
  `src/etl/workers/marketwarehouse_deals.q`.*

- **E-04** — Keep transformation, merge decisions, cursor calculations,
  coverage decisions and query construction deterministic and unit-testable.
  Keep connections, timers, publishing, checkpoint persistence, retry
  reporting and lifecycle orchestration in the worker shell.

- **E-05** — Publish a nonempty page before recording the cursor that
  acknowledges it. Treat a failed fetch as having no publication effect and no
  checkpoint effect.

## Data and state

- **E-06** — Keep each worker's checkpoint private to that worker. Store the
  full run specification alongside its cursor, and discard saved state when
  the current specification differs. **Never use another worker's checkpoint
  as evidence that a dataset is complete.**

- **E-07** — Publish durable cross-process completeness only through the
  append-only `etl_coverage` table, and only after the underlying work is
  complete. Stage a completion event for every completed bounded window,
  *including an empty window*.

- **E-08** — Represent every bounded input and coverage interval as half-open
  `[range_from,range_to)`: include the start, exclude the end, and reject
  empty and reversed intervals. Compose adjacent windows only at their common
  boundary.

- **E-09** — Require `source_version` when recording a bounded run. Treat it
  as one immutable source-release label, carry it in the run specification and
  the output provenance, and require coverage consumers to filter on it.

- **E-10** — Derive continuous coverage from one matching version only. Never
  merge intervals from different versions to satisfy a dependency.

- **E-11** — Route local historical reads, including `etl_coverage` admission
  checks, through a gateway addressing both `rdb` and `hdb` targets. **Do not
  bind historical correctness to an rdb-only handle: completion data moves
  after EOD.**

- **E-12** — Register every external source table, target mapping, required
  field and required type in the centralised source contract. Validate both
  generated fixtures and live external metadata against that same contract.
  *Grounded in `src/etl/core/source_contract.q`,
  `tests/q/test_source_contract.q`.*

## Operational

- **E-13** — Make bounded retries idempotent through the range-and-
  `source_version` contract: resume from a compatible private checkpoint when
  available, and query version-specific `etl_coverage` before re-fetching a
  completed table. **Do not assume exactly-once processing** — the framework
  establishes retry-safe publication and coverage skipping, which is a weaker
  and more honest guarantee.

- **E-14** — Implement dry-run as diagnostic-only execution: fetch and
  transform as usual, then exit without publishing rows, without publishing
  coverage, and without writing a checkpoint.

- **E-15** — Split authority explicitly. q and TorQ own process startup,
  source reads, query failures, checkpoints, run and window counts, and
  coverage events. Airflow owns task ordering, scheduling, retries, timeouts,
  concurrency and alert routing. Exchange **structured worker status** — never
  infer either layer's facts from the other's log text.

- **E-16** — Resolve declared TorQ dependencies through `.servers`, fail during
  initialisation when a required connection or coverage precondition is
  unavailable, and register recurring polling through the shared runtime
  helper where the worker participates in the bounded contract.

- **E-17** — Regenerate the TorQ configuration on every `bootstrap()` from
  vendored inputs plus uqf-owned additions and overrides. Never hand-edit
  vendored configuration, and never depend on a previously generated file
  still being correct.

## Validation

- **E-18** — Add deterministic qUnit coverage for every changed bounded
  lifecycle decision: contract completeness, cursor advancement, run-spec
  invalidation, window boundaries, coverage staging, and version-specific
  coverage admission.

- **E-19** — Use `etl_test_doubles` to replace fetch, publish, checkpoint and
  logging adapters when testing stateful control flow. Keep testing
  deterministic business logic directly. **Do not let adapter doubles
  substitute for transform and coverage tests.**

- **E-20** — Run the live external metadata smoke test separately, when an
  external schema or adapter changes. The deterministic suite proves local
  behaviour, **not** that a configured external service is reachable or
  compatible. *Grounded in `tests/integration/external_source_meta_smoke.q`.*

- **E-21** — Use the suite matching the changed layer: `scripts/test.sh q-unit`
  for q behaviour, `scripts/test.sh q-backfill-process` for bounded process
  behaviour, and the focused Python suites for orchestration changes.

## Open questions (undecided in the canonical document)

- **E-22** — Decide whether continuous workers need a framework-level public
  freshness or coverage contract. Today `etl_coverage` serves bounded
  historical completion while continuous workers keep private cursor state,
  and no shared cross-worker statement of freshness exists — so anything
  consuming continuous output has no sanctioned way to ask how current it is.

- **E-23** — Decide whether a future bounded worker may use a different
  idempotency mechanism alongside versioned coverage. The framework
  standardises compatible checkpoints, publish-before-checkpoint ordering, and
  a coverage precheck; it does not define a universal row-level
  idempotency-key interface.

- **E-24** — Decide whether the continuous-worker lifecycle should become an
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
