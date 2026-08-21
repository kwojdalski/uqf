# Running the TorQ Finance Starter Pack demo

`lib/torq/` (the TorQ production framework) and `lib/torq-finance-starter-pack/`
(a layered reference application built on top of it - feed handlers,
tickerplant, RDB, an HDB seeded with two days of sample quote/trade data,
gateway) are both vendored into this repo for reference (see the README's
Licensing section) but neither is wired into `src/init.q` or anything else
uqf itself runs - this library has no long-running processes for TorQ's
machinery to manage.

`scripts/torq_demo.py` bridges the two vendored trees so you can actually
start the demo up and poke at it, without editing or writing into either
`lib/` directory. The actual bootstrapping logic lives in
`python/uqf-client/src/uqf_client/torq_demo.py`, shared with
`scripts/torq_demo_mcp.py`'s FastMCP server (see "MCP server" below) so the
CLI and the MCP tools can't drift apart.

## Quick start

```
uv run --project python/uqf-client scripts/torq_demo.py start all      # start every startwithall=1 process
uv run --project python/uqf-client scripts/torq_demo.py summary        # status table
uv run --project python/uqf-client scripts/torq_demo.py stop all       # stop everything
```

Run from anywhere - the command resolves its own location and works out
`lib/torq`/`lib/torq-finance-starter-pack`'s absolute paths itself; `uv run
--project python/uqf-client` resolves that package's dependencies (typer,
loguru, rich, kola, fastmcp) on demand, no separate `uv sync` step needed.
First `start` bootstraps a data directory at `scripts/output/torq-demo/`
(already gitignored, matching `scripts/output/`'s existing use for
`timer_replay_example.q`'s run artifacts) by copying the app's sample
`hdb/`/`dqe/` data there - `logs/`, `tplogs/`, `wdbhdb/`, and every process's
actual read/write activity all happen inside that directory, never inside
`lib/`. Run `... scripts/torq_demo.py clean` to wipe it and start fresh
next time.

Requires real kdb+/KDB-X (`q` on `PATH`) - not the PeachQ binary used
elsewhere in this repo for `src/`/`tests/` - plus `envsubst` and `rlwrap`
(TorQ's own `torq.sh`, which this still drives under the hood, needs both;
on macOS: `brew install gettext rlwrap`).

## Commands

```
start [PROCS] [--port N]      start (default: all startwithall=1 processes)
stop [PROCS] [--port N]       stop
restart [PROCS] [--port N]    restart
summary [--port N]            rich status table (up/down, pid, port)
print [PROCS] [--port N]      show exact startup command line(s), no-op otherwise
clean                         wipe scripts/output/torq-demo/
query EXPR --port N           run a synchronous q expression against a process
raw -- ARGS...                pass any other torq.sh verb straight through
                               (e.g. `raw -- debug rdb1`, `raw -- top feed1`)
```

`PROCS` is `all` or a space-separated list of process names. `--port` sets
`KDBBASEPORT` (default `6010`, see the port table below). Full `--help` is
available on the command itself and on every subcommand.

## What actually starts

By default (`start all`) 14 processes come up: the 13 marked
`startwithall=1` in the vendored
`lib/torq-finance-starter-pack/appconfig/process.csv`, plus `fxfeed1` -
uqf's own addition, appended as one extra row to a *copy* of that csv that
`uqf_client.torq_demo.bootstrap()` generates on the fly (never editing the
vendored file itself). The vendored README explains why the rest stay off:
the KDB-X community edition's connection limits mean `monitor1`,
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
for it in `uqf_client.torq_demo.bootstrap()`'s process.csv-generation block
(pick a free port offset - the table above lists every offset already
taken).

## Connecting

Every process (except the passwordless `feed1`) is protected by
`lib/torq-finance-starter-pack/appconfig/passwords/accesslist.txt` -
placeholder demo credentials, `admin:admin` works for everything.

```
uv run --project python/uqf-client scripts/torq_demo.py query \
    "select count i by sym from quote" --port 6012        # rdb1
uv run --project python/uqf-client scripts/torq_demo.py query \
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
uv run --project python/uqf-client scripts/torq_demo.py summary
```

prints a status table (`up`/`down`, pid, port, color-coded) for every
process defined in `process.csv`, not just the ones `start all` brought up.
Per-process stdout/stderr logs land in `scripts/output/torq-demo/logs/`
(`out_<procname>.log` / `err_<procname>.log`) - check these first if a
process shows `down` unexpectedly.

## MCP server

`scripts/torq_demo_mcp.py` exposes the same start/stop/restart/summary/
clean/query operations as MCP tools (`torq_demo_start`, `torq_demo_stop`,
etc.), built with [FastMCP](https://gofastmcp.com/), for an MCP client (e.g.
Claude) to drive the demo directly instead of shelling out to the CLI.
Point an MCP client's server command at:

```
uv run --project python/uqf-client scripts/torq_demo_mcp.py
```

(stdio transport, the default). `torq_demo_query` returns a list of row
dicts for table results (via the same `kola`-backed `uqf_client.torq_demo.query`
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
