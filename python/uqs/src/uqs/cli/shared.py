"""The Typer app itself, and what every command module shares.

`app`, the global `--debug` callback, the option types that repeat across
commands, and the small helpers that turn a refusal into an exit code.

Separated so the command modules can import one thing without importing each
other. cli/entry.py imports all of them for their registration side effect, which
is what keeps `uqs start` spelled that way after the split.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from uqs import paths as stack_paths
from uqs.cli import completion
from uqs.logger import configure_logging, get_logger
from uqs.paths import UqsError
from uqs.stack import runtime

app = typer.Typer(
    no_args_is_help=True,
    help="Bridges lib/torq + lib/torq-finance-starter-pack into a runnable demo.",
)
console = Console()
log = get_logger(__name__)

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


def _run_streaming(result_fn, *args, **kwargs) -> None:
    try:
        result = result_fn(_paths(), *args, capture=False, **kwargs)
    except UqsError as exc:
        _die(exc)
        return
    raise typer.Exit(code=result.returncode)
