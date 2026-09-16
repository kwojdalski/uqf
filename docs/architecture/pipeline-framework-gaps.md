# What a Dagster-shaped pipeline framework needs, and what `src/etl/` has

An assessment, not a plan. It answers two questions: **what is missing**
relative to how a modern orchestration framework is structured, and **is that
implementable in q**.

Short answers: less is missing than the framework's age suggests, the gaps
are concentrated in three places, and yes — q is unusually *well* suited to
most of it, for a reason given in §4.

## 1. The mapping — what already exists

Dagster's abstractions, against this tree:

| Dagster concept | `src/etl/` today | State |
|---|---|---|
| **Resource** — pluggable external connection | `.qsrc` source contract: a declaration carrying `query`, `fixture`, credentials | **partial** — source-shaped only |
| **Op / asset compute** | `.qbw` bounded worker: `init`/`plan`/`fetch`/`publish`/`checkpoint` | **yes** |
| **Graph / job** | `.qdag` — edges *derived* from declared inputs and outputs | **yes** |
| **Partitions** | `.qcov` half-open time intervals + `source_version` | **partial** — time only |
| **Materialization record** | `.qcov` rows, now bitemporal (D-11) | **partial** — no metadata |
| **Backfill** | `.qbw.plan` narrows by cursor then coverage | **yes**, and better than most |
| **Config** | `.qwcfg` typed getters, stated precedence, accumulated errors | **partial** — global, not per-op |
| **Retry policy** | `.qwrt` classification: data failures don't retry, transport does | **yes** |
| **Logging / events** | `.qlog` four levels over TorQ's `.lg`, structured fields | **yes** |
| **Sensors / monitoring** | `.qhb` heartbeat, status files, `/ops/backfill` | **yes** |
| **Asset checks** | `.qdqc` — nine check functions | **exists, wired to nothing** |
| **Schedules** | *deliberately absent* — Airflow owns ordering (ETL-15) | **by decision** |
| **IO manager** — compute/storage separation | — | **missing** |
| **Run identity** | — | **missing** |

That is a substantial framework. The worker contract, the coverage ledger and
the derived DAG are the three things most homegrown pipelines never get, and
they are the three this tree does have.

## 2. The three real gaps

### 2.1 No IO manager — compute and storage are fused

`.qbw.publish` is four lines and they decide everything:

```q
publish:{[worker;batch]
    t:.qsrc.declaration[(declaration worker)`source]`target;
    if[not t in tables `.; t set 0#batch];
    t insert batch;
    count batch}
