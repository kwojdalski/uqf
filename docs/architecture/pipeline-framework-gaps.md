# What a Dagster-shaped pipeline framework needs, and what `src/etl/` has

**This assessment is closed.** It asked two questions — what is missing
relative to how a modern orchestration framework is structured, and is that
implementable in q — found three real gaps and four smaller ones, and every
one of those has since been built or been decided against. What remains
different from Dagster is listed in §3, and each difference is a decision
with a stated condition for reopening it, not an item waiting for someone.

Kept rather than deleted because the *reasoning* is the part worth having:
each gap below says what was wrong before, which is why the thing that
replaced it is shaped the way it is. A reader wondering why `.qio` exists,
or why `run_id` is not a parameter, will not find that anywhere else.

## 1. The mapping

Dagster's abstractions, against this tree:

| Dagster concept | `src/etl/` today | State |
|---|---|---|
| **Op / asset compute** | `.qbw` bounded worker (`init`/`plan`/`fetch`/`publish`/`checkpoint`) and `.qstream` streaming job (`on_batch`/`on_timer`) | **yes** |
| **Graph / job** | `.qdag` — edges *derived* from declared inputs and outputs, over four job kinds: `bounded`, `continuous`, `stream`, `reaction` | **yes** |
| **Partitions** | `.qcov` half-open time intervals + `source_version`, plus a categorical `partition` | **yes** — §2.4 |
| **Materialization record** | `.qcov` rows, bitemporal, with per-window metadata from `.qrun` | **yes** — §2.3 |
| **Backfill** | `.qbw.plan` narrows by cursor then coverage | **yes**, and better than most |
| **Retry policy** | `.qwrt` classification: data failures don't retry, transport does | **yes** |
| **Logging / events** | `.qlog` four levels over TorQ's `.lg`, structured fields | **yes** |
| **Sensors / monitoring** | `.qhb` heartbeat, `.qstatus` status files, `/ops/backfill` | **yes** |
| **Asset checks** | `.qdqc`, reached through a worker's declared `check` | **yes** — §2.2 |
| **IO manager** — compute/storage separation | `.qio` — a manager is a declared dict | **yes** — §2.1 |
| **Run identity** | `.qrun` — one identity per execution | **yes** — §2.3 |
| **Resource** — pluggable external connection | `.qsrc` source contract | **source-shaped, by decision** — §3 |
| **Config** | `.qwcfg` typed getters, stated precedence, accumulated errors | **global, by decision** — §3 |
| **Asset identity** | an asset is the table a job declares; it has no record of its own | **absent, by decision** — §3 |
| **Schedules** | *deliberately absent* — Airflow owns ordering (ETL-15) | **by decision** — §3 |

The worker contract, the coverage ledger and the derived DAG are the three
things most homegrown pipelines never get, and they are the three this tree
had from the start.

## 2. The gaps it found, and what closed each

### 2.1 No IO manager — compute and storage were fused

`.qbw.publish` was four lines and they decided everything: every worker
wrote to an in-process table named by its source declaration, and that was
the only thing a worker could do with its output. There was no way to run a
worker in a test and capture its output without touching a table, to write
the same asset to a partitioned HDB instead of memory, or to change where
an asset lands without changing the worker.

**Closed by `.qio`.** A manager is a dict carrying `write`, defaulting to
`.qio.memory` — the in-process insert — so a worker that says nothing about
io behaves exactly as it did. `.qio.discard` writes nothing and reports
honestly, which is what makes a pipeline runnable end to end without
touching storage.

No `read` or `exists`: nothing in this framework reads a target back
through an abstraction, and a capability reached from no live path is the
shape this repository keeps finding and deleting. They go in when something
calls them.

### 2.2 `.qdqc` was wired to nothing — a pipeline could publish garbage and record success

Nine check functions existed and no worker called any of them. The sequence
was fetch → publish → **record coverage as complete**, so a window of nulls
or of semantically wrong rows was recorded as covered and read as published
forever. The ledger could hold a lie and nothing anywhere would say so.

**Closed by the declared `check`.** A worker declaration takes an optional
callback, run between transform and publish. A failed check takes the same
terminal-window path as a failed fetch: nothing published, no coverage
staged, the run continues, and the next run plans that window again because
coverage never claimed it. `demo_deals_backfill` declares one, so the path
is exercised rather than merely available.

### 2.3 No run identity, and materialisations carried no metadata

Three questions had no answer: what did one execution produce, what else
did it produce or fail to produce, and are two assets consistent because
they were built together. `rows_published` was the only fact recorded.

**Closed by `.qrun`.** `etl_coverage` gains a `run_id`, written from
`.qrun.current[]` rather than passed in — the one design argument worth
restating: ETL-09 requires `source_version` to be a *parameter* because an
optional filter is one a caller forgets, and a wrong `source_version` is
silent corruption. `run_id` is different in kind: a fact about the
executing process, with exactly one right answer at any instant. Threading
it through five signatures would manufacture the chance to pass the wrong
one, a failure mode that otherwise cannot occur. Outside a run the column
records the null guid, honestly — a materialisation staged by hand belongs
to no run, and saying so beats inventing an identity.

