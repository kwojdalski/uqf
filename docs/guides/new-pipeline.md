# Adding a data pipeline

How to take an external source of rows and land it in a local table, with
completeness you can query and a bound you can see. The worked example below is
a real one: every command was run against this tree, and the output shown is
what it printed.

A pipeline here is **three declarations**, and nothing that registers them. The
lifecycle --- windowing, retries, coverage, checkpoints, dry-run, the job graph ---
is the shell's, and you do not write any of it. If you find yourself writing a
loop over days, you are rebuilding `.qbw`.

  | You write                          | It says                                                                  |
  | ---                                | ---                                                                      |
  | a **source** in `src/etl/sources/` | what the rows are, where they come from, how to window them              |
  | a **transform**, beside the worker | what a fetched batch becomes before it is published, with example tables |
  | a **worker** in `src/etl/workers/` | which source, which transform, which target dataset, how wide a window   |

[`src/etl/init.q`](../../src/etl/init.q) **globs** those three directories, so
there is no fourth row: a declaration loads because its file exists. It used to
be a `\l` line per file --- twenty-six of them, a hand-kept copy of `ls` whose
failure mode was a file nobody loaded.

Everything else follows from those. Why it is shaped this way is [the pipeline
philosophy](../architecture/pipeline-philosophy.md). Every key each declaration
accepts, and what it refuses, is in [the declaration
reference](../reference/pipeline-declarations.md).

## Scaffolding it

`uqs new-job` writes the skeleton: the q files, the table definition and a test.
There is no registry entry - the process is read from the job's own declaration.

<!-- Source: docs/diagrams/scaffolding.d2. Rendered by
     scripts/generate/render_diagrams.py, which CI runs with --check. -->

![What uqs new-job writes, in five bands: the plan, the files it creates, the three files it appends to, what globs each one up afterwards, and the handler and test left deliberately red](../diagrams/scaffolding.svg)

Read it top to bottom. The **appends** are the whole reason the middle band
exists: everything else is picked up by a glob, and those files hold the facts
the tree cannot derive from itself --- the table definition, `nsList`, the one
hand-kept list of test namespaces, and `expected` in
[`tests/q/test_stack_tables.q`](../../tests/q/test_stack_tables.q), the gate
every new table passes through. After writing them, `new-job` reruns
`scripts/generate/generate_operational_docs.py`, so `processes.md`,
`src/etl/generated/pipeline_dag.q` and the port lock never lag the job it just
declared.

```
uqs new-job markout2 --subscribe-to trades,quote \
    --publishes my_metric --columns "sym:symbol, value:float"

uqs new-job fx_rates --kind backfill --dataset fx_rates \
    --columns "sym:symbol, mid:float" --width 1D

uqs new-job fx_rates_1h --kind backfill --dataset fx_rates_1h \
    --source fx_rates --columns "sym:symbol, mid:float" --width 0D01
```

The third is a second worker over the second's source: a source that already
exists is reused rather than rewritten, and a table that already exists is not
defined again. `--columns` is required whenever a new source or a new table is
written, and refused when neither is. Its dataset is its own because
`.qbw.define` refuses two workers on one dataset and partition - their coverage
would compose, and a range full of gaps would read as complete - so `new-job`
refuses a dataset another worker already fills without a partition.

A streaming job follows the same rule for what it publishes: `--publishes` takes
a comma list, a table the plant already defines is published onto without
`--columns`, and `--columns` shapes the one new table. Its `--subscribe-to` must
name tables the plant defines, so a producer is scaffolded before its consumer.

`--dry-run` prints what it would write and writes nothing. `kind` is derived for
a streaming job, and the new process's port is appended to the port lock, so no
existing process moves.

The scaffold itself --- what each shape gets, why the handler throws, and the
one generated body that does *not* throw --- is
[`scaffolding/`](../scaffolding/README.md), a page per shape. This guide picks
up where that leaves off, so what it needs from there is just the list of what a
fresh scaffold leaves red.

### What a fresh scaffold leaves red

The handler throws and the test fails, deliberately. For a job that subscribes
and publishes, its scaffolded test also carries a `contract_driver` that throws
until written - the batch
[`tests/q/test_job_output_contracts.q`](../../tests/q/test_job_output_contracts.q)
drives the job with to hold every table it publishes to its plant table:

```
uqs new-job dxprobe --subscribe-to trades --publishes dx_t --columns "sym:symbol, v:float"
q tests/run_tests.q
```

