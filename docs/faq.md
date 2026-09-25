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
