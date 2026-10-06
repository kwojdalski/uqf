"""The Typer app itself, and what every command module shares.

`app`, the global `--debug` callback, the option types that repeat across
commands, and the small helpers that turn a refusal into an exit code.

Separated so the command modules can import one thing without importing each
other. cli/entry.py imports all of them for their registration side effect, which
is what keeps `uqs start` spelled that way after the split.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from uqs import paths as stack_paths
from uqs.cli import completion, table_browser
from uqs.logger import configure_logging, get_logger
from uqs.paths import UqsError
from uqs.stack import runtime

app = typer.Typer(
    no_args_is_help=True,
    help="Bridges lib/torq + lib/torq-finance-starter-pack into a runnable demo.",
)
console = Console()
log = get_logger(__name__)

#: `uqs job ...`: the commands that WRITE jobs into the tree - new, remove,
#: install - rather than act on a running fleet. A group, as `crypto` and
#: `replay` are, because the flat app exists to keep `uqs start` spelled that
#: way, and none of these is a lifecycle command. Registered on `app` in
#: create.py, the first of the three modules entry.py imports, so it lists
#: where they did.
job_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Write ETL jobs into the tree: scaffold one, remove a scaffold, install finished ones.",
)

#: `uqs data ...`: moving data the stack has already written into where it
#: belongs, and checking the result - replaying a tickerplant log into the HDB
#: and checking the HDB's shape. Here rather than in either module because
#: both register on it: replay.py and inspect.py. `backfill` stays top level,
#: being the one of these people reach for most.
data_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Move written data into the HDB and check its shape: replay, hdb-check.",
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


@app.callback()
def _configure(
    ctx: typer.Context,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Log at DEBUG. Same as LOG_LEVEL=DEBUG, and wins over it."),
    ] = False,
) -> None:
    """Global options, applied before any subcommand runs."""
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


def _debug_requested(ctx: typer.Context, flag: bool) -> bool:
    """Whether debug was asked for in any of the three ways it can be: the
    command's own `--debug`, the global one, or LOG_LEVEL=DEBUG."""
    parent = ctx.parent.obj if ctx.parent is not None else None
    return flag or bool((parent or {}).get("debug")) or _env_log_level() == "DEBUG"


PortOpt = Annotated[int, typer.Option("--port", help="KDBBASEPORT - shifts every process's port")]
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
    raise typer.Exit(code=1)


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
