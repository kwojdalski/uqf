# FAQ

Short answers, each pointing to the page that has the long one.

## How is this different from plain TorQ?

TorQ is not replaced. The tickerplant, the RDB/WDB/HDB, discovery and the
gateway are TorQ's, vendored under `lib/torq` and never edited. uqf changes what
sits *around* those processes:

  | Question               | Plain TorQ                                                                    | uqf on TorQ                                                                                                                                    |
  | ---                    | ---                                                                           | ---                                                                                                                                            |
  | A unit of work         | a process: a `process.csv` row naming its own q script                        | a job: a declaration in `src/etl/` (`.qstream.define`, `.qbw.define`), run by one generic runner script picked by process name                 |
  | Configuration          | edit `process.csv` and the config directories                                 | `lib/torq` is never edited; `process.csv` and `database.q` are regenerated on every `uqs` command from the declarations and `uqs_tables.q`     |
  | Who knows TorQ exists  | every process - code is written against `.servers`, `.u.upd`, `.lg`           | only `.qpipe`, in `scripts/`; a gate fails the build if anything under `src/` reaches for it                                                   |
  | History and backfills  | replay a tickerplant log, or write your own loader                            | bounded workers (`.qbw`): a window range, written through `.qio`, recorded in a coverage ledger (`.qmatz`) so a re-run redoes only gaps        |
  | A table on the plant   | define it in `database.q`; `.u.upd` onto an undefined one drops rows silently | defined from what jobs declare they publish; a job refuses to start when a table it publishes is missing                                       |
  | Adding one             | write the script, add the row, pick a free port                               | `uqs new-job` scaffolds it; no registration step - the port, the process row and the job graph are derived                                     |
  | Operating it           | `torq.sh start`, `stop`, `summary`, per process                               | `uqs`: start profiles held to the licence's connection budget, `up`, `logs`, `conn`, `summary`; `.qlog` adds DBG and key=value fields to `.lg` |

The slide version is [the deck](presentation/uqf.qmd); why the framework is
shaped this way is [the pipeline
philosophy](architecture/pipeline-philosophy.md).

## Do I need TorQ, or a running stack, to use any of this?

No, for most of it. Nothing under `src/` loads TorQ: the quant library and the
ETL framework load in a plain q session (`\l src/init.q`, then
`\l src/etl/init.q`), and `q tests/run_tests.q` runs the whole unit suite with
no stack. The streaming jobs themselves run on stock kdb+ through
`scripts/processes/run_stream.q`, which stands up a bare tickerplant and wires a
job to it. TorQ is what the *deployed* stack runs on.

## Do I need KDB-X?