reports `.dxprobetest.test_dxprobe_is_implemented`, and the `.jobouttest` tests
that drive every publishing job fail on the throwing driver.

Nothing else in the q suite needs an edit. `test_every_job_is_registered`
derives its jobs from `src/etl/streaming/` (#352), and
`test_every_registered_job_has_a_file` holds the other direction: a job that
registers anywhere other than its own file in that directory fails it. The
scaffold adds a new table to `expected` in `test_stack_tables.q`. That list
stays a **deliberate gate** --- a new table is either a capability nobody wired
up or a stray definition --- and the scaffold passes it by defining the table
and naming its owner in the same plan. It also registers your test's NAMESPACE
in `run_tests.q` (#350); without that the stub loaded and never ran, so the one
red the scaffold exists to leave was the one you could not see.

`uv run pytest python/uqs` fails twice:

1. `test_no_scaffold_left.py` lists, by `path:line`, every placeholder still
   marked `SCAFFOLDED` - the handler, the test, the driver, the job's `note`,
   and for a new table its desk catalog description. That list is the to-do
   list; each line clears when its placeholder is replaced and the marker
   deleted with it.
2. `test_the_prose_architecture_doc_is_consistent_with_the_registry` asks that
   [`docs/architecture/stack.md`](../architecture/stack.md) name the new process -
   authored prose, so the one step with no placeholder.

A new table's desk catalog entry is written for you: a SCAFFOLDED line in
[`uqs_catalog.q`](../../scripts/processes/uqs_catalog.q). The description is
yours: it is read by someone deciding whether your table is the one they want,
and the table's own name there would pass every test and tell them nothing. If
the desk should not see the table, delete that line and add the table to
`.qcat.hidden` with the reason --- `tests/q/test_catalog.q` refuses a published
table that is in neither list, so "we forgot" cannot pass as "deliberately
hidden".

Only the prose is written. The columns and their types are `meta`'s answer on a
running process, not a copy anybody maintains, so a column type with no
equivalent in the front end no longer blocks the catalog step.

Everything that IS derived - `processes.md`, `src/etl/generated/pipeline_dag.q`
and `docs/man.q` - it regenerates before it returns.

The rest of this guide is what to write into that skeleton, and why each part is
shaped the way it is.

## Before you start: is it bounded or continuous?

Two different shells. Which one a job wants is
[`scaffolding/`](../scaffolding/README.md)'s opening question, asked there over
all four shapes; what follows here is what the two shells actually are, which is
what you need before writing into either.

<!-- Source: docs/diagrams/pipeline-decision.d2. Rendered by
     scripts/generate/render_diagrams.py, which CI runs with --check. -->

![A decision tree: known range or not chooses the bounded worker or the streaming shell; whether it reads another table chooses a feed or an etl; whether it publishes a new table decides if columns must be declared; every path ends at the same new-job command and the same three remaining steps](../diagrams/pipeline-decision.svg)

Three questions, and only the first is hard to change afterwards --- the other
two are flags on one command, and the kinds below them are derived from the
edges rather than asked for. What is **not** on the tree is the part this guide
is about: the transform, the check, the io manager and the partition are
declarations you write *inside* the file the scaffold gives you, not choices
about which file to make.

**Bounded** --- you know the range before you start: a backfill, a nightly
window, a restatement. It runs, it finishes, it exits. `.qbw`, and the rest of
this guide.

**Continuous** --- it subscribes and never finishes: a tickerplant feed, a
poller. `.qcont` in
[`src/etl/core/continuous_state.q`](../../src/etl/core/continuous_state.q),
whose state is a cursor rather than a range.

Both have a transform, and both are **one file per job**. A continuous job is a
file under [`src/etl/streaming/`](../../src/etl/streaming) holding every step ---
schemas, transform, batch handler, timer body, its own buffers --- and a
`.qstream.define` call naming the tables it subscribes to, the tables it
publishes and the TorQ process that runs it. A **feed** is the same thing with
no subscription: it declares a `period` and an `on_timer` that builds rows and
publishes them. One generic process script,
[`scripts/processes/torq_stream.q`](../../scripts/processes/torq_stream.q), runs
whichever job the process it was started as claims.

A job never calls TorQ: it calls `publish` in its own namespace, which the
runner wires to the tickerplant and a test wires to a recorder
(`tests/q/test_stream_job.q`). That seam is what lets the whole job --- not just
its transform --- be loaded and driven in a plain q process.

**Every table you publish must exist on the plant.** `.u.upd` onto a table the
tickerplant does not define discards the rows *silently* --- no error, no
warning, just a table that stays empty while the job reports healthy. This is
not hypothetical: `fxpositions1` published a correct sixteen-row book onto
`fx_position` and `fx_limit_breach` every five seconds, and neither table
existed. `database.q` is generated from each pipeline's `publishes` rather than
from a single `schema` field, precisely because a job can publish two tables and
own neither, and `.qpipe.assert_publishable` makes a process refuse to start
when the plant has no table for something it declares. So the failure is now
loud at startup instead of silent forever --- but only for what the job
*declares*, which is one more reason the register call has to name every table
the job actually publishes.

**Normalizer** --- a continuous job of one particular shape: several tables
carrying the same fact in different spellings, one canonical table out. `.qnorm`
in [`src/etl/core/normalizer.q`](../../src/etl/core/normalizer.q). An instance
declares its output and one `.qxf` transform per source, and the shell owns the
rest --- it dispatches on the table a batch arrived on, projects the batch onto
the columns that source's transform declares, applies it, and publishes. It also
performs the `.qstream.define` itself, so the job's edges cannot disagree with
its mappings, and it refuses at `define` any mapping whose declared output
drifts from the canonical table, column, type and order. Two ship: `executions`
(`trades` + `crypto_trades`) and `marks` (`quote` + `crypto_book`), which is how
`posbook1` holds FX and crypto positions in one book without knowing either
market's tape format. A third market is a mapping in a normalizer, not a branch
in a consumer.
`uqs new-job NAME --kind normalizer --subscribe-to a,b --columns ...` scaffolds
one: the canonical table NAME, and per source its schema, a throwing mapping and
a typed example row, so the file loads while each mapping stays red.

```q
.qnorm.define[`executions;`procname`output`input!(
    `executions1;
    .qsub.executions.executions;
    `trades`crypto_trades!`executions_from_trades`executions_from_crypto_trades)];
