"""Reading and changing one process's configuration, and reading its output.

Split out of cli/inspect.py when that file passed the 400-line budget adding
an interactive qcon session to `query`. The line it was split on is what each command is ABOUT:
`inspect` answers questions about the DATA in a running stack - the HDB's
shape, a q expression, a table's columns - while these four are about a
PROCESS: the row it runs under, the catalogue it comes from, and what it has
written to its log.

Imported immediately after `inspect` in cli/entry.py, which keeps `--help`
listing these commands exactly where they were before the split.
"""

from __future__ import annotations

import shlex
from typing import Annotated

import typer
from rich.table import Table

from uqs.cli import completion
from uqs.cli.renames import renamed
from uqs.cli.shared import (
    BasePortOpt,
    ExportOpt,
    ProcsArg,
    _die,
    _export,
    _paths,
    _procs,
    app,
    console,
)
from uqs.paths import UqsError
from uqs.stack import attached_logs
from uqs.stack import channel as stack_channel
from uqs.stack import logs as stack_logs
from uqs.stack import multitail as stack_multitail
from uqs.stack import procs as stack_procs

#: `uqs config get/set`: one process's process.csv row, read resolved or
#: changed through process_overrides.csv. `uqs list overrides` and
#: `uqs list fields` stay kinds of `list` (cli/lists.py), the stack's own
#: tables.
config_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Read or set a process's process.csv fields (persisted to process_overrides.csv).",
)
app.add_typer(config_app, name="config")


@config_app.command("get", cls=renamed({"--port": "--base-port", "--raw": "--unresolved"}))
def config_get(
    procname: Annotated[
        str, typer.Argument(metavar="PROC", help="The process", autocompletion=completion.procname)
    ],
    field: Annotated[str | None, typer.Argument(autocompletion=completion.csv_fields)] = None,
    port: BasePortOpt = None,
    raw: Annotated[
        bool, typer.Option("--unresolved", help="Show unresolved ${VAR}/{VAR}+N placeholders as-is")
    ] = False,
    export: ExportOpt = None,
) -> None:
    """Show a process's effective process.csv row (or one field of it), with
    ${VAR}/{VAR}+N placeholders (KDBBASEPORT, KDBHDB, ...) resolved against
    the same env torq.sh itself would use - pass --unresolved to see them literal.
    """
    try:
        row = stack_procs.get_process_config(_paths(), procname, base_port=port, resolve=not raw)
    except UqsError as exc:
        _die(exc)
        return
    if field is not None:
        console.print(row.get(field, ""))
        return
    table = Table(title=f"{procname} config")
    table.add_column("field")
    table.add_column("value")
    for k, v in row.items():
        table.add_row(k, v)
    console.print(table)
    _export([{"field": k, "value": v} for k, v in row.items()], export)


@config_app.command("set")
def config_set(
    procname: Annotated[
        str, typer.Argument(metavar="PROC", help="The process", autocompletion=completion.procname)
    ],
    field: Annotated[str, typer.Argument(autocompletion=completion.csv_fields)],
    value: str,
) -> None:
    """Set one process.csv field for *procname* (persisted to
    process_overrides.csv, applied on every later start/stop/summary/...).
    """
    try:
        stack_procs.set_process_config(_paths(), procname, field, value)
    except UqsError as exc:
        _die(exc)
        return
    console.print(f"{procname}.{field} = {value}")