KDB-X is what everything here is verified against; its personal edition is free
for non-commercial use. Other q implementations may work for the pure-q modules
in `src/`, but none is verified - see the README's
[Requirements](../README.md#requirements).

## Why does a job call `publish` rather than `.u.upd`?

Because `.u.upd` is the tickerplant's own function, and a job should not know
where its rows go. `publish` starts as a stub that throws; the runner wires it
to `.qpipe.publish`, which calls `.u.upd` over a handle and fixes what the plant
gets wrong (it drops a `time` column the plant would stamp twice, unkeys keyed
tables, enlists single rows); a test wires it to a recorder instead. See [the
etl scaffolding page](scaffolding/etl.md#rules).

## How do I add a job?

`uqs new-job`, then implement what it leaves failing. There is no list to add it
to: `src/etl/init.q` loads every declaration file it finds, the process registry
is read from the declarations, and the port is appended to
`scripts/processes/process_ports.csv`. [Scaffolding](scaffolding/README.md) has
one page per shape; [adding a pipeline](guides/new-pipeline.md) walks a bounded
worker end to end; every key a declaration takes is in [the declaration
reference](reference/pipeline-declarations.md).

## My process is `up` but its table stays empty. Why?

Usually because nothing publishes what it subscribes to. A job started without
its producers subscribes successfully, heartbeats, and receives nothing - no
error anywhere. Start it through a profile, or start its producers with it:
naming a leaf in a profile pulls in everything it reads. See [running the
stack](guides/uqs.md).

## How do I debug a job that misbehaves?

Work from the outside in. Each step answers a narrower question than the one
before, and most problems stop at the first two.

**1. Is it running, and is it wired to anything?** `uqs summary` shows every
process's status next to what it subscribes to, what it publishes, and which
processes it therefore needs running. A job that is `up` but idle is usually a
job whose inputs nobody is producing, and this column says so.

**2. What does its log say?** `uqs logs <process> -f` follows it
(`uqs multitail <process>` gives one pane per log file, and `uqs up <process>`
starts it and streams its log in the foreground). Three lines tell you where
rows stop:

  | Last line you see             | Means                                                            |
  | ---                           | ---                                                              |
  | `waiting for the tickerplant` | the plant is not up, and the job is blocked until it is          |
  | no `first batch received`     | the job subscribed but nothing arrives - its producer is missing |
  | no `first rows published`     | batches arrive, but the handler publishes nothing                |

**3. Turn on DBG for that one process.** Without a restart:
`uqs query ".qlog.debug 1b" --port <port>`. A backfill takes `--debug`
(`uqs backfill <worker> --debug ...`), which starts it with `-verbose`. DBG is
per process on purpose: switched on fleet-wide, it buries the one worker you are
looking at.

**4. Look inside the running process.** `uqs conn <process>` opens a qcon
session on it by name. From there,
`.qstream.def `<job>` is its declaration, `.qsub.<job>` holds its state, and `.qpipe.received` / `.qpipe.published\`
count the rows in and out per table.

**5. Reproduce it without the stack.** Load the tree in plain q
(`\l src/init.q`, `\l src/etl/init.q`), point the job's `publish` at a recorder
with `.qstream.wire`, and call `.qsub.<job>.on_batch` with a batch you build -
the pattern in [the etl scaffolding
page](scaffolding/etl.md#testing-it-without-a-stack). To run it as a real
process on stock kdb+, use `scripts/processes/run_stream.q`.

**A backfill** has exited by the time you look, so read what it left on disk. In
q, after loading the tree: `.qmatz.attach[]` then `.qmatz.ledger[]` for what was
covered, `.qrun.attach[]` then `.qrun.history[]` for each run and its outcome,
and `.qhb.report[]` for heartbeats. All three live in the status directory,
`$UQFSTATUSDIR`.

**Query errors from the HDB** such as
`./2026.01.07/arbitrage. OS reports: No such file or directory` mean a partition
is missing a table or column: `uqs hdb-check` names which.

**Before tracing q by hand**, run
[`qlinter`](https://github.com/kwojdalski/q-lint) on the file. Several of this
tree's most expensive bugs are patterns it checks for, such as a builtin used as
a parameter name, or a line holding only `/`.

## Where do a backfill's rows end up?

In the HDB, in the partition of each row's own date - not through the
tickerplant. A backfill started by `uqs backfill` writes through `.qio.hdb`: it
refuses rows dated today or later (the tickerplant's and end-of-day's), gives
each row a `time` from its own time column, and appends it to
`<hdb>/<date>/<table>/`. At the end of the run each partition it touched is
sorted with `p#sym`, and the running HDB is asked to reload. The tickerplant
would stamp old rows with today's time, file them under today's date, and hand
them to every subscriber as if they had just happened. In plain q - a test, or a
prompt - the same worker writes to an in-memory table instead. See
[`io_manager.q`](../src/etl/core/io_manager.q).

## How is a backfill job different from TorQ's dataloader?

TorQ ships a generic loader, `.loader.loadallfiles` in
`lib/torq/code/common/dataloader.q`. It reads a directory of delimited files in
chunks (`.Q.fsn`), optionally passes each chunk through a `dataprocessfunc`,
enumerates it, and appends it to the date partitions picked by a `partitioncol`.
At the end it re-sorts each partition and sets attributes through
`.sort.sorttab`, which reads TorQ's `sort.csv`. It is a good tool for what it
does: a bulk, one-off load of flat files. uqf does not use it.

A bounded worker (`.qbw`) writes into the same kind of partitions but answers a
different question: not "load these files" but "make this dataset correct for
this range, and know that it is".

  |                              | TorQ dataloader                                                                                           | uqf backfill (`.qbw`)                                                                                                                   |
  | ---                          | ---                                                                                                       | ---                                                                                                                                     |
  | Reads from                   | files in a directory: CSV or other delimited, with headers and types given as parameters                  | any source a declaration describes: a q process over IPC, a database over ODBC (DuckDB, SingleStore), or a fixture with no connection   |
  | Unit of work                 | every file in the directory, in chunks of bytes                                                           | a time range, cut into windows of the worker's `width`                                                                                  |
  | Knows what it already loaded | no. Its file and partition lists reset on every call, so loading a directory twice appends its rows twice | yes. A coverage ledger (`.qmatz`) records each window per `source_version`, so a re-run is idle and a partial run redoes only the gaps  |
  | Contract with the source     | the headers and types you pass it                                                                         | a declared source contract (`.qsrc`): columns, types, time column, row key and zone, checked on every batch and against the live source |
  | Transform                    | an optional function, untested by the loader                                                              | a declared `.qxf` transform with worked examples, verified on every test run                                                            |
  | Bad data                     | a failed write is logged and the load goes on                                                             | an optional quality check fails the window: nothing published, no coverage recorded, so the next run plans the window again             |
  | Failure and restart          | start again from the directory                                                                            | retries per window, a checkpoint to resume from, a single-instance lock, and a run ledger (`.qrun`) of every run and its outcome        |
  | Today's partition            | not guarded                                                                                               | refused: today belongs to the tickerplant and end-of-day                                                                                |
  | After the write              | sort and attributes via `sort.csv`; optional compression                                                  | sort by sym and time with `p#sym`, missing tables filled (`.Q.chk`), and the running HDB asked to reload                                |
  | Where it runs                | any TorQ process that loads it                                                                            | a registered process started by `uqs backfill`, visible to discovery while it runs; or plain q, writing to memory, for tests            |

For a directory of CSVs you need in the HDB once, the dataloader is enough. For
a source you will read again - where it matters which ranges are done, under
which release of the data, and that a failed window is retried rather than
half-written - use a bounded worker.

## Can jobs be chained before their rows reach the tickerplant?

Not between streaming jobs, by design, and `.qpipe` has no path for it:
`.qpipe.publish` only ever sends to the tickerplant. What you can chain depends
on what is being chained.

  | You want                                   | Do this                                                                                                                                                                                                                                                                   |
  | ---                                        | ---                                                                                                                                                                                                                                                                       |
  | several steps inside one streaming job     | Compose them in the job's own handler: `on_batch` can call any number of functions, or `.qxf` transforms (which may read several inputs), and `publish` once at the end. A normalizer (`.qnorm`) is this pattern built in: one declared transform per source, one output. |
  | one streaming job to feed another          | Publish, and have the second subscribe. Every step goes through the tickerplant: nothing reads another job's output directly.                                                                                                                                             |
  | a backfill to trigger another backfill     | A reaction: `.qreact.on_worker[dataset;worker;spec_fn]` runs `worker` over each range another worker publishes into `dataset`. The chain never touches the tickerplant, because backfills never do, and the job graph draws it.                                           |
  | several steps inside one backfill          | One worker has exactly one transform (`.qbw.define` refuses more), so put the steps in that transform's function, or split them into two workers chained by a reaction.                                                                                                   |

Why streaming jobs are not chained in-process: each step's output is an ordinary
tickerplant table, so it is queryable in the RDB. A downstream job can be
restarted, replaced or added without touching the one upstream, and adding an
engine means adding a subscriber, not changing a producer. The cost is a hop
through the plant, and a licensed connection per process - which is why a job
that is only an intermediate step is often better folded into its consumer's
handler than run as a process of its own. See the diagram in [Desk System,
composed](architecture/pipeline-architecture-example.md).

## How is it decided whether rows go to the RDB or the HDB?

Not per row, and not by `.qpipe`: by which kind of process the job runs in. The
runner that starts the process wires its output once, at startup.

  | Process                                        | Its rows go                                                                                                                                                     | Wired by                                                                                            |
  | ---                                            | ---                                                                                                                                                             | ---                                                                                                 |
  | a streaming job (`.qstream`, `.qnorm`)         | to the tickerplant, which stamps `time` and fans them out: `rdb1` holds today in memory, `wdb1` writes the day down and it is sorted into the HDB at end of day | `torq_stream.q`: the job's `publish` becomes `.qpipe.publish`, which calls `.u.upd`                 |
  | a backfill (`.qbw`, started by `uqs backfill`) | straight into the HDB partition of each row's own date - never the tickerplant, never the RDB                                                                   | `torq_backfill.q`: sets `.qio.default` to `.qio.hdb`, then `.qpipe.reload_hdb` once the run is done |

So the rule is about the data's age, enforced by process type. Live rows go
through the tickerplant because subscribers must see them and today's partition
is end-of-day's to write. History goes straight to its own date, because the
tickerplant would stamp it with today's time. The one hard edge is checked:
`.qio.hdb` refuses rows dated today or later, so a backfill cannot write into
the partition end-of-day owns. `.qpipe`'s only part in the HDB path is the
reload request, because that is the step that needs TorQ.

## Without TorQ, or with no tickerplant running, where does the data end up?

Nowhere silently. Every one of these cases either stops loudly or keeps the rows
somewhere you can see:

  | Situation                                            | What happens                                                                                                                                                                                                                                                                                   |
  | ---                                                  | ---                                                                                                                                                                                                                                                                                            |
  | TorQ stack, but the tickerplant is not running       | A streaming job never starts. `.qpipe.subscribe_etl` blocks until the plant is up, and the job's last log line is `waiting for the tickerplant - if this is the last line, it is not running`. Nothing is published, so nothing is lost.                                                       |
  | Plain q, no TorQ, job not wired                      | `.qpipe` is not loaded at all - nothing under `src/` uses it - and the job's `publish` is still the stub it starts as, which throws `publish: <job> is not wired`. A job that produces rows fails rather than dropping them.                                                                   |
  | Plain q, job wired to a recorder (`.qstream.wire`)   | The rows are wherever the recorder puts them, usually a table in that q session. This is how the tests run.                                                                                                                                                                                    |
  | Stock kdb+, `scripts/processes/run_stream.q`         | `.qtick` stands in for the tickerplant. It stamps `time`, sends each batch to its subscribers, and writes a tick log under `-logdir` (default `tplog/`), which a restarted job replays. There is no RDB, HDB or end-of-day writedown, so rows live in the subscribers' memory and in that log. |
  | Plain q, a bounded worker                            | The default output is `.qio.memory`, so rows land in a table named after the source's target in that q session. Only a backfill started by `uqs backfill` writes to the HDB.                                                                                                                   |

The rule underneath: `publish` only reaches a tickerplant when a runner wires it
to one, and a job that is not wired throws instead of running silently.

## Why doesn't my job start with the stack?

A job starts on demand unless its declaration says `start_with_all` `1b`,
because every started process spends one of the licence's sixteen concurrent
connections (two held back for ad-hoc handles). `uqs start <name>` starts it
anyway; the budget is in [running the stack](guides/uqs.md).

## Can I edit `lib/torq`, `process.csv` or `database.q`?

No. `lib/torq` is vendored and never edited. `process.csv` and `database.q` are
generated from the job declarations and `scripts/processes/uqs_tables.q` on
every `uqs` command, so an edit to either is overwritten. Change the declaration
or `uqs_tables.q` instead.

## Where do ports come from?

`scripts/processes/process_ports.csv`, a generated, append-only lock of each
process's offset from the base port. A new process gets the next free offset; a
retired one keeps its row, so no offset is ever reused.
[`reference/processes.md`](reference/processes.md) lists every process and its
port, generated from the same registry.
