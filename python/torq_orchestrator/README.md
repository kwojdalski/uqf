# torq-orchestrator

Bridges the two vendored TorQ trees at the repo root - `lib/torq/` (the
production framework) and `lib/torq-finance-starter-pack/` (a layered
reference app built on top of it) - into a runnable demo, without editing
or writing into either. Full writeup: [docs/guides/uqf-stack.md](../../docs/guides/uqf-stack.md)
at the repo root.

Standalone package on purpose: this is process orchestration, not q
pricing, so it stays out of `python/uqf_client` (which depends on nothing
but `kola`) and carries its own dependencies (`typer`, `rich`, `loguru`,
`fastmcp`, `kola`).

## Layout

```
uqf_stack_mcp.py    FastMCP server exposing the same operations as MCP tools
src/torq_orchestrator/
  cli.py            the Typer CLI itself - also reachable as the `uqf-stack`
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

One-time, installs the `uqf-stack` command onto your `PATH` as an editable
link back to this source (edits picked up immediately, no reinstall):

```
uv tool install --editable python/torq_orchestrator
```

then, from anywhere:

```
uqf-stack start all
uqf-stack summary
uqf-stack stop all
```

Without that step (e.g. CI, a fresh checkout), `uv run` works the same,
just longer:

```
uv run --project python/torq_orchestrator uqf-stack start all
```

`uv run --project python/torq_orchestrator` resolves this package's
dependencies on demand, no separate `uv sync` needed - though `uv sync`
here also works if you want the `.venv` up front.

Requires KDB-X (`q` on `PATH`
elsewhere in this repo) plus `envsubst` and `rlwrap` on `PATH` (`torq.sh`,
which this drives under the hood, needs both - macOS: `brew install
gettext rlwrap`).

## Commands

```
start [PROCS] [--port N]              start (default: all startwithall=1 processes)
stop [PROCS] [--port N]               stop
restart [PROCS] [--port N]            restart
summary [--port N] [--export FILE]    rich status table (up/down, pid, port)
print [PROCS] [--port N]              show exact startup command line(s)
clean                                 wipe ../../scripts/output/uqf-stack/
query EXPR --port N [--export FILE]   run a synchronous q expression
list [KIND] [--export FILE]           list every item of KIND - no argument shows the kinds
config-get PROCNAME [FIELD] [--raw] [--export FILE]   show a process's effective process.csv row, resolved
config-set PROCNAME FIELD VALUE       persist a process.csv field override
logs [PROCS] [-f] [-n N] [--level L]  tail out_/err_*.log through the CLI's own logger
new-process                           interactive wizard to add a new process
crypto start/stop/status              proof of concept: cryptorust (Rust) publishing over kdb+ IPC
raw -- ARGS...                        anything else torq.sh supports
```

`--help` on the command itself or any subcommand has the full picture.

## Exporting output

`summary`/`query`/`config-get`/`list` all take `--export FILE`, writing the
same rows shown on screen to `FILE` as CSV or Parquet (format inferred from
the extension) via [polars](https://pola.rs/) - `query`'s table results are
already a `polars.DataFrame` (that's what [kola](https://pypi.org/project/kola/)
returns for a table-shaped q result), the other commands' row lists get
wrapped into one the same way. Anything not tabular (e.g. `query`'s result
for `count t`, a bare atom) is rejected with an error rather than silently
exported as a bogus one-cell table.

```
uqf-stack list processes --export processes.csv
uqf-stack query "select from quotes" --port 6050 --export quotes.parquet
```

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
under `scripts/output/uqf-stack/` either, which `bootstrap()` rebuilds
from scratch on every single command, so anything written there directly
would just be overwritten by the next `start`/`stop`/`summary`/... call.
`process_overrides.csv` is what survives instead: a small
`procname,field,value` file, applied on top of the vendored + `fxfeed1`
rows every time `bootstrap()` (re)generates `process.csv`.

```
uqf-stack config-set fxfeed1 startwithall 0
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
uqf-stack logs "stp1 rdb1" -n 50
uqf-stack logs -f --level WARNING
```

## crypto recorder (cryptorust) - a proof of concept

`crypto start`/`stop`/`status` (a nested command group) build and launch a sibling
`~/github_projects/cryptorust` checkout's own `kdb-market-data-recorder`
Rust binary, pointed at this demo's `stp1` - proving the kdb+ infra here
isn't TorQ/q-specific, any process that speaks kdb+ IPC can publish onto
it. See `docs/guides/uqf-stack.md`'s own section for the full picture (schema,
credentials, `$CRYPTORUST_ROOT`).

## MCP server

```
uv run --project python/torq_orchestrator python/torq_orchestrator/uqf_stack_mcp.py
```

Exposes `uqf_stack_start`/`stop`/`restart`/`summary`/`print`/`clean`/`query`/
`get_config`/`set_config`/`list`/`logs`, plus the crypto recorder lifecycle
(`crypto_start`/`stop`/`status`, `crypto_fills_start`/`stop`/`status`), as
MCP tools (stdio transport) for an MCP client to drive the demo directly.
`new-process` (an interactive wizard) and `raw` (an arbitrary passthrough
to `torq.sh`) aren't exposed - see `uqf_stack_mcp.py` for the exact,
current tool list.

## Testing

```
uv run --project python/torq_orchestrator pytest
```

`tests/test_core.py` builds a minimal fake `lib/torq` +
`lib/torq-finance-starter-pack` tree under `tmp_path` so `core.py`'s pure
logic (path resolution, process.csv generation/idempotency, config
get/set) is tested without touching the real vendored trees or actually
starting any q process.