```

The rest of this guide is the bounded case.

## 1. Declare the source

A source declares its **shape**, not its plumbing. Create
`src/etl/sources/fx_rates.q`:

```q
/ fx_rates.q - an external reference-rate source (.qfeed.fx_rates).

\d .qfeed.fx_rates

source_name:`fx_rates
columns:`rate_time`sym`mid    / the columns this adapter reads
types:"psf"                    / one q type character per field
target:`fx_rates               / the local table they land in
time_column:`rate_time         / the column the window is taken on
row_key:`rate_time`sym         / what identifies a row uniquely
tz:`UTC                        / what time_column is expressed in

query:{[h;range_from;range_to]
    h({[from_ts;to_ts]
        select rate_time, sym, mid from `fx_rates
            where rate_time>=from_ts, rate_time<to_ts
      };range_from;range_to)}

fixture:{[]
    ([] rate_time:2026.09.11D09:00:00.000000000+1D*til 5;
        sym:`EURUSD`GBPUSD`EURUSD`USDJPY`EURUSD;
        mid:1.0842 1.2631 1.0847 149.82 1.0851)}

.qsrc.define[source_name;
    `source`table_name`target`time_column`row_key`columns`types`query`fixture`tz!
    (source_name;`fx_rates;target;time_column;row_key;columns;types;query;fixture;tz)];

