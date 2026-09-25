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
