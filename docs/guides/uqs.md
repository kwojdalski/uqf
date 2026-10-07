# Running the uqf stack

`uqs` starts, inspects and stops the TorQ fleet this tree runs - the vendored
`lib/torq/` and `lib/torq-finance-starter-pack/` plus uqf's own jobs - without
writing into either `lib/` directory. [The stack page](../architecture/stack.md)
draws the topology.

## Contents

- [Quick start](#quick-start)
- [Reading the database's shape](#reading-the-databases-shape)
- [Commands](#commands)
- [Replaying a tickerplant log](#replaying-a-tickerplant-log)
- [Listing things](#listing-things)
- [What actually starts](#what-actually-starts) --- including
  [runtimes](#runtimes) and [profiles](#profiles)
- [Changing a process's config](#changing-a-processs-config)
- [Logs](#logs)
- [Connecting](#connecting)
- [Services](#services)
- [Adding a process](#adding-a-process) --- including [installing jobs from
  elsewhere](#installing-jobs-from-elsewhere)
- [MCP server](#mcp-server)
- [Other commands](#other-commands)
- [Installing](#installing)

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
uqs schema fx_orderbook         # one table's columns, types and attributes
uqs schema 'crypto*'        # every table matching a pattern, one block each
uqs schema --proc hdb1      # the history instead of today
uqs schema --port 6052      # a port directly, skipping --proc resolution
uqs schema -i               # browse it, re-read every 5 seconds
uqs schema -i --every 1     # faster; --every 0 re-reads only on R
```

With `-i` the browser keeps asking the process: row counts climb as a feed
publishes, and a table that turns up after the browser opened appears in the
list. The filter and the highlighted row survive each re-read. The line under
the filter box gives the time of the last one. A failed read is reported and the
next tick tries again. `R` re-reads at once.

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

It reads the live process, not `src/etl/plant_tables.q`'s declarations.

Two things the output says that `meta` alone does not:

- **Case is the vector/atom distinction.** `f` is a float column; `F` is a float
  *vector* column, one list per row - the shape `fx_orderbook` and
  `mkt_orderbook` are built around and that every pricing function in `src/`
  expects. They render as `float` and `float vector`.
- **A column's type can change with its contents.** An empty vector column
  reports as `general`, because q cannot know the element type until a row
  exists. The same table reads `general` before its first publish and
  `float vector` after. Not a bug, but worth knowing before treating one reading
  as the schema.

The table argument is a shell-style pattern (`crypto*`, `*trade*`), matched
case-sensitively against the names the process reported. An exact name is just a
pattern that matches itself, so there is one behaviour rather than two - quote
the pattern, or the shell will try to expand it against your filenames first.

## Commands

`uqs --help` lists every command, and `uqs <command> --help` gives its arguments
and options. The sections below cover what `--help` cannot.

`PROCS` is `all` (the default) or one or more process names, each its own word -
`uqs start posbook1 demo_markout1` - which is what lets TAB complete them. A
single quoted `"posbook1 demo_markout1"` still works. `--port` sets
`KDBBASEPORT` (default: the runtime's own base port, `6050` for `uqf`; see the
port table below). `--export FILE` (on `summary`/`query`/`list`/`config get`)
additionally writes the same rows to `FILE` as CSV or Parquet, format inferred
from the extension; a result that is not a table, such as `count t`, is refused.

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

A process's start line cannot drift from the process, which is why they are read
rather than configured. `--port` is not needed either: the plant's `-stackid` is
the stack.

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

`--sort` orders by any column the chosen kind produces, `--reverse` flips it:

```
uqs list processes --sort port              # 6050, 6051, 6052, ...
uqs list processes --sort proctype
uqs list processes --sort port --reverse
uqs list env --sort name
```

Numeric columns sort as numbers (`6100` after `659`) and empty cells go last. An
unknown column is refused with the ones that listing has. The order reaches
`--export` too.

`--interactive` (`-i`) opens the table in a browser instead of printing it, and
filters its rows as you type. On `list`, `summary`, `run list`, `run status`,
`gaps` and `schema`:

```
uqs list processes -i      # type "hdb" to keep the hdb rows
uqs summary -i             # "down" for what is down, "up warn" for both at once
uqs run list -i            # Enter prints the run you picked, for `uqs run show`
```

A term matches any one cell as a substring or, failing that, as a subsequence -
`upq` finds `uqs_plant_q` - and a space between terms means every one must
match. Matching is case-insensitive and ignores colour. Up, down and the page
keys move through the rows; Enter exits printing the highlighted row,
tab-separated; Escape exits printing nothing. It needs a terminal, and refuses
in a pipe rather than hang. `--sort` still decides the order, and `--export`
writes every row, not just the ones a filter left.

`uqs summary -i` also acts on the highlighted process: `s` starts it, `x` stops
it, `r` restarts it, and the table re-reads itself when that finishes; `R`
re-reads it on demand. It also re-reads the fleet every 2 seconds on its own,
keeping the filter and the highlighted row; `--every 10` slows that down and
`--every 0` leaves only `R`. A re-read slower than the interval skips the ticks
it overlaps rather than piling them up. There, letters are commands, so `/`
opens the filter and Enter or Escape goes back to the table.

`uqs graph` draws every process and who feeds whom, as trees, with each edge
labelled by the tables it carries and each process coloured up or down:

```
uqs graph                      # from the feeds down: "if this stops, what starves"
uqs graph posbook1 --upstream  # what must be up for posbook1 to work
uqs graph -i                   # browse it: type to filter, ctrl+t to turn it over
```

The graph is not a tree - one feed reaches much of the fleet - so a process
reached a second time is drawn once and marked `↺ shown above` where it would
hide a subtree. Processes with no declared edges, most of the vendored TorQ
fleet, are listed together at the end. `--offline` skips asking the fleet what
is up. In the browser, a filter keeps every match and the path to it, the side
panel describes the highlighted process, and Enter exits printing its name.

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

### Runtimes

All of the above is the `uqf` runtime, the default. The `torq` runtime is the
starter pack as it ships, with nothing of this tree's added. Each runtime is a
declaration in
[`python/uqs/src/uqs/runtimes.py`](../../python/uqs/src/uqs/runtimes.py): its
data directory, whether it has this tree's pipelines, and whether it has this
tree's overlays on the starter pack. A new runtime is one entry there.

```
uqs --runtime torq start      # or UQS_RUNTIME=torq uqs start
uqs --runtime torq stop
```

  |                | `uqf` (default)                                                         | `torq`                                                            | `crypto`                                                                                        | `fx`                                                                                        |
  | ---            | ---                                                                     | ---                                                               | ---                                                                                             | ---                                                                                         |
  | processes      | the vendored rows, with overlays, plus every pipeline                   | the vendored `process.csv`, unchanged: `feed1` on, `monitor1` off | the vendored rows, with overlays, plus the `crypto` profile's pipelines and what they depend on | the vendored rows, with overlays, plus the `fx` profile's pipelines and what they depend on |
  | tables         | `database.q` plus this tree's (`fx_orderbook`, the crypto mocks, ...)   | the starter pack's `database.q`: `trade`, `quote`, `packets`      | `database.q` plus the tables those pipelines read and write                                     | `database.q` plus the tables those pipelines read and write                                 |
  | config layers  | TorQ's, `scripts/torqconfig` and `scripts/torqcode`, the starter pack's | TorQ's and the starter pack's                                     | as `uqf`                                                                                        | as `uqf`                                                                                    |
  | data directory | `output/uqs`                                                            | `output/uqs-torq`                                                 | `output/uqs-crypto`                                                                             | `output/uqs-fx`                                                                             |
  | base port      | `6050`                                                                  | `6150`                                                            | `6250`                                                                                          | `6350`                                                                                      |

`crypto` and `fx` are focused stacks: the starter pack, this tree's layers, and
one profile's pipelines with everything they depend on in the job graph - the
same walk `uqs start --profile` takes, so the runtime and the profile cannot
disagree about what the profile needs. Their schemas define only the tables
those pipelines read and write, so their HDBs hold nothing they never write, and
they fit the licence's connection cap with room to spare:

```
uqs --runtime crypto start     # cryptomock1 and crypto_markout1, on 6250
uqs --runtime crypto list processes
```

Such a runtime can start only the profiles its processes cover, and refuses the
others naming what is missing, as `torq` does.

Without the service layer, `torq` has none of the [query
policies](../architecture/query-policies.md): no data-access API, no `.pm` on
the gateway, and the starter pack's access list everywhere.

Each runtime keeps its own data directory, because an HDB one runtime wrote
holds tables the other does not declare. Each also has its own base port, and
the spans of ports the runtimes' processes use do not overlap, so `uqs start`
and `uqs --runtime torq start` run side by side with no flags. That is the
quickest way to tell whether a problem is TorQ's or this tree's: the same query
against both at once. `--port` still moves either anywhere.

Before starting, `start` and `restart` check whether another stack already holds
a port they need: another runtime, the same runtime on another `--port`, or a
stack from another checkout of this repository. If one does, the command refuses
before anything starts, naming the stack and how to stop it:

```
$ uqs start
ERROR    | the torq runtime's stack (base port 6050) is already using ports this start needs: stp1 :6050 (pid 4242), ... Stop it with `uqs --runtime torq stop --port 6050`, or start this one elsewhere with --port.
```

`uqs summary` names the runtime and base port it is reporting on in its title.

On `torq`, `list processes` shows the 23 starter-pack processes and
`list profiles` shows only `essential`, the one profile whose processes all ship
with the starter pack. `essential` leaves out `feed1`, so it runs with no data
coming in; plain `start` includes it. Commands that work only on this tree's
pipelines refuse, with the reason: `graph`, `backfill`, `gaps`, `run`, `stream`
and `feed`. So does any profile that needs a uqf process:

```
$ uqs --runtime torq start --profile fx
ERROR    | profile(s) fx needs executions1, fxfeed1, ..., which the torq runtime does not have - it is the starter pack as it ships: its processes and tables, nothing of uqf's. Use --profile essential, or --runtime uqf for this tree's processes
```

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
uqs start --profile essential vectorize1 cross1   # a profile plus named processes
UQS_LICENCE_CONNECTIONS=32 uqs start --profile all   # on a licence that allows it
```

Process names given beside `--profile` are added to the set: the profile's
members, infrastructure first, then the names, each started once. They are not
widened to what they read - a name whose producer is missing is warned about, as
on a plain start - and `all` cannot be added. The budget below is checked on the
whole combined set, so a profile that fits plus names that take it past the cap
is refused before anything starts. `uqs up` takes the same combination.

  | profile     | leaves                                        | slots                           |
  | ---         | ---                                           | ---                             |
  | `default`   | what `start all` runs today                   | 13/14                           |
  | `fx`        | `posbook1`, `demo_markout1`, `fxpositions1`   | 13/14                           |
  | `arbitrage` | `arbitrage1`, `crossarb1`                     | 10/14                           |
  | `depth`     | `vectorize1`, `cross1`                        | 8/14                            |
  | `crypto`    | `cryptomock1`                                 | 5/14                            |
  | `essential` | none - the TorQ stack alone (see below)       | 4/14                            |
  | `all`       | every profile's leaves except `crypto`'s      | 19/14 - refused on this licence |

**A profile over the cap is refused**; a positional `start` over it is only
warned about. `fx` and `arbitrage` each fit and together need sixteen:

```
$ uqs start --profile fx,arbitrage
profile(s) arbitrage, fx need 16 tickerplant connections, and only 14 are
available (16 on this licence, 2 held back for ad-hoc handles). ...
```

**`all` needs a larger licence, and says so.** It is every standing set at once -
the union of the other profiles' leaves, derived so a new leaf joins it
automatically - and that holds nineteen plant connections, more than the
community licence has. On that licence it is refused like any profile over the
cap. On a q licence that allows more concurrent connections, say how many with
`UQS_LICENCE_CONNECTIONS` and it starts: that setting is the budget every start
is held to - `--profile`, `uqs list profiles`' `fits` column and the
positional-start warning. On PeachQ (`UQF_Q_IMPL=peachq`, see the README's
Requirements) there is no cap at all, so `all` starts as it is. `all` leaves out
`crypto`, because the mock replaces cryptorust's recorders rather than joining
them; ask for it with `--profile all,crypto`.

Profiles never change `startwithall`, so `start all` is untouched. A closure
stops at a table fed from outside the stack: `fx` needs `crypto_book` but does
not start `cryptomock1`, which replaces cryptorust's recorder rather than
joining it.

**`essential` is the TorQ stack with nothing on top**: `discovery1`, `stp1`,
`rdb1`, `hdb1`, `wdb1`, `sort1`, `sortworker1`, `gateway1`, `monitor1`,
`housekeeping1`, `sctp1`, `metrics1`, `reporter1` and `tpreplay1` - fourteen
processes, four plant slots (`rdb1`, `wdb1`, `sctp1` and `metrics1` subscribe).
That is the infrastructure every other profile starts less its second HDB
(`hdb2`) and second sort worker (`sortworker2`), plus `reporter1` and
`tpreplay1`, which only `essential` starts. At end of day `wdb1` hands its
writedown to `sort1`, which sorts it into the HDB with `sortworker1`, so `wdb1`
stays free. `reporter1` holds no plant slot, but it does open handles to
`gateway1`, `rdb1` and `hdb1`. `tpreplay1` starts and exits: it is the one-shot
replay [`data replay`](#replaying-a-tickerplant-log) aims with a log, a schema
and an HDB, and started without them it exits at startup, before it reads or
empties anything - so expect it shown down. Composing `essential` with a job
profile - `--profile essential,fx` - starts both sets.

Profiles are declared in `python/uqs/src/uqs/model/profiles.py`, by their
leaves.

### Idle subscribers

A subscriber started without its producer subscribes **successfully**. The table
is defined on the tickerplant whether or not anybody publishes to it, so the
process comes up, heartbeats, reports `up`, and receives nothing for as long as
you leave it. There is no error and no symptom except an output table that stays
empty.

So `start` and `restart` warn when what you start has an input nothing running
publishes:

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

`summary` opens with one line saying which q runs the fleet, and the connection
budget that follows from it:

```
interpreter: kdbx (/Users/me/.kx/bin/q) - 16 connections
interpreter: peachq (/opt/peachq/q) - no connection cap
```

The implementation is asked of the binary; when it disagrees with `UQF_Q_IMPL`,
a warning follows. `uqs list env` carries both as rows.

`summary` shows the whole graph, not just the unsatisfied part of it, in three
columns derived from the same declarations:

```
uqs summary                     # all ten columns
uqs summary --columns status    # the seven status ones, for a narrow terminal
uqs summary --columns "Process,Depends on,Inputs,Outputs"
uqs summary --sort Port         # by any column, numeric-aware; --reverse for descending
uqs summary --sort Status --columns status
```

`--sort` takes any column, shown or not, and sorts as `uqs list --sort` does.

```
┃ Process      ┃ Depends on                  ┃ Inputs                ┃ Outputs        ┃
│ posbook1     │ executions1, marketdata1    │ executions,           │ position       │
│              │                             │ market_data           │                │
│ databento1   │ (databento_mbp10: external) │ databento_mbp10       │ eq_orderbook │
│ executions1  │ fxtradesfeed1, cryptomock1, │ trades, crypto_trades │ executions     │
│              │ (crypto_trades: external)   │                       │                │
│ upstream_    │ (upstream_trades: external) │ upstream_trades       │ imported_trades│
│ backfill1    │                             │ (source)              │                │
```

`Inputs` and `Outputs` are the tables a process subscribes to and publishes;
`Depends on` resolves those inputs to the **processes** that produce them, which
is the question behind every `up, but idle` line above. A table produced from
outside the process list is named as external. A backfill subscribes and
publishes nothing on the plant, so its row shows what its bounded worker
declares instead: the `source` it reads, marked `(source)`, and the `dataset` it
writes. A process with no declared edges - every vendored TorQ one - shows a
dash. On a narrow terminal, `--columns status` drops the graph columns.

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

The probe is the handshake, not a query, so no q code runs on the process.
`--probe-timeout 0` skips it.

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
  until the rest fit. Those show `-` in the Heartbeat column. `uqs start`,
  `restart`, `up` and `summary` each print one line naming the proctypes given
  up, counting only types something in the fleet runs as. On a fully licensed
  kdb+/KDB-X, or on PeachQ, nothing is dropped.
- **uqf's standing ETLs (`metrics`) are monitored too**; `backfill` workers are
  not, because a finished job's heartbeat would age into a false `error`.

To get the upstream behaviour back:

```bash
uqs config set monitor1 startwithall 0
```

### Default ports

Base `6050` for the `uqf` runtime (`6150` for `torq`), override with
`--port <n>`. The vendored infrastructure:

  | Port        | Process       | Role                                                     |
  | ---         | ---           | ---                                                      |
  | 6050        | stp1          | segmented tickerplant                                    |
  | 6051        | discovery1    | service discovery                                        |
  | 6052        | rdb1          | real-time DB (today's ticks)                             |
  | 6053 / 6054 | hdb1 / hdb2   | historical DB (the vendored sample data)                 |
  | 6055        | wdb1          | writedown process (rolls RDB -> HDB)                     |
  | 6056        | sort1         | sorts data before writedown                              |
  | 6057        | gateway1      | single query entry point across hdb/rdb                  |
  | 6059        | monitor1      | process monitor ([above](#monitor1))                     |
  | 6061        | housekeeping1 | log/process housekeeping                                 |
  | 6064        | feed1         | the vendored dummy feed - simulated equity quotes/trades |
  | 6065        | sctp1         | segmented chained tickerplant                            |
  | 6066 / 6067 | sortworker1/2 | sort worker pool                                         |
  | 6068        | metrics1      | metrics collector                                        |

uqf's own processes start at `6069` (`fxfeed1`); every one, with its port, is in
the generated [process table](../reference/processes.md).

## Changing a process's config

`config set` writes an override to `python/uqs/process_overrides.csv` (tracked
in git), applied every time `process.csv` is regenerated - which is every
command, so the generated file itself is never the place to edit. The vendored
`process.csv` is never touched.

```
uqs config get fxfeed1
uqs config get fxfeed1 startwithall
uqs config set fxfeed1 startwithall 0
```

`config get` resolves placeholders as `torq.sh` does at start time -
`load=${KDBHDB}` to the real path, `port={KDBBASEPORT}+3` to `6053`; `--raw`
shows the literal value.

Valid `FIELD`s are `process.csv`'s own columns: `host`, `port`, `proctype`,
`procname`, `U`, `localtime`, `g`, `T`, `w`, `load`, `startwithall`, `extras`,
`qcmd`. A change takes effect on the next `start`/`restart` of that process (the
running process itself isn't touched).

### What sources connect to

`config sources` shows the `sources.csv` the stack reads: the first of TorQ's
application, service and base config layers that has one, the rule
`.proc.getconfigfile` applies. For each row it shows where that source's
credential comes from and what would stop it resolving: a stub, an unset secret
variable, a `${VAR}` outside the list. A secret shows only as set or not set.
`config sources stub SOURCE` adds a `SCAFFOLDED` row for `SOURCE` to the
gitignored application layer's file, to fill in. If that file doesn't exist yet,
it starts as a copy of the one the stack reads, so no row is hidden. A row
already there is never changed. See [where a source
connects](../reference/pipeline-declarations.md#where-a-source-connects-sourcescsv).

```
uqs config sources
uqs config sources stub duckdb_deals
```

## Logs

Every process writes `out_<procname>.log` and `err_<procname>.log` in
`output/uqs/logs/` - check these first when a process shows `down`. `logs` reads
them through the CLI's own coloured logger:

```
uqs logs                          # last 20 lines per process, all processes
uqs logs stp1 rdb1 -n 50          # last 50 lines each, merged and time-sorted
uqs logs -f                       # the last 20 lines, then live, Ctrl-C to stop
uqs logs fxorderbookfeed1 -f --level WARNING   # live tail, warnings/errors only
```

Lines are sorted by the log's own timestamp. `-f` keeps following across
restarts and the daily roll. `--level` filters to that level and above.

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

**`uqs up` is the foreground form of all this**: it starts what `start` would -
the same names, `all`, or `--profile` - then streams those processes' logs to
this console until Ctrl-C, which stops what it started, the way
`docker compose up` does. The console is the run.

```
uqs up                        # the default set, streamed; Ctrl-C stops it
uqs up rdb1 fxpositions1      # just these
uqs up --profile fx --level WARNING
```

What a process prints while it loads is shown too. Processes already running
when it began are left running at Ctrl-C.

### Where a process stopped

Every uqf process script - `torq_stream.q` (every streaming job),
`torq_backfill.q`, `torq_tap.q`, `run_stream.q` - logs the stages where it can
stall, so the last line in `uqs logs <procname>` says where it stopped:

  | Last line                                                                    | What it means                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
  | ---                                                                          | ---                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
  | `qtorq: loading uqf tree from ...` with no `loaded in` after it              | a q file failed to load - the error follows, or is in `err_<procname>.log`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
  | `starting streaming job`                                                     | the job and what it subscribes to and publishes, logged before anything can block                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
  | `waiting for the tickerplant - if this is the last line, it is not running`  | `stp1` is down: `uqs start stp1`. This used to wait forever in silence                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
  | `subscribing` / `streaming job wired - running`                              | subscribed and running                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
  | no `first batch received` for a table                                        | nothing is arriving on it: its publisher is down or publishes nothing                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
  | `first batch received` but no `first rows published`                         | input arrives and the job publishes nothing from it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
  | `on_batch failed`                                                            | the job's handler threw, with the table and the error                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
  | `backfill process failed` then `backtrace`                                   | a backfill's error, and where it happened                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
  | `no credential - running on the source's fixture` (WARNING)                  | a backfill is publishing the fixture, not live data. The line gives the `sources.csv` row to add (`settings_row`), the variable that overrides it (`UQF_SOURCE_CRED_<SOURCE>`), what the value should be for that source - an ODBC connection string, or `host:port` for kdb+ - and an example. Add the row (`uqs config sources stub SOURCE` starts one), or export the variable in the shell you run `uqs backfill` from                                                                                                                                                                                                            |
  | `hdb reload: could not open a handle to every registered hdb` (ERROR)        | a backfill wrote its rows and the running HDB has not reloaded them. `registered` is how many HDBs discovery knows, `opened` how many let this process in; TorQ's own `connection to ... failed: access` line just before says it was refused. A backfill connects as the ETL identity (`appconfig/passwords/metrics.txt`) unless `passwords/backfill.txt` or `<procname>.txt` gives it its own - see its `outbound credential` line at startup. `hdb reload requested` now reports `registered`, `opened` and `reloaded`, so `0 0 0` is "no HDB running"                                                                             |
  | `idle - every window in the range is already covered`                        | nothing to do at this `--version`: coverage says the range is done. A new source release is a new version                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
  | `checkpoint is for another run - starting from the beginning`                | the range or version changed since the last run, so its checkpoint does not apply                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
  | `state file unreadable - using the previous generation` (WARNING)            | a cursor, checkpoint or ledger file did not parse - a crash mid-write, a full disk, a hand edit - and its `.bak` was used instead: at most one page or window is done again                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
  | `load_checkpoint: ... is unreadable ... and so is its .bak`                  | neither generation of a backfill's checkpoint parses. `uqs remove checkpoint WORKER` starts the run over; windows already in the coverage ledger are still skipped                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
  | `retrying after a transport error`                                           | the source failed transiently; the attempt, backoff and error follow                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |

**More detail - the DEBUG level** - adds the process's pid, port and cwd, the
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

One level below that, `uqs backfill <worker> ... --trace` logs every query the
source is sent (the SQL statement, or the q lambda and its bounds) and includes
everything `--debug` shows. The log record stays one line, so `-f` and `grep`
keep working, with the query as a full-length q string. `uqs logs` prints it as
a header and an indented block:

```
query sent range_from=2026.09.10D00:00:00.000000000 range_to=2026.09.11D00:00:00.000000000
    {[from_ts;to_ts]
            select deal_id from `demo_deals
                where deal_time>=from_ts, deal_time<to_ts
          }
```

`uqs logs --level DEBUG` hides these lines again.

Every line logged while a window runs carries its `worker`, `run` and
`range_from`/`range_to`, so one window's lines can be grepped together; request
traces add a `request` number shared by that request's `sent`, `returned` and
`failed` lines.

Two limits apply when grouping lines by these fields:

- **A reaction's lines carry the window that fired it.** Reactions run
  synchronously when a window publishes, so their lines carry that window's
  `worker`, `run` and range unless the reaction sets those fields itself.
  Reactions replayed at the start of a run carry none of them. Timers and IPC
  handlers never run in the middle of a window, so they never pick up its
  fields.
- **`request` restarts at 1 in every process.** When reading logs from more than
  one process, identify a request by its process and `request` together. The
  process is given by the log file (`out_<procname>.log`), not by a field on the
  line.

### CLI's own logging

`logs --level` filters what the *q processes* wrote. `--debug` shows what `uqs`
itself is doing:

```
uqs --debug summary        # this invocation only
uqs summary --debug        # the same, spelled on the command
LOG_LEVEL=DEBUG uqs summary   # same, for a shell session
```

`NO_COLOR=1` turns colour off; `FORCE_COLOR=1` keeps it through a pipe. On
`summary`, `--debug` says why a column is blank:

```
summary base_port=6050 torqdata=.../output/uqs
process listing: 47 line(s)
configured ports for 46 process(es)
monitor1 not reached; Heartbeat column is a monitoring gap, not a verdict
parsed 46 row(s): 23 up, 23 down
starved process(es): executions1, marketdata1
```

It also prints each process's load time on its latest start:

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

What each process does, one short card each, is [Services -
Showcase](../services/README.md); a card links to a full page on how the process
is built and why where there is one. This guide covers operating the stack as a
whole. `tap1`, the diagnostic subscriber that logs every batch, is [there
too](../services/tap.md).

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

**Every query must be one `--trace` can see.** A polling feed or a backfill
source that calls its handle directly, as `h(...)`, `h@...` or `h"select ..."`,
is refused before anything is copied, `--dry-run` included. The refusal names
each line. Send each query through `.qetl.source.ipc_call` (a polling feed's
cursor), `.qetl.source.ipc` (a backfill window), `.qetl.source.local` or
`.qetl.io.odbc.run_sql`. Those log it at TRACE for `uqs stream preview --trace`
and `uqs backfill --trace`. A line that must stay direct ends with
`/ untraced: <why>`.

After installing it regenerates the derived files (`process_ports.csv`,
`pipeline_dag.q`, `processes.md`, `docs/man.q`) - a declaration the generators
refuse is reported here, with what they said - and warns about any table a job
reads, publishes or fills that `src/etl/plant_tables.q` does not define. Then
check it is running:

```
uqs list processes                      # the registry sees the new processes
uqs start --print <procname>                    # the exact start line
uqs start <procname>                    # streaming jobs
uqs backfill <worker> --version v1 --from 2026-09-01 --to 2026-09-02 --mode plan
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

It refuses a process that is not running, naming the `uqs start` to fix it.
`qcon` ships with kdb+, not with this repository.

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

## MCP server

`uqs-mcp` exposes the CLI's operations as MCP tools (`uqs_start`, `uqs_summary`,
`uqs_query`, ... - [`mcp.py`](../../python/uqs/src/uqs/mcp.py) has the list),
for an MCP client to drive the stack. `raw` is not exposed. Point the client's
server command at:

```
uv run uqs-mcp
```

(stdio transport). `uqs_query` returns row dicts for a table.

## Other commands

Anything `torq.sh` itself supports but isn't wrapped as its own subcommand above -
`debug <processname>` to run one process in the foreground for troubleshooting,
`top <processname>` - is available via `raw -- <args>`, which passes straight
through to `lib/torq/torq.sh`.

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