Three reads answer the three questions: `.qcov.materialisations_of[run]`,
`.qcov.contributing_runs[dataset;partition;version]`, and `.qrun.unfinished[]`
— the executions that began and never reported an outcome, which is the state an
interrupted process leaves and which nothing else records.

Arbitrary metadata is the worker's own `facts` hook: a function from the
batch to a dict, attached to the window's materialisation. The framework
records what it can know without a schema (rows, source_version, dry_run);
anything needing to know what a column *means* goes there.

### 2.4 Partitions were time-only

Coverage carried only `[range_from, range_to)`, so a categorical partition
— per-`sym`, per-region — had no expression, and `.qbw.define` refused two
workers sharing a dataset *because* their coverage rows would have been
indistinguishable. That refusal was correct given the schema, and it was
the ceiling on parallelism.

**Closed.** `etl_coverage` carries `partition`, required on write and on
read; the refusal keys on the (dataset, partition) pair, so one dataset can
be filled by several workers at once.

### 2.5 Only one job role had a shell

`.qbw` made a backfill a declaration; feeds and subscribers were
hand-rolled scripts, eight of them, each repeating the same twenty lines of
subscribe-and-publish wiring — and the copies were weaker than the original
they were copied from, returning an empty list where it threw.

**Closed by `.qstream`.** A job declares `subscribes`, `publishes`,
`on_batch` and `on_timer` in its own file under `src/etl/streaming/`, and
one runner (`scripts/processes/torq_stream.q`) runs any of them, selected
by procname. `.qpipe` did *not* become the shell: it stayed the TorQ
adapter the runner calls, which is the layering
[`pipeline-philosophy.md`](pipeline-philosophy.md) §10 states and
`check_etl_layering.py` enforces.

The live Databento adapter is the test of whether that shell generalised:
its feed handler is Python, outside q entirely, and the job that folds its
rows reuses the `.qxf` transform the ODBC backfill already declared. One
fold, two paths, no second implementation.

## 3. What is deliberately not here

Each of these is a difference from Dagster that this tree has decided
against, with what would change the decision.

- **Resources are source-shaped.** `.qsrc` is a resource in all but name,
  but a worker cannot declare "I need a clock" or "I need a cache" — it
  declares a source. Generalising is mostly renaming, and nothing has asked
  for a second kind of resource in the life of this tree. *Reopen when a
  worker genuinely needs a non-source dependency; the shape to copy is
  `.qio`, which is a resource that already generalised.*

- **Config is global.** `.qwcfg` has precedence, typed getters and
  accumulated errors, but a worker does not declare its config schema, so a
  missing key is found at first read rather than at startup. *Reopen when a
  worker has enough configuration for that distinction to cost something;
  today the fields are few and `.qbw.define` already refuses a malformed
  declaration at load.*

- **Assets have no identity of their own.** `.qdag` derives edges from
  declared inputs and outputs, which is asset-shaped thinking, but an asset
  is just a table name: no description, no owner, no freshness policy
  attached to the asset rather than to the job that happens to produce it.
  This is the one with real value left in it. *Reopen when something needs
  to ask a question of an asset rather than of a job — "who owns this", "is
  this stale" — because the answer would otherwise be spread across the
  jobs that write it.*

- **Scheduling stays out.** ETL-15 gives ordering, retries, timeouts and
  alerting to Airflow. Re-implementing any of it here would create a second
  authority for the same decisions, and the two would disagree. *This one
  does not reopen: it is the authority split, not a gap.*

## 4. Why q suited this better than it looks

Three properties made the work easier than it would have been elsewhere,
and they are why the pieces above are small:

**Functions are values, and a dict of functions is a first-class object.**
That is exactly what a resource, an IO manager and an asset check are.
`.qsrc` proved the pattern before any of them: a source is a dictionary
carrying `query`, `fixture` and friends, validated at registration. `.qio`
is the same shape with `write`. No new language mechanism was needed for
any of them — only further instances of a pattern already shipped.

**Tables are the native data structure.** The materialisation log, the run
table and the check results are *just tables*, queryable with the same
syntax as the data they describe. In Python these are ORM objects and a
database; here they are three lines each.

**The namespace-per-module convention already gives module boundaries.**
`.qio`, `.qrun`, `.qstream` and `.qstatus` slotted in beside
`.qsrc`/`.qbw`/`.qcov` without disturbing anything.

Two things are genuinely harder in q, and both proved manageable:

- **No type system for config schemas**, so validation is a runtime
  validator — which `.qwcfg` and `.qsrc.register` both are. Achievable, not
  free, and the reason §3's config item is still open.
- **No decorators, so registration is explicit.** A module registers itself
  as it loads. That is arguably better than Dagster's `@asset`: a
  declaration and its implementation cannot drift, because there is no way
  to have one without the other.
