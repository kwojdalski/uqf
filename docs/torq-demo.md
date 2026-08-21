# Running the TorQ Finance Starter Pack demo

`lib/torq/` (the TorQ production framework) and `lib/torq-finance-starter-pack/`
(a layered reference application built on top of it - feed handlers,
tickerplant, RDB, an HDB seeded with two days of sample quote/trade data,
gateway) are both vendored into this repo for reference (see the README's
Licensing section) but neither is wired into `src/init.q` or anything else
uqf itself runs - this library has no long-running processes for TorQ's
machinery to manage.

`python/torq_orchestrator/torq_demo.py` bridges the two vendored trees so
you can actually start the demo up and poke at it, without editing or
writing into either `lib/` directory. The actual bootstrapping/config logic
lives in `python/torq_orchestrator/src/torq_orchestrator/core.py`, shared
with `torq_demo_mcp.py`'s FastMCP server (see "MCP server" below) so the
CLI and the MCP tools can't drift apart. It's a standalone package
(`python/torq_orchestrator/`), separate from `python/uqf-client/` (the
pricing library's q-IPC client) - this has nothing to do with pricing, and
keeping it separate keeps `uqf-client` itself down to its one real
dependency (`kola`).

## Quick start

```
uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo.py start all      # start every startwithall=1 process
uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo.py summary        # status table
uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo.py stop all       # stop everything
```

Run from anywhere - the command resolves its own location and works out
`lib/torq`/`lib/torq-finance-starter-pack`'s absolute paths itself; `uv run
--project python/torq_orchestrator` resolves that package's dependencies
(typer, loguru, rich, kola, fastmcp) on demand, no separate `uv sync` step
needed. First `start` bootstraps a data directory at
`scripts/output/torq-demo/` (already gitignored, matching
`scripts/output/`'s existing use for `timer_replay_example.q`'s run
artifacts) by copying the app's sample `hdb/`/`dqe/` data there - `logs/`,
`tplogs/`, `wdbhdb/`, and every process's actual read/write activity all
happen inside that directory, never inside `lib/`. Run `... torq_demo.py
clean` to wipe it and start fresh next time.

Requires real kdb+/KDB-X (`q` on `PATH`) - not the PeachQ binary used
elsewhere in this repo for `src/`/`tests/` - plus `envsubst` and `rlwrap`
(TorQ's own `torq.sh`, which this still drives under the hood, needs both;
on macOS: `brew install gettext rlwrap`).

## Commands

```
start [PROCS] [--port N]              start (default: all startwithall=1 processes)
stop [PROCS] [--port N]               stop
restart [PROCS] [--port N]            restart
summary [--port N]                    rich status table (up/down, pid, port)
print [PROCS] [--port N]              show exact startup command line(s), no-op otherwise
clean                                 wipe scripts/output/torq-demo/
query EXPR --port N                   run a synchronous q expression against a process
list [KIND] [--port N]                list every item of KIND ('processes', 'fields',
                                       'overrides', 'env') - no argument shows the kinds
config-get PROCNAME [FIELD] [--port N] [--raw]  show a process's effective process.csv row
                                                 (or one field), with placeholders resolved
                                                 unless --raw
config-set PROCNAME FIELD VALUE       persist a process.csv field override for a process
raw -- ARGS...                        pass any other torq.sh verb straight through
                                       (e.g. `raw -- debug rdb1`, `raw -- top feed1`)
```

`PROCS` is `all` or a space-separated list of process names. `--port` sets
`KDBBASEPORT` (default `6010`, see the port table below). Full `--help` is
available on the command itself and on every subcommand.

## Listing things

`list` (no argument) prints the kinds it knows about; `list KIND` lists
every item of that kind - not just processes:

```
uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo.py list
uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo.py list processes
```

- `processes` - every process's `procname`/`proctype`/`port`/`startwithall`,
  resolved and with any `config-set` overrides applied - the full set
  `config-get`/`config-set`/`start <procname>` accept, without already
  needing to know a name ahead of time
- `fields` - `process.csv`'s valid columns (what `config-set`'s `FIELD`
  argument accepts)
- `overrides` - every `config-set` override currently in effect
- `env` - `build_env()`'s resolved `KDBBASEPORT`/`KDBHDB`/... values (the
  same env `config-get`'s placeholder resolution and `torq.sh` itself use)

New kinds are one function + one `core.LISTABLE_KINDS` entry, not a new
CLI command each time - see `core.py`'s `_list_*` functions.

## What actually starts

By default (`start all`) 14 processes come up: the 13 marked
`startwithall=1` in the vendored
`lib/torq-finance-starter-pack/appconfig/process.csv`, plus `fxfeed1` -
uqf's own addition, appended as one extra row to a *copy* of that csv that
`torq_orchestrator.core.bootstrap()` generates on the fly (never editing
the vendored file itself). The vendored README explains why the rest stay
off: the KDB-X community edition's connection limits mean `monitor1`,
`reporter1`, `filealerter1`, `dqc1`/`dqcdb1`, `dqe1`/`dqedb1` stay off
unless you have a fully-licensed kdb+/KDB-X. `killtick` and `tpreplay1` are
on-demand utility processes, not part of the standing stack, so they also
don't auto-start.

Default ports (base `6010`, override with `--port <n>`):

| Port | Process | Role |
|---|---|---|
| 6010 | stp1 | segmented tickerplant |
| 6011 | discovery1 | service discovery |
| 6012 | rdb1 | real-time DB (today's ticks) |
| 6013 / 6014 | hdb1 / hdb2 | historical DB (the vendored sample data) |
| 6015 | wdb1 | writedown process (rolls RDB -> HDB) |
| 6016 | sort1 | sorts data before writedown |
| 6017 | gateway1 | single query entry point across hdb/rdb |
| 6021 | housekeeping1 | log/process housekeeping |
| 6024 | feed1 | the vendored dummy feed - simulated equity quotes/trades |
| 6025 | sctp1 | segmented chained tickerplant |
| 6026 / 6027 | sortworker1/2 | sort worker pool |
| 6028 | metrics1 | metrics collector |
| 6029 | fxfeed1 | uqf's own feed - simulated FX quotes (see below) |

## Changing a process's config

`config-get`/`config-set` read and write a *process.csv field override* -
not the vendored `process.csv` (never edited) and not the *generated* one
in `scripts/output/torq-demo/` either (regenerated from scratch on every
`bootstrap()` call, i.e. every `start`/`stop`/`summary`/...  - anything
written directly there would just be clobbered on the next command).
Overrides persist instead in `python/torq_orchestrator/process_overrides.csv`
(a small `procname,field,value` csv, created on first `config-set` -
tracked in git like any other config, not gitignored), and
`bootstrap()` applies them on top of the vendored+fxfeed1 rows every time
it (re)generates `process.csv`.

```
uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo.py config-get fxfeed1
uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo.py config-get fxfeed1 startwithall
uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo.py config-set fxfeed1 startwithall 0
```

`config-get` resolves `process.csv`'s two placeholder styles by default -
`${VAR}`/`$VAR` (e.g. `load=${KDBHDB}` -> the real path,
`U=${TORQAPPHOME}/appconfig/passwords/accesslist.txt` -> the real path) and
the port column's own `{VAR}`/`{VAR}+N`/`{VAR}-N` arithmetic shorthand
(e.g. `port={KDBBASEPORT}+3` -> `6013`) - the same values `torq.sh` itself
substitutes at process-start time, evaluated against
`build_env(paths, --port)`. Pass `--raw` to see the literal, unresolved
value instead (e.g. to copy it into a `config-set` call).

Valid `FIELD`s are `process.csv`'s own columns: `host`, `port`, `proctype`,
`procname`, `U`, `localtime`, `g`, `T`, `w`, `load`, `startwithall`,
`extras`, `qcmd`. A change takes effect on the next `start`/`restart` of
that process (the running process itself isn't touched).

## fxfeed1 - adding your own row-generating process

`scripts/torq_fx_feed.q` is a second, independent feed process publishing
synthetic top-of-book quotes for `EURUSD`/`GBPUSD`/`USDJPY`/`AUDUSD` (a
small random walk around a fixed spot, `+/-` 1 pip wide) into the same
`quote` table the vendored `feed1` already writes equity quotes into -
`sym` is just a symbol column, so FX pairs and equity tickers coexist in
one table with no schema change. It's the concrete worked example for "how
do I add a process that publishes rows": it mirrors
`lib/torq-finance-starter-pack/code/tick/feed.q`'s own pattern exactly -

1. find the tickerplant via discovery: `.servers.startupdepcycles[...]` /
   `.servers.gethandlebytype[...]`
2. build one row per pair as plain vectors (see the file's own comment on
   why they must stay vectors, not dicts keyed by pair - a dict here
   silently produces a `length` error on insert, the hard way to find out)
3. publish with `h (`.u.upd;`quote;data)`
4. repeat on a timer: `.timer.repeat[...]`

To add your own: copy `torq_fx_feed.q`'s shape, drop the new file anywhere
under `scripts/` (it's referenced by absolute path, not `KDBAPPCODE`, so it
doesn't need to live inside either vendored `lib/` tree), and add a line
for it in `torq_orchestrator.core.bootstrap()`'s process.csv-generation
block (pick a free port offset - the table above lists every offset
already taken).

## Connecting

Every process (except the passwordless `feed1`) is protected by
`lib/torq-finance-starter-pack/appconfig/passwords/accesslist.txt` -
placeholder demo credentials, `admin:admin` works for everything.

```
uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo.py query \
    "select count i by sym from quote" --port 6012        # rdb1
uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo.py query \
    "select from quote where sym in \`EURUSD\`GBPUSD\`USDJPY\`AUDUSD" --port 6012
```

`query` returns a Polars DataFrame (via `kola`, the same IPC library
`uqf_client.UqfClient` uses for the pricing library itself) for table
results. From a plain q session instead: `q)h:hopen
\`:localhost:6012:admin:admin`, then `h "..."`, then `hclose h`.

