# Adding a data pipeline

How to take an external source of rows and land it in a local table, with
completeness you can query and a bound you can see. The worked example below
is a real one: every command was run against this tree, and the output shown
is what it printed.

A pipeline here is **two declarations and one line in a loader**. The
lifecycle — windowing, retries, coverage, checkpoints, dry-run, the job graph
— is the shell's, and you do not write any of it. If you find yourself
writing a loop over days, you are rebuilding `.qbw`.

| You write | It says |
|---|---|
| a **source** in `src/etl/sources/` | what the rows are, where they come from, how to window them |
| a **worker** in `src/etl/workers/` | which source, which target dataset, how wide a window |
| one `\l` line in [`src/etl/init.q`](../../src/etl/init.q) | load them, in order |

Everything else follows from those. Why it is shaped this way is
[the pipeline philosophy](../architecture/pipeline-philosophy.md); what the
framework guarantees is [ETL-nn](../reference/etl-framework-requirements.md).

## Before you start: is it bounded or continuous?

Two different shells, and picking the wrong one is the only structural
mistake here that is expensive to undo.

**Bounded** — you know the range before you start: a backfill, a nightly
window, a restatement. It runs, it finishes, it exits. `.qbw`, and the rest
of this guide.

**Continuous** — it subscribes and never finishes: a tickerplant feed, a
poller. `.qcont` in [`src/etl/core/continuous_state.q`](../../src/etl/core/continuous_state.q),
whose state is a cursor rather than a range.

The rest of this guide is the bounded case.

## 1. Declare the source

A source declares its **shape**, not its plumbing. Create
`src/etl/sources/fx_rates.q`:

```q
/ fx_rates.q - an external reference-rate source (.qsfx).

\d .qsfx

source_name:`fx_rates
fields:`rate_time`sym`mid     / the columns this adapter reads
types:"psf"                    / one q type character per field
target:`fx_rates               / the local table they land in
time_field:`rate_time          / the column the window is taken on
row_key:`rate_time`sym         / what identifies a row uniquely (D-11)
tz:`UTC                        / what time_field is expressed in (L-06)

query:{[h;range_from;range_to]
    h({[from_ts;to_ts]
        select rate_time, sym, mid from fx_rates
            where rate_time>=from_ts, rate_time<to_ts
      };range_from;range_to)}

fixture:{[]
    ([] rate_time:2026.09.11D09:00:00.000000000+1D*til 5;
        sym:`EURUSD`GBPUSD`EURUSD`USDJPY`EURUSD;
        mid:1.0842 1.2631 1.0847 149.82 1.0851)}

.qsrc.register[source_name;
    `source`table`target`time_field`row_key`fields`types`query`fixture`tz!
    (source_name;`fx_rates;target;time_field;row_key;fields;types;query;fixture;tz)];

\d .
```

Five of those deserve a sentence each, because each is a decision rather
than a formality.

**`fields` is what you READ, not everything the source has.** Declaring a
column the worker never touches means an upstream change to an unused column
breaks the run.

**`query` is a parameterised lambda, never string concatenation.** The bounds
are arguments to a functional select evaluated on the remote side, so no
caller value is ever spliced into query text. `"select ... where t>=",string
range_from` is how a crafted value becomes an injection, and how a type
coercion becomes a silently wrong window rather than an error. Where a driver
cannot parameterise — ODBC — there is exactly one escape function,
`.qodbc.literal`, and everything goes through it.

**The window is half-open `[from;to)`** — `>=` on the lower bound and `<` on
the upper. One wrong operator double-publishes every boundary row, and the
duplicate surfaces far from here.

**`row_key` is only correct if the source guarantees uniqueness.** A source
that reuses ids after a purge silently merges unrelated rows. It is declared
per source for that reason; see
[the restatement design](../architecture/restatement-design.md).

**`tz` is a claim, not a default.** An unstated zone is the shape of
the bug: every later reader assumes UTC while the source hands over local
wall-clock time, and the two differ by an offset that changes twice a year.
`UTC` is the only value needing no zone table — push the conversion upstream
if you can.

**`fixture` must satisfy the same contract as the live source**, and it must
be deterministic. A fixture that changes between runs makes a failing
assertion impossible to attribute. It is what the worker uses when no
credential is configured, which is a stated demo path rather than a fallback
for a failed connection — an outage must never quietly become synthetic data
recorded as covered.

## 2. Write the worker

Mostly a declaration. Create `src/etl/workers/fx_rates_backfill.q`:

```q
/ fx_rates_backfill.q - the fx_rates bounded worker (.qfxbf).

