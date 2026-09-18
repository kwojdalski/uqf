# UQF-Copy Alignment Plan

> **Provenance.** Transcribed from screenshots of the plan as authored in the
> WSL `uqf` checkout, where it lives at
> `docs/changes/2026-09-16-uqf-copy-alignment-plan.md`. Filed here under
> `docs/migrations/` because that is where this tree keeps plans for
> restructurings written before the work — the same place the canonical
> Bitbucket alignment plan sits.
>
> Two things a reader must check against the original rather than trust here:
> the three commit ids in the snapshot table were transcribed from a photograph
> and are **not** verified, and the author's home directory has been redacted
> (it carried a corporate user id, which A-04 keeps out of this public tree).
> A baseline that does not reproduce from its recorded ids is worse than one
> with no ids, so confirm them before relying on the snapshot.

## Objective

Align `<home>/repos/uqf-copy` with the local UQF checkout while keeping this
repository authoritative for public contracts, schemas, process behavior, and
operational configuration. Port useful behavior from `uqf-copy`; do not merge
or cherry-pick its history wholesale.

The detailed evidence is in `2026-09-16-133123-drift-uqf-copy.md`, which lives
alongside the original in the WSL checkout's `docs/changes/` and is **not**
present in this tree — deliberately left as a plain filename rather than a
link, because a link that cannot resolve is worse than a name a reader can go
and find.

The comparison used these local snapshots without fetching remotes:

| Role | Repository | Commit |
|---|---|---|
| Contract authority | `uqf` | `2fff68a2c9a743170fbc372733795d5b25947de9` |
| Source of candidate changes | `uqf-copy` | `9314302693906927a2d5893ded5f72a3894742bd0` |
| Merge base | both | `b63464e9903564950347caca66a94a1907da78a1` |

The snapshot contains 188 `uqf`-only commits and 118 `uqf-copy`-only commits.
That scale, plus incompatible ETL and configuration decisions, makes selective
behavioral porting safer than branch merging.

## Operating Rules

1. Work in `uqf-copy`; use local UQF as the read-only behavioral reference.
2. Freeze both commit IDs at the start of each work package. Regenerate the
   drift report if either head changes.
3. Create one issue and one reviewable commit series per work package below.
4. Port tests or write characterization tests before porting implementation.
5. Preserve UQF's public q namespaces, schemas, CLI contracts, process names,
   checkpoint fields, and generated-configuration ownership unless a separate
   migration decision explicitly changes them.
6. Never copy generated docs, runtime databases, logs, checkpoints, secrets, or
   vendored files as implementation source.
7. Record every `uqf-copy`-only path as **port**, **adapt**, **retain local**,
   or **drop**. Completion requires no unclassified paths.

## Target Decisions

| Area | Decision | Reason |
|---|---|---|
| Repository authority | Keep local UQF contracts | UQF is the requested target and has the production-oriented TorQ/backfill behavior |
| Git history | Do not merge histories wholesale | Both branches contain large independent post-base histories |
| ETL model | Keep UQF source adapters, coverage model, generated config, and workers | These are compatibility boundaries already exercised by UQF integration tests |
| `uqf-copy` ETL utilities | Port selectively behind UQF contracts | Generic bounded workers, coercion, logging, DAG metadata, and freshness may reduce duplication |
| Python orchestrator split | Adopt after behavior parity tests | The split modules are useful, but UQF has newer CLI/config/monitoring behavior |
| Frontend | Port as a new optional subsystem | `uqf-copy` has a substantial frontend absent from UQF; it must consume UQF APIs rather than define them |
| CI and static checks | Port early in isolated commits | q-trap, hook-scope, environment, and full-Python checks can protect later work |
| Generated/reference docs | Regenerate from UQF sources | Copying outputs would hide source and path differences |
| Alternate decision/drift ledgers | Mine decisions, then archive or rewrite | `uqf-copy` declares itself primary, which conflicts with this plan |

## Work Packages

### 0. Freeze and classify the baseline

**Actions**

- Commit or explicitly shelve the current UQF provenance work before taking a
  new comparison snapshot.
- Confirm `uqf-copy` is clean and record both heads, remotes, tags, merge base,
  and tool versions.
