"""The Typer app itself, and what every command module shares.

`app`, the global `--debug` callback, the option types that repeat across
commands, and the small helpers that turn a refusal into an exit code.

Separated so the command modules can import one thing without importing each
other. cli/entry.py imports all of them for their registration side effect, which
is what keeps `uqs start` spelled that way after the split.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from importlib.metadata import version as installed_version
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from uqs import paths as stack_paths
from uqs import runtimes
from uqs.cli import completion, table_browser
from uqs.logger import configure_logging, get_logger, json_requested
from uqs.paths import UqsError
from uqs.stack import runtime

app = typer.Typer(
    no_args_is_help=True,
    help="Bridges lib/torq + lib/torq-finance-starter-pack into a runnable demo.",
)
console = Console()
log = get_logger()

#: `uqs job ...`: everything about one ETL job short of the fleet's
#: lifecycle (#1417). Authoring - new, remove, install - WRITES jobs into the
#: tree; operations - backfill, preview, gaps, runs - run a bounded worker,
#: look at a polling feed's next page, find a streaming job's holes and read
#: the run ledger. Two help panels keep the halves apart. Registered on `app`
#: in create.py; the operations register on it from their own modules.
job_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="ETL jobs: write them into the tree (new, remove, install) and operate on "
    "them - backfill a bounded worker, preview a polling feed, find a streaming "
    "job's gaps, read the run ledger.",
)
#: The two help panels `uqs job --help` lists its commands under.
JOB_AUTHORING_PANEL = "Authoring"
JOB_OPERATIONS_PANEL = "Operations"

#: `uqs data ...`: moving data the stack has already written into where it
#: belongs, and checking the result - replaying a tickerplant log into the HDB
#: and checking the HDB's shape. Here rather than in either module because
#: both register on it: replay.py and inspect.py. A backfill is a job's
#: operation, so it is `uqs job backfill`, not one of these.
data_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Move written data into the HDB and check its shape: replay, seed, hdb-check.",
)

DEFAULT_LOG_LEVEL = "INFO"


def _env_log_level() -> str:
    """The level LOG_LEVEL asks for, or the default if it asks for nothing.

    `.env.example` lists LOG_LEVEL as a developer knob and
    docs/reference/environment.md names it a variable this package reads, but
    until now the only thing that read it was the `logged_function` trace
    decorator - `configure_logging` was called with a hardcoded INFO, so
    `LOG_LEVEL=DEBUG uqs summary` printed exactly what INFO did. A
    documented knob that does nothing is worse than no knob, because the
    reader concludes there is nothing to see rather than that the switch is
    unwired.

    An unrecognised value falls back to the default rather than aborting: a
    typo in a log level must not stop the fleet being started or inspected.
    """
    level = os.environ.get("LOG_LEVEL", "").strip().upper()
    if level in {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}:
        return level
    return DEFAULT_LOG_LEVEL


def _print_version(given: bool) -> None:
    """`uqs --version`: the installed uqs, eagerly, before any command runs."""
    if given:
        print(f"uqs {installed_version('uqs')}")
        raise typer.Exit()


@app.callback()
def _configure(
    ctx: typer.Context,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Log at DEBUG. Same as LOG_LEVEL=DEBUG, and wins over it."),
    ] = False,
    runtime_name: Annotated[
        str | None,
        typer.Option(
            "--runtime",
            help=(
                "Which stack to build and run: "
                + "; ".join(f"{k} - {v.description}" for k, v in runtimes.RUNTIMES.items())
                + f". Same as {runtimes.RUNTIME_ENV}=<name>, and wins over it."
            ),
            autocompletion=lambda: list(runtimes.RUNTIMES),
        ),
    ] = None,
    target: Annotated[
        str | None,
        typer.Option(
            "--target",
            help=(
                "Run the command on a deployed release instead of this machine: a target "
                "from deploy_targets.toml (`uqs list targets`). Add --yes to skip the "
                "confirmation a command that changes the server asks for."
            ),
        ),
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", help="With --target: run without asking", hidden=True)
    ] = False,
    version: Annotated[
        bool,
        typer.Option(
            "--version", is_eager=True, callback=_print_version, help="Print uqs's version and exit"
        ),
    ] = False,
) -> None:
    """Global options, applied before any subcommand runs."""
    if target is not None or yes:
        # The entry point forwards `uqs --target NAME ...` before this parses
        # anything (cli/entry.py); reaching here means it did not, so running
        # the command locally would be running it on the wrong machine.
        _die(UqsError("--target goes before the command: `uqs --target NAME <command>`"))
    # Through the environment rather than an argument, so every path this
    # command builds - and every uqs a child process runs - agrees on it.
    if runtime_name is not None:
        if runtime_name not in runtimes.RUNTIMES:
            _die(
                UqsError(
                    f"--runtime {runtime_name!r} is not a runtime - choose one of: "
                    + ", ".join(runtimes.RUNTIMES)
                )
            )
        os.environ[runtimes.RUNTIME_ENV] = runtime_name
    try:
        declared = runtimes.RUNTIMES[stack_paths.runtime_from_env()]
    except UqsError as exc:
        _die(exc)
        return
    _refuse_without_pipelines(declared, ctx.invoked_subcommand or "")
    # main() has already configured logging from the environment so that
    # anything logged during Typer's own startup lands somewhere. Re-running
    # it here is what makes --debug take effect, and the flag wins over the
    # environment because it is the more deliberate of the two.
    if debug:
        configure_logging(component="uqs", level="DEBUG")
    # Kept for commands that do more than log louder in debug mode - `summary`
    # adds a section - so `uqs --debug summary` and `uqs summary --debug` are
    # the same request.
    ctx.obj = {"debug": debug}


def _refuse_without_pipelines(declared: runtimes.Runtime, command: str) -> None:
    """Refuse `command` - its path, `graph` or `job backfill` - on a runtime
    without this tree's pipelines, when UQF_ONLY_COMMANDS lists it."""
    needs = runtimes.UQF_ONLY_COMMANDS.get(command)
    if not declared.pipelines and needs:
        _die(
            UqsError(
                f"`uqs {command}` {needs}; "
                f"the {declared.name} runtime has none of this tree's pipelines. "
                f"Use --runtime {runtimes.DEFAULT_RUNTIME}"
            )
        )


