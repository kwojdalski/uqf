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
torq_demo.py       Typer CLI - the main entry point
torq_demo_mcp.py    FastMCP server exposing the same operations as MCP tools
src/torq_orchestrator/
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
tests/
  test_core.py      tests core.py's pure logic (paths, process.csv
                     generation/idempotency, config get/set) against a
                     fake vendored tree - no real q process needed
```

## Quick start

```
uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo.py start all
uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo.py summary
uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo.py stop all
```

Run from the repo root (or anywhere - both scripts resolve their own
location). `uv run --project python/torq_orchestrator` resolves this
package's dependencies on demand, no separate `uv sync` needed - though
`uv sync` here also works if you want the `.venv` up front.

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
config-get PROCNAME [FIELD] [--raw]   show a process's effective process.csv row, resolved
config-set PROCNAME FIELD VALUE       persist a process.csv field override
raw -- ARGS...                        anything else torq.sh supports
```

`--help` on the command itself or any subcommand has the full picture.

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
uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo.py config-set fxfeed1 startwithall 0
```

`config-get` resolves both of `process.csv`'s placeholder styles by
default - `${VAR}`/`$VAR` (`load=${KDBHDB}` -> the real path) and the port
column's `{VAR}`/`{VAR}+N` arithmetic shorthand (`port={KDBBASEPORT}+3` ->
`6013`), evaluated the same way `torq.sh` itself does at process-start
time. Pass `--raw` to see the literal value instead.

Valid fields are `process.csv`'s own columns: `host`, `port`, `proctype`,
`procname`, `U`, `localtime`, `g`, `T`, `w`, `load`, `startwithall`,
`extras`, `qcmd`. Takes effect on that process's next `start`/`restart` -
a currently-running instance of it is untouched.

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
