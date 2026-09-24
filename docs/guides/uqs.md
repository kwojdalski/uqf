# Running the uqf stack

`lib/torq/` (the TorQ production framework) and `lib/torq-finance-starter-pack/`
(a layered reference application built on top of it - feed handlers,
tickerplant, RDB, an HDB seeded with two days of sample quote/trade data,
gateway) are both vendored into this repo for reference (see the README's
Licensing section) but neither is wired into `src/init.q` or anything else
uqf itself runs - this library has no long-running processes for TorQ's
machinery to manage.

The `uqs` CLI (`python/uqs/`) bridges the two vendored trees so
you can actually start the demo up and poke at it, without editing or
writing into either `lib/` directory. The actual bootstrapping/config logic
lives in `python/uqs/src/uqs/`'s `model/` and `stack/` modules,
shared with `uqs_mcp.py`'s FastMCP server (see "MCP server" below) so the
CLI and the MCP tools can't drift apart. It's a standalone package
(`python/uqs/`).

See [docs/architecture/stack.md](../architecture/stack.md) for diagrams of the current
process topology, table-level data pipeline, and config-generation flow.

## Contents

- [Quick start](#quick-start)
- [Reading the database's shape](#reading-the-databases-shape)
- [Commands](#commands)
- [Listing things](#listing-things)
- [What actually starts](#what-actually-starts) — including [profiles](#profiles-a-named-start-set-that-fits)
- [Changing a process's config](#changing-a-processs-config)
- [Logs](#logs)
- [Connecting](#connecting)
- [Verifying it's alive](#verifying-its-alive)
- [What lib/torq ships that uqf deliberately does not use](#what-libtorq-ships-that-uqf-deliberately-does-not-use)
- [Services](#services)
- [MCP server](#mcp-server)
- [Other commands](#other-commands)
- [Known harmless warnings](#known-harmless-warnings)

## Quick start

The CLI is also a `uqs` script entry point
(`pyproject.toml`'s `[project.scripts]`), so every command below is
`uv run --project python/uqs uqs ...`. Shorter still,
one-time setup:

```
scripts/dev/install.sh
```

installs `uqs` onto your `PATH` as an editable link back to this repo's
source, so from then on, from anywhere:

```
uqs start all      # start every startwithall=1 process
uqs summary        # status table
uqs stop all       # stop everything
```

The script is `uv tool install --force --editable python/uqs` plus the two
steps a one-liner skips: removing any install registered under a former
distribution name, and then checking the command actually runs. Both matter
for the reason the next paragraph gives. It is idempotent - re-run it after
pulling, or any time `uqs` behaves like an older copy of itself.

**Tab completion**, once per shell, after the install above:

```
uqs --install-completion      # bash, zsh, fish or PowerShell - detected
```

Open a new shell and TAB completes process names (`uqs start pos<TAB>`,
`uqs logs rdb1 st<TAB>`), `--profile` names, the kinds `list` takes and the
columns `--sort` takes for that kind, `config-get`/`config-set` fields,
`--port` on `query`/`schema` (zsh and fish show which process each port
belongs to), `--level`, `--stream`, `new-job --kind`, and the plant's tables
for `--subscribes`/`--publishes`. Every value is read from the same registry
the command resolves against, so a new pipeline completes as soon as it is
declared. `uqs --show-completion` prints the script instead of installing it.
It needs `uqs` on your `PATH`: the `uv run --project python/uqs uqs` form
has nothing for the shell to call.

**Editable updates the code, not the script names, and not the path.** A
`.pth` file points the install at `python/uqs/src`, so every source edit is
live the moment it is saved - no reinstall for a new command, option or fix.
Two things are NOT live, and both bite after a rename:

- **The console scripts** in `[project.scripts]` are generated once, at
  install time, and the generated script names its import directly. An
  install predating a rename keeps working, keeps picking up new code, and
  still calls itself by the old name - which looks like the rename never
  happened.
- **The path in the `.pth` file** is written once too, so an install made
  before the package DIRECTORY moved points at somewhere that no longer
  exists. That one does not degrade gracefully: the command fails to import
  rather than running old code, which is the better of the two failures but
  needs the same fix.

This package has been renamed twice, so there are two old names in
circulation. `uv tool list` shows which you have:

| if `uv tool list` shows | it is from | the script imports |
|---|---|---|
| `uqs` | now | `uqs.cli` |
| `uqf-stack` | the rename before this one | `uqf_stack.cli` - gone |
| `torq-orchestrator` | before that | `torq_orchestrator.cli` - gone |

A distribution rename means `uv tool install` will NOT replace the old entry:
it installs a second tool, and the old one goes on owning a broken command on
your `PATH`. Uninstall by the old name first, whichever you have:

```
uv tool uninstall uqf-stack              # or torq-orchestrator, if older
uv tool install --force --editable python/uqs
```

**The data directory has moved twice.** It was `scripts/output/uqf-stack/`,
became `scripts/output/uqs/` with the package rename, and is now
`output/uqs/`, beside everything else the repository generates at runtime.
It holds the HDB, the tickerplant logs and the write-down database, and
nothing reads the old paths any more - `uqs` refuses to run while an old one
exists and the new one does not, and prints the command. No downtime needed:
every path is inside the one checkout, so this is a rename on one filesystem
and every running process keeps the files it already has open:

```
mkdir -p output && mv scripts/output/uqs output/uqs          # or scripts/output/uqf-stack
```

Restart the stack afterwards when convenient, so each process reopens at the
new path rather than through the handle it already holds.

Forgetting the move does not error - `bootstrap()` regenerates `process.csv`
and `database.q` on every command, so the stack would start cleanly against an
EMPTY HDB and every historical query would return no rows. That is why
`check_data_dir_was_migrated` refuses to run when the old directory is present
and the new one is not, and prints the `mv` above.

It refuses `stop` as well, which looks unhelpful and is not: `process.csv`
lives inside the data directory, so a `stop` allowed through would bootstrap
the NEW directory into existence, leave both present, silence the check for
good and orphan the old HDB. The block stays; the message is what had to give,
and it no longer tells you to stop first.

`--force` is what re-generates the scripts. The same applies to any entry
point added or renamed later.

Without that one-time step, or in CI/a fresh checkout, fall back to `uv
run`:

```
uv run --project python/uqs uqs start all
uv run --project python/uqs uqs summary
uv run --project python/uqs uqs stop all
```

Run from anywhere - the command resolves its own location and works out
`lib/torq`/`lib/torq-finance-starter-pack`'s absolute paths itself; `uv run
--project python/uqs` (or the installed `uqs`)
resolves that package's dependencies (typer, loguru, rich, kola, fastmcp)
on demand, no separate `uv sync` step needed. First `start` bootstraps a
data directory at
`output/uqs/` (gitignored with the rest of `output/`, which also holds
`timer_replay_example.q`'s run artifacts and the dev tools' databases) by
copying the app's sample `hdb/`/`dqe/` data there - `logs/`,
`tplogs/`, `wdbhdb/`, and every process's actual read/write activity all
happen inside that directory, never inside `lib/`. Run `uqs clean`
to wipe it and start fresh next time.

Requires KDB-X (`q` on `PATH`)
elsewhere in this repo for `src/`/`tests/` - plus `envsubst` and `rlwrap`
(TorQ's own `torq.sh`, which this still drives under the hood, needs both;
on macOS: `brew install gettext rlwrap`).

## Reading the database's shape

`schema` answers "what tables are there, and what shape are they" without
anyone typing `meta` at a q prompt:

```
uqs schema                  # every table on rdb1, with row and column counts
uqs schema quotes           # one table's columns, types and attributes
uqs schema 'crypto*'        # every table matching a pattern, one block each
uqs schema --proc hdb1      # the history instead of today
uqs schema --port 6052      # a port directly, skipping --proc resolution
```

### When the history will not answer at all

```
$ uqs schema --proc hdb1
could not read the schema from hdb1 (6053):
  "./2015.01.07/arbitrage. OS reports: No such file or directory"
```

That is not a missing table. A partitioned kdb+ database requires every
table to exist in **every** partition, and one absent directory fails the
whole query - so the error names whichever table sorts first rather than
the partition that is actually short.

```
uqs hdb-check         # which partitions are missing what
uqs hdb-check --fix   # write an empty copy of each into them
```

`--fix` is additive and idempotent: a table directory that exists is never
touched, and a table a partition holds that `database.q` no longer declares
is left alone - that is history. Running it twice changes nothing the
second time.

It should rarely be needed by hand, because `bootstrap` now does it on
every `uqs` command. It is there because the situation is easy to
create: each day's partition holds whatever tables existed when it was
written, so **every table added leaves every earlier partition short**, and
the vendored sample partitions ship holding `quote` and `trade` alone. TorQ
fills only the partition the wdb is currently writing
(`lib/torq/code/processes/wdb.q`'s `filldb`), so nothing ever goes back
(#348).

**It reads the live process, not the declarations.**
`scripts/processes/uqs_tables.q` says what the tickerplant is *configured* to
carry; that is not evidence a table exists in the process you are about to
query. A tickerplant that failed to load its schema file, or an RDB that has
not replayed, looks identical in every other view - so reporting the
declarations here would be confidently wrong exactly when it mattered.

Two things the output says that `meta` alone does not:

- **Case is the vector/atom distinction.** `f` is a float column; `F` is a
  float *vector* column, one list per row - the shape `quotes` and
  `mkt_orderbook` are built around and that every pricing function in `src/`
  expects. They render as `float` and `float vector`.
- **A column's type can change with its contents.** An empty vector column
  reports as `general`, because q cannot know the element type until a row
  exists. The same table reads `general` before its first publish and
  `float vector` after. Not a bug, but worth knowing before treating one
  reading as the schema.

Row counts are shown because "declared but empty" and "carrying data" is
usually the thing being looked for, and empty tables are named explicitly
rather than left to be spotted.

The table argument is a shell-style pattern (`crypto*`, `*trade*`), matched
case-sensitively against the names the process reported. An exact name is
just a pattern that matches itself, so there is one behaviour rather than
two - quote the pattern, or the shell will try to expand it against your
filenames first.

## Commands

```
start [PROCS] [--port N]              start (default: all startwithall=1 processes)
stop [PROCS] [--port N]               stop
restart [PROCS] [--port N]            restart
summary [--port N] [--export FILE] [--columns all|status|C,...] [--timeout S]
        [--probe-timeout S] [--debug]  status table plus the declared graph
                                      (--columns status for just up/down/pid/port/
                                      Responds; --timeout defaults to 120s,
                                      --probe-timeout to 0.5s per process;
                                      --debug adds each process's load time)
print [PROCS] [--port N]              show exact startup command line(s), no-op otherwise
backfill WORKER --version V --from T --to T [--port N]
                                      run a bounded worker over [--from, --to); dates
                                      without an offset are UTC. Passed to the process
                                      as flags, never environment variables
clean                                 wipe output/uqs/
query EXPR --port N [--export FILE]   run a synchronous q expression against a process
schema [TABLE|PATTERN] [--proc P] [--export FILE]  tables in a running process, or the
                                      columns of every table matching a pattern
list [KIND] [--port N] [--export FILE] [--sort COL] [--reverse]  list every item of KIND
                                       ('processes', 'profiles', 'fields', 'overrides',
                                       'env', 'dependencies') - no argument shows the kinds
config-get PROCNAME [FIELD] [--port N] [--raw] [--export FILE]  show a process's effective
                                                 process.csv row (or one field), with
                                                 placeholders resolved unless --raw
config-set PROCNAME FIELD VALUE       persist a process.csv field override for a process
logs [PROCS] [-f] [-n N] [--level L]  tail out_/err_*.log through the CLI's own colorized
                                       logger instead of raw files (see "Logs" below)
crypto start/stop/status              proof of concept: cryptorust (Rust) publishing over
                                       kdb+ IPC (see "crypto recorder" below)
crypto fills-start/fills-stop/        proof of concept: cryptorust's simulated + real fills
  fills-status                        over kdb+ IPC (see "crypto fills recorder" below)
raw -- ARGS...                        pass any other torq.sh verb straight through
                                       (e.g. `raw -- debug rdb1`, `raw -- top feed1`)
```

`PROCS` is `all` (the default) or one or more process names, each its own
word - `uqs start posbook1 markout1` - which is what lets TAB complete them.
A single quoted `"posbook1 markout1"` still works. `--port` sets
`KDBBASEPORT` (default `6050`, see the port table below). `--export FILE`
(on `summary`/`query`/`list`/`config-get`) additionally writes the same
rows to `FILE` as CSV or Parquet, format inferred from the extension - see
`python/uqs/README.md`'s "Exporting output" section. Full
`--help` is available on the command itself and on every subcommand.

## Listing things

`list` (no argument) prints the kinds it knows about; `list KIND` lists
every item of that kind - not just processes:

```
uqs list
uqs list processes
```

- `processes` - every process's `procname`/`proctype`/`port`/`startwithall`,
  resolved and with any `config-set` overrides applied - the full set
  `config-get`/`config-set`/`start <procname>` accept, without already
  needing to know a name ahead of time - plus the `inputs` and `outputs`
  its job declares: the tables it subscribes to and publishes, or for a
  backfill worker the dataset it fills. The same declarations `summary`'s
  graph columns come from, but readable without a running stack
- `fields` - `process.csv`'s valid columns (what `config-set`'s `FIELD`
  argument accepts)
- `overrides` - every `config-set` override currently in effect
- `env` - `build_env()`'s resolved `KDBBASEPORT`/`KDBHDB`/... values (the
  same env `config-get`'s placeholder resolution and `torq.sh` itself use)
- `dependencies` - each process's input tables and who publishes them, so
  you can see what a process needs before starting it on its own

New kinds are one function + one `LISTABLE_KINDS` entry, not a new
CLI command each time - see `stack/listing.py`'s `_list_*` functions.

`--sort` orders by any column the chosen kind produces, `--reverse` flips it:

```
uqs list processes --sort port              # 6050, 6051, 6052, ...
uqs list processes --sort proctype
uqs list processes --sort port --reverse
uqs list env --sort name
```

Two things it gets right that a plain sort would not. **Numeric columns sort
numerically**: `port` is a string, and as text `6100` comes before `659`,
which looks like the sort silently did nothing on the one column most worth
sorting. And **empty cells group at the end** rather than sorting as the
empty string among real values - an unset override is absent, not "before
aaa".

The column name is matched case-insensitively, and because the columns differ
per kind there is no fixed set to check against: an unknown name is refused
with the columns that listing actually produced. The order reaches `--export`
too, so an exported CSV matches what was on screen.

## What actually starts

By default (`start all`) the processes marked `startwithall=1` come up: the
vendored rows in `lib/torq-finance-starter-pack/appconfig/process.csv`, plus
uqf's own pipelines, appended as extra rows to a *copy* of that csv that
`uqs.stack.runtime.bootstrap()` generates on the fly (never editing the
vendored file itself). The vendored README explains why the rest stay off:
the KDB-X community edition's connection limits mean `reporter1`,
`filealerter1`, `dqc1`/`dqcdb1`, `dqe1`/`dqedb1` stay off unless you have a
fully-licensed kdb+/KDB-X. `killtick` and `tpreplay1` are on-demand utility
processes, not part of the standing stack, so they also don't auto-start.

### Profiles: a named start set that fits

`start all` is one answer to "what should be running", and on this licence it
is nearly the only one you can afford. Fourteen tickerplant slots are
available (sixteen on the licence, two held back for ad-hoc handles) and the
default start holds **thirteen**. The arbitrage chain needs four, so it cannot
run until something stops.

A profile names the processes you actually came for; everything they read is
derived from the same dependency graph `summary`'s **Depends on** column uses:

```
uqs list profiles
uqs start --profile arbitrage
uqs start --profile depth,crypto
```

| profile | leaves | slots |
|---|---|---|
| `default` | what `start all` runs today | 13/14 |
| `fx` | `posbook1`, `markout1`, `fxpositions1` | 12/14 |
| `arbitrage` | `arbitrage1`, `crossarb1` | 10/14 |
| `depth` | `vectorize1`, `cross1` | 8/14 |
| `crypto` | `cryptomock1` | 5/14 |

**A profile over the cap is refused, not warned.** That is the opposite of a
positional `start`, deliberately: naming processes yourself is your call, and
an ordering that briefly exceeds the cap is a legitimate thing to do. A
profile is a set *this tree* named, so one that cannot run is its mistake to
report rather than yours to discover when the plant resets a handle. `fx` and
`arbitrage` each fit and together need seventeen:

```
$ uqs start --profile fx,arbitrage
profile(s) arbitrage, fx need 17 tickerplant connections, and only 14 are
available (16 on this licence, 2 held back for ad-hoc handles). ...
```

Two things profiles deliberately do **not** do. They do not change
`startwithall`, so `start all` is untouched - `default` describes that set so
the two can be compared, and a test fails if they drift. And a closure stops
at a table fed from outside the stack: `posbook1` needs `crypto_book`, which
cryptorust's recorder publishes, so the `fx` profile does **not** drag in
`cryptomock1` - that mock replaces the recorder rather than joining it, which
is why it is a profile of its own.

Profiles are declared in `python/uqs/src/uqs/model/profiles.py`. Each one
names leaves, never members, so adding a process to a chain does not mean
editing whatever profiles contain it.

**The slot count has been checked against the wire**, not just computed. On a
stack running the default set, `lsof -nP -iTCP:6050 -sTCP:ESTABLISHED` showed
thirteen client connections — exactly the thirteen this predicts, name for
name — plus `stp1` itself as the listener and one external process
(cryptorust's recorder) that is in no `Pipeline` and so in no profile. The
budget is blind to anything outside the registry that opens a handle, which
is part of what the two reserved slots absorb.

### Started is not the same as fed

A subscriber started without its producer subscribes **successfully**. The
table is defined on the tickerplant whether or not anybody publishes to it,
so the process comes up, heartbeats, reports `up`, and receives nothing for
as long as you leave it. There is no error and no symptom except an output
table that stays empty.

That matters more than it used to: seven processes are on demand, and the
direct-arbitrage chain is three of them deep. So `start` and `restart` say
something when what you are starting has an input nothing running
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

`summary` shows the whole graph, not just the unsatisfied part of it, in
three columns derived from the same declarations:

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
`Depends on` resolves those inputs to the **processes** that produce them,
which is the question behind every `up, but idle` line above. A table
produced from outside the process list is named as external rather than
dropped - "nothing in this list provides it" and "nothing provides it" are
different facts, and only one is a problem. Cells break at the commas once
there are more than two entries, so a table name is never split across
lines, and a process with no declared edges - every vendored TorQ one - shows
a dash.

Ten columns need a wide terminal; at eighty they squeeze and Rich elides the
headers. `--columns status` gives the seven status columns back. They are shown by
default anyway, because a column nobody knows about answers nothing: a reader
on a narrow terminal can ask for fewer, while one who never learns the graph
is there has no such move.

### Responds: can each process answer within half a second

`Status` comes from a PID lookup, and a hung process still has a PID.
`Heartbeat` notices only after a tolerance of missed beats. `Responds` asks
every `up` process directly, all at once, whether it can complete the kdb+
handshake within `--probe-timeout` (0.5s by default):

| Responds | Meaning |
|---|---|
| `4ms` | it answered, in that long |
| `timeout` | it accepted the connection but did not answer in time - busy in a long query, a timer or its load. A process at its licence connection cap can look the same |
| `reset` | it dropped the connection - what a process past its connection cap does to a new handle |
| `rejected` | it closed the connection without answering: it refused the credentials |
| `refused` | nothing is listening on its port |
| `-` | not probed: the process is down, or `summary`'s own budget ran out |

Any `up` process that does not answer is also named in a red line under the
table.

The probe is the handshake, not a query, so no q code runs on the process;
q answers the handshake from its main loop, which is exactly what is busy
when a process is unresponsive. It is done from `uqs` with a plain socket,
because kola's timeout is whole seconds. It is not q's `-T`, which limits
how long one client query may run on a process - it says nothing about a
process stuck in its own timer, and it applies to every client, the
gateway's long queries included. Each probe briefly holds one inbound
connection, and TorQ logs it like any other. `--probe-timeout 0` skips it.

### summary gives up after two minutes

`summary` is the command you run when something is already wrong, which makes
it the worst thing in the CLI to hang - and both of its blocking steps could.
`torq.sh summary` is a subprocess that had no timeout at all, and the
heartbeat lookup talks to `monitor1`, which at its connection cap accepts the
TCP connection and then never answers.

```
uqs summary --timeout 10   # fail fast
uqs summary --timeout 0    # wait forever
```

It is one **budget for the whole command**, not a limit per call - two steps
given ten seconds each is a twenty-second hang, which is not what anyone
means by a ten-second timeout. The subprocess is asked first and the
heartbeat query gets whatever is left, with a floor of one second so the last
step fails on its own terms rather than on an expired clock. Running out is a
refusal, not a traceback:

```
torq.sh summary did not finish within 120s. It is still bootstrapping, or a
process it queries is not answering - raise --timeout if the stack is simply
slow to start
```

A heartbeat lookup that runs out is not fatal: it degrades to the same
"monitor1 could not be reached" the column already knows how to say.

Both warnings are **advisory and never block a start**. Bringing a subscriber
up before its feed is how you avoid missing the first batch, and some tables
come from outside the process list entirely - the Databento feed handler,
cryptorust's recorders, a backfill run - which is why those are named as
context rather than reported as faults. Processes you name in the same
command count as present, so the recommended form for a chain is silent:

```bash
uqs start marketdata1 superbook1 arbitrage1
```

`crossarb1` is a second consumer of that chain, answering the other
arbitrage question - the direct book against a synthetic route rather than
two sources on one pair. The chain plus BOTH detectors is four plant
connections against three spare, so run one or the other unless you stop
something first. See [the cross-arbitrage service](../services/cross-arbitrage.md).

The graph behind all of this is the `subscribes`/`publishes` pair on each
`Pipeline`, the same declaration the generated `database.q` and the `.qdag`
job graph are built from - so what you are warned about and what is running
cannot describe different systems.

### monitor1 is the one vendored default this tree overrides

Upstream ships `monitor1` with `startwithall=0`, for the licence reason
above. That left the heartbeat unusable: every process *publishes* a
heartbeat regardless, but `monitor1` is the only thing that *collects* them,
so `.hb.hb` was empty and `uqs summary`'s Heartbeat column read
"not collected" on a perfectly healthy stack. A health signal that only ever
appears if an operator knows to start one more process by hand is not a
health signal, so `procs.VENDORED_STARTWITHALL_OVERLAY` turns it on.

Two consequences worth knowing:

- **Under the community licence the coverage is partial, and deliberately
  so.** `monitor1` opens one handle per process it monitors, and the licence
  caps a q process at 16 concurrent connections. Untrimmed on this tree it
  wants 32 - so it saturated, and a saturated monitor cannot *accept* the
  handle `uqs summary` needs to read `.hb.hb`. Heartbeats were
  collected correctly and nothing could read them, which is the worst shape
  of monitoring failure available: an empty Heartbeat column on a fleet that
  was being monitored perfectly well.

  `stack/monitor_budget.py` therefore trims `.servers.CONNECTIONS` to fit
  `LICENCE_CONNECTION_LIMIT` minus `INBOUND_RESERVE`, giving up
  proctypes in `MONITOR_CONNECTION_SACRIFICE_ORDER` - `sortworker`,
  `reporter`, `housekeeping`, `feed`, then `metrics` - until the rest fit.
  Core infrastructure is never given up: a stack whose `rdb` or plant is
  unheard is not monitored in any useful sense. Processes of a dropped
  proctype show `-` in the Heartbeat column, and `summary` names them rather
  than leaving the dash to be guessed at. On a fully-licensed kdb+/KDB-X
  nothing is trimmed and it reaches the whole fleet.

  Partial coverage that can be queried beats full coverage that cannot.
- **`monitor1` subscribes to the proctypes in `.servers.CONNECTIONS`**, and
  the vendored settings file lists TorQ's own types only. The orchestrator
  reads that list back out and appends `metrics` (via a `-.servers.CONNECTIONS`
  command-line override, since `.proc.override[]` runs after every config
  layer) so uqf's standing ETLs are covered too. `backfill` is deliberately
  left off: a bounded worker's heartbeat row would outlive the job and age
  into a permanent false `error`.

To get the upstream behaviour back:

```bash
uqs config-set monitor1 startwithall 0
```

Default ports (base `6050`, override with `--port <n>`):

| Port | Process | Role |
|---|---|---|
| 6050 | stp1 | segmented tickerplant |
| 6051 | discovery1 | service discovery |
| 6052 | rdb1 | real-time DB (today's ticks) |
| 6053 / 6054 | hdb1 / hdb2 | historical DB (the vendored sample data) |
| 6055 | wdb1 | writedown process (rolls RDB -> HDB) |
| 6056 | sort1 | sorts data before writedown |
| 6057 | gateway1 | single query entry point across hdb/rdb |
| 6061 | housekeeping1 | log/process housekeeping |
| 6064 | feed1 | the vendored dummy feed - simulated equity quotes/trades |
| 6065 | sctp1 | segmented chained tickerplant |
| 6066 / 6067 | sortworker1/2 | sort worker pool |
| 6068 | metrics1 | metrics collector |
| 6069 | fxfeed1 | uqf's own feed - simulated FX quotes (see below) |
| 6074 | quotesfeed1 | uqf's own feed - simulated depth-aware FX quotes into `quotes` (see below) |

## Changing a process's config

`config-get`/`config-set` read and write a *process.csv field override* -
not the vendored `process.csv` (never edited) and not the *generated* one
in `output/uqs/` either (regenerated from scratch on every
`bootstrap()` call, i.e. every `start`/`stop`/`summary`/...  - anything
written directly there would just be clobbered on the next command).
Overrides persist instead in `python/uqs/process_overrides.csv`
(a small `procname,field,value` csv, created on first `config-set` -
tracked in git like any other config, not gitignored), and
`bootstrap()` applies them on top of the vendored+fxfeed1 rows every time
it (re)generates `process.csv`.

```
uqs config-get fxfeed1
uqs config-get fxfeed1 startwithall
uqs config-set fxfeed1 startwithall 0
```

`config-get` resolves `process.csv`'s two placeholder styles by default -
`${VAR}`/`$VAR` (e.g. `load=${KDBHDB}` -> the real path,
`U=${TORQAPPHOME}/appconfig/passwords/accesslist.txt` -> the real path) and
the port column's own `{VAR}`/`{VAR}+N`/`{VAR}-N` arithmetic shorthand
(e.g. `port={KDBBASEPORT}+3` -> `6053`) - the same values `torq.sh` itself
substitutes at process-start time, evaluated against
`build_env(paths, --port)`. Pass `--raw` to see the literal, unresolved
value instead (e.g. to copy it into a `config-set` call).

Valid `FIELD`s are `process.csv`'s own columns: `host`, `port`, `proctype`,
`procname`, `U`, `localtime`, `g`, `T`, `w`, `load`, `startwithall`,
`extras`, `qcmd`. A change takes effect on the next `start`/`restart` of
that process (the running process itself isn't touched).

## Logs

Every process writes its own `out_<procname>.log`/`err_<procname>.log` in
`output/uqs/logs/` (stable symlink aliases TorQ itself
maintains onto the current run's timestamped file - see `torq.q`'s
`createlog`/`fileredirect`), in a fixed pipe-delimited format:
`time|host|proctype|procname|loglevel|id|message`. `logs` tails these
through the same colorized logger the rest of the CLI uses, instead of
`tail`-ing N raw files by hand - no TorQ-side config change needed (no
`-jsonlogs`, nothing added to `extras`):

```
uqs logs                          # last 20 lines per process, all processes
uqs logs stp1 rdb1 -n 50          # last 50 lines each, merged and time-sorted
uqs logs -f                       # live tail, every process, Ctrl-C to stop
uqs logs quotesfeed1 -f --level WARNING   # live tail, warnings/errors only
```

`-f`/`--follow` spawns one `tail -F` per file (so it follows TorQ's own log
rolling/restart-aliasing correctly) and merges them through a queue; without
`-f`, the last `-n`/`--lines` lines of each file are read, parsed, and
printed sorted by the log's own timestamp - not wall-clock arrival order.
`--level` filters to that level and above (`DEBUG`/`INFO`/`WARNING`/`ERROR`).

`uqs multitail` follows the same files in
[multitail](https://www.vanheusden.com/multitail/), one pane per file,
titled with its file name, instead of merging them - so two busy processes
stay side by side rather than interleaved. It takes the same process names
and needs the `multitail` binary (`brew install multitail`,
`apt install multitail`):

```
uqs multitail rdb1 fxpositions1          # a pane for each out_/err_ log, stacked
uqs multitail all --stream err -c 2      # every process's err_ log, in two columns
uqs multitail stp1 -n 100 --print        # show the multitail command, run nothing
```

A process that has never started has no log and gets no pane; a name that is
not a process is refused. Press `q` to leave multitail.

### The CLI's own logging, which is a different thing

`logs --level` filters what the *q processes* wrote. It says nothing about
what `uqs` itself is doing, and the two are easy to confuse when a
command reports something surprising about a fleet whose own logs look fine.

```
uqs --debug summary        # this invocation only
uqs summary --debug        # the same, spelled on the command
LOG_LEVEL=DEBUG uqs summary   # same, for a shell session
```

`--debug` wins over `LOG_LEVEL`; an unrecognised `LOG_LEVEL` falls back to
`INFO` rather than refusing to run.

This matters most on `summary`, because two of its three lookups degrade
instead of failing. A port map that cannot be built leaves every `down` row
with a blank port, and an unreachable `monitor1` leaves the whole Heartbeat
column blank - at the default level both look the same as a stack with
nothing to report. `--debug` prints the reason, and separates `monitor1` not
being a declared process from `monitor1` being declared but not answering:

```
summary base_port=6050 torqdata=.../output/uqs
torq.sh summary returncode=0 stdout_lines=47
configured ports for 46 process(es)
monitor1 not reached; Heartbeat column is a monitoring gap, not a verdict
parsed 46 row(s): 23 up, 23 down
starved process(es): executions1, marks1
```

`parsed N row(s)` against `stdout_lines` is the one to read when the table
looks short: it is the only place the rows `torq.sh` emitted and the rows the
parser kept are both visible.

In debug, `summary` also prints how long each process took to load on its
latest start, slowest first:

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

It is read from each process's `out_` log, not asked over IPC, so a process
at its connection cap cannot make it hang. TorQ prints its banner twice on a
start: when the log file is created, and again at the very end of torq.q,
once every code directory, the `-load` file and `.servers.startup[]` have run.
The load time is the first timestamped line to the last one before that
second banner, both on the process's own clock. `Started` is on that clock
too - GMT unless the process runs with `-localtime`. After a daily log roll
the start is found in the older file that holds it; `python/uqs/src/uqs/stack/startup.py`
has the details.

## Services

What each running service does, how it is built and why, is one page per
service in [`docs/services/`](../services/README.md) - this guide covers
operating the stack as a whole. `tap1`, the diagnostic subscriber that logs
every batch, is [there too](../services/tap.md).

## Adding a process

There is one way: declare a job in q and let `uqs new-job` scaffold
it. See [adding a pipeline](new-pipeline.md).

## Connecting

Every process (except the passwordless `feed1`) is protected by
`lib/torq-finance-starter-pack/appconfig/passwords/accesslist.txt` -
placeholder demo credentials, `admin:admin` works for everything.

```
uqs query \
    "select count i by sym from quote" --port 6052        # rdb1
uqs query \
    "select from quote where sym in \`EURUSD\`GBPUSD\`USDJPY\`AUDUSD" --port 6052
```

`query` returns a Polars DataFrame (via `kola`, the same IPC library
the frontend gateway uses) for table results. From a plain q session instead: `q)h:hopen
\`:localhost:6052:admin:admin`, then `h "..."`, then `hclose h`.

The gateway (6057) is the intended single entry point for querying across
the RDB and HDB together rather than connecting to each directly - see
`lib/torq-finance-starter-pack/docs/gettingstarted.md` and
`lib/torq/code/processes/gateway.q` for its `.gw.execute` API; this repo
doesn't wrap or simplify it further (`query` above connects directly to
whichever port you give it).

## Verifying it's alive

```
uqs summary
```

prints a status table (`up`/`down`, pid, port, color-coded) for every
process defined in `process.csv`, not just the ones `start all` brought up.
Per-process stdout/stderr logs land in `output/uqs/logs/`
(`out_<procname>.log` / `err_<procname>.log`) - check these first if a
process shows `down` unexpectedly.

## What lib/torq ships that uqf deliberately does not use

Three access layers are vendored in `lib/torq` and wired into nothing:

- the Grafana JSON datasource adapter
- the generic `dataaccess.q` access layer
- the `dqerest.q` REST bridge

None is loaded by any process in the generated `process.csv`, and none is
referenced under `src/`, `python/` or `docs/`. **This is a decision, not an
oversight** (issue #89, 2026-09-16), recorded here so the next reader does
not rediscover them and assume they are a shortcut.

They look like free functionality and are not. Enabling any of them is
backend work on the order of writing a small API layer, and none of them
closes the two gaps that actually needed new work — fleet health and
backfill status — which `python/uqf_frontend` was built for instead. With
the frontend serving both the ops and desk audiences (#53), Grafana's
fixed-panel model fits the desk side's user-driven filtering poorly, and the
BFF authorises in Python under one service credential (#54), which the
q-side access layers were designed to replace rather than sit beside.

They stay in the vendored tree **untouched**, because the standing rule is
that `lib/torq` is a pristine copy of upstream: removing files would turn
the next TorQ upgrade from a copy into a three-way merge, and every overlay
in `uqs` relies on that copy being exact. Unused files cost
nothing. If a future audience genuinely wants Grafana, the adapter is
there; the decision to be revisited then is #54's, not this one.

## MCP server

`python/uqs/uqs_mcp.py` exposes the same
start/stop/restart/summary/print/clean/query/config-get/config-set/list/
logs/crypto-lifecycle operations as MCP tools (`uqs_start`,
`uqs_stop`, `uqs_get_config`, `uqs_set_config`,
`uqs_logs`, `uqs_crypto_start`/`_stop`/`_status`,
`uqs_crypto_fills_start`/`_stop`/`_status`, etc. - see the file
itself for the full, current list), built with
[FastMCP](https://gofastmcp.com/), for an MCP client (e.g. Claude) to
drive the demo directly instead of shelling out to the CLI. Not
exposed: `raw` (an arbitrary passthrough to `torq.sh`, deliberately left
off as a scope boundary). Point an MCP
client's server command at:

```
uv run --project python/uqs python/uqs/uqs_mcp.py
```

(stdio transport, the default). `uqs_query` returns a list of row
dicts for table results (via the same `kola`-backed `uqs.stack.runtime.query`
the CLI's `query` command calls), or the raw scalar/dict result otherwise.

## Other commands

Anything `torq.sh` itself supports but isn't wrapped as its own subcommand
above - `debug <processname>` to run one process in the foreground for
troubleshooting, `top <processname>` - is available via `raw -- <args>`,
which passes straight through to `lib/torq/torq.sh`.

An interactive console is no longer among them: **`uqs query --console`**
(`-i`) hands the connection to `qcon` instead of running one expression, and
takes the same `--port`/`--host`/`--user`/`--passwd` a query does:

```
uqs query --port 6052 --console        # a session on rdb1
uqs query --port 6052 "select from quote"   # one expression, as before
```

It requires `qcon` on `PATH`, which ships with kdb+ rather than with this
repository; without it the command says so and points back at `--port`/`expr`,
which works over IPC and needs nothing installed. `rlwrap` is used for line
editing when present and skipped when not.

## Known harmless warnings

`hostname -I`/`hostname -A` (Linux-only flags `torq.sh` calls unconditionally
at startup) print `illegal option` warnings on macOS's BSD `hostname` - safe
to ignore, they don't affect anything the demo actually uses.
