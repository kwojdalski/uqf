# uqs

Bridges the two vendored TorQ trees at the repo root - `lib/torq/` (the
production framework) and `lib/torq-finance-starter-pack/` (a layered
reference app built on top of it) - into a runnable demo, without editing
or writing into either. Full writeup: [docs/guides/uqs.md](../../docs/guides/uqs.md)
at the repo root.

Standalone package on purpose: this is process orchestration, not q
pricing, so it stays out of `python/uqf_client` (which depends on nothing
but `kola`) and carries its own dependencies (`typer`, `rich`, `loguru`,
`fastmcp`, `kola`).

## Layout

```
uqs_mcp.py    FastMCP server exposing the same operations as MCP tools
src/uqs/
  paths.py          where every file in the tree lives, and `repo_root()`,
                     which searches upward for a marker rather than counting
                     directory levels - which is what makes the folders safe
  model/            what the stack DECLARES: the pipeline registry, the edges
                     between pipelines, the plant's table schemas, the
                     process dependency graph. Starts nothing, opens nothing
  stack/            the RUNNING fleet: starting and stopping processes, the
                     environment they inherit, querying them, reading their
                     logs, and keeping the process count inside the licence's
                     connection budget
  cli/              the `uqs` command line, one module per command
                     family, all registering onto one shared Typer `app` so
                     `uqs start` stays spelled that way. `entry.py` is
                     the `[project.scripts]` entry point
  scaffold/         `uqs new-job` and `new-process`: the plan, the file
                     templates, and the interactive wizard that fills them in
  external/         processes this tree starts but does not own - the crypto
                     recorder, the Databento feed and its streamer
  checks/           read-only diagnostics: the smoke test over a running
                     fleet, the HDB's on-disk shape, the plant schema as the
                     live processes report it
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
tests/                  flat, one test module per source module, because
                         pytest discovery and `-k` are easier to aim at a
                         flat tree than the source is to read as one
```

The folders are layers, and the imports only ever point one way:

```
logger/ paths.py          depend on nothing in this package
model/           -> paths, logger
stack/           -> model, paths, logger
external/ checks/ -> stack, model, paths, logger
scaffold/ cli/   -> whatever they need below them, imported from the
                    module that defines it (there is no facade)
```

`model/` importing from `stack/` would be the change that breaks this, and
the reason to care is not tidiness: it would mean the declared shape of the
stack could no longer be read without the code that starts processes. So it
is checked rather than asserted - `test_module_split.py::test_the_folders_are_layers`
fails on any import that points back up.

## Quick start

One-time, installs the `uqs` command onto your `PATH` as an editable
link back to this source (edits picked up immediately, no reinstall):

```
uv tool install --editable python/uqs
```

If `uv tool list` shows a `torq-orchestrator`, that is an install from before
this package was renamed; its script still imports `torq_orchestrator` and now
fails. `uv tool uninstall torq-orchestrator` first - see
[docs/guides/uqs.md](../../docs/guides/uqs.md) for why an editable
install picks up source edits but not this.

then, from anywhere:

```
uqs start all
uqs summary
uqs stop all
```

Without that step (e.g. CI, a fresh checkout), `uv run` works the same,
just longer:

```
uv run --project python/uqs uqs start all
```

`uv run --project python/uqs` resolves this package's
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
clean                                 wipe ../../scripts/output/uqs/
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
uqs list processes --export processes.csv
uqs query "select from quotes" --port 6050 --export quotes.parquet
```

## Listing things

`list` isn't limited to processes - it dispatches on a small registry
(`stack.listing.LISTABLE_KINDS`), currently `processes` (procname/proctype/port/
startwithall, resolved and with overrides applied - the default), `fields`
(`process.csv`'s valid `config-set` columns), `overrides` (every
`config-set` override in effect), and `env` (`build_env()`'s resolved
`KDBBASEPORT`/`KDBHDB`/... values). Adding a new kind is one function plus
one registry entry - see `stack/listing.py`'s `_list_*` functions.

## Config setters

`config-get`/`config-set` read and write **`process_overrides.csv`** - not
the vendored `process.csv` (never edited) and not the *generated* one
under `scripts/output/uqs/` either, which `bootstrap()` rebuilds
from scratch on every single command, so anything written there directly
would just be overwritten by the next `start`/`stop`/`summary`/... call.
`process_overrides.csv` is what survives instead: a small
`procname,field,value` file, applied on top of the vendored + `fxfeed1`
rows every time `bootstrap()` (re)generates `process.csv`.

```
uqs config-set fxfeed1 startwithall 0
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
uqs logs "stp1 rdb1" -n 50
uqs logs -f --level WARNING
```

## crypto recorder (cryptorust) - a proof of concept

`crypto start`/`stop`/`status` (a nested command group) build and launch a sibling
`~/github_projects/cryptorust` checkout's own `kdb-market-data-recorder`
Rust binary, pointed at this demo's `stp1` - proving the kdb+ infra here
isn't TorQ/q-specific, any process that speaks kdb+ IPC can publish onto
it. See `docs/guides/uqs.md`'s own section for the full picture (schema,
credentials, `$CRYPTORUST_ROOT`).

## MCP server

```
uv run --project python/uqs python/uqs/uqs_mcp.py
```

Exposes `uqs_start`/`stop`/`restart`/`summary`/`print`/`clean`/`query`/
`get_config`/`set_config`/`list`/`logs`, plus the crypto recorder lifecycle
(`crypto_start`/`stop`/`status`, `crypto_fills_start`/`stop`/`status`), as
MCP tools (stdio transport) for an MCP client to drive the demo directly.
`new-process` (an interactive wizard) and `raw` (an arbitrary passthrough
to `torq.sh`) aren't exposed - see `uqs_mcp.py` for the exact,
current tool list.

## Testing

```
uv run --project python/uqs pytest
```

`tests/test_core.py` builds a minimal fake `lib/torq` +
`lib/torq-finance-starter-pack` tree under `tmp_path` so `core.py`'s pure
logic (path resolution, process.csv generation/idempotency, config
get/set) is tested without touching the real vendored trees or actually
starting any q process.