\d .qfxbf

worker_name:`fx_rates_backfill

/ The contract's required globals (ETL-01, ETL-02), written by .qbw.init.
source_version:`;
range_from:0Np;
range_to:0Np;
handle:0Ni;
progress:`windows_completed`windows_failed`rows_published`cursor!(0;0;0;0Np);
last_batch:();

/ The contract's required methods, delegated to the shell.
spec:{[] .qbw.spec worker_name}
init:{[run_spec] .qbw.init[worker_name;run_spec]}
plan:{[cursor] .qbw.plan[worker_name;cursor]}
fetch:{[from_ts;to_ts] .qbw.fetch[worker_name;from_ts;to_ts]}
publish:{[batch] .qbw.publish[worker_name;batch]}
checkpoint:{[cursor] .qbw.checkpoint[worker_name;cursor]}
run:{[] .qbw.run worker_name}
cleanup:{[] .qbw.cleanup worker_name}

/ Refuse a batch that is shaped correctly but cannot be true.
quality_check:{[batch]
    if[0=count batch; :.qbw.no_failures[]];
    bad:select from batch where not mid>0;
    $[0=count bad;
      .qbw.no_failures[];
      ([] check:enlist `positive_mid;
          status:enlist `fail;
          detail:enlist "non-positive mid on ",string[count bad]," row(s)")]}

\d .