- Produce machine-readable inventories for A-only, B-only, modified, renamed,
  generated, vendored, and runtime paths.
- Create a decision ledger with one row per `uqf-copy`-only path and one owner.

**Gate**

- Both snapshots reproduce from commit IDs.
- Every dirty path is excluded or assigned to a work package.
- Every B-only path has an initial disposition.

**Rollback**: no code changes; discard only generated inventory files.

### 1. Establish protective checks

**Candidate ports**

- `.github/workflows/ci.yml`
- `scripts/gates/check_q_traps.py`
- `scripts/gates/check_hook_scopes.py`
- `scripts/gates/check_env_reference.py`
- Relevant tests under `python/uqf_frontend/tests/` only where they test the
  checker itself rather than the frontend.

**Actions**

- Adapt checks to UQF's existing `scripts/test.sh`, pre-commit configuration,
  Python package paths, and q source layout.
- Add checks one at a time; fix only findings caused by each newly enabled
  check in the same work package.
- Keep network-dependent and live-service tests opt-in.

**Gate**

```sh
uv run pre-commit run --all-files
scripts/test.sh q-unit
uv run pytest python/torq_orchestrator/tests python/uqf_client/tests python/uqf_airflow_provider/tests
scripts/dev/build.sh
```

**Rollback**: revert an individual check and its directly required fixes.

### 2. Lock contracts before implementation ports

**Actions**

- Compare q public functions, table schemas, process names, CLI commands,
  environment variables, checkpoint records, and Airflow payloads.
- Add characterization tests in `uqf-copy` for the current UQF behavior.
- Resolve these known conflicts before porting ETL code:
  - UQF coverage records include its current partition/source-version model;
    do not adopt `uqf-copy`'s no-partition assertion implicitly.
  - UQF owns generated `process.csv`, `database.q`, and `setenv.sh`; preserve
    that generate-never-edit boundary.
  - Keep UQF's `python/config/` location and process manifest semantics.
- Preserve UQF's gateway routing, summary lineage, and CLI exit behavior.

**Gate**

- Schema comparison tests pass.
- CLI help and representative exit-code snapshots match UQF.
- Checkpoint migration or incompatibility is documented before worker changes.

**Rollback**: retain adapters at the boundary; do not rewrite stored state.

### 3. Port low-coupling q capabilities

Review and port independently:

1. Event-tape model and tests from `docs/event-tape.md`, source modules, and
   `tests/q/test_event_tape.q`.
2. `vpin` and `trade_arrival_rate` after the event-tape contract is accepted.
3. Vanna/volga, expanding VAMP, and reject-rate additions where absent locally.
4. Logging helpers from `src/etl/core/log.q` if they can wrap TorQ `.lg`
   without changing UQF worker status contracts.
5. Coercion helpers from `src/etl/core/coercion.q` only after comparing them
   against UQF's source-specific adapters and singleton handling.

For each capability, port its tests first, then the smallest implementation and
API documentation. Do not combine pricing, execution, and ETL changes.

**Gate**: focused unit tests, full `scripts/test.sh q-unit`, numerical
reference checks for analytics, and no public namespace/signature regression.

### 4. Reconcile ETL foundations

**Candidate paths requiring adaptation**

- `bounded_worker.q`
- `continuous_state.q`
- `dag.q` and generated pipeline metadata
- source doubles and lifecycle tests
- forward-only cursor and retry classification behavior

**Actions**

- Map each candidate onto UQF's existing `worker_runtime.q`,
  `worker_config.q`, `coverage.q`, `backfill_state.q`, source adapters, and
  worker entrypoints.
- Prefer extracting a shared helper only where two current UQF workers already
  duplicate the same lifecycle.
- Preserve UQF's source-version provenance, gateway coverage prechecks,
  checkpoint schema behavior, publication schemas, and bounded versus
  continuous worker distinction.
- Treat retry, cursor, coverage, and status transitions as separate commits.

**Gate**

```sh
scripts/test.sh q-unit
scripts/test.sh q-backfill-process
```

Also run deterministic replay, resume, duplicate-page, empty-page, failure,
and forward-only cursor cases. Run live external checks only when configured.

**Rollback**: keep existing worker entrypoint selectable until the adapted
implementation passes replay and checkpoint tests.

