# torq-orchestrator

Bridges the two vendored TorQ trees at the repo root - `lib/torq/` (the
production framework) and `lib/torq-finance-starter-pack/` (a layered
reference app built on top of it) - into a runnable demo, without editing
or writing into either. Full writeup: [docs/torq-demo.md](../../docs/torq-demo.md)
at the repo root.

Standalone package on purpose: this is process orchestration, not q
pricing, so it stays out of `python/uqf-client` (which depends on nothing
but `kola`) and carries its own dependencies (`typer`, `rich`, `loguru`,
`fastmcp`, `kola`).

## Layout

```
torq_demo.py       thin backward-compatible shim over src/torq_orchestrator/cli.py
torq_demo_mcp.py    FastMCP server exposing the same operations as MCP tools
src/torq_orchestrator/
  cli.py            the Typer CLI itself - also reachable as the `torq-demo`
                     script entry point (pyproject.toml [project.scripts])
  wizard.py         `new-process`'s interactive console wizard - prompts,
                     writes a Stage-1-only skeleton .q file, registers it
  core.py           all the actual logic (paths, bootstrap, process.csv
                     generation, config get/set, torq.sh driving, q query)
                     - no CLI/MCP framework code, both front ends import
                     straight from here so they can't drift apart
  logger/           small loguru-based logging package (ported from a
                     sibling project's generic logger, see git history) -
                     used for the CLI/MCP server's own status/error output
process_overrides.csv   created on first `config-set` - per-process
                         process.csv field overrides (see "Config setters"
                         below); tracked in git like any other config
extra_processes.csv     created by `new-process` (or add_extra_process()) -
                         whole new process rows, process_overrides.csv's
                         sibling for adding a process rather than tweaking one
extra_schema.q           created by add_extra_table_schema() - extra table
                         defs appended to the generated stp1 schema copy
tests/
  test_core.py      tests core.py's pure logic (paths, process.csv
                     generation/idempotency, config get/set) against a
                     fake vendored tree - no real q process needed
```

## Quick start

One-time, installs the `torq-demo` command onto your `PATH` as an editable
link back to this source (edits picked up immediately, no reinstall):

```
uv tool install --editable python/torq_orchestrator
```

then, from anywhere:

```
torq-demo start all
torq-demo summary
torq-demo stop all
```

Without that step (e.g. CI, a fresh checkout), `uv run` works the same,
just longer:

```
uv run --project python/torq_orchestrator torq-demo start all
```

The full `.../torq_demo.py` path form still works too (a thin shim over
the same CLI, kept for anything that already invokes it that way).
`uv run --project python/torq_orchestrator` resolves this package's
dependencies on demand, no separate `uv sync` needed - though `uv sync`
here also works if you want the `.venv` up front.

Requires real kdb+/KDB-X (`q` on `PATH`, not the PeachQ binary used
elsewhere in this repo) plus `envsubst` and `rlwrap` on `PATH` (`torq.sh`,
which this drives under the hood, needs both - macOS: `brew install
gettext rlwrap`).

## Commands

```
start [PROCS] [--port N]              start (default: all startwithall=1 processes)
stop [PROCS] [--port N]               stop
restart [PROCS] [--port N]            restart
summary [--port N]                    rich status table (up/down, pid, port)
print [PROCS] [--port N]              show exact startup command line(s)
clean                                 wipe ../../scripts/output/torq-demo/
query EXPR --port N                   run a synchronous q expression
list [KIND]                           list every item of KIND - no argument shows the kinds
config-get PROCNAME [FIELD] [--raw]   show a process's effective process.csv row, resolved
config-set PROCNAME FIELD VALUE       persist a process.csv field override
logs [PROCS] [-f] [-n N] [--level L]  tail out_/err_*.log through the CLI's own logger
new-process                           interactive wizard to add a new process
crypto start/stop/status              proof of concept: cryptorust (Rust) publishing over kdb+ IPC
raw -- ARGS...                        anything else torq.sh supports
```

`--help` on the command itself or any subcommand has the full picture.

## Listing things

`list` isn't limited to processes - it dispatches on a small registry
(`core.LISTABLE_KINDS`), currently `processes` (procname/proctype/port/
startwithall, resolved and with overrides applied - the default), `fields`
(`process.csv`'s valid `config-set` columns), `overrides` (every
`config-set` override in effect), and `env` (`build_env()`'s resolved
`KDBBASEPORT`/`KDBHDB`/... values). Adding a new kind is one function plus
one registry entry - see `core.py`'s `_list_*` functions.

## Config setters

`config-get`/`config-set` read and write **`process_overrides.csv`** - not
the vendored `process.csv` (never edited) and not the *generated* one
under `scripts/output/torq-demo/` either, which `bootstrap()` rebuilds
from scratch on every single command, so anything written there directly
would just be overwritten by the next `start`/`stop`/`summary`/... call.
`process_overrides.csv` is what survives instead: a small
`procname,field,value` file, applied on top of the vendored + `fxfeed1`
rows every time `bootstrap()` (re)generates `process.csv`.

```
torq-demo config-set fxfeed1 startwithall 0
```

`config-get` resolves both of `process.csv`'s placeholder styles by
default - `${VAR}`/`$VAR` (`load=${KDBHDB}` -> the real path) and the port
column's `{VAR}`/`{VAR}+N` arithmetic shorthand (`port={KDBBASEPORT}+3` ->
`6053`), evaluated the same way `torq.sh` itself does at process-start
time. Pass `--raw` to see the literal value instead.

Valid fields are `process.csv`'s own columns: `host`, `port`, `proctype`,
`procname`, `U`, `localtime`, `g`, `T`, `w`, `load`, `startwithall`,
`extras`, `qcmd`. Takes effect on that process's next `start`/`restart` -
a currently-running instance of it is untouched.

## Logs

`logs` tails each process's `out_<procname>.log`/`err_<procname>.log`
(stable aliases TorQ maintains onto the current run's timestamped file)
through the same colorized loguru logger the rest of the CLI uses, parsing
`.lg.format`'s pipe-delimited `time|host|proctype|procname|loglevel|id|message`
line shape - no TorQ-side config change (no `-jsonlogs`). `-f`/`--follow`
runs one `tail -F` per file (correctly follows TorQ's own log rolling)
merged through a queue; without it, the last `-n` lines per file are
parsed and printed sorted by the log's own timestamp. `--level` filters to
that level and above.

```
torq-demo logs "stp1 rdb1" -n 50
torq-demo logs -f --level WARNING
```

## crypto recorder (cryptorust) - a proof of concept

`crypto start`/`stop`/`status` (a nested command group) build and launch a sibling
`~/github_projects/cryptorust` checkout's own `kdb-market-data-recorder`
Rust binary, pointed at this demo's `stp1` - proving the kdb+ infra here
isn't TorQ/q-specific, any process that speaks kdb+ IPC can publish onto
it. See `docs/torq-demo.md`'s own section for the full picture (schema,
credentials, `$CRYPTORUST_ROOT`).

## MCP server

```
uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo_mcp.py
```

Exposes `torq_demo_start`/`stop`/`restart`/`summary`/`clean`/`query`/
`get_config`/`set_config` as MCP tools (stdio transport) for an MCP client
to drive the demo directly.

## Testing

```
uv run --project python/torq_orchestrator pytest
```

`tests/test_core.py` builds a minimal fake `lib/torq` +
`lib/torq-finance-starter-pack` tree under `tmp_path` so `core.py`'s pure
logic (path resolution, process.csv generation/idempotency, config
get/set) is tested without touching the real vendored trees or actually
starting any q process.
