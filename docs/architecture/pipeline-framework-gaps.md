# What a Dagster-shaped pipeline framework needs, and what `src/etl/` has

**This assessment is closed.** It asked two questions — what is missing
relative to how a modern orchestration framework is structured, and is that
implementable in q — found three real gaps and four smaller ones, and every
one of those has since been built or been decided against.

*Reviewed 2026-09-23.* Every file, namespace and `§` citation below still
resolves, and three of §3's four decisions are unchanged. The fourth, asset
identity, has narrowed: assets now carry descriptions, kept complete by a
gate. That bullet says what is left. What remains
different from Dagster is listed in §3, and each difference is a decision
with a stated condition for reopening it, not an item waiting for someone.

Kept rather than deleted because the *list* is worth having: it says which
questions were asked and how each was answered, which is not recoverable from
the code. The reasoning behind each answer is not repeated here — it lives in
the header of the module that closed the gap, where someone reading that code
will actually meet it. §2 names the module for each.

## 1. The mapping

Dagster's abstractions, against this tree:

| Dagster concept | `src/etl/` today | State |
|---|---|---|
| **Op / asset compute** | `.qbw` bounded worker (`init`/`plan`/`fetch`/`publish`/`checkpoint`) and `.qstream` streaming job (`on_batch`/`on_timer`) | **yes** |
| **Graph / job** | `.qdag` — edges *derived* from declared inputs and outputs, over four job kinds: `bounded`, `continuous`, `stream`, `reaction` | **yes** |
| **Partitions** | `.qmatz` half-open time intervals + `source_version`, plus a categorical `partition` | **yes** — §2.4 |
| **Materialization record** | `.qmatz` rows, bitemporal, with per-window metadata from `.qrun` | **yes** — §2.3 |
| **Backfill** | `.qbw.plan` narrows by cursor then coverage | **yes**, and better than most |
| **Retry policy** | `.qwrt` classification: data failures don't retry, transport does | **yes** |
| **Logging / events** | `.qlog` four levels over TorQ's `.lg`, structured fields | **yes** |
| **Sensors / monitoring** | `.qhb` heartbeat, `.qstatus` status files, `/ops/backfill` | **yes** |
| **Asset checks** | `.qdqc`, reached through a worker's declared `check` | **yes** — §2.2 |
| **IO manager** — compute/storage separation | `.qio` — a manager is a declared dict | **yes** — §2.1 |
| **Run identity** | `.qrun` — one identity per execution | **yes** — §2.3 |
| **Resource** — pluggable external connection | `.qsrc` source contract | **source-shaped, by decision** — §3 |
| **Config** | `.qwcfg` typed getters, stated precedence, accumulated errors | **global, by decision** — §3 |
| **Asset identity** | an asset is the table a job declares; it now has a *description* of its own in the desk catalog, but no owner and no freshness policy | **partly closed** — §3 |
| **Schedules** | *deliberately absent* — Airflow owns ordering (ETL-15) | **by decision** — §3 |

The worker contract, the coverage ledger and the derived DAG are the three
things most homegrown pipelines never get, and they are the three this tree
had from the start.

## 2. The gaps it found, and what closed each

Three real gaps and two smaller ones. The `§` numbers are cited from source
headers, so they are stable: `io_manager.q:4` names §2.1 and `run.q:2` names
§2.3.