### 5. Adapt the Python orchestrator decomposition

`uqf-copy` splits `core.py` into `crypto.py`, `env.py`, `listing.py`,
`paths.py`, `pipelines.py`, `procs.py`, `runtime.py`, and `schemas.py`. Treat
this as a refactor candidate, not a source replacement.

**Actions**

- First port `test_module_split.py` as an architecture constraint adapted to
  UQF's package.
- Extract one responsibility per commit while retaining `core.py` re-exports
  for existing callers.
- Reconcile UQF-only behavior after each extraction: shared `python/config/`,
  monitoring, logs, crypto recorder, backfill planning, process groups,
  generated schemas, and summary data contracts.
- Remove compatibility re-exports only after repository-wide usage search.

**Gate**: all orchestrator tests, CLI snapshots, generated-config tests, and a
TorQ demo smoke test pass after every extraction.

**Rollback**: revert the latest extraction; public imports remain stable.

### 6. Evaluate Airflow changes

Review `sensor.py`, `status_reader.py`, `translate.py`, provider packaging, and
Airflow-independent test doubles.

- Port the status translation layer first because it is independently testable.
- Keep UQF's q-side status authority and workflow mapping.
- Resolve the existing Airflow 3.1 hanging-test issue before enabling broader
  provider tests in the default gate.
- Avoid importing Airflow at module import time where a protocol or test double
  suffices.

**Gate**: bounded provider tests on supported Airflow versions and contract
fixtures matching actual UQF status payloads.

### 7. Port the frontend as an optional consumer

Treat `python/uqf_frontend/` and `web/` as one feature program with separate
backend and UI packages.

**Actions**

- Write an architecture decision for authentication, authorization, deployment,
  query limits, and schema exposure.
- Port the backend at UQF's gateway/client contracts; do not embed alternate
  table schemas from `uqf-copy`.
- Port read-only health and fleet views first, then coverage/status, then data
  queries and captures.
- Add the React application only after backend contracts and security tests
  pass.

**Gate**: backend unit/security tests, gateway contract tests, frontend tests,
production build, and an end-to-end read-only smoke against a local TorQ demo.

**Rollback**: keep packages optional and disabled from default process startup.

### 8. Reconcile documentation and decisions

- Port durable decisions from `uqf-copy` into UQF's `docs/decisions/` format.
- Rewrite links for UQF's organized docs paths; do not restore superseded root
  pages such as `docs/torq-demo.md` or duplicate requirements files.
- Keep source documentation authoritative and regenerate qDoc, diagrams,
  process references, and operational blocks.
- Archive evidence reports in `docs/changes/`; do not treat them as current
  architecture documentation.

**Gate**

```sh
scripts/dev/gen-docs.sh
uv run pytest python/torq_orchestrator/tests/test_doc_links.py \
  python/torq_orchestrator/tests/test_qmd_references.py
scripts/dev/build.sh
```

### 9. Final parity review

- Re-run repository, file, function, and capability drift agents against the
  new `uqf-copy` head.
- Require zero unexplained public-contract differences and zero unclassified
  B-only paths.
- Differences may remain only when marked intentional with an owner, rationale,
  validation, and revisit schedule.
- Run all offline gates from a clean checkout. Run opt-in TorQ and external
  integration checks in their configured environments.

**Exit criteria**

- `uqf-copy` implements the accepted UQF contracts and behavior.
- Ported `uqf-copy` capabilities have focused tests and documentation.
- Generated/runtime/vendored differences are classified rather than copied.
- The final drift report contains only intentional differences.

## Recommended Issue Order

1. Port q-trap and hook-scope checks.
2. Add contract characterization tests.
3. Port event tape and analytics in separate issues.
4. Evaluate coercion and logging helpers.
5. Reconcile bounded/continuous worker primitives.
6. Split the orchestrator behind compatibility exports.
7. Adapt Airflow status translation and bounded tests.
8. Add the optional frontend backend, then the React UI.
9. Reconcile durable decisions and regenerate docs.
10. Run final two-level drift review and close remaining dispositions.

This order deliberately front-loads checks and contracts, then moves from
low-coupling libraries toward stateful workers and user-facing applications.