\d .
```

Five of those deserve a sentence each, because each is a decision rather than a
formality.

**The namespace is `.qfeed.<source name>`, and it is the same word twice.**
Sources live under one `.qfeed` root and are named exactly as they register, so
`\d .qfeed.fx_rates` goes with
`source_name:`fx_rates` and nothing else — `test_source_contract.q` reads every file under `src/etl/sources/` and fails if the two disagree. Before the root, the namespace was an abbreviation (`.qsdemo` for `demo_deals`) that no check compared with anything. Workers do the same thing under `.qwrk\`.

**`columns` is what you READ, not everything the source has.** Declaring a
column the worker never touches means an upstream change to an unused column
breaks the run.

**`query` is a parameterised lambda, never string concatenation.** The bounds
are arguments to a functional select evaluated on the remote side, so no caller
value is ever spliced into query text.
`"select ... where t>=",string range_from` is how a crafted value becomes an
injection, and how a type coercion becomes a silently wrong window rather than
an error. Where a driver cannot parameterise --- ODBC --- there is exactly one
escape function, `.qodbc.literal`, and everything goes through it.

**The window is half-open `[from;to)`** --- `>=` on the lower bound and `<` on
the upper. One wrong operator double-publishes every boundary row, and the
duplicate surfaces far from here.

**`row_key` is only correct if the source guarantees uniqueness.** A source that
reuses ids after a purge silently merges unrelated rows. It is declared per
source for that reason.

**`tz` is a claim, not a default.** An unstated zone is the shape of the bug:
every later reader assumes UTC while the source hands over local wall-clock
time, and the two differ by an offset that changes twice a year. `UTC` is the
only value needing no zone table --- push the conversion upstream if you can.

**`fixture` must satisfy the same contract as the live source**, and it must be
deterministic. A fixture that changes between runs makes a failing assertion
impossible to attribute. It is what the worker uses when no credential is
configured, which is a stated demo path rather than a fallback for a failed
connection --- an outage must never quietly become synthetic data recorded as
covered.

## 2. Write the worker

Mostly a declaration. Create `src/etl/workers/fx_rates_backfill.q`:

```q
/ fx_rates_backfill.q - the fx_rates bounded worker (.qwrk.fx_rates_backfill).

\d .qwrk.fx_rates_backfill

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

/ The transform: fetched rows in, published rows out, and an example of each.
.qxf.define[`fx_rates_pips;`inputs`output`fn`examples!(
    enlist[`batch]!enlist ([] rate_time:`timestamp$(); sym:`symbol$(); mid:`float$());
    ([] rate_time:`timestamp$(); sym:`symbol$(); mid:`float$(); pip_factor:`long$());
    {[batch] update pip_factor:?[sym like "*JPY";100;10000] from batch};
    enlist `inputs`expected!(
        enlist[`batch]!enlist ([] rate_time:2026.09.11D09:00 2026.09.12D09:00; sym:`EURUSD`USDJPY; mid:1.0842 149.82);
        ([] rate_time:2026.09.11D09:00 2026.09.12D09:00; sym:`EURUSD`USDJPY; mid:1.0842 149.82; pip_factor:10000 100)))];