```

Every worker writes to an in-process table named by its source declaration.
That is the *only* thing a worker can do with its output. There is no way to:

- run a worker in a test and capture its output without touching a table;
- write the same asset to a partitioned HDB instead of an in-memory table;
- write to two places (a table and an archive) without editing the shell;
- change where an asset lands without changing the worker.

**This is the single biggest modularity gap.** Dagster's IO manager exists
precisely because "what to compute" and "where it goes" change for different
reasons and at different times.

### 2.2 `.qdqc` is wired to nothing — a pipeline can publish garbage and record success

Nine check functions exist — `check_market_data_quality`, `check_stale_quotes`,
`check_position_notional_limits`, `summarize_checks` and more. No worker calls
any of them. `grep -n 'dqc' src/etl/core/bounded_worker.q` returns nothing.

So the sequence today is: fetch → publish → **record coverage as complete**.
A window of nulls, a window with a schema-shaped but semantically wrong
payload, a window with every price at zero — all are recorded as covered, and
`is_covered` will report them as published forever.

This is the repository's own recurring pattern (a capability that exists and
is reached from no live path), and here the consequence is a coverage ledger
that lies.

### 2.3 No run identity, and materialisations carry no metadata

A coverage row records `dataset, source_version, range, rows_published,
recorded_at, superseded_at`. There is no `run_id`, so it is not possible to
ask:

- which materialisations came from one execution;
- what else that execution produced or failed to produce;
- whether two assets are consistent because they were built together.

And `rows_published` is the only fact recorded about a materialisation.
Dagster attaches arbitrary metadata — row counts, min/max of the partition
column, null fractions, a checksum, the query that produced it. That metadata
is what makes a materialisation *auditable* rather than merely *recorded*.

## 3. The smaller gaps, honestly ranked lower

- **Resources are source-shaped.** `.qsrc` is a resource in all but name, but
  a worker cannot declare "I need a clock", "I need a second connection", "I
  need a cache". Generalising it is mostly renaming.
- **Partitions are time-only.** Coverage indexes on `[range_from, range_to)`.
  A categorical partition (per-`sym`, per-region) has no expression, and
  `.qbw.define` actively refuses two workers sharing a dataset *because*
  coverage has no partition dimension. That refusal is correct today and is
  exactly what a partition key would relax.
- **Config is global.** `.qwcfg` has precedence, typed getters and accumulated
  errors — genuinely good — but a worker does not *declare* its config schema,
  so a missing key is found at first read rather than at startup.
- **Assets are implicit.** `.qdag` derives edges from declared inputs and
  outputs, which is asset-shaped thinking. But an asset has no identity of its
  own: no description, no owner, no freshness policy attached to *the asset*
  rather than to the worker that happens to produce it.

## 4. Is this implementable in q? Yes — and q is a better fit than it looks

Three properties of q make this easier than in most languages:

**Functions are values, and a dict of functions is a first-class object.**
That is exactly what a resource, an IO manager and an asset check all are.
`.qsrc` already proves the pattern works in this codebase: a source is a
dictionary carrying `query`, `fixture` and friends, validated at registration.
An IO manager is the same shape with `write`, `read` and `exists`. No new
language mechanism is needed — only a second instance of a pattern already
shipped.

**Tables are the native data structure.** A materialisation event log, a run
table, an asset catalogue and a check-result table are all *just tables*, and
they are queryable with the same syntax as the data they describe. In Python
these are ORM objects and a database; here they are three lines each.

**The namespace-per-module convention already gives module boundaries.**
`.qio`, `.qres`, `.qcheck`, `.qrun` slot in beside `.qsrc`/`.qbw`/`.qcov`
without disturbing anything.

Two things are genuinely harder in q, and should be acknowledged rather than
discovered later:

- **No type system for config schemas.** Dagster validates config against a
  declared schema before a run starts. In q that has to be a runtime
  validator — which `.qwcfg` already is in miniature, and which `.qsrc.register`
  already does for source declarations. So it is achievable, just not free.
- **No decorators, so registration is explicit.** Dagster's `@asset` does
  registration implicitly. Here a module registers itself on load, which this
  tree already does deliberately (a declaration and its implementation cannot
  drift because there is no way to have one without the other). That is
  arguably better, and it is certainly not a blocker.

## 5. Recommended order, by leverage

1. **Asset checks in the publish path.** `.qdqc` exists; the work is a
   `check` callback on the worker declaration, run between fetch and publish,
   with a failed check recorded as a *failed window* rather than a covered
   one. Highest value per line, and it closes a case where the ledger
   currently lies.
2. **IO manager.** Extract `publish`'s four lines behind a declared
   `io` dict (`write`/`read`/`exists`), defaulting to today's in-memory
   insert so nothing changes for existing workers. Unlocks testing,
   alternative storage, and multi-target writes.
3. **Run identity and materialisation metadata.** A `run_id` on the coverage
   row plus a free-form metadata dict, and a `.qrun` table recording each
   execution. Makes the ledger an event log.
4. **Generalise resources**, then **categorical partitions**, then
   **per-op config schemas**. Each is worth doing and none blocks the others.

Scheduling stays out, deliberately: ETL-15 gives ordering, retries and
alerting to Airflow, and re-implementing them here would create the second
authority H-01 exists to warn about.