.qbw.define[`fx_rates_backfill;
    `ns`source`dataset`width`check!
    (`.qfxbf;`fx_rates;`fx_rates;1D;.qfxbf.quality_check)];
```

**The globals stay in the worker's namespace deliberately.** ETL-01 requires
`source_version`, `range_from` and `range_to` to be names in *this*
namespace, so that "is this worker complete" is a check rather than a
code-review question. Moving them into the shell would make every worker
pass the contract vacuously.

**The five methods are one line each, and they are still worth calling in a
test.** `.qbfstate.require_contract` checks they *exist*; nothing checks they
are wired correctly. A delegator with its arguments swapped —
`.qbw.fetch[worker;to_ts;from_ts]` — fetches a backwards window and passes
every test that never calls it.

**`check` is optional, and it runs between fetch and publish.** A batch that
fails is never published and its window is never recorded as covered, so the
next run plans it again. Make the conditions ones no correct row could meet —
a non-positive rate, a null key — rather than statistical outliers. A check
that fires on merely unusual data trains its reader to ignore it, and an
ignored check is worse than none because it still reads as protection.

`io` and `facts` are the other optional keys: an
[IO manager](../../src/etl/core/io_manager.q) to write somewhere other than
an in-memory table, and a function from the fetched batch to a dictionary of
labels recorded as materialisation metadata.

## 3. Register it

Add two lines to [`src/etl/init.q`](../../src/etl/init.q), sources before
workers:

```q
\l src/etl/sources/fx_rates.q
\l src/etl/workers/fx_rates_backfill.q
```

Order matters and is not obvious from the filenames — a declaration
registers itself on load, so the registry has to exist first. The file's own
header lists the couplings.

Nothing else needs registering. The job graph adopts the worker from its own
declaration:

```q
q).qdag.adopt_workers[];
q).qdag.declaration `fx_rates_backfill
kind   | `bounded
inputs | ,`fx_rates@fx_rates
outputs| ,`fx_rates
```

## 4. Run it

In a q session from the repository root:

```q
\l src/init.q
\l scripts/torq_pipeline.q
\l src/etl/init.q

.qfxbf.init[`source_version`range_from`range_to!(`v1;2026.09.11D00:00;2026.09.16D00:00)];
.qfxbf.run[]
```

`scripts/torq_pipeline.q` is easy to forget and the failure is obscure: it
defines `.qpipe`, which is where the status and lock directories come from,
and without it `init` dies inside `mkdir` on a path built from nothing.

The run reports:

```
state:             completed
windows completed: 5
rows published:    5
rows in target:    5
covered:           1
```

Five daily windows over a five-day range, each published and recorded.

As a process, which is what an orchestrator starts — the worker and its
range come from the environment, because a backfill that guessed a range
would publish the wrong window and record it as covered:

```
UQF_BACKFILL_WORKER=fx_rates_backfill \
UQF_BACKFILL_VERSION=v1 \
UQF_BACKFILL_FROM=2026.09.11D00:00 \
UQF_BACKFILL_TO=2026.09.16D00:00 \
  q scripts/torq_backfill.q
```

## 5. Check what it claims

Coverage is **recorded, not derived** — a completion event is staged for
every completed window, including an empty one. That is what makes "we ran
and there was nothing" distinguishable from "we never ran", and a derived
ledger cannot express the difference at all.

```q
q).qcov.is_covered[`fx_rates;`;`v1;.z.p;2026.09.11D00:00;2026.09.16D00:00]
1b
```

The `` ` `` is the partition, and it is required on every read for the
reason `source_version` is: an optional filter is one a caller forgets, and
forgetting this one reports a gap-ridden range as complete. `` ` `` means
"this dataset has no partition dimension" — see below.

Run it a second time and it is **idle**, not failed:

```q
q).qfxbf.run[][`state]
`idle
```

"Ran, found no work" is a success (C-07). An orchestrator that cannot tell
the two apart retries a successful no-op forever.

`.qcov.missing` narrows a range to what is still absent, `.qcov.history`
shows every claim ever made, and `.qcov.contributing_runs` says which
executions built it.

## 6. Test it

Add `tests/q/test_fx_rates_backfill.q`, register the file *and* its namespace
in [`tests/run_tests.q`](../../tests/run_tests.q) — the namespace list is
separate, and forgetting it means the tests silently never run — then:

```
scripts/test.py q-unit
```

Worth covering beyond the happy path, because each has bitten this tree:
a second run is idle; a version bump re-runs the whole range; a partial run
is narrowed to the gap; a middle gap is not bridged by a window spanning it;
a contract-breaking source records **no** coverage rather than publishing
nulls; a dry run publishes nothing; and each of the five contract methods
actually delegates.

`scripts/test.py coverage` will tell you which of those you missed.

## Filling one dataset with several workers

One worker per `(dataset, partition)` pair. Declare a `partition` and two
workers can fill one dataset at once:

```q
.qbw.define[`fx_rates_eurusd;
    `ns`source`dataset`width`partition!(`.qfxeur;`fx_rates;`fx_rates;1D;`EURUSD)];
```

Coverage is then recorded and read under that partition, and **no read unions
across partitions** — a range covered for `` `EURUSD `` says nothing about
`` `USDJPY ``. Declaring nothing gets the `` ` `` sentinel, which behaves
exactly as before the column existed.

Two workers on the same dataset *and* partition are still refused, for the
unchanged reason:

```
define: clash declares dataset fx_rates[EURUSD], already claimed by
fx_rates_eurusd - two workers on one dataset and partition produce coverage
rows nothing can tell apart
```

## What you did not have to write

Windowing, resumption from a checkpoint, skipping what is already published,
retry with backoff that distinguishes transport from data failures, the
dry-run gate, coverage staged only after the publication it describes,
single-instance locking, heartbeats, structured logs, and a node in the job
graph. All of it is `.qbw` and `.qwrt`, and all of it is the same for every
worker — which is the point.
