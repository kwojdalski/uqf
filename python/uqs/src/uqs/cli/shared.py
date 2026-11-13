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
from importlib.metadata import version as installed_version
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.table import Table

from uqs import paths as stack_paths
from uqs import runtimes
from uqs.cli import completion, json_out, rendering, table_browser
from uqs.cli.concepts import target_opt
from uqs.generated import q_facts
from uqs.logger import LOG_LEVEL_ENV, configure_logging, get_logger, json_requested
from uqs.paths import UqsError, UqsUsageError
from uqs.stack import listing, table_export

app = typer.Typer(
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    help="Build, run, inspect and deploy the uqf kdb+ stack: its processes, jobs and data.",
)
console = rendering.console()
log = get_logger()

#: `uqs job ...`: everything about one ETL job short of the fleet's lifecycle
#: (#1417). Authoring WRITES jobs into the tree; operations run a worker, look
#: at a feed's next page, find a job's holes and read the run ledger - two help
#: panels. Registered on `app` in create.py; operations from their own modules.
job_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Jobs: new, list, remove, install, backfill, preview, check, gaps, receiver, "
    "checkpoint, runs, attachment.\n\nETL jobs: write them into the tree, and operate on them.",
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
    help="Fill, check, change and compare the HDB: replay, seed, migrate, check, alter, "
    "compress, snapshot, diff.",
)

DEFAULT_LOG_LEVEL = "INFO"


def _env_log_level() -> str:
    """The level LOG_LEVEL asks for, or the default if it asks for nothing.

    `.env.example` lists LOG_LEVEL as a developer knob and
    docs/reference/environment.md names it a variable this package reads, but
    until now the only thing that read it was the `logged_function` trace
    decorator - `configure_logging` was called with a hardcoded INFO, so
    `LOG_LEVEL=DEBUG uqs status` printed exactly what INFO did. A
    documented knob that does nothing is worse than no knob, because the
    reader concludes there is nothing to see rather than that the switch is
    unwired.

    An unrecognised value falls back to the default rather than aborting: a
    typo in a log level must not stop the fleet being started or inspected.
    """
    level = os.environ.get(LOG_LEVEL_ENV, "").strip().upper()
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
        typer.Option(
            "--debug",
            help="Log at DEBUG. Same as LOG_LEVEL=DEBUG, and wins over it. `status` adds "
            "how long each process took to load; `job backfill` starts its process with "
            "-verbose, so its own log carries DEBUG lines too.",
        ),
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
        target_opt(
            "--target",
            "Run the command on a deployed release instead of this machine: a target from "
            "deploy_targets.toml (`uqs target list`). Add --yes to skip the confirmation a "
            "command that changes the server asks for.",
        ),
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="With --target: run without asking")
    ] = False,
    version: Annotated[
        bool,
        typer.Option(
            "--version", is_eager=True, callback=_print_version, help="Print uqs's version and exit"
        ),
    ] = False,
) -> None:
    """Global options, applied before any subcommand runs."""
    if target is not None:
        # The entry point forwards `uqs --target TARGET ...` before this parses
        # anything (cli/entry.py); reaching here means it did not, so running
        # the command locally would be running it on the wrong machine.
        _die(UqsUsageError("--target goes before the command: `uqs --target TARGET <command>`"))
    if yes:
        # `uqs --yes job remove X`: before the command, --yes only goes with
        # --target; the hint named --target although none was given (#1885)
        _die(
            UqsUsageError(
                "--yes before the command only goes with --target: put it after the command - "
                "`uqs <command> ... --yes`"
            )
        )
    # Through the environment rather than an argument, so every path this
    # command builds - and every uqs a child process runs - agrees on it.
    if runtime_name is not None:
        if runtime_name not in runtimes.RUNTIMES:
            _die(
                UqsUsageError(
                    f"--runtime {runtime_name!r} is not a runtime - choose one of: "
                    + ", ".join(runtimes.RUNTIMES)
                )
            )
        os.environ[runtimes.RUNTIME_ENV] = runtime_name
    try:  # refused here, before any command reads it
        stack_paths.runtime_from_env()
    except UqsError as exc:
        _die(exc)
    # main() has already configured logging from the environment so that
    # anything logged during Typer's own startup lands somewhere. Re-running
    # it here is what makes --debug take effect, and the flag wins over the
    # environment because it is the more deliberate of the two.
    if debug:
        configure_logging(component="uqs", level="DEBUG")
    # Kept for commands that do more than log louder in debug mode - `status`
    # adds a section.
    ctx.obj = {"debug": debug}