@app.command(cls=renamed({"-n": "--lines", "--print": "--show-command"}))
def logs(
    procs: ProcsArg = None,
    follow: Annotated[
        bool, typer.Option("--follow", "-f", help="Keep streaming new lines (Ctrl-C to stop)")
    ] = False,
    lines: Annotated[
        int, typer.Option("--lines", "-l", help="Lines per process log to show (history)")
    ] = 20,
    level: Annotated[
        str | None,
        typer.Option(
            help=stack_logs.LEVEL_HELP,
            autocompletion=completion.choices(*stack_logs.LEVEL_CHOICES),
        ),
    ] = None,
    multitail: Annotated[
        bool,
        typer.Option(
            "--multitail", help="Follow in multitail instead, one pane per out_/err_*.log file"
        ),
    ] = False,
    stream: Annotated[
        str | None,
        typer.Option(
            "--stream",
            help="With --multitail: which log files get a pane, out, err or both (default)",
            autocompletion=completion.choices("out", "err", "both"),
        ),
    ] = None,
    columns: Annotated[
        int | None,
        typer.Option("--columns", "-c", help="With --multitail: split the panes into N columns"),
    ] = None,
    print_only: Annotated[
        bool,
        typer.Option(
            "--show-command", help="With --multitail: show its command without running it"
        ),
    ] = False,
    channel: Annotated[
        bool,
        typer.Option(
            "--channel",
            help="Subscribe to the lines the processes publish (WARNING and up by default) "
            "instead of reading their files - live, until Ctrl-C",
        ),
    ] = False,
    ids: Annotated[
        list[str] | None,
        typer.Option(
            "--id", help="With --channel: only this worker or component's lines (repeatable)"
        ),
    ] = None,
    attach: Annotated[
        str | None,
        typer.Option(
            "--attach",
            help="Read an attached backfill's or receiver's log instead: the latest run for "
            "this attachment (`uqs job backfill --attach`)",
        ),
    ] = None,
    worker: Annotated[
        str | None,
        typer.Option(
            "--worker",
            help="With --attach: whose run - the worker, or <dataset>.receiver. Needed when "
            "the attachment has logs of more than one",
        ),
    ] = None,
    run: Annotated[
        str | None,
        typer.Option(
            "--run",
            help="With --attach: an older run, by its log's file name or a prefix of its "
            "stamp (e.g. 20261027T1304)",
        ),
    ] = None,
) -> None:
    """Tail out_/err_*.log for one or more processes through the same
    colorized logger the CLI itself uses, instead of raw per-process files -
    e.g. `logs stp1 rdb1 -f`, `logs -f --level WARNING`.

    `--multitail` follows the same files in the `multitail` binary, one pane
    each: `logs all --multitail --stream err -c 2`.

    `--channel` subscribes instead of reading files: every line the processes
    publish, filtered by `--id` and `--level` - e.g. `logs all --channel
    --level ERROR`, `logs fxpositions1 --channel --id fx_positions` (#1067).

    `--attach` reads an attached backfill's or receiver's run instead, which is
    no TorQ process: `logs --attach pb-uat --worker piggybank_ledger_backfill -f`
    (#1725). Its q errors and RESULT line are shown whatever `--level` says.

    What the options combine with (#1730): the PROCS are what is read, every
    option a filter on how. `--attach NAME` replaces PROCS - one attachment's
    run, narrowed by `--worker` (a worker, or <dataset>.receiver) and `--run`,
    which need it. `--channel` and `--multitail` each read PROCS another way:
    `--id` needs `--channel`; `--stream`, `--columns` and `--show-command` need
    `--multitail`. `--follow`, `--lines` and `--level` go with the files and
    with `--attach`.
    """
    if attach is None:
        for option, used in {"--worker": worker, "--run": run}.items():
            if used is not None:
                _die(UqsError(f"{option} needs --attach"))
                return
    else:
        clash = {"a process name": bool(procs), "--channel": channel, "--multitail": multitail}
        for option, used in clash.items():
            if used:
                _die(UqsError(f"{option} does not apply with --attach, which reads one run's log"))
                return
        try:
            attached_logs.show(
                _paths(), attach, worker=worker, run=run,
                follow=follow, lines=lines, min_level=level,
            )  # fmt: skip
        except UqsError as exc:
            _die(exc)
        return
    if ids and not channel:
        _die(UqsError("--id needs --channel"))
        return
    if channel:
        for option, used in {"--follow": follow, "--multitail": multitail}.items():
            if used:
                _die(UqsError(f"{option} does not apply with --channel, which is always live"))
                return
    given = {
        "--stream": stream is not None,
        "--columns": columns is not None,
        "--show-command": print_only,
    }
    if multitail:
        given = {"--follow": follow, "--level": level is not None}
    for option, used in given.items():
        if used:
            relation = "does not apply with" if multitail else "needs"
            _die(UqsError(f"{option} {relation} --multitail"))
            return
    try:
        if channel:
            stack_channel.follow_channel(_paths(), _procs(procs), ids=ids or (), min_level=level)
        elif multitail:
            argv = stack_multitail.multitail_command(
                _paths(), _procs(procs), stream=stream or "both", columns=columns or 1, lines=lines
            )
            if print_only:
                console.print(shlex.join(argv), markup=False, highlight=False, soft_wrap=True)
                return
            stack_multitail.run_multitail(argv)
        elif follow:
            stack_logs.follow_logs(_paths(), _procs(procs), min_level=level, lines=lines)
        else:
            stack_logs.print_recent_logs(_paths(), _procs(procs), lines=lines, min_level=level)
    except UqsError as exc:
        _die(exc)
