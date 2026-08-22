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

See [docs/torq/README.md](torq/README.md) for diagrams of the current
process topology, table-level data pipeline, and config-generation flow.

## Quick start

The CLI is also a `torq-demo` script entry point
(`pyproject.toml`'s `[project.scripts]`), which shortens every command
below - `uv run --project python/torq_orchestrator torq-demo start all`
instead of spelling out `torq_demo.py`'s path. Shorter still, one-time
setup:

```
uv tool install --editable python/torq_orchestrator
```

installs `torq-demo` onto your `PATH` as an editable link back to this
repo's source (edits are picked up immediately, no reinstall), so from then
on, from anywhere:

```
torq-demo start all      # start every startwithall=1 process
torq-demo summary        # status table
torq-demo stop all       # stop everything
```

Without that one-time step, or in CI/a fresh checkout, fall back to `uv
run`:

```
uv run --project python/torq_orchestrator torq-demo start all
uv run --project python/torq_orchestrator torq-demo summary
uv run --project python/torq_orchestrator torq-demo stop all
```

The longer `uv run --project python/torq_orchestrator
python/torq_orchestrator/torq_demo.py ...` form (a thin shim over the same
CLI) still works too, for anything that already invokes it by path.

Run from anywhere - the command resolves its own location and works out
`lib/torq`/`lib/torq-finance-starter-pack`'s absolute paths itself; `uv run
--project python/torq_orchestrator` (or the installed `torq-demo`)
resolves that package's dependencies (typer, loguru, rich, kola, fastmcp)
on demand, no separate `uv sync` step needed. First `start` bootstraps a
data directory at
`scripts/output/torq-demo/` (already gitignored, matching
`scripts/output/`'s existing use for `timer_replay_example.q`'s run
artifacts) by copying the app's sample `hdb/`/`dqe/` data there - `logs/`,
`tplogs/`, `wdbhdb/`, and every process's actual read/write activity all
happen inside that directory, never inside `lib/`. Run `torq-demo clean`
to wipe it and start fresh next time.

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
logs [PROCS] [-f] [-n N] [--level L]  tail out_/err_*.log through the CLI's own colorized
                                       logger instead of raw files (see "Logs" below)
new-process                           interactive wizard to add a new process (see below)
raw -- ARGS...                        pass any other torq.sh verb straight through
                                       (e.g. `raw -- debug rdb1`, `raw -- top feed1`)
```

`PROCS` is `all` or a space-separated list of process names. `--port` sets
`KDBBASEPORT` (default `6050`, see the port table below). Full `--help` is
available on the command itself and on every subcommand.

## Listing things

`list` (no argument) prints the kinds it knows about; `list KIND` lists
every item of that kind - not just processes:

```
torq-demo list
torq-demo list processes
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

By default (`start all`) 15 processes come up: the 13 marked
`startwithall=1` in the vendored
`lib/torq-finance-starter-pack/appconfig/process.csv`, plus `fxfeed1` and
`quotesfeed1` - uqf's own additions, appended as extra rows to a *copy* of
that csv that `torq_orchestrator.core.bootstrap()` generates on the fly
(never editing the vendored file itself). The vendored README explains why
the rest stay off: the KDB-X community edition's connection limits mean
`monitor1`, `reporter1`, `filealerter1`, `dqc1`/`dqcdb1`, `dqe1`/`dqedb1`
stay off unless you have a fully-licensed kdb+/KDB-X. `killtick` and
`tpreplay1` are on-demand utility processes, not part of the standing
stack, so they also don't auto-start.

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
in `scripts/output/torq-demo/` either (regenerated from scratch on every
`bootstrap()` call, i.e. every `start`/`stop`/`summary`/...  - anything
written directly there would just be clobbered on the next command).
Overrides persist instead in `python/torq_orchestrator/process_overrides.csv`
(a small `procname,field,value` csv, created on first `config-set` -
tracked in git like any other config, not gitignored), and
`bootstrap()` applies them on top of the vendored+fxfeed1 rows every time
it (re)generates `process.csv`.

```
torq-demo config-get fxfeed1
torq-demo config-get fxfeed1 startwithall
torq-demo config-set fxfeed1 startwithall 0
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

## quotesfeed1 - a real database for one of uqf's own table shapes

`scripts/torq_quotes_feed.q` is a proof of concept for getting an actual
on-disk kdb+ database, built with the TorQ Finance Starter Pack's own
tickerplant/RDB/WDB/HDB machinery, seeded with a table shape uqf's *own*
pricing code understands - rather than the vendored pack's generic
`quote`/`trade` tables. It publishes synthetic depth-aware FX quotes (3
levels per side, level-0-first vectors) into a new `quotes` table:
`time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes - the same shape
`src/forwards.q`'s `require_quotes_cols` expects (`ts` there; `time` here,
since the tickerplant's own `upd` machinery requires the first column
literally named `time` - rename it back with `select ts:time,... from
quotes` before handing rows to `.qfwd.cross_book_at`/etc).

Getting this table into a real, on-disk database took no changes to
`rdb.q`/`wdb.q`/`hdb.q` at all - the vendored RDB's default
`subscribeto:` `` ` `` already means "every table in the schema", and WDB
writes down whatever the RDB has, so a brand new table only needs two
things:

1. **Schema** - `torq_orchestrator.core._generated_schema_content()`
   appends the `quotes` table definition to a *copy* of the vendored
   `database.q` (written to `scripts/output/torq-demo/database.q` on every
   `bootstrap()`, same generate-never-edit approach as `process.csv`), and
   `_base_process_rows()` repoints `stp1`'s `-schemafile` extras arg at
   that copy instead of the vendored file.
2. **Feed** - `torq_quotes_feed.q` itself, wired in as a process.csv row
   exactly like `fxfeed1` (port offset `+24`).

```
torq-demo query \
    "select time,sym,bid_prices,ask_prices from quotes" --port 6052        # rdb1
torq-demo query \
    "select ts:time,sym,bid_prices,bid_sizes,ask_prices,ask_sizes from quotes" --port 6052
```

After an EOD writedown (`raw -- eod`/`wdb1`'s own cycle, or just leaving
the demo running past midnight) `quotes` rows land in the HDB alongside
`quote`/`trade`, queryable the same way.

## Logs

Every process writes its own `out_<procname>.log`/`err_<procname>.log` in
`scripts/output/torq-demo/logs/` (stable symlink aliases TorQ itself
maintains onto the current run's timestamped file - see `torq.q`'s
`createlog`/`fileredirect`), in a fixed pipe-delimited format:
`time|host|proctype|procname|loglevel|id|message`. `logs` tails these
through the same colorized logger the rest of the CLI uses, instead of
`tail`-ing N raw files by hand - no TorQ-side config change needed (no
`-jsonlogs`, nothing added to `extras`):

```
torq-demo logs                          # last 20 lines per process, all processes
torq-demo logs "stp1 rdb1" -n 50        # last 50 lines each, merged and time-sorted
torq-demo logs -f                       # live tail, every process, Ctrl-C to stop
torq-demo logs quotesfeed1 -f --level WARNING   # live tail, warnings/errors only
```

`-f`/`--follow` spawns one `tail -F` per file (so it follows TorQ's own log
rolling/restart-aliasing correctly) and merges them through a queue; without
`-f`, the last `-n`/`--lines` lines of each file are read, parsed, and
printed sorted by the log's own timestamp - not wall-clock arrival order.
`--level` filters to that level and above (`DEBUG`/`INFO`/`WARNING`/`ERROR`).

## Adding a new process interactively

`torq-demo new-process` is a console wizard for the "how do I add a
process" question `fxfeed1`/`torq_quotes_feed.q`/`torq_cross_etl.q` answer
by example - it walks through the same handful of decisions (name,
publish-or-subscribe, which table, port), writes a **Stage 1 only**
skeleton `.q` file into `scripts/` (connects/subscribes and logs - no
business logic, per the torq-developer skill's PROCESS SETUP GUIDE),
registers it, and optionally starts it immediately to run through the
Stage 1 verification checklist live (checks `err_<proc>.log` is empty and
the process shows up in `summary`):

```
torq-demo new-process
```

Registration goes into `python/torq_orchestrator/extra_processes.csv` (a
new sibling of `process_overrides.csv` - same never-edit-the-vendored/
generated-files approach, tracked in git) rather than editing `core.py`
source - `_base_process_rows()` reads it generically, so adding a process
this way is a data change, not a code change. If the new process publishes
into a brand-new table (not `quote`/`trade`/`quotes`), add its schema line
to `python/torq_orchestrator/extra_schema.q` by hand (also read
generically, same idea) before starting it.

The skeleton file only gets you through Stage 1 (plumbing) - fill in the
actual publish/subscribe logic afterwards by hand, copying whichever of
`scripts/torq_fx_feed.q`/`torq_quotes_feed.q` (publisher) or
`torq_cross_etl.q` (subscriber) matches what you picked.

## Connecting

Every process (except the passwordless `feed1`) is protected by
`lib/torq-finance-starter-pack/appconfig/passwords/accesslist.txt` -
placeholder demo credentials, `admin:admin` works for everything.

```
torq-demo query \
    "select count i by sym from quote" --port 6052        # rdb1
torq-demo query \
    "select from quote where sym in \`EURUSD\`GBPUSD\`USDJPY\`AUDUSD" --port 6052
```

`query` returns a Polars DataFrame (via `kola`, the same IPC library
`uqf_client.UqfClient` uses for the pricing library itself) for table
results. From a plain q session instead: `q)h:hopen
\`:localhost:6052:admin:admin`, then `h "..."`, then `hclose h`.

The gateway (6057) is the intended single entry point for querying across
the RDB and HDB together rather than connecting to each directly - see
`lib/torq-finance-starter-pack/docs/gettingstarted.md` and
`lib/torq/code/processes/gateway.q` for its `.gw.execute` API; this repo
doesn't wrap or simplify it further (`query` above connects directly to
whichever port you give it).

## Verifying it's alive

```
torq-demo summary
```

prints a status table (`up`/`down`, pid, port, color-coded) for every
process defined in `process.csv`, not just the ones `start all` brought up.
Per-process stdout/stderr logs land in `scripts/output/torq-demo/logs/`
(`out_<procname>.log` / `err_<procname>.log`) - check these first if a
process shows `down` unexpectedly.

## crypto recorder (cryptorust) - a proof of concept

`torq-demo crypto-start`/`crypto-stop`/`crypto-status` are a proof of
concept that this demo's kdb+ infra isn't TorQ/q-specific: anything that
can speak kdb+ IPC can publish onto the same tickerplant alongside the q
feeds above, including a process written in an entirely different
language, in a completely separate project. Specifically, they build and
launch a sibling checkout of [cryptorust](https://github.com/kwojdalski/cryptorust)
(a Rust crypto trading system - see `~/github_projects/cryptorust`) - its
own `kdb-market-data-recorder` binary (`src/bin/kdb_market_data_recorder.rs`
there) connects live venue order books (Binance, Bybit, ...) straight to
this demo's `stp1` over raw IPC (via the [`kxkdb`](https://github.com/KxSystems/kxkdb)
crate) and calls `.u.upd` directly - the exact same wire protocol
`torq_fx_feed.q`/`torq_quotes_feed.q` use, just from Rust instead of q.

```
torq-demo crypto-start                    # binance_spot, BTC-USDT/ETH-USDT by default
torq-demo crypto-start --venues binance_spot,bybit_spot --symbols BTC-USDT
torq-demo crypto-status
torq-demo crypto-stop
```

Rows land in `crypto_book` (`time`/`venue`/`sym`/`bid_prices`/`bid_sizes`/
`ask_prices`/`ask_sizes` - see `core.py`'s `CRYPTO_BOOK_TABLE_SCHEMA`),
flowing through `rdb1`/`wdb1`/`hdb` exactly like `quote`/`trade`/`quotes`/
`wide_book`:

```
torq-demo query "select from crypto_book" --port <rdb1's port>
```

Requires a `~/github_projects/cryptorust` checkout (override the path via
`$CRYPTORUST_ROOT`) with a Rust toolchain on `PATH` - `crypto-start` runs
`cargo build` itself the first time, which can take a while. It also
reuses this demo's own `feed:pass` credential (see `appconfig/passwords/
feed.txt`) to authenticate against `stp1`'s access-list, same as any other
feed process here - no separate cryptorust-side credential to set up.

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
