# Running the uqf stack

`lib/torq/` (the TorQ production framework) and `lib/torq-finance-starter-pack/`
(a layered reference application built on top of it - feed handlers,
tickerplant, RDB, an HDB seeded with two days of sample quote/trade data,
gateway) are both vendored into this repo for reference (see the README's
Licensing section) but neither is wired into `src/init.q` or anything else uqf
itself runs - this library has no long-running processes for TorQ's machinery to
manage.

The `uqs` CLI (`python/uqs/`) bridges the two vendored trees so you can actually
start the demo up and poke at it, without editing or writing into either `lib/`
directory. The actual bootstrapping/config logic lives in
`python/uqs/src/uqs/`'s `model/` and `stack/` modules, shared with `uqs.mcp`'s
FastMCP server (see "MCP server" below) so the CLI and the MCP tools can't drift
apart. It's a standalone package (`python/uqs/`).

See [docs/architecture/stack.md](../architecture/stack.md) for diagrams of the
current process topology, table-level data pipeline, and config-generation flow.

## Contents

- [Quick start](#quick-start)
- [Reading the database's shape](#reading-the-databases-shape)
- [Commands](#commands)
- [Replaying a tickerplant log](#replaying-a-tickerplant-log)
- [Listing things](#listing-things)
- [What actually starts](#what-actually-starts) --- including
  [profiles](#profiles)
- [Changing a process's config](#changing-a-processs-config)
- [Logs](#logs)
- [Connecting](#connecting)
- [Verifying it's alive](#verifying-its-alive)
- [What uqf leaves unused](#what-uqf-leaves-unused)
- [Services](#services)
- [Adding a process](#adding-a-process) --- including [installing jobs from
  elsewhere](#installing-jobs-from-elsewhere)
- [MCP server](#mcp-server)
- [Other commands](#other-commands)
- [Installing](#installing)
- [Known harmless warnings](#known-harmless-warnings)

## Quick start

```
scripts/dev/install.sh     # once: puts `uqs` on your PATH, editable
uqs start all              # every startwithall=1 process
uqs summary                # status table
uqs stop all               # stop everything
```

Without the install - in CI or a fresh checkout - prefix each command with
`uv run --project python/uqs`, which resolves the package's dependencies on
demand. Either form runs from any directory.

The first `start` creates the data directory `output/uqs/` (gitignored) and
copies the app's sample `hdb/`/`dqe/` data into it. Logs, tickerplant logs, the
write-down database and every process's reads and writes stay inside it, never
inside `lib/`. `uqs remove output` wipes it (see [Cleaning up](#cleaning-up)).

Requires KDB-X (`q` on `PATH`), plus `envsubst` and `rlwrap`, which TorQ's
`torq.sh` needs (on macOS: `brew install gettext rlwrap`). See
[Installing](#installing) for tab completion and reinstalling after a rename.

## Reading the database's shape

`schema` answers "what tables are there, and what shape are they" without anyone
typing `meta` at a q prompt:

```
uqs schema                  # every table on rdb1, with row and column counts
uqs schema quotes           # one table's columns, types and attributes
uqs schema 'crypto*'        # every table matching a pattern, one block each
uqs schema --proc hdb1      # the history instead of today
uqs schema --port 6052      # a port directly, skipping --proc resolution
```

### Missing partitions

```
$ uqs schema --proc hdb1
could not read the schema from hdb1 (6053):
  "./2015.01.07/arbitrage. OS reports: No such file or directory"
```

That is not a missing table. A partitioned kdb+ database requires every table to
exist in **every** partition, and one absent directory fails the whole query -
so the error names whichever table sorts first rather than the partition that is
actually short.

```
uqs data hdb-check         # which partitions are missing what
uqs data hdb-check --fix   # write an empty copy of each into them
```

`--fix` is additive and idempotent: a table directory that exists is never
touched, and a table a partition holds that `database.q` no longer declares is
left alone - that is history. Running it twice changes nothing the second time.

It should rarely be needed by hand, because `bootstrap` now does it on every
`uqs` command. It is there because the situation is easy to create: each day's
partition holds whatever tables existed when it was written, so **every table
added leaves every earlier partition short**, and the vendored sample partitions
ship holding `quote` and `trade` alone. TorQ fills only the partition the wdb is
currently writing (`lib/torq/code/processes/wdb.q`'s `filldb`), so nothing ever
goes back (#348).

**It reads the live process, not the declarations.**
`scripts/processes/uqs_tables.q` says what the tickerplant is *configured* to
carry; that is not evidence a table exists in the process you are about to
query. A tickerplant that failed to load its schema file, or an RDB that has not
replayed, looks identical in every other view - so reporting the declarations
here would be confidently wrong exactly when it mattered.

Two things the output says that `meta` alone does not:

- **Case is the vector/atom distinction.** `f` is a float column; `F` is a float
  *vector* column, one list per row - the shape `quotes` and `mkt_orderbook` are
  built around and that every pricing function in `src/` expects. They render as
  `float` and `float vector`.
- **A column's type can change with its contents.** An empty vector column
  reports as `general`, because q cannot know the element type until a row
  exists. The same table reads `general` before its first publish and
  `float vector` after. Not a bug, but worth knowing before treating one reading
  as the schema.

Row counts are shown because "declared but empty" and "carrying data" is usually
the thing being looked for, and empty tables are named explicitly rather than
left to be spotted.

The table argument is a shell-style pattern (`crypto*`, `*trade*`), matched
case-sensitively against the names the process reported. An exact name is just a
pattern that matches itself, so there is one behaviour rather than two - quote
the pattern, or the shell will try to expand it against your filenames first.

## Commands

```
start [PROCS] [--port N] [--print]    start (default: all startwithall=1 processes);
                                      --print shows the startup command line(s) instead
stop [PROCS] [--port N]               stop
restart [PROCS] [--port N]            restart
up [PROCS] [--profile P] [--level L]  start, then stream every started process's log to
                                      this console; Ctrl-C stops what it started
summary [--port N] [--export FILE] [--columns all|status|C,...] [--timeout S]
        [--probe-timeout S] [--debug]  status table plus the declared graph
                                      (--columns status for just up/down/pid/port/
                                      Responds; --timeout defaults to 120s,
                                      --probe-timeout to 0.5s per process;
                                      --debug adds each process's load time)
backfill WORKER --version V --from T --to T [--on-conflict S] [--port N] [--debug]
                                      run a bounded worker over [--from, --to); dates
                                      without an offset are UTC. Passed to the process
                                      as flags, never environment variables. --debug
                                      starts it with -verbose: DBG lines in its log.
                                      --on-conflict (upsert, replace, ignore, append,
                                      fail) overrides the worker's own for this run
data replay [--proc P] [--date D] [--dir PATH] [--hdb PATH] [--schema PATH]
            [--table T]... [--port N] [--dry-run]
                                      replay a tickerplant log into the HDB. With
                                      nothing passed, every one of those comes off
                                      the running plant and hdb process - including
                                      the base port (see below)
data hdb-check [--fix]                HDB partitions missing a declared table or column
remove output [--match REGEX] [--dry-run]
                                      wipe output/uqs/, or part of it
remove checkpoint WORKER              delete a worker's checkpoint, so its next run
                                      starts at --from. Refused while a run of it may
                                      be live. Covered windows are still skipped:
                                      re-fetch those under a new --version
job new NAME [--kind K] [--dry-run] ...
                                      scaffold an ETL job: its q files, table and
                                      test (see docs/scaffolding/)
job remove NAME [--dry-run] [--force] [-y]
                                      undo a scaffold, keeping anything still in use
job install DIR [--mode copy|symlink] [--overwrite] [--dry-run] [-y]
                                      install the sources, workers and streaming jobs
                                      in DIR into src/etl/ (see "Adding a process")
query [EXPR] [--proc P|--port N] [--servers T] [--raw] [--export FILE]
                                      run a q expression against a process - gateway1,
                                      which routes it to the RDB and HDB, unless
                                      --proc or --port says otherwise; with no EXPR,
                                      an interactive session on it (routed on the
                                      gateway, qcon elsewhere). --raw sends to the
                                      gateway as typed. --proc looks the port up, and
                                      refuses a stopped process
schema [TABLE|PATTERN] [--proc P] [--export FILE]  tables in a running process, or the
                                      columns of every table matching a pattern
list [KIND] [--port N] [--export FILE] [--sort COL] [--reverse]  list every item of KIND
                                       ('processes', 'profiles', 'fields', 'overrides',
                                       'env', 'dependencies', 'jobs') - no argument shows
                                       the kinds
config get PROCNAME [FIELD] [--port N] [--raw] [--export FILE]  show a process's effective
                                                 process.csv row (or one field), with
                                                 placeholders resolved unless --raw
config set PROCNAME FIELD VALUE       persist a process.csv field override for a process
logs [PROCS] [-f] [-n N] [--level L] [--multitail]
                                       tail out_/err_*.log through the CLI's own colorized
                                       logger instead of raw files (see "Logs" below)
feed start|stop|status NAME           an external publisher into the tickerplant: databento,
                                       kafka, crypto or crypto-fills (see docs/services/);
                                       `feed status` alone shows every feed
raw -- ARGS...                        pass any other torq.sh verb straight through
                                       (e.g. `raw -- debug rdb1`, `raw -- top feed1`)
```

`PROCS` is `all` (the default) or one or more process names, each its own word -
`uqs start posbook1 markout1` - which is what lets TAB complete them. A single
quoted `"posbook1 markout1"` still works. `--port` sets `KDBBASEPORT` (default
`6050`, see the port table below). `--export FILE` (on
`summary`/`query`/`list`/`config get`) additionally writes the same rows to
`FILE` as CSV or Parquet, format inferred from the extension - see
`python/uqs/README.md`'s "Exporting output" section. Full `--help` is available
on the command itself and on every subcommand.

### Cleaning up

`uqs remove output` wipes `output/uqs/`. `--match REGEX` keeps only the entries
whose path *under* `output/uqs/` the regex finds:

```
uqs remove output --dry-run                   # what a full wipe would remove
uqs remove output --match '^logs$' --dry-run  # the logs alone, still removing nothing
uqs remove output --match '^(logs|tplogs)$'   # and now actually remove them
uqs remove output --match 'out_rdb1'          # one process's files, wherever they sit
uqs remove checkpoint demo_deals_backfill     # one backfill worker's resume point
```

A directory the pattern matches goes whole; one it does not is descended into,
which is how `out_rdb1` is found without naming `logs`. The match is a search,
so anchor with `^`/`$` to be exact. `--dry-run` (`-n`) lists what would go, with
sizes - worth running first, since none of this is reversible.

## Replaying a tickerplant log

`data replay` is TorQ's own `tickerlogreplay` - the `tpreplay1` process - with
the aiming done for you:

```
uqs data replay --dry-run
uqs data replay --date 2026-09-22 --table quote --table trade
```

What it replays, and into what, is read off the processes that are **running**,
not off the configuration that describes them:

  | what              | where it comes from                                            |
  | ---               | ---                                                            |
  | the log directory | the running plant's own `-tplogdir`, newest day for that plant |
  | the schema        | that plant's `-schemafile`                                     |
  | the database      | the `-load` of the hdb process on the same stack               |
  | the base port     | the plant's `-stackid`                                         |

The two can disagree, and silently. This tree's data directory moved from
`scripts/output/uqf-stack` to `output/uqs`, so a plant started before the move
still writes its log under the old path while every config-derived answer names
the new one; a replay aimed at the configured path would have found *a* log,
replayed it without complaint, and written down a day nobody asked for. A
process's start line cannot drift from the process.

That is also why `--port` is not needed here: the plant is running under a
`-stackid`, and that is the stack. Pass one only to override it.

Every row in that table is an option (`--dir`, `--schema`, `--hdb`, `--port`),
and an option that is given wins - supply all four and nothing is asked of the
machine at all, which is how to replay a log after the stack it came from has
been stopped. `--proc` picks between plants when more than one is up with a log
of its own; with exactly one, it is not needed, and with several the command
refuses rather than ranking them.

**It empties what it writes.** TorQ's replay defaults are kept whole, so the
tables being replayed are cleared in the partitions the replay touches before it
writes them. `--dry-run` prints the resolved plan and the start line and runs
nothing.

## Listing things

`list` (no argument) prints the kinds it knows about; `list KIND` lists every
item of that kind - not just processes:

```
uqs list
uqs list processes
```

- `processes` - every process's `procname`/`proctype`/`port`/`startwithall`,
  resolved and with any `config set` overrides applied - the full set
  `config get`/`config set`/`start <procname>` accept, without already needing
  to know a name ahead of time - plus the `inputs` and `outputs` its job
  declares: the tables it subscribes to and publishes, or for a backfill worker
  the dataset it fills. The same declarations `summary`'s graph columns come
  from, but readable without a running stack
- `fields` - `process.csv`'s valid columns (what `config set`'s `FIELD` argument
  accepts)
- `overrides` - every `config set` override currently in effect
- `env` - `build_env()`'s resolved `KDBBASEPORT`/`KDBHDB`/... values (the same
  env `config get`'s placeholder resolution and `torq.sh` itself use)
- `dependencies` - each process's input tables and who publishes them, so you
  can see what a process needs before starting it on its own
- `jobs` - every streaming job, normalizer and bounded worker, read from its own
  q declaration: its kind, process, what it reads and writes (a backfill's
  source, and its dataset and partition), whether it starts with the stack, and
  whether it is still a `scaffolded` job `uqs job new` wrote or `written` work.
  `uqs list jobs --sort state` puts the unfinished scaffolds together

New kinds are one function + one `LISTABLE_KINDS` entry, not a new CLI command
each time - see `stack/listing.py`'s `_list_*` functions.

`--sort` orders by any column the chosen kind produces, `--reverse` flips it:

```
uqs list processes --sort port              # 6050, 6051, 6052, ...
uqs list processes --sort proctype
uqs list processes --sort port --reverse
uqs list env --sort name
```

Two things it gets right that a plain sort would not. **Numeric columns sort
numerically**: `port` is a string, and as text `6100` comes before `659`, which
looks like the sort silently did nothing on the one column most worth sorting.
And **empty cells group at the end** rather than sorting as the empty string
among real values - an unset override is absent, not "before aaa".

The column name is matched case-insensitively, and because the columns differ
per kind there is no fixed set to check against: an unknown name is refused with
the columns that listing actually produced. The order reaches `--export` too, so
an exported CSV matches what was on screen.

## What actually starts

By default (`start all`) the processes marked `startwithall=1` come up: the
vendored rows in `lib/torq-finance-starter-pack/appconfig/process.csv`, plus
uqf's own pipelines, appended as extra rows to a *copy* of that csv that
`uqs.stack.runtime.bootstrap()` generates on the fly (never editing the vendored
file itself). The vendored README explains why the rest stay off: the KDB-X
community edition's connection limits mean `reporter1`, `filealerter1`,
`dqc1`/`dqcdb1`, `dqe1`/`dqedb1` stay off unless you have a fully-licensed
kdb+/KDB-X. `killtick` and `tpreplay1` are on-demand utility processes, not part
of the standing stack, so they also don't auto-start - `tpreplay1` is what
[`data replay`](#replaying-a-tickerplant-log) starts, for one replay, and it
exits when the replay is done.

### Profiles

`start all` is one answer to "what should be running", and on this licence it is
nearly the only one you can afford. Fourteen tickerplant slots are available
(sixteen on the licence, two held back for ad-hoc handles) and the default start
holds **thirteen**. The arbitrage chain needs four, so it cannot run until
something stops.

A profile names the processes you actually came for; everything they read is
derived from the same dependency graph `summary`'s **Depends on** column uses:

```
uqs list profiles
uqs start --profile arbitrage
uqs start --profile depth,crypto
uqs start --profile essential      # the TorQ stack alone, no uqf jobs
UQS_LICENCE_CONNECTIONS=32 uqs start --profile all   # on a licence that allows it
```

  | profile     | leaves                                   | slots                           |
  | ---         | ---                                      | ---                             |
  | `default`   | what `start all` runs today              | 13/14                           |
  | `fx`        | `posbook1`, `markout1`, `fxpositions1`   | 12/14                           |
  | `arbitrage` | `arbitrage1`, `crossarb1`                | 10/14                           |
  | `depth`     | `vectorize1`, `cross1`                   | 8/14                            |
  | `crypto`    | `cryptomock1`                            | 5/14                            |
  | `essential` | none - the TorQ stack alone (see below)  | 2/14                            |
  | `all`       | every profile's leaves except `crypto`'s | 20/14 - refused on this licence |

**A profile over the cap is refused, not warned.** That is the opposite of a
positional `start`, deliberately: naming processes yourself is your call, and an
ordering that briefly exceeds the cap is a legitimate thing to do. A profile is
a set *this tree* named, so one that cannot run is its mistake to report rather
than yours to discover when the plant resets a handle. `fx` and `arbitrage` each
fit and together need seventeen:

```
$ uqs start --profile fx,arbitrage
profile(s) arbitrage, fx need 17 tickerplant connections, and only 14 are
available (16 on this licence, 2 held back for ad-hoc handles). ...
```

**`all` needs a larger licence, and says so.** It is every standing set at once -
the union of the other profiles' leaves, derived so a new leaf joins it
automatically - and that holds twenty plant connections, more than the community
licence has. On that licence it is refused like any profile over the cap. On a q
licence that allows more concurrent connections, say how many with
`UQS_LICENCE_CONNECTIONS` and it starts: that setting is the budget every start
is held to - `--profile`, `uqs list profiles`' `fits` column and the
positional-start warning. On PeachQ (`UQF_Q_IMPL=peachq`, see the README's
Requirements) there is no cap at all, so `all` starts as it is. `all` leaves out
`crypto`, because the mock replaces cryptorust's recorders rather than joining
them; ask for it with `--profile all,crypto`.

Two things profiles deliberately do **not** do. They do not change
`startwithall`, so `start all` is untouched - `default` describes that set so
the two can be compared, and a test fails if they drift. And a closure stops at
a table fed from outside the stack: `posbook1` needs `crypto_book`, which
cryptorust's recorder publishes, so the `fx` profile does **not** drag in
`cryptomock1` - that mock replaces the recorder rather than joining it, which is
why it is a profile of its own.

**`essential` is the TorQ stack with nothing on top**: `discovery1`, `stp1`,
`rdb1`, `hdb1`, `hdb2`, `wdb1`, `gateway1`, `monitor1` and `housekeeping1` -
nine processes, two plant slots. It is the one profile that starts less than the
full infrastructure: no chained plant (`sctp1`), no `metrics1`, and no sort
processes (`sort1`, `sortworker1`, `sortworker2`). The day still rolls over
without them: at end of day `wdb1` looks for a sort process, logs
`can't connect to the sortandreload - no sortandreload process detected` as an
error, and sorts the writedown into the HDB itself - so expect that error line,
and `wdb1` busy while it sorts. Composing it with a job profile -
`--profile essential,fx` - starts the full infrastructure that job profile
needs, sort processes included.

Profiles are declared in `python/uqs/src/uqs/model/profiles.py`. Each one names
leaves, never members, so adding a process to a chain does not mean editing
whatever profiles contain it.

**The slot count has been checked against the wire**, not just computed. On a
stack running the default set, `lsof -nP -iTCP:6050 -sTCP:ESTABLISHED` showed
thirteen client connections --- exactly the thirteen this predicts, name for
name --- plus `stp1` itself as the listener and one external process
(cryptorust's recorder) that is in no `Pipeline` and so in no profile. The
budget is blind to anything outside the registry that opens a handle, which is
part of what the two reserved slots absorb.

### Idle subscribers

A subscriber started without its producer subscribes **successfully**. The table
is defined on the tickerplant whether or not anybody publishes to it, so the
process comes up, heartbeats, reports `up`, and receives nothing for as long as
you leave it. There is no error and no symptom except an output table that stays
empty.

That matters more than it used to: seven processes are on demand, and the
direct-arbitrage chain is three of them deep. So `start` and `restart` say
something when what you are starting has an input nothing running publishes:

```
$ uqs start superbook1
warning superbook1 subscribes to `market_data`, which no running process
        publishes - start `uqs start marketdata1`.
```

and `summary` says the same about processes that are already up:

```
2 running process(es) have an input nothing running publishes - up, but idle:
  · executions1 subscribes to `crypto_trades`, which no running process
    publishes - start `uqs start cryptomock1`, unless it is coming
    from cryptorust's kdb recorder, which cryptomock1 stands in for.
```

Both warnings are **advisory and never block a start**. Bringing a subscriber up
before its feed is how you avoid missing the first batch, and some tables come
from outside the process list entirely - the Databento feed handler,
cryptorust's recorders, a backfill run - which is why those are named as context
rather than reported as faults. Processes you name in the same command count as
present, so the recommended form for a chain is silent:

```bash
uqs start marketdata1 superbook1 arbitrage1
```

`crossarb1` is a second consumer of that chain, answering the other arbitrage
question - the direct book against a synthetic route rather than two sources on
one pair. The chain plus BOTH detectors is four plant connections on top of
whatever is already running, which is what [`--profile arbitrage`](#profiles)
exists for: it starts that set and is checked against the budget first, where a
positional start is only warned about. See [the cross-arbitrage
service](../services/cross-arbitrage.md).

The graph behind all of this is the `subscribe_to`/`publishes` pair on each
`Pipeline`, the same declaration the generated `database.q` and the `.qetl.dag`
job graph are built from - so what you are warned about and what is running
cannot describe different systems.

`summary` shows the whole graph, not just the unsatisfied part of it, in three
columns derived from the same declarations:

```
uqs summary                     # all ten columns
uqs summary --columns status    # the seven status ones, for a narrow terminal
uqs summary --columns "Process,Depends on,Inputs,Outputs"
```

```
┃ Process      ┃ Depends on                  ┃ Inputs                ┃ Outputs        ┃
│ posbook1     │ executions1, marks1         │ executions, marks     │ position       │
│ databento1   │ (databento_mbp10: external) │ databento_mbp10       │ databento_book │
│ executions1  │ fxtradesfeed1, cryptomock1, │ trades, crypto_trades │ executions     │
│              │ (crypto_trades: external)   │                       │                │
```

`Inputs` and `Outputs` are the tables a process subscribes to and publishes;
`Depends on` resolves those inputs to the **processes** that produce them, which
is the question behind every `up, but idle` line above. A table produced from
outside the process list is named as external rather than dropped - "nothing in
this list provides it" and "nothing provides it" are different facts, and only
one is a problem. Cells break at the commas once there are more than two
entries, so a table name is never split across lines, and a process with no
declared edges - every vendored TorQ one - shows a dash.

Ten columns need a wide terminal; at eighty they squeeze and Rich elides the
headers. `--columns status` gives the seven status columns back. They are shown
by default anyway, because a column nobody knows about answers nothing: a reader
on a narrow terminal can ask for fewer, while one who never learns the graph is
there has no such move.

### Responds column

`Status` comes from a PID lookup, and a hung process still has a PID.
`Heartbeat` notices only after a tolerance of missed beats. `Responds` asks
every `up` process directly, all at once, whether it can complete the kdb+
handshake within `--probe-timeout` (0.5s by default):

  | Responds   | Meaning                                                                                                                                                      |
  | ---        | ---                                                                                                                                                          |
  | `4ms`      | it answered, in that long                                                                                                                                    |
  | `timeout`  | it accepted the connection but did not answer in time - busy in a long query, a timer or its load. A process at its licence connection cap can look the same |
  | `reset`    | it dropped the connection - what a process past its connection cap does to a new handle                                                                      |
  | `rejected` | it closed the connection without answering: it refused the credentials                                                                                       |
  | `refused`  | nothing is listening on its port                                                                                                                             |
  | `-`        | not probed: the process is down, or `summary`'s own budget ran out                                                                                           |

Any `up` process that does not answer is also named in a red line under the
table.

The probe is the handshake, not a query, so no q code runs on the process; q
answers the handshake from its main loop, which is exactly what is busy when a
process is unresponsive. Each probe briefly holds one inbound connection, and
TorQ logs it like any other. `--probe-timeout 0` skips it.

### summary --timeout

`summary` gets **one budget for the whole command** (120s by default): listing
the processes, asking `monitor1` for heartbeats, and probing each process. Any
of them could otherwise hang - `monitor1` at its connection cap accepts a
connection and never answers.

```
uqs summary --timeout 10   # fail fast
uqs summary --timeout 0    # wait forever
```

The listing goes first and the heartbeat lookup gets what is left (at least one
second); the probe takes the smaller of `--probe-timeout` and the remainder, or
is skipped, showing `-`. A listing that runs out is a refusal, not a traceback:

```
listing processes did not finish within 120s
```

A heartbeat lookup that runs out degrades to the column's usual "monitor1 could
not be reached".

### monitor1

Upstream ships `monitor1` with `startwithall=0`; uqs turns it on
(`VENDORED_STARTWITHALL_OVERLAY` in `stack/procs.py`). It is the only process
that collects heartbeats, so without it `summary`'s Heartbeat column reads "not
collected" on a healthy stack.

- **Under the community licence, coverage is partial.** A q process may hold 16
  connections, and a monitor with none left cannot accept the query `summary`
  reads heartbeats with. So `stack/monitor_budget.py` stops monitoring
  `sortworker`, `reporter`, `housekeeping`, `feed`, then `metrics` processes
  until the rest fit. Those show `-` in the Heartbeat column, and `summary`
  names them. On a fully licensed kdb+/KDB-X nothing is dropped.
- **uqf's standing ETLs (`metrics`) are monitored too**; `backfill` workers are
  not, because a finished job's heartbeat would age into a false `error`.

To get the upstream behaviour back:

```bash
uqs config set monitor1 startwithall 0
```

### Default ports

Base `6050`, override with `--port <n>`:

  | Port        | Process       | Role                                                                       |
  | ---         | ---           | ---                                                                        |
  | 6050        | stp1          | segmented tickerplant                                                      |
  | 6051        | discovery1    | service discovery                                                          |
  | 6052        | rdb1          | real-time DB (today's ticks)                                               |
  | 6053 / 6054 | hdb1 / hdb2   | historical DB (the vendored sample data)                                   |
  | 6055        | wdb1          | writedown process (rolls RDB -> HDB)                                       |
  | 6056        | sort1         | sorts data before writedown                                                |
  | 6057        | gateway1      | single query entry point across hdb/rdb                                    |
  | 6061        | housekeeping1 | log/process housekeeping                                                   |
  | 6064        | feed1         | the vendored dummy feed - simulated equity quotes/trades                   |
  | 6065        | sctp1         | segmented chained tickerplant                                              |
  | 6066 / 6067 | sortworker1/2 | sort worker pool                                                           |
  | 6068        | metrics1      | metrics collector                                                          |
  | 6069        | fxfeed1       | uqf's own feed - simulated FX quotes (see below)                           |
  | 6074        | quotesfeed1   | uqf's own feed - simulated depth-aware FX quotes into `quotes` (see below) |

## Changing a process's config

`config get`/`config set` read and write a *process.csv field override* - not
the vendored `process.csv` (never edited) and not the *generated* one in
`output/uqs/` either (regenerated from scratch on every `bootstrap()` call, i.e.
every `start`/`stop`/`summary`/... - anything written directly there would just
be clobbered on the next command). Overrides persist instead in
`python/uqs/process_overrides.csv` (a small `procname,field,value` csv, created
on first `config set` - tracked in git like any other config, not gitignored),
and `bootstrap()` applies them on top of the vendored+fxfeed1 rows every time it
(re)generates `process.csv`.

```
uqs config get fxfeed1
uqs config get fxfeed1 startwithall
uqs config set fxfeed1 startwithall 0
```

`config get` resolves `process.csv`'s two placeholder styles by default -
`${VAR}`/`$VAR` (e.g. `load=${KDBHDB}` -> the real path,
`U=${TORQAPPHOME}/appconfig/passwords/accesslist.txt` -> the real path) and the
port column's own `{VAR}`/`{VAR}+N`/`{VAR}-N` arithmetic shorthand (e.g.
`port={KDBBASEPORT}+3` -> `6053`) - the same values `torq.sh` itself substitutes
at process-start time, evaluated against `build_env(paths, --port)`. Pass
`--raw` to see the literal, unresolved value instead (e.g. to copy it into a
`config set` call).

Valid `FIELD`s are `process.csv`'s own columns: `host`, `port`, `proctype`,
`procname`, `U`, `localtime`, `g`, `T`, `w`, `load`, `startwithall`, `extras`,
`qcmd`. A change takes effect on the next `start`/`restart` of that process (the
running process itself isn't touched).

## Logs

Every process writes its own `out_<procname>.log`/`err_<procname>.log` in
`output/uqs/logs/` (stable symlink aliases TorQ itself maintains onto the
current run's timestamped file - see `torq.q`'s `createlog`/`fileredirect`), in
a fixed pipe-delimited format:
`time|host|proctype|procname|loglevel|id|message`. `logs` tails these through
the same colorized logger the rest of the CLI uses, instead of `tail`-ing N raw
files by hand - no TorQ-side config change needed (no `-jsonlogs`, nothing added
to `extras`):

```
uqs logs                          # last 20 lines per process, all processes
uqs logs stp1 rdb1 -n 50          # last 50 lines each, merged and time-sorted
uqs logs -f                       # the last 20 lines, then live, Ctrl-C to stop
uqs logs quotesfeed1 -f --level WARNING   # live tail, warnings/errors only
```

Either way the last `-n`/`--lines` lines of each file are read, parsed, and
printed sorted by the log's own timestamp - not wall-clock arrival order. `-f`/
`--follow` then keeps going: one follower per file (`uqs.stack.follow`, the one
`--multitail` panes use), merged through a queue. It follows the
`out_<procname>.log` alias by name, so when TorQ points it at a new file - every
restart, and the daily roll - the stream moves with it; the system `tail -F`,
which this used before, could stay on the old file and go quiet. `--level`
filters to that level and above (`DEBUG`/`INFO`/`WARNING`/`ERROR`).

`uqs logs --multitail` follows the same files in
[multitail](https://www.vanheusden.com/multitail/), one pane per file, titled
with its file name, instead of merging them - so two busy processes stay side by
side rather than interleaved. It takes the same process names and needs the
`multitail` binary (`brew install multitail`, `apt install multitail`):

```
uqs logs --multitail rdb1 fxpositions1          # a pane for each out_/err_ log, stacked
uqs logs --multitail all --stream err -c 2      # every process's err_ log, in two columns
uqs logs --multitail stp1 -n 100 --print        # show the multitail command, run nothing
```

A process that has never started has no log and gets no pane; a name that is not
a process is refused. Press `q` to leave multitail.

Each pane runs uqs's own follower (`python -m uqs.stack.follow`) rather than
multitail's `-f`. TorQ re-points `out_<procname>.log` at a new file on every
start and at the daily roll, and multitail's `-f` hands following to the system
`tail`, which can stay on the file the name used to point at - a pane that went
quiet after the first restart. The follower checks what the name points at
whenever it runs out of lines and moves to the new run's file.

**`uqs up` is the foreground form of all this**: it starts what `start` would -
the same names, `all`, or `--profile` - then streams those processes' logs to
this console until Ctrl-C, which stops what it started, the way
`docker compose up` does. The console is the run.

```
uqs up                        # the default set, streamed; Ctrl-C stops it
uqs up rdb1 fxpositions1      # just these
uqs up --profile fx --level WARNING
```

It follows the log files from *before* the start runs, so what a process prints
while it loads is shown - `fxpositions1` spends forty seconds there - and a log
the start creates, on a first run or after `uqs remove output`, is read from its
first line. Processes that were already running when it began are left running
at Ctrl-C; if `summary` cannot say which those were, it stops everything it was
asked to start. To start in the background and watch separately instead,
`uqs start` and `uqs logs -f` are still there.

### Where a process stopped

Every uqf process script - `torq_stream.q` (every streaming job),
`torq_backfill.q`, `torq_tap.q`, `run_stream.q` - logs the stages where it can
stall, so the last line in `uqs logs <procname>` says where it stopped:

  | Last line                                                                   | What it means                                                                                                                                                                                                                                                                     |
  | ---                                                                         | ---                                                                                                                                                                                                                                                                               |
  | `qtorq: loading uqf tree from ...` with no `loaded in` after it             | a q file failed to load - the error follows, or is in `err_<procname>.log`                                                                                                                                                                                                        |
  | `starting streaming job`                                                    | the job and what it subscribes to and publishes, logged before anything can block                                                                                                                                                                                                 |
  | `waiting for the tickerplant - if this is the last line, it is not running` | `stp1` is down: `uqs start stp1`. This used to wait forever in silence                                                                                                                                                                                                            |
  | `subscribing` / `streaming job wired - running`                             | subscribed and running                                                                                                                                                                                                                                                            |
  | no `first batch received` for a table                                       | nothing is arriving on it: its publisher is down or publishes nothing                                                                                                                                                                                                             |
  | `first batch received` but no `first rows published`                        | input arrives and the job publishes nothing from it                                                                                                                                                                                                                               |
  | `on_batch failed`                                                           | the job's handler threw, with the table and the error                                                                                                                                                                                                                             |
  | `backfill process failed` then `backtrace`                                  | a backfill's error, and where it happened                                                                                                                                                                                                                                         |
  | `no credential - running on the source's fixture` (WARN)                    | a backfill is publishing the fixture, not live data. The line names the variable (`UQF_SOURCE_CRED_<SOURCE>`), what its value should be for that source - an ODBC connection string, or `host:port` for kdb+ - and an example. Export it in the shell you run `uqs backfill` from |
  | `idle - every window in the range is already covered`                       | nothing to do at this `--version`: coverage says the range is done. A new source release is a new version                                                                                                                                                                         |
  | `checkpoint is for another run - starting from the beginning`               | the range or version changed since the last run, so its checkpoint does not apply                                                                                                                                                                                                 |
  | `retrying after a transport error`                                          | the source failed transiently; the attempt, backoff and error follow                                                                                                                                                                                                              |

**More detail - the DBG level** - adds the process's pid, port and cwd, the
subscription result, the tables the tickerplant defines, every timer installed,
and every batch and publish with running totals. A backfill adds its
declaration, stage timings, and the ETL core's own decisions: the coverage gaps
it planned against, each fetch (live or fixture, the bounds sent to the source,
rows fetched and kept, time taken), contract checks, coercion failures, every
retry, lock and checkpoint, and each coverage record. Streaming jobs add their
registration and publish wiring, and every transform call its rows in and out.
Two ways to switch it on:

```
uqs backfill <worker> ... --debug                     # a backfill: passes -verbose
uqs raw -- start <procname> -extras -verbose          # any process, at start
uqs query ".qetl.log.debug 1b" --port <port>              # a process already running, no restart
```

`-verbose` is uqf's own flag, taken by every process script. It is not TorQ's
`-debug`, which also stops the log going to its file.

### CLI's own logging

`logs --level` filters what the *q processes* wrote. `--debug` shows what `uqs`
itself is doing:

```
uqs --debug summary        # this invocation only
uqs summary --debug        # the same, spelled on the command
LOG_LEVEL=DEBUG uqs summary   # same, for a shell session
```

`--debug` wins over `LOG_LEVEL`. `NO_COLOR=1` turns colour off and
`FORCE_COLOR=1` keeps it through a pipe (e.g. `less -R`). Floats in these lines
are cut to six decimal places (`LOG_FLOAT_DECIMALS` in `logger/floats.py`).

It matters most on `summary`, where a port map that cannot be built or an
unreachable `monitor1` leaves a column blank rather than failing. `--debug`
prints why:

```
summary base_port=6050 torqdata=.../output/uqs
process listing: 47 line(s)
configured ports for 46 process(es)
monitor1 not reached; Heartbeat column is a monitoring gap, not a verdict
parsed 46 row(s): 23 up, 23 down
starved process(es): executions1, marks1
```

If the table looks short, compare `parsed N row(s)` with the listing's line
count. `uqs raw -- summary` still runs torq.sh's own, slower check.

With `--debug`, `summary` also prints each process's load time on its latest
start, read from its own `out_` log rather than over IPC (see
`stack/startup.py`):

```
     Load time on each process's latest start, from its own log
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Process      ┃ Started             ┃ Load time ┃ Note                        ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ fxpositions1 │ 2026-09-24 08:00:00 │ 41.70s    │                             │
│ rdb1         │ 2026-09-24 08:00:00 │ 3.25s     │                             │
│ posbook1     │ 2026-09-24 08:00:00 │           │ no closing banner yet -     │
│              │                     │           │ still loading, or it        │
│              │                     │           │ stopped while loading       │
└──────────────┴─────────────────────┴───────────┴─────────────────────────────┘
```

## Services

What each running service does, how it is built and why, is one page per service
in [`docs/services/`](../services/README.md) - this guide covers operating the
stack as a whole. `tap1`, the diagnostic subscriber that logs every batch, is
[there too](../services/tap.md).

## Adding a process

There is one way: declare a job in q and let `uqs job new` scaffold it. See
[adding a pipeline](new-pipeline.md).

### Installing jobs from elsewhere

Jobs written outside the tree - a `sidecars/` folder, another repository - are
installed with:

```
uqs job install ../sidecars                        # the wizard
uqs job install ../sidecars --dry-run              # just the plan
uqs job install ../sidecars --mode symlink --yes   # no questions, for scripts
```

A file in `src/etl/sources`, `src/etl/workers` or `src/etl/streaming` is already
registered - `src/etl/init.q` and the registry glob those folders - so
installing is putting each file in the right one. Which one is decided by what
the file declares, not its name or where it sits in the sidecar:
`.qetl.source.define` is a source, `.qetl.job.bounded.define` a worker,
`.qetl.job.stream.define` or `.qetl.job.stream.normalize` a streaming job. The
plan table shows every `.q` file and what will happen to it; these are skipped,
with the reason:

- a file declaring nothing (a helper), or more than one kind (split it: the
  three folders load in a fixed order);
- a job or worker name the tree already declares - q refuses the second
  declaration, so the tree would stop loading;
- `test_*.q` - a q test runs only once its namespace is in `nsList` in
  `tests/run_tests.q`, which is a decision rather than a copy.

The wizard asks **copy** or **symlink**. A copy is a snapshot the tree owns:
edit the sidecar and install again. A symlink keeps the sidecar the source of
truth - edits are live on the next start - but moving or deleting the sidecar
breaks the tree. A file already in place and identical is left alone; one that
differs is replaced only if you agree, or with `--overwrite` (`--yes` alone
keeps it).

After installing it regenerates the derived files (`process_ports.csv`,
`pipeline_dag.q`, `processes.md`, `docs/man.q`) - a declaration the generators
refuse is reported here, with what they said - and warns about any table a job
reads, publishes or fills that `scripts/processes/uqs_tables.q` does not define.
Then check it is running:

```
uqs list processes                      # the registry sees the new processes
uqs start --print <procname>                    # the exact start line
uqs start <procname>                    # streaming jobs
uqs backfill <worker> --version v1 --from 2026-09-01 --to 2026-09-02
uqs summary --columns status            # up, and Responds: yes
uqs summary --debug                     # how long each took to load
uqs logs <procname> -f
q tests/run_tests.q
```

A process that is `down` or not answering: read
`output/uqs/logs/err_<procname>.log`, or run it in the foreground with
`uqs raw -- debug <procname>`. Commit what `git status` shows afterwards,
derived files included - CI checks they are current.

## Connecting

Every process (except the passwordless `feed1`) is protected by
`lib/torq-finance-starter-pack/appconfig/passwords/accesslist.txt` - placeholder
demo credentials, `admin:admin` works for everything.

```
uqs query \
    "select count i by sym from quote" --port 6052        # rdb1
uqs query \
    "select from quote where sym in \`EURUSD\`GBPUSD\`USDJPY\`AUDUSD" --port 6052
```

For an interactive session, name the process and let `uqs` find its port, or
name none and get the gateway:

```
uqs query                          # a session on gateway1, every line routed
uqs query --raw                    # plain qcon on gateway1, nothing routed
uqs query --proc rdb1              # qcon localhost:6052:admin:admin, under rlwrap if installed
uqs query --base-port 7000         # gateway1 in a stack started with --port 7000
```

The gateway holds no tables of its own, so what is sent to it is routed: `uqs`
wraps it in TorQ's `.gw.syncexec`, which runs it on the RDB and HDB and joins
what they return. That holds for a one-off expression and for every line of the
gateway session, which is uqs's own console for that reason - qcon is a separate
program, and what is typed into it never passes through `uqs`. `--servers` names
other process types. Three things are not wrapped:

- a `.gw.*` call of your own, and a `\` system command, are sent as typed;
- an in-place `update` or
  `delete ... from `trade` is **refused** - routed, it would change the RDB's live data. The copy form, `update
  ... from
  trade`, is routed like any query; to change a table, name its process with `--proc\`;
- with `--raw`, nothing is: the gateway gets exactly what you typed.

```
uqs query "select count i by sym from trade"   # .gw.syncexec[...;`rdb`hdb]
uqs query "select from trade" --servers hdb    # history only
uqs query "tables[]" --raw                     # the gateway's own tables[]
uqs query "select from trade" --proc rdb1      # rdb1 directly, as typed
```

In the session, `\\` (q's own), `exit` or Ctrl-D leaves; Ctrl-C drops the line
being typed. A line that fails is reported and the session goes on.

It refuses a process that is not running - with the `uqs start` to fix it -
rather than leaving qcon to report a refused connection, which reads the same as
a wrong port. `qcon` ships with kdb+, not with this repository.

Results print as q's own console prints them: the process formats its answer
with `.Q.s`, laid out to this terminal's size (or in full when the output is
piped), and puts its own `\c` back afterwards. To see what `kola` - the IPC
library `uqs` connects with - makes of a result instead, a Polars DataFrame for
a table, Python objects for the rest, choose the `kola` renderer:

```
uqs query "select from trade" --render kola   # this call
export UQS_QUERY_RENDER=kola                  # every call, and the gateway session
```

`--render` wins over `UQS_QUERY_RENDER`; the default is `q`. `--export` always
writes the data itself, whichever is chosen. From a plain q session instead:
`q)h:hopen \`:localhost:6052:admin:admin`, then `h "..."`, then `hclose h\`.

The gateway (6057) is the intended single entry point for querying across the
RDB and HDB together rather than connecting to each directly - see
`lib/torq-finance-starter-pack/docs/gettingstarted.md` and
`lib/torq/code/processes/gateway.q` for its `.gw.execute` API; this repo doesn't
wrap or simplify it further (`query` above connects directly to whichever port
you give it).

## Verifying it's alive

```
uqs summary
```

prints a status table (`up`/`down`, pid, port, color-coded) for every process
defined in `process.csv`, not just the ones `start all` brought up. Per-process
stdout/stderr logs land in `output/uqs/logs/` (`out_<procname>.log` /
`err_<procname>.log`) - check these first if a process shows `down`
unexpectedly.

## What uqf leaves unused

Three access layers are vendored in `lib/torq` and wired into nothing:

- the Grafana JSON datasource adapter
- the generic `dataaccess.q` access layer
- the `dqerest.q` REST bridge

None is loaded by any process in the generated `process.csv`, and none is
referenced under `src/`, `python/` or `docs/`. **This is a decision, not an
oversight** (issue #89, 2026-09-16), recorded here so the next reader does not
rediscover them and assume they are a shortcut.

They look like free functionality and are not. Enabling any of them is backend
work on the order of writing a small API layer, and none of them closes the two
gaps that actually needed new work --- fleet health and backfill status ---
which `python/uqf_frontend` was built for instead. With the frontend serving
both the ops and desk audiences (#53), Grafana's fixed-panel model fits the desk
side's user-driven filtering poorly, and the BFF authorises in Python under one
service credential (#54), which the q-side access layers were designed to
replace rather than sit beside.

They stay in the vendored tree **untouched**, because the standing rule is that
`lib/torq` is a pristine copy of upstream: removing files would turn the next
TorQ upgrade from a copy into a three-way merge, and every overlay in `uqs`
relies on that copy being exact. Unused files cost nothing. If a future audience
genuinely wants Grafana, the adapter is there; the decision to be revisited then
is #54's, not this one.

## MCP server

`python/uqs/src/uqs/mcp.py` (the `uqs-mcp` command) exposes the same
start/stop/restart/summary/print/clean/query/config get/config set/list/
logs/crypto-lifecycle operations as MCP tools (`uqs_start`, `uqs_stop`,
`uqs_get_config`, `uqs_set_config`, `uqs_logs`,
`uqs_crypto_start`/`_stop`/`_status`,
`uqs_crypto_fills_start`/`_stop`/`_status`, etc. - see the file itself for the
full, current list), built with [FastMCP](https://gofastmcp.com/), for an MCP
client (e.g. Claude) to drive the demo directly instead of shelling out to the
CLI. Not exposed: `raw` (an arbitrary passthrough to `torq.sh`, deliberately
left off as a scope boundary). Point an MCP client's server command at:

```
uv run uqs-mcp
```

(stdio transport, the default). `uqs_query` returns a list of row dicts for
table results (via the same `kola`-backed `uqs.stack.runtime.query` the CLI's
`query` command calls), or the raw scalar/dict result otherwise.

## Other commands

Anything `torq.sh` itself supports but isn't wrapped as its own subcommand above -
`debug <processname>` to run one process in the foreground for troubleshooting,
`top <processname>` - is available via `raw -- <args>`, which passes straight
through to `lib/torq/torq.sh`.

An interactive console is no longer among them: **`uqs query` with no
expression** hands the connection to `qcon` instead of running one, and takes
the same `--port`/`--host`/`--user`/`--passwd` a query does:

```
uqs query --port 6052                       # a session on rdb1
uqs query --port 6052 "select from quote"   # one expression, as before
```

It requires `qcon` on `PATH`, which ships with kdb+ rather than with this
repository; without it the command says so and points back at giving an
expression, which works over IPC and needs nothing installed. `rlwrap` is used
for line editing when present and skipped when not.

## Installing

`scripts/dev/install.sh` runs `uv tool install --force --editable python/uqs`,
first removing any install registered under a former package name, then checks
that `uqs` runs. It is idempotent.

Editable means every source edit is live at once. The console script and the
package path are written at install time, though, so after a package rename or
move `uqs` keeps its old name, or fails to import: re-run the script.

**Tab completion**, once per shell, from an interactive terminal:

```
uqs --install-completion      # bash, zsh, fish or PowerShell - detected
```

TAB then completes process names, `--profile` names, `list` kinds and `--sort`
columns, `config get`/`config set` fields and the plant's tables, all read from
the same registry the commands resolve against. `uqs --show-completion` prints
the script instead. From a script or CI there is no tty to detect the shell
from, and the install fails with `Shell None is not supported.`

## Known harmless warnings

`hostname -I`/`hostname -A` (Linux-only flags `torq.sh` calls unconditionally at
startup) print `illegal option` warnings on macOS's BSD `hostname` - safe to
ignore, they don't affect anything the demo actually uses.