.qbw.define[`fx_rates_backfill;
    `source`dataset`width`transform`check!
    (`fx_rates;`fx_rates;1D;`fx_rates_pips;.qwrk.fx_rates_backfill.quality_check)];
```

**The namespace is `.qwrk.<worker name>`, and you do not choose it.** Every
worker instance lives under the one `.qwrk` root, named exactly as it is
registered, and `.qbw.define` derives the namespace from the worker name --- a
supplied `ns` key is refused. So
`key `.qwrk` lists every loaded worker, and a worker has one name rather than a name and an abbreviation to keep in step. The library's own modules stay flat (`.qbw`, `.qmatz`, `.qsrc\`);
the nesting marks the line between the framework and what runs on it.

**The contract's names are stamped by `define`, not written by you.** The
lifecycle contract requires `source_version`, `range_from` and `range_to` to be
names in *this* namespace, so that "is this worker complete" is a check rather
than a code-review question --- and `.qbw.define` writes them there, along with
`handle`, the run accumulators, and the eight methods (`init`, `plan`, `fetch`,
`publish`, `checkpoint`, `spec`, `run`, `cleanup`), each a one-line delegate to
the shell with the shell's own parameter names:

```q
q).qwrk.fx_rates_backfill.fetch
{[from_ts;to_ts] .qbw.fetch[`fx_rates_backfill;from_ts;to_ts]}
```

Until #227 every worker file carried that block by hand. The method list comes
from `.qbfstate.bounded_worker_methods`, so a method added to the contract
reaches every worker without any file being edited.

**To override a method, define it before the `define` call.** `define` fills
only the names the namespace does not already have, and `.qbw.run` reaches
`plan`, `fetch` and `publish` through the worker's namespace rather than calling
its own --- so a worker with a genuinely different publish path writes
`publish:{[batch] ...}` above its `define` and the run loop uses it. An override
that wants the default for part of its work calls the shell by its full name,
`.qbw.publish[`fx_rates_backfill;batch\]`. Tests should call an override through `.qwrk.fx_rates_backfill.run\[\]\`,
not only directly: the first version of the shell honoured overrides from the
prompt and from nowhere else.

**`transform` is required, because it is the job.** It runs between fetch and
the check, so the check and the target both see its output. Its one input must
be the source's `columns` and `types` exactly, and `define` refuses a transform
written against any other shape. The expected table is written by hand:
`tests/q/test_transform.q` runs every registered transform's examples on every
build, calls each twice to catch a clock or a random draw in the output, and
feeds each an empty batch. A job that publishes what it fetched declares
`.qxf.passthrough` rather than leaving the step out.

**`check` is optional, and it runs between the transform and publish.** A batch
that fails is never published and its window is never recorded as covered, so
the next run plans it again. Make the conditions ones no correct row could meet ---
a non-positive rate, a null key --- rather than statistical outliers. A check
that fires on merely unusual data trains its reader to ignore it, and an ignored
check is worse than none because it still reads as protection.

`io` and `facts` are the other optional keys: an [IO
manager](../../src/etl/core/io_manager.q) to write somewhere other than an
in-memory table, and a function from the transformed batch to a dictionary of
labels recorded as materialisation metadata.

## 3. Register it

**Nothing, for the load.** `src/etl/init.q` globs `sources/`, `workers/` and
`streaming/`, so a declaration file is loaded the moment it exists. It used to
list all twenty-six by hand, in the right place.

Two orderings still hold, and the file explains both: directories load sources
before workers, because `.qbw.define` looks its source up at define time; and
within `streaming/` the two jobs that read another job's table at load time are
named in a `lead` list. Add a file that does the same and you will get a bare
`` `.qsub.<name> `` on load --- put it in that list.

A test file needs no registration either. `tests/run_tests.q` globs
`tests/q/test_*.q` and derives its namespace list from what actually loaded. It
used to keep two hand-written lists, and forgetting the second one was silent:
the file loaded, its tests never ran, and the suite stayed green.

The job graph adopts the worker from its own declaration:

```q
q).qdag.adopt_workers[];
q).qdag.def `fx_rates_backfill
kind   | `bounded
inputs | ,`fx_rates@fx_rates
outputs| ,`fx_rates
```

### Its process comes from the declaration

There is no registration to add. The uqs process registry is READ from the q
declarations - every `.qstream.define`/`.qnorm.define` under
`src/etl/streaming/` and every `.qbw.define` under `src/etl/workers/` - by
[`model/declarations.py`](../../python/uqs/src/uqs/model/declarations.py). It
used to be a hand-kept Python list restating each one, which made a new job two
edits in two languages and let a fully declared worker sit with no process to
run it, invisible to every grep.

A worker's declaration names its process, and may say why it exists:

```q
.qbw.define[`fx_rates_backfill;
    `source`dataset`width`transform`procname`note!
    (`fx_rates;`fx_rates;1D;`fx_rates_passthrough;
     `fx_rates_backfill1;
     "bounded: reads the vendor's daily fixings over ODBC")];
```

`procname` defaults to `<worker>1` when absent, in q and in the registry alike.
A backfill never starts with the stack - it registers with discovery, runs its
range and exits - so a worker has no `start_with_all`.

A streaming job already names its `procname`, `subscribe_to` and `publishes`,
and those are its process's edges. Two more keys are optional:

  | key              | means                                                | absent    |
  | ---              | ---                                                  | ---       |
  | `start_with_all` | `1b` to start with the stack                         | on demand |
  | `note`           | why it is deployed as it is, shown in `processes.md` | no note   |

Default on demand, because joining the default start spends one of the plant's
sixteen licensed connections (#285) - a decision to make on purpose.

**Ports** are the one fact no declaration can supply, because a port has to
survive other processes being added around it. Each process's offset lives in
[`scripts/processes/process_ports.csv`](../../scripts/processes/process_ports.csv),
a generated, append-only lock: a process not yet in it gets the next free
offset, and `generate_operational_docs.py` (which `new-job` runs) writes it
down. A retired process keeps its row, so its offset is never reused, and
`--check` fails in CI on a process the lock lacks.

## 4. Run it

In a q session from the repository root:

```q
\l src/init.q
\l scripts/processes/torq_pipeline.q
\l src/etl/init.q

.qwrk.fx_rates_backfill.init[`source_version`range_from`range_to!(`v1;2026.09.11D00:00;2026.09.16D00:00)];
.qwrk.fx_rates_backfill.run[]
```

`scripts/processes/torq_pipeline.q` is easy to forget and the failure is
obscure: it defines `.qpipe`, which is where the status and lock directories
come from, and without it `init` dies inside `mkdir` on a path built from
nothing.

The run reports:

```
state:             completed
windows completed: 5
rows published:    5
rows in target:    5
covered:           1
```

Five daily windows over a five-day range, each published and recorded.

As a process, which is what an orchestrator starts --- the worker, its source
version and its range are all required flags, because a backfill that guessed a
range would publish the wrong window and record it as covered:

```
uqs backfill fx_rates_backfill --version v1 --from 2026-09-11 --to 2026-09-16
```

## 5. Check what it claims

Coverage is **recorded, not derived** --- a completion event is staged for every
completed window, including an empty one. That is what makes "we ran and there
was nothing" distinguishable from "we never ran", and a derived ledger cannot
express the difference at all.

```q
q).qmatz.is_covered[`fx_rates;`;`v1;.z.p;2026.09.11D00:00;2026.09.16D00:00]
1b
```

The `` ` `` is the partition, and it is required on every read for the reason
`source_version` is: an optional filter is one a caller forgets, and forgetting
this one reports a gap-ridden range as complete. `` ` `` means "this dataset has
no partition dimension" --- see below.

Run it a second time and it is **idle**, not failed:

```q
q).qwrk.fx_rates_backfill.run[][`state]
`idle
```

