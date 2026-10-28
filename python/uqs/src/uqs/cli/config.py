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

import json
import shlex
from typing import Annotated

import typer
from rich.table import Table

from uqs.cli import completion
from uqs.cli.json_out import emit, json_opt
from uqs.cli.renames import renamed
from uqs.cli.shared import (
    BasePortOpt,
    ExportOpt,
    InteractiveOpt,
    ProcsArg,
    _die,
    _export,
    _paths,
    _procs,
    _show,
    _sorted_items,
    app,
    console,
)
from uqs.deploy import targets
from uqs.paths import UqsError
from uqs.stack import attached_logs, listing
from uqs.stack import channel as stack_channel
from uqs.stack import logs as stack_logs
from uqs.stack import multitail as stack_multitail
from uqs.stack import procs as stack_procs
from uqs.stack.listing import LISTABLE_KINDS

#: `uqs config get/set`: one process's process.csv row, read resolved or
#: changed through process_overrides.csv. `uqs list overrides` and
#: `uqs list fields` stay kinds of `list`, where they share its --sort,
#: --export and completion with every other kind.
config_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Read or set a process's process.csv fields (persisted to process_overrides.csv).",
)
app.add_typer(config_app, name="config")


GetJson = json_opt(
    '{"process", "fields": [{field, value}]} - or, given a FIELD, {"process", "field", "value"}'
)


@config_app.command("get", cls=renamed({"--port": "--base-port", "--raw": "--unresolved"}))
def config_get(
    procname: Annotated[str, typer.Argument(autocompletion=completion.procname)],
    field: Annotated[str | None, typer.Argument(autocompletion=completion.csv_fields)] = None,
    port: BasePortOpt = None,
    raw: Annotated[
        bool, typer.Option("--unresolved", help="Show unresolved ${VAR}/{VAR}+N placeholders as-is")
    ] = False,
    export: ExportOpt = None,
    as_json: GetJson = False,
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
    if as_json and field is not None:
        emit({"process": procname, "field": field, "value": row.get(field, "")})
        return
    if as_json:
        emit({"process": procname, "fields": [{"field": k, "value": v} for k, v in row.items()]})
        _export([{"field": k, "value": v} for k, v in row.items()], export)
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


@app.command("list", cls=renamed({"--port": "--base-port"}))
def list_items(
    kind: Annotated[
        str | None,
        typer.Argument(
            help="What to list - run with no argument to see every kind",
            autocompletion=completion.list_kinds,
        ),
    ] = None,
    port: BasePortOpt = None,
    export: ExportOpt = None,
    sort: Annotated[
        str | None,
        typer.Option(
            "--sort",
            help="Sort by this column (case-insensitive, numeric-aware).",
            autocompletion=completion.list_columns,
        ),
    ] = None,
    reverse: Annotated[
        bool, typer.Option("--reverse", help="Sort descending. Only meaningful with --sort.")
    ] = False,
    interactive: InteractiveOpt = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the rows as a JSON list, for a script.")
    ] = False,
) -> None:
    """List every item of KIND - run with no argument to see the available
    kinds. Not just processes: 'fields' lists process.csv's valid config set
    columns, 'overrides' lists every process_overrides.csv entry currently
    set, 'env' lists build_env()'s resolved KDBBASEPORT/KDBHDB/... values,
    'targets' lists this machine's deploy_targets.toml.

    `--sort` takes any column the chosen kind produces, which differ between
    kinds. The order reaches `--export` too, so an exported CSV matches what
    was on screen.
    """
    if kind is None:
        if as_json:
            print(json.dumps(sorted(LISTABLE_KINDS)))
        else:
            console.print(f"Available kinds: {', '.join(sorted(LISTABLE_KINDS))}")
        return
    try:
        items = listing.list_items(_paths(), kind, base_port=port)
    except UqsError as exc:
        _die(exc)
        return
    if kind == "targets" and not items and not as_json:
        console.print(
            f"no targets in {targets.declaration_path(_paths().repo_root)} - add one with "
            "`uqs target add`, or `uqs deploy push` to a server"
        )
        return
    items = _sorted_items(items, sort, reverse)
    if as_json:
        print(json.dumps(items, indent=2))
        _export(items, export)
        return
    table = Table(title=f"{kind} ({len(items)})")
    if items:
        for col in items[0]:
            table.add_column(col)
        for item in items:
            table.add_row(*item.values())
    _show(table, interactive)
    _export(items, export)


@config_app.command("set")
def config_set(
    procname: Annotated[str, typer.Argument(autocompletion=completion.procname)],
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


LogsJson = json_opt(
    '{"lines": [{time, host, procname, proctype, loglevel, id, message}]} - the history '
    "only: not with --follow, --channel or --multitail"
)
_LINE_KEYS = ("time", "host", "procname", "proctype", "loglevel", "id", "message")


def _json_lines(records: list[dict]) -> None:
    emit({"lines": [{k: r.get(k) for k in _LINE_KEYS} for r in records]})


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
    as_json: LogsJson = False,
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
    """
    if as_json:
        for option, used in {
            "--follow": follow,
            "--channel": channel,
            "--multitail": multitail,
        }.items():
            if used:
                _die(UqsError(f"--json prints the history: {option} does not apply with it"))
                return
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
            if as_json:
                _json_lines(attached_logs.recent(
                    _paths(), attach, worker=worker, run=run, lines=lines, min_level=level
                ))  # fmt: skip
                return
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
        elif as_json:
            _json_lines(stack_logs.get_recent_logs(_paths(), _procs(procs), lines, level))
        elif follow:
            stack_logs.follow_logs(_paths(), _procs(procs), min_level=level, lines=lines)
        else:
            stack_logs.print_recent_logs(_paths(), _procs(procs), lines=lines, min_level=level)
    except UqsError as exc:
        _die(exc)