@job_app.callback()
def _job(ctx: typer.Context) -> None:
    # The root callback has already chosen the runtime (and refused a bad
    # one); it sees only `job`, and `job new` works on any runtime, so the
    # operations that need this tree's pipelines are refused here, by path.
    _refuse_without_pipelines(
        runtimes.RUNTIMES[stack_paths.runtime_from_env()], f"job {ctx.invoked_subcommand}"
    )


def _debug_requested(ctx: typer.Context, flag: bool) -> bool:
    """Whether debug was asked for in any of the three ways it can be: the
    command's own `--debug`, the global one, or LOG_LEVEL=DEBUG."""
    parent = ctx.parent.obj if ctx.parent is not None else None
    return flag or bool((parent or {}).get("debug")) or _env_log_level() == "DEBUG"


PortOpt = Annotated[
    int | None,
    typer.Option(
        "--port",
        help="KDBBASEPORT - shifts every process's port. Default: the runtime's own "
        + ", ".join(f"{r.name} {r.base_port}" for r in runtimes.RUNTIMES.values()),
    ),
]
ProcsArg = Annotated[
    list[str] | None,
    typer.Argument(
        help="'all' (the default), or one or more process names",
        autocompletion=completion.procnames,
        show_default=False,
    ),
]
ExportOpt = Annotated[
    Path | None,
    typer.Option("--export", help="Also write output to FILE as .csv or .parquet"),
]


#: The connection to one q process, shared by `query` and `schema` (#1738:
#: every option states what it means, so one name cannot quietly mean two things).
QHostOpt = Annotated[str, typer.Option("--host", help="Host the q process listens on")]
QUserOpt = Annotated[str, typer.Option("--user", help="User to log in to the q process as")]
QPasswdOpt = Annotated[str, typer.Option("--passwd", help="Password for --user")]