"Ran, found no work" is a success. An orchestrator that cannot tell the two
apart retries a successful no-op forever.

`.qmatz.missing` narrows a range to what is still absent, `.qmatz.history` shows
every claim ever made, and `.qmatz.contributing_runs` says which executions
built it.

## 6. Test it

Add `tests/q/test_fx_rates_backfill.q`. The file itself needs no registration ---
`tests/run_tests.q` globs `tests/q/test_*.q` --- but its NAMESPACE does: add
`.<name>test` to that file's `nsList`. Forgetting it used to mean the suite
loaded your tests and silently never ran them;
`test_the_runner_runs_every_suite_it_loads` now fails instead, naming the
namespace that is missing. Then:

```
scripts/test.py q-unit
```

Worth covering beyond the happy path, because each has bitten this tree: a
second run is idle; a version bump re-runs the whole range; a partial run is
narrowed to the gap; a middle gap is not bridged by a window spanning it; a
contract-breaking source records **no** coverage rather than publishing nulls; a
dry run publishes nothing; and each of the five contract methods actually
delegates.

`scripts/test.py coverage` will tell you which of those you missed.

None of that runs your `query`. The unit suite never sets a credential, so a
worker there runs on its fixture, and the query lambda is never sent anywhere.
To see it run against a real second process, follow
[`tests/q/run_two_instances.q`](../../tests/q/run_two_instances.q): start a
plain q process holding the upstream table, set
`UQF_SOURCE_CRED_<SOURCE>=host:port`, and run the worker
(`scripts/test.py q-two-instances` does exactly this for `upstream_trades`). One
trap that only shows up there: write `` from `trade ``, never `from trade`. The
lambda carries your `\d .qfeed.fx_rates` across the wire, so a bare name
resolves in that namespace on the remote and throws; `test_source_contract.q`
refuses it.

## Recomputing a table when the one it reads is published

A published window announces itself. Register a reaction and it runs, with the
range that was just published, as soon as the window is recorded:

```q
`positions set ([sym:`symbol$(); window:`timestamp$()] notional:`float$());