| | Gap | Closed by | Where the reasoning is |
|---|---|---|---|
| **2.1** | **No IO manager.** `.qbw.publish` was four lines that decided everything: every worker wrote to an in-process table named by its source declaration, and could do nothing else. No way to capture a worker's output in a test, or to land an asset elsewhere, without editing the shell. | `.qio` — a manager is a dict carrying `write`, defaulting to `.qio.memory`, so a worker that declares no io behaves as before. `.qio.discard` writes nothing and says so, which is what makes a pipeline runnable end to end without storage. No `read`/`exists`: nothing reads a target back through an abstraction. | `src/etl/core/io_manager.q` header |
| **2.2** | **`.qdqc` was wired to nothing.** Nine check functions existed and no worker called one. fetch → publish → record coverage complete, so a window of nulls was recorded as covered and read as published forever — the ledger could hold a lie and nothing would say so. | A worker's optional declared `check`, run between transform and publish. A failed check takes the failed-fetch path: nothing published, no coverage staged, and the next run plans that window again. `demo_deals_backfill` declares one, so the path is exercised rather than merely available. | `src/etl/core/bounded_worker.q:66` (why `check` is optional and `transform` is not) |
| **2.3** | **No run identity, no materialisation metadata.** Coverage described the *window*, never the *execution*, so three questions had no answer: what did one execution produce, what else did it produce or fail to produce, and are two assets consistent because they were built together. `rows_published` was the only fact recorded. | `.qrun`, and a `run_id` on `etl_coverage` written from `.qrun.current[]` rather than passed in. Reads: `.qmatz.materialisations_of`, `.qmatz.contributing_runs`, `.qrun.unfinished[]`. Arbitrary metadata is the worker's `facts` hook. | `src/etl/core/run.q` header — including why `run_id` is ambient where ETL-09 requires `source_version` to be a parameter |
| **2.4** | **Partitions were time-only.** Coverage carried only `[range_from, range_to)`, so a categorical partition had no expression and `.qbw.define` refused two workers sharing a dataset — correctly, given the schema, and it was the ceiling on parallelism. | `etl_coverage` carries `partition`, required on write and on read; the refusal keys on the (dataset, partition) pair, so one dataset can be filled by several workers at once. | `src/etl/core/materialisation.q` |
| **2.5** | **Only one job role had a shell.** `.qbw` made a backfill a declaration; feeds and subscribers were eight hand-rolled scripts repeating the same twenty lines of subscribe-and-publish wiring — and the copies were weaker than the original, returning an empty list where it threw. | `.qstream`: a job declares `subscribes`, `publishes`, `on_batch` and `on_timer` in one file under `src/etl/streaming/`, and one runner (`scripts/processes/torq_stream.q`) runs any of them by procname. `.qpipe` did *not* become the shell — it stayed the TorQ adapter the runner calls, the layering [`pipeline-philosophy.md`](pipeline-philosophy.md) §10 states and `check_etl_layering.py` enforces. | `src/etl/core/stream_job.q` header |

The live Databento adapter is the test of whether §2.5's shell generalised:
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

- **Assets have partial identity — a description, and nothing else.** This
  was written as "an asset is just a table name", and half of that has since
  stopped being true. `.qcat` in `scripts/processes/uqs_catalog.q` carries a
  prose description per table, and `tests/q/test_catalog.q` fails until a
  newly published table is described or explicitly hidden — so the set is
  complete by construction rather than by diligence, which is the part that
  usually rots. Its column types are not stored at all: they are `meta`'s
  answer on a running process. `uqs new-job` names the file among the steps it leaves
  you.

  What that closed is "what IS this table", asked of the asset rather than of
  the job. What it did not close: **no owner, no freshness policy**, and the
  description lives in the front end's catalog rather than on the q
  declaration — so a q process cannot ask, and the description can disagree
  with the declaration without anything noticing. *Reopen the rest when
  something needs "who owns this" or "is this stale"; the shape to copy is
  the catalog's own gate, which is what made the descriptions complete.*

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
`.qsrc`/`.qbw`/`.qmatz` without disturbing anything.

Two things are genuinely harder in q, and both proved manageable:

- **No type system for config schemas**, so validation is a runtime
  validator — which `.qwcfg` and `.qsrc.register` both are. Achievable, not
  free, and the reason §3's config item is still open.
- **No decorators, so registration is explicit.** A module registers itself
  as it loads. That is arguably better than Dagster's `@asset`: a
  declaration and its implementation cannot drift, because there is no way
  to have one without the other.