The gateway (6017) is the intended single entry point for querying across
the RDB and HDB together rather than connecting to each directly - see
`lib/torq-finance-starter-pack/docs/gettingstarted.md` and
`lib/torq/code/processes/gateway.q` for its `.gw.execute` API; this repo
doesn't wrap or simplify it further (`query` above connects directly to
whichever port you give it).

## Verifying it's alive

```
uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo.py summary
```

prints a status table (`up`/`down`, pid, port, color-coded) for every
process defined in `process.csv`, not just the ones `start all` brought up.
Per-process stdout/stderr logs land in `scripts/output/torq-demo/logs/`
(`out_<procname>.log` / `err_<procname>.log`) - check these first if a
process shows `down` unexpectedly.

## MCP server

`python/torq_orchestrator/torq_demo_mcp.py` exposes the same
start/stop/restart/summary/clean/query/config-get/config-set operations as
MCP tools (`torq_demo_start`, `torq_demo_stop`, `torq_demo_get_config`,
`torq_demo_set_config`, etc.), built with
[FastMCP](https://gofastmcp.com/), for an MCP client (e.g. Claude) to
drive the demo directly instead of shelling out to the CLI. Point an MCP
client's server command at:

```
uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo_mcp.py
```

(stdio transport, the default). `torq_demo_query` returns a list of row
dicts for table results (via the same `kola`-backed `torq_orchestrator.core.query`
the CLI's `query` command calls), or the raw scalar/dict result otherwise.

## Other commands

Anything `torq.sh` itself supports but isn't wrapped as its own subcommand
above - `debug <processname>` to run one process in the foreground for
troubleshooting, `top <processname>`, `qcon <processname> admin:admin` for
an interactive console (requires `qcon` on `PATH`, not installed by
default - `query`/`hopen`, as above, work without it) - is available via
`raw -- <args>`, which passes straight through to `lib/torq/torq.sh`.

## Known harmless warnings

`hostname -I`/`hostname -A` (Linux-only flags `torq.sh` calls unconditionally
at startup) print `illegal option` warnings on macOS's BSD `hostname` - safe
to ignore, they don't affect anything the demo actually uses.