.qreact.on[`demo_deals;`rebuild_positions;{[ds;range_from;range_to]
    / recompute exactly what changed - the range is handed to you
    `positions upsert select sum notional by sym, window:range_from from
        select from demo_deals where deal_time within (range_from;range_to-1)
    }];
```

Run against the five-day demo range, that fills itself as each window publishes,
with nothing calling it by hand:

```
run:  `state`windows_completed`rows_published!(`completed;5;5)

sym    window                       | notional
------------------------------------| --------
EURUSD 2026.09.11D00:00:00.000000000| 1000000
GBPUSD 2026.09.12D00:00:00.000000000| 2500000
EURUSD 2026.09.13D00:00:00.000000000| 750000
USDJPY 2026.09.14D00:00:00.000000000| 3000000
EURUSD 2026.09.15D00:00:00.000000000| 1250000
```

**Key the derived rows by the window, not only by `sym`.** The handler is called
once per window with that window's range, so a derived row keyed on `sym` alone
is overwritten by the next window rather than added to - EURUSD's three deals
would read as its last one. Keyed by the window each contribution is stored
once, the total is `select sum notional by sym from positions`, and
re-publishing a window replaces its own row instead of double counting. That
last property is what makes a restatement safe.

Nothing polls, and nothing is missed: `.qbw.do_window` fires the event after
`finish_window` records the materialisation, so the reaction sees a ledger that
already includes the window it is being told about. A dry run publishes nothing
and therefore announces nothing.

**Who should react is derivable; what they should do is not.** `.qdag` already
knows which jobs read a dataset ---
`.qreact.dag_consumers[`demo_deals\]` names them — but running a downstream worker needs a `source_version`, which is a decision about which release of the upstream data the run claims. No framework can invent one, so the graph tells you who to wire and the handler says what running means. `.qreact.audit\[\]\`
lists graph edges with no reaction behind them, and reactions on datasets the
graph does not know.

**Three things the dispatcher guarantees**, each because the alternative fails
quietly rather than loudly:

- **A cascade is a loop, not recursion.** A handler that publishes notifies from
  inside the first notification; that work is queued and drained by the call
  already draining. A chain cannot grow the stack.
- **A failing reaction never fails the publication.** The rows are written and
  the coverage staged before any handler runs, so a downstream bug cannot turn a
  successful materialisation into a failed one. Failures land in
  `.qreact.history` and the log.
- **A cascade terminates.** The same `(dataset, range)` is dispatched at most
  once per drain, so `a -> b -> a` settles; `.qreact.max_depth` bounds a chain
  that keeps inventing new ranges.

### Reactions are nodes in the job graph

`.qdag.adopt_all[]` picks up reactions alongside workers, feeders and the
streaming processes, so one graph covers the whole system. A reaction's
**input** is the dataset it watches --- that is a fact, it is what fires it. Its
**output** is whatever it declared, and the three ways of registering one differ
in exactly that:

  |                      | Output                                        | In the graph as                                                        |
  | ---                  | ---                                           | ---                                                                    |
  | `.qreact.on`         | none                                          | a terminal node — reads the dataset, says nothing about what it writes |
  | `.qreact.on_writing` | **asserted** by you                           | a full node, listed in ```audit[]``asserted```                         |
  | `.qreact.on_worker`  | **derived** from the worker's own declaration | a full node that cannot disagree with what the worker does             |

Prefer `on_worker` where it applies: the worker already declares its target
through its source, so nothing is restated and `dag.q`'s "derive, never
re-declare" rule survives. `on_writing` is for a handler that writes something
no worker owns --- worth having, because it puts the edge in the graph, but it
is a claim about an opaque lambda rather than a checked fact, and
`.qreact.audit[]` lists those separately so a drawing can mark them.

**The payoff is that a reactive cycle is refused when you wire it**, not when it
runs. Before reactions were in the graph, `a → b → a` survived until the
per-drain guard and `max_depth` stopped it mid-cascade; now:

```
q).qdag.topological[]
'topological: cycle among a~to_b, b~to_a
```

A reaction node is named `<dataset>~<reaction>`, because a reaction name is
unique per dataset rather than globally. Build that name with
`.qdag.reaction_job[dataset;name]` rather than typing it: `~` cannot appear in a
q symbol literal, so `` `demo_deals~rebuild `` parses as a *match* against a
variable called `rebuild` and fails with a value error naming that variable
instead of anything about the graph.

**When a timer is still right.** This answers "recompute because data arrived".
It cannot answer "recompute because time passed" --- `markout1` scores a fill
once a quote at its horizon should exist, and no publication event can tell it
that. `.qpipe.safe_timer` remains the tool for that question.

## Filling one dataset with several workers

One worker per `(dataset, partition)` pair. Declare a `partition` and two
workers can fill one dataset at once:

```q
.qbw.define[`fx_rates_eurusd;
    `source`dataset`width`transform`partition!(`fx_rates;`fx_rates;1D;`fx_rates_pips;`EURUSD)];
```

Coverage is then recorded and read under that partition, and **no read unions
across partitions** --- a range covered for `` `EURUSD `` says nothing about
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
retry with backoff that distinguishes transport from data failures, the dry-run
gate, coverage staged only after the publication it describes, single-instance
locking, heartbeats, structured logs, and a node in the job graph. All of it is
`.qbw` and `.qwrt`, and all of it is the same for every worker --- which is the
point.
