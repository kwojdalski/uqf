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
| **Op / asset compute** | `.qbw` bounded worker (`init`/`plan`/`fetch`/`publish`/`checkpoint`) and `.qstream` streaming job (`on_batch`/`on_timer`) | **yes** |
| **Graph / job** | `.qdag` — edges *derived* from declared inputs and outputs | **yes** |
| **Partitions** | `.qcov` half-open time intervals + `source_version`, plus a categorical `partition` | **yes** — see §5.4 |
| **Materialization record** | `.qcov` rows, bitemporal, with per-window metadata from `.qrun` | **yes** — see §2.3 |
| **Backfill** | `.qbw.plan` narrows by cursor then coverage | **yes**, and better than most |
| **Config** | `.qwcfg` typed getters, stated precedence, accumulated errors | **partial** — global, not per-op |
| **Retry policy** | `.qwrt` classification: data failures don't retry, transport does | **yes** |
| **Logging / events** | `.qlog` four levels over TorQ's `.lg`, structured fields | **yes** |
| **Sensors / monitoring** | `.qhb` heartbeat, status files, `/ops/backfill` | **yes** |
| **Asset checks** | `.qdqc`, reached through a worker's declared `check` | **yes** — see §2.2 |
| **Schedules** | *deliberately absent* — Airflow owns ordering (ETL-15) | **by decision** |
| **IO manager** — compute/storage separation | `.qio` — a manager is a declared dict | **yes** — see §2.1 |
| **Run identity** | `.qrun` — one identity per execution | **yes** — see §2.3 |

The four rows that read **missing** or **partial — no metadata** when this
register was written are now done, and the table says so; §2 and §5 are
where each was closed. A summary that still described the gaps its own body
reported as fixed is worse than no summary, because the table is the part a
reader skims.

That is a substantial framework. The worker contract, the coverage ledger and
the derived DAG are the three things most homegrown pipelines never get, and
they are the three this tree does have.

## 2. The three real gaps

### 2.1 ~~No IO manager~~ — CLOSED

**Fixed.** `.qio` declares where output goes: a manager is a dict carrying
`write`, defaulting to `.qio.memory` — today's in-process insert — so a worker
that says nothing about io behaves exactly as before. `.qio.discard` writes
nothing and reports honestly, which is what makes a pipeline runnable end to
end without touching storage.

No `read` or `exists`: nothing in this framework reads a target back through
an abstraction, and a capability reached from no live path is the shape this
repository keeps finding. They go in when something calls them.

The original text follows, because the fusion it describes is why the seam
exists.

---

### 2.1 (as written) No IO manager — compute and storage are fused

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

### 2.2 ~~`.qdqc` is wired to nothing~~ — CLOSED

**Fixed.** A worker declaration now takes an optional `check` callback, run
between fetch and publish. A failed check takes the same terminal-window path
as a failed fetch: not published, no coverage staged, run continues,
and the next run plans the window again because coverage never claimed it.

`demo_deals_backfill` declares one, so the path is exercised rather than
merely available. The original text follows, because the failure it describes
is the reason the gate exists.

---

### 2.2 (as written) `.qdqc` is wired to nothing — a pipeline can publish garbage and record success

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

### 2.3 ~~No run identity, and materialisations carry no metadata~~ — CLOSED

**Fixed.** `.qrun` (`src/etl/core/run.q`) adds an execution identity and a
place to record facts about what an execution produced.

`etl_coverage` gains a `run_id` column, written from `.qrun.current[]` rather
than passed in. That is deliberate and is the one design argument worth
restating here: ETL-09 requires `source_version` to be a parameter because an
optional filter is one a caller forgets, but `source_version` is a *choice*
and a wrong one is silent corruption, whereas `run_id` is a *fact about the
executing process* with exactly one right answer at any instant. Threading it
through five signatures would create the chance to pass the wrong one, a
failure mode that otherwise does not exist. Outside a run the column records
the null guid, honestly: a materialisation staged by hand belongs to no run,
and saying so beats inventing an identity.

Three reads answer the three questions the gap named:
`.qcov.materialisations_of[run]` (what one execution produced — including
superseded rows, since restating a run's output later does not change what it
produced), `.qcov.contributing_runs[dataset;version]` (which executions
assembled a dataset), and `.qrun.unfinished[]` (executions that began and
never reported an outcome — the state an interrupted process leaves, which
nothing else records).

Metadata is long-form in `etl_run_meta` — one row per fact, keyed by run *and*
window — rather than a column per kind of fact, because the facts worth
attaching are not known in advance and a wide table would need a migration per
new one. The framework records what it can know without reading a column
(`rows`, `source_version`, `dry_run`); anything needing to know what a column
*means* comes from the worker's optional `facts` function, which
`demo_events_backfill` demonstrates with event span, distinct syms and the
terminal-event share.

One measured trap is recorded in the code: `first 1?0Ng` is the obvious way to
mint a run id and is wrong here, because q seeds its random state identically
at every process start — three separate interpreters each returned the same
first guid. On a *shared* ledger that stamps two executions with one identity,
which is worse than the gap it closes: absent attribution is visibly absent,
wrong attribution reads as correct. `.qrun.mint` derives from host, pid and
clock instead.

The original text follows.

---

### 2.3 (as written) No run identity, and materialisations carry no metadata

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
- ~~**Partitions are time-only.**~~ **Done** (#185). Coverage carried only
  `[range_from, range_to)`, so a categorical partition (per-`sym`,
  per-region) had no expression and `.qbw.define` refused two workers sharing
  a dataset *because* their coverage rows would be indistinguishable. That
  refusal was correct given the schema, and it was the ceiling on
  parallelism. `etl_coverage` now carries `partition`, required on write and
  on read; the refusal keys on the (dataset, partition) pair, so one dataset
  can be filled by several workers at once.
- **Config is global.** `.qwcfg` has precedence, typed getters and accumulated
  errors — genuinely good — but a worker does not *declare* its config schema,
  so a missing key is found at first read rather than at startup.
- **Assets are implicit.** `.qdag` derives edges from declared inputs and
  outputs, which is asset-shaped thinking. But an asset has no identity of its
  own: no description, no owner, no freshness policy attached to *the asset*
  rather than to the worker that happens to produce it.
- **Only one of the three job roles has a shell.** **Closed.** `.qstream`
  (`src/etl/core/stream_job.q`) is for a streaming job what `.qbw` is for a
  backfill: a job declares `subscribes`, `publishes`, `on_batch` and
  `on_timer` from its own file under `src/etl/streaming/`, and one generic
  runner (`scripts/processes/torq_stream.q`) runs any of them. The four hand-rolled
  `torq_*_etl.q` scripts and the four feed scripts this entry described are
  gone, and with them the `:()`-on-no-tickerplant copies. `.qpipe` did *not*
  become the shell: it stayed the TorQ adapter the runner calls, which is
  the layering [`pipeline-philosophy.md`](pipeline-philosophy.md) §10 now
  states and `check_etl_layering.py` enforces.

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

1. ~~**Asset checks in the publish path.**~~ **Done** — see §2.2.
2. ~~**IO manager.**~~ **Done** — see §2.1.
3. ~~**Run identity and materialisation metadata.**~~ **Done** — see §2.3.
4. ~~**Categorical partitions.**~~ **Done** — see the partition bullet in §3.
5. **Generalise resources**, then **per-op config schemas**. Each is worth
   doing and neither blocks the other.

Scheduling stays out, deliberately: ETL-15 gives ordering, retries and
alerting to Airflow, and re-implementing them here would create the second
authority the never-edit-the-vendored-tree rule exists to warn about.
