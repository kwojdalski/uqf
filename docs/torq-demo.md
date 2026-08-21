# Running the TorQ Finance Starter Pack demo

`lib/torq/` (the TorQ production framework) and `lib/torq-finance-starter-pack/`
(a layered reference application built on top of it - feed handlers,
tickerplant, RDB, an HDB seeded with two days of sample quote/trade data,
gateway) are both vendored into this repo for reference (see the README's
Licensing section) but neither is wired into `src/init.q` or anything else
uqf itself runs - this library has no long-running processes for TorQ's
machinery to manage.

`scripts/torq_demo.sh` bridges the two vendored trees so you can actually
start the demo up and poke at it, without editing or writing into either
`lib/` directory.

## Quick start

```
scripts/torq_demo.sh start all      # start every startwithall=1 process
scripts/torq_demo.sh summary        # one-line-per-process status table
scripts/torq_demo.sh stop all       # stop everything
```

Run from anywhere - the script resolves its own location and works out
`lib/torq`/`lib/torq-finance-starter-pack`'s absolute paths itself. First
`start` bootstraps a data directory at `scripts/output/torq-demo/` (already
gitignored, matching `scripts/output/`'s existing use for
`timer_replay_example.q`'s run artifacts) by copying the app's sample
`hdb/`/`dqe/` data there - `logs/`, `tplogs/`, `wdbhdb/`, and every process's
actual read/write activity all happen inside that directory, never inside
`lib/`. Run `scripts/torq_demo.sh clean` to wipe it and start fresh next
time.

Requires real kdb+/KDB-X (`q` on `PATH`) - not the PeachQ binary used
elsewhere in this repo for `src/`/`tests/` - plus `envsubst` and `rlwrap`
(TorQ's own `torq.sh` needs both; on macOS: `brew install gettext rlwrap`).

## What actually starts

By default (`start all`) 14 processes come up: the 13 marked
`startwithall=1` in the vendored
`lib/torq-finance-starter-pack/appconfig/process.csv`, plus `fxfeed1` -
uqf's own addition, appended as one extra row to a *copy* of that csv that
`torq_demo.sh` generates on the fly (never editing the vendored file
itself; see the script's `generated_procs` section). The vendored README
explains why the rest stay off: the KDB-X community edition's connection
limits mean `monitor1`, `reporter1`, `filealerter1`, `dqc1`/`dqcdb1`,
`dqe1`/`dqedb1` stay off unless you have a fully-licensed kdb+/KDB-X.
`killtick` and `tpreplay1` are on-demand utility processes, not part of the
standing stack, so they also don't auto-start.

Default ports (base `6010`, override with `TORQ_DEMO_PORT=<n>
scripts/torq_demo.sh start all`):

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
for it to `torq_demo.sh`'s `generated_procs` section (pick a free port
offset - `docs/torq-demo.md`'s table above lists every offset already
taken).

## Connecting

Every process (except the passwordless `feed1`) is protected by
`lib/torq-finance-starter-pack/appconfig/passwords/accesslist.txt` -
placeholder demo credentials, `admin:admin` works for everything. From any
q session:

```
q)h:hopen `:localhost:6012:admin:admin        / rdb1 - today's live ticks
q)h "select count i by sym from quote"
q)h "select from quote where sym in `EURUSD`GBPUSD`USDJPY`AUDUSD"  / fxfeed1's rows
q)hclose h
```

The gateway (6017) is the intended single entry point for querying across
the RDB and HDB together rather than connecting to each directly - see
`lib/torq-finance-starter-pack/docs/gettingstarted.md` and
`lib/torq/code/processes/gateway.q` for its `.gw.execute` API; this repo
doesn't wrap or simplify it further.

## Verifying it's alive

```
scripts/torq_demo.sh summary
```

prints a status table (`up`/`down`, pid, port) for every process defined in
`process.csv`, not just the ones `start all` brought up. Per-process stdout/
stderr logs land in `scripts/output/torq-demo/logs/` (`out_<procname>.log` /
`err_<procname>.log`) - check these first if a process shows `down`
unexpectedly.

## Other commands

`scripts/torq_demo.sh` is a thin wrapper - everything after the first
argument passes straight through to `lib/torq/torq.sh`, whose own `usage`
output (run the wrapper with no arguments) lists the rest: `stop
<processname(s)>` / `restart` for a subset instead of `all`, `print all` to
see the exact startup command lines without starting anything, `debug
<processname>` to run one process in the foreground for troubleshooting,
`top <processname>`, and `qcon <processname> admin:admin` to open an
interactive console (requires `qcon` on `PATH`, not installed by default -
`hopen` from a q session, as above, works without it).

## Known harmless warnings

`hostname -I`/`hostname -A` (Linux-only flags `torq.sh` calls unconditionally
at startup) print `illegal option` warnings on macOS's BSD `hostname` - safe
to ignore, they don't affect anything the demo actually uses.