def _debug_requested(ctx: typer.Context) -> bool:
    """Whether debug was asked for: the global `--debug` or LOG_LEVEL=DEBUG."""
    root = ctx.find_root().obj
    return bool((root or {}).get("debug")) or _env_log_level() == "DEBUG"


BasePortOpt = Annotated[
    int | None,
    typer.Option(
        "--base-port",
        min=1,
        max=65535,
        help="The stack's base port, KDBBASEPORT - shifts every process's port. "
        "Default: the runtime's own "
        + ", ".join(f"{r.name} {r.base_port}" for r in runtimes.RUNTIMES.values()),
    ),
]
#: The context_settings of a command that passes words it does not know on.
PASS_THROUGH = {"allow_extra_args": True, "ignore_unknown_options": True}

ProcsArg = Annotated[
    list[str] | None,
    typer.Argument(
        metavar="PROC...",
        help="'all' (the default), or one or more process names",
        autocompletion=completion.procnames,
        show_default=False,
    ),
]


def _export_path(path: Path | None) -> Path | None:
    """--export's FILE, refused as the command line is read - before any output."""
    if path is not None:
        try:
            table_export.check_path(path)
        except UqsError as exc:
            raise typer.BadParameter(str(exc)) from None
    return path


ExportOpt = Annotated[
    Path | None,
    typer.Option(
        "--export",
        metavar="FILE",
        callback=_export_path,
        help="Also write output to FILE as .csv or .parquet",
    ),
]


#: The connection to one q process, shared by `query` and `schema` (#1738:
#: every option states what it means, so one name cannot quietly mean two things).
QHostOpt = Annotated[str, typer.Option("--host", help="Host the q process listens on")]


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
        table_export.export_table(rows, export)
    except UqsError as exc:
        _die(exc)
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


#: The exit codes every command keeps to (#1885): a refusal - an unknown name,
#: a check that fails - is 1; a command line wrong as given is 2, as Click's
#: own usage errors are. An empty result is 0, and so is nothing to do.
EXIT_REFUSED, EXIT_USAGE = 1, 2
#: A job that did part of its job and says how to finish it (#2226): an
#: attached backfill whose rows are written and covered but which a reader
#: has not reloaded - running the same command again asks the readers again.
EXIT_PARTIAL = 3
#: A run that stopped because an operator asked it to (#2679, #2677): it finished its current
#: window, kept everything it had completed, and left the rest for a re-run. q's own code for
#: the stop, so the CLI and the runner say the same number.
EXIT_STOPPED = q_facts.STOP_EXIT_CODE


def _die(exc: UqsError) -> NoReturn:
    log.error("{}", exc)
    details = getattr(exc, "json_details", None)
    json_refusal(str(exc), details() if callable(details) else None)
    raise typer.Exit(code=EXIT_USAGE if isinstance(exc, UqsUsageError) else EXIT_REFUSED)


def json_refusal(message: str, details: dict | None = None) -> None:
    """With --json, the refusal as the one JSON document stdout carries (#1685).

    The log line has gone to stderr (uqs.logger.json_requested); without this
    a refused --json command would leave stdout empty, and a caller parsing it
    would read a refusal as a broken pipe. Nothing without --json. `details` are
    what a structured refusal adds beside its message - a backfill coordination
    refusal's `findings` (#1827) - and never replace `ok` or `error`."""
    if json_requested():
        json_out.emit({**(details or {}), "ok": False, "error": message})


def _run_streaming(result_fn, *args, **kwargs) -> None:
    try:
        result = result_fn(_paths(), *args, capture=False, **kwargs)
    except UqsError as exc:
        _die(exc)
    raise typer.Exit(code=result.returncode)


#: The process a command acts on, as its first positional (#1730).
ProcArg = Annotated[
    str | None,
    typer.Argument(
        metavar="PROC",
        help="The process, by name, e.g. rdb1",
        autocompletion=completion.procname,
        show_default=False,
    ),
]


def _proc_first(
    proc: str | None, rest: str | None, port_given: bool
) -> tuple[str | None, str | None]:
    """`CMD [PROC] [X]` given one word: PROC if it names a process of this stack
    and no --port was given, else X (`uqs query 'count quote'` asks the gateway)."""
    if proc is None or rest is not None:
        return proc, rest
    if port_given or proc not in _known_procs():
        return None, proc
    return proc, None


def _known_procs() -> set[str]:
    try:  # only no registry (no vendored process.csv) means no names (#1759)
        return set(listing.configured_ports(_paths(), None))
    except FileNotFoundError:
        return set()