InteractiveOpt = Annotated[
    bool,
    typer.Option(
        "--interactive",
        "-i",
        help="Browse the table, fuzzy-filtering rows as you type; Enter prints the row picked.",
    ),
]


def _show(
    table: Table,
    interactive: bool,
    actions: Sequence[table_browser.RowAction] = (),
    refresh: Callable[[], Table] | None = None,
    every: float | None = None,
) -> None:
    """Print `table`, or browse it with --interactive (see table_browser.py),
    with any row `actions` the command offers, the `refresh` that re-reads it
    and, if `every` is given, how many seconds apart it re-reads on its own."""
    try:
        table_browser.show(table, interactive, console, actions, refresh, every)
    except UqsError as exc:
        _die(exc)


def _export(rows, export: Path | None) -> None:
    if export is None:
        return
    try:
        runtime.export_table(rows, export)
    except UqsError as exc:
        _die(exc)
        return
    if json_requested():  # stdout is the one JSON document
        log.info("exported to {}", export)
        return
    console.print(f"[green]exported to {export}[/]")


def _procs(names: list[str] | None) -> str:
    """ProcsArg's words as the one space-separated string the stack takes.

    The argument is variadic so that each name is its own word - which is what
    lets TAB complete it, and what `logs stp1 rdb1` always claimed to accept.
    A single quoted "stp1 rdb1" still works: it arrives as one word and the
    stack splits it the same way.
    """
    return " ".join(names) if names else "all"


def _paths():
    return stack_paths.default_paths()


def _lines(result) -> int:
    """Non-empty stdout line count, for a debug line that must not itself fail."""
    return len((result.stdout or "").strip().splitlines())


def _die(exc: UqsError) -> None:
    log.error("{}", exc)
    json_refusal(str(exc))
    raise typer.Exit(code=1)


def json_refusal(message: str) -> None:
    """With --json, the refusal as the one JSON document stdout carries (#1685).

    The log line has gone to stderr (uqs.logger.json_requested); without this
    a refused --json command would leave stdout empty, and a caller parsing it
    would read a refusal as a broken pipe. Nothing without --json."""
    if json_requested():
        print(json.dumps({"ok": False, "error": message}, indent=2))


def _sort_key(value: object):
    """Sort key for one cell, numeric where the whole column is numeric.

    Returned as a tuple so empties group together at one end rather than
    sorting as the empty string among real values - a process with no
    override set is not "before aaa", it is absent.
    """
    text = "" if value is None else str(value).strip()
    if not text:
        return (1, 0.0, "")
    try:
        return (0, float(text), "")
    except ValueError:
        return (0, 0.0, text.casefold())


def _sorted_items[Row: dict](items: list[Row], sort: str | None, reverse: bool) -> list[Row]:
    """`items` ordered by one column, or untouched when none is named.

    The column is matched case-insensitively against the keys the rows
    actually have, because those differ per command and per kind - `list
    processes` has procname/proctype/port/startwithall, `list env` has
    name/value, `summary` has Process/Status/PID/... - so there is no fixed
    set to validate against and an unknown name has to name the real ones
    back. Shared by `uqs list --sort` and `uqs summary --sort`.

    Numeric columns sort numerically. `port` is a string like "6051", and
    lexicographically "6100" sorts before "659" - which looks like the sort
    silently did nothing on the one column most worth sorting.
    """
    if not sort or not items:
        return items
    known = {column.casefold(): column for column in items[0]}
    column = known.get(sort.strip().casefold())
    if column is None:
        _die(
            UqsError(
                f"cannot sort by {sort!r}: no such column. Available: {', '.join(sorted(items[0]))}"
            )
        )
        return items
    return sorted(items, key=lambda item: _sort_key(item.get(column, "")), reverse=reverse)


def _run_streaming(result_fn, *args, **kwargs) -> None:
    try:
        result = result_fn(_paths(), *args, capture=False, **kwargs)
    except UqsError as exc:
        _die(exc)
        return
    raise typer.Exit(code=result.returncode)
