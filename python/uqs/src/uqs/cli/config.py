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
from uqs.cli.choices import ChoiceOpt
from uqs.cli.concepts import attach_opt
from uqs.cli.json_out import emit, json_opt, records, timestamp
from uqs.cli.renames import ATTACHMENT, alias_command, renamed, warn_renamed
from uqs.cli.safety import ChangesState, ReadOnly, dry_run_note
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
from uqs.paths import UqsError, UqsUsageError
from uqs.stack import attached_logs, process_unset
from uqs.stack import channel as stack_channel
from uqs.stack import logs as stack_logs
from uqs.stack import multitail as stack_multitail
from uqs.stack import procs as stack_procs

#: `uqs config show/set/unset/list`: process.csv rows, set in process_overrides.csv (#1855).
config_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Process configuration: show, set, unset, list, env, field, sources.\n\nprocess.csv "
    "fields, set in process_overrides.csv; the resolved env; the sources the stack reads.",
)
app.add_typer(config_app, name="config")


GetJson = json_opt(
    '{"process", "fields": [{field, value}]} - or, given a FIELD, {"process", "field", "value"}'
)

#: The old spellings `config show` still takes (#1729).
_SHOW_RENAMES = {"--port": "--base-port", "--raw": "--unresolved"}


@config_app.command("show", cls=renamed(_SHOW_RENAMES, ReadOnly))
def config_get(
    procname: Annotated[
        str, typer.Argument(metavar="PROC", help="The process", autocompletion=completion.procname)
    ],
    field: Annotated[
        str | None, typer.Argument(metavar="FIELD", autocompletion=completion.csv_fields)
    ] = None,
    port: BasePortOpt = None,
    raw: Annotated[
        bool, typer.Option("--unresolved", help="Show unresolved ${VAR}/{VAR}+N placeholders as-is")
    ] = False,
    export: ExportOpt = None,
    as_json: GetJson = False,
) -> None:
    """Show a process's effective process.csv row, or one FIELD of it.

    ${VAR}/{VAR}+N placeholders resolve as torq.sh would; --unresolved shows them literal."""
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
        print(row.get(field, ""))  # the value alone, as `$(uqs config show ...)` takes it
        return
    table = Table(title=f"{procname} config")
    table.add_column("field")
    table.add_column("value")
    for k, v in row.items():
        table.add_row(k, v)
    console.print(table)
    _export([{"field": k, "value": v} for k, v in row.items()], export)


@config_app.command("set", cls=ChangesState)
def config_set(
    procname: Annotated[
        str, typer.Argument(metavar="PROC", help="The process", autocompletion=completion.procname)
    ],
    pairs: Annotated[
        list[str],
        typer.Argument(
            metavar="FIELD=VALUE...",
            help="process.csv fields and their values, as `uqs target set` takes its "
            "settings: port=6061 startwithall=0",
            autocompletion=completion.csv_fields,
        ),
    ],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Check the setting and show it; write nothing"),
    ] = False,
) -> None:
    """Set PROC's process.csv fields in process_overrides.csv; `uqs config unset` removes one."""
    settings = _pairs(pairs)
    try:
        for field, value in settings:  # every one checked before any is written
            stack_procs.set_process_config(_paths(), procname, field, value, True)
        if not dry_run:
            for field, value in settings:
                stack_procs.set_process_config(_paths(), procname, field, value, False)
    except UqsError as exc:
        _die(exc)
        return
    for field, value in settings:
        verb = "would set" if dry_run else "set"
        console.print(f"{verb} {procname}.{field} = {value}", markup=False)
    if dry_run:
        console.print(dry_run_note())


def _pairs(words: list[str]) -> list[tuple[str, str]]:
    """FIELD=VALUE words as (field, value); `FIELD VALUE`, the old two-word
    form (#1858), as one pair, warned."""
    if len(words) == 2 and "=" not in words[0]:
        warn_renamed(
            f"config set PROC {words[0]} {words[1]}", f"config set PROC {words[0]}={words[1]}"
        )
        return [(words[0], words[1])]
    out = []
    for word in words:
        field, sep, value = word.partition("=")
        if not sep or not field:
            _die(UqsError(f"{word!r} is not FIELD=VALUE"))
        out.append((field, value))
    return out


@config_app.command("unset", cls=ChangesState)
def config_unset(
    procname: Annotated[
        str, typer.Argument(metavar="PROC", help="The process", autocompletion=completion.procname)
    ],
    fields: Annotated[
        list[str],
        typer.Argument(
            metavar="FIELD...",
            help="Fields whose override to remove, so process.csv's own value applies",
            autocompletion=completion.csv_fields,
        ),
    ],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Check the change and show it; write nothing"),
    ] = False,
) -> None:
    """Remove PROC's override of each FIELD: process.csv's own value applies again."""
    try:
        process_unset.unset_process_config(_paths(), procname, fields, dry_run)
    except UqsError as exc:
        _die(exc)
        return
    verb = "would unset" if dry_run else "unset"
    console.print(f"{verb} {procname}.{', '.join(fields)}", markup=False)
    if dry_run:
        console.print(dry_run_note())


LogsJson = json_opt(
    '{"lines": [{time, host, process, proctype, loglevel, id, message}]} - the history '
    "only: not with --follow, --channel or --multitail; time ISO-8601 with its offset"
)
_LINE_KEYS = ("time", "host", "procname", "proctype", "loglevel", "id", "message")


def _json_lines(rows: list[dict]) -> None:  # q's local .z.P, with this machine's offset
    lines = records([{k: r.get(k) for k in _LINE_KEYS} for r in rows])
    emit({"lines": [line | {"time": timestamp(line["time"])} for line in lines]})


_LevelOpt = ChoiceOpt(
    stack_logs.LEVEL_CHOICES, "--level", stack_logs.LEVEL_HELP, case_sensitive=False
)
_StreamOpt = ChoiceOpt(
    stack_multitail.MULTITAIL_STREAMS,
    "--stream",
    "With --multitail: which log files get a pane",
    default=stack_multitail.DEFAULT_STREAM,
)
_AttachOpt = attach_opt(
    "The attachment whose backfill or receiver log to read instead: its latest "
    "run (`uqs job backfill --attachment`)"
)


@app.command(
    cls=renamed(
        {
            "-n": "--lines",
            "--print": "--show-command",
            "--columns": "--pane-columns",
            "-c": "--pane-columns",
            "--run": "--stamp",  # a log's stamp, not the run_id "run" means (#1890)
            **ATTACHMENT,
        },
        ReadOnly,
    )
)
def logs(
    procs: ProcsArg = None,
    follow: Annotated[
        bool, typer.Option("--follow", "-f", help="Keep streaming new lines (Ctrl-C to stop)")
    ] = False,
    lines: Annotated[
        int, typer.Option("--lines", "-l", help="Lines per process log to show (history)")
    ] = 20,
    level: _LevelOpt = None,
    multitail: Annotated[
        bool,
        typer.Option(
            "--multitail", help="Follow in multitail instead, one pane per out_/err_*.log file"
        ),
    ] = False,
    stream: _StreamOpt = None,
    pane_columns: Annotated[
        int | None,
        typer.Option("--pane-columns", help="With --multitail: lay the panes out in N columns"),
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
    attach: _AttachOpt = None,
    worker: Annotated[
        str | None,
        typer.Option(
            "--worker",
            help="With --attachment: whose run - the worker, or <dataset>.receiver. Needed when "
            "the attachment has logs of more than one",
        ),
    ] = None,
    run: Annotated[
        str | None,
        typer.Option(
            "--stamp",
            help="With --attachment: an older run, by its log's file name or a prefix of its "
            "stamp (e.g. 20261027T1304)",
        ),
    ] = None,
    as_json: LogsJson = False,
) -> None:
    """Show the logs of one or more processes.

    Tails out_/err_*.log through the CLI's own colorized logger, not raw
    files - e.g. `logs stp1 rdb1 -f`, `logs -f --level WARNING`.

    `--multitail` follows the same files in the `multitail` binary, one pane
    each: `logs all --multitail --stream err --pane-columns 2`.

    `--channel` subscribes instead of reading files: every line the processes
    publish, filtered by `--id` and `--level` - e.g. `logs all --channel
    --level ERROR`, `logs fxpositions1 --channel --id fx_positions`.

    `--attachment` reads an attached backfill's or receiver's run instead, which is
    no TorQ process: `logs --attachment pb-uat --worker piggybank_ledger_backfill -f`.
    Its q errors and RESULT line are shown whatever `--level` says.

    What the options combine with: the PROCs are what is read, every
    option a filter on how. `--attachment ATTACHMENT` replaces PROC - one attachment's
    run, narrowed by `--worker` (a worker, or <dataset>.receiver) and `--stamp`,
    which need it. `--channel` and `--multitail` each read PROC another way:
    `--id` needs `--channel`; `--stream`, `--pane-columns` and `--show-command` need
    `--multitail`. `--follow`, `--lines` and `--level` go with the files and
    with `--attachment`.
    """
    if as_json:
        live = {"--follow": follow, "--channel": channel, "--multitail": multitail}
        for option, used in live.items():
            if used:
                _die(UqsUsageError(f"--json prints the history: {option} does not apply with it"))
                return
    if attach is None:
        for option, used in {"--worker": worker, "--stamp": run}.items():
            if used is not None:
                _die(UqsUsageError(f"{option} needs --attachment"))
                return
    else:
        clash = {"a process name": bool(procs), "--channel": channel, "--multitail": multitail}
        for option, used in clash.items():
            if used:
                _die(
                    UqsUsageError(
                        f"{option} does not apply with --attachment, which reads one run's log"
                    )
                )
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
        _die(UqsUsageError("--id needs --channel"))
        return
    if channel:
        for option, used in {"--follow": follow, "--multitail": multitail}.items():
            if used:
                _die(UqsUsageError(f"{option} does not apply with --channel, which is always live"))
                return
    given = {
        "--stream": stream is not None,
        "--pane-columns": pane_columns is not None,
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
                _paths(),
                _procs(procs),
                stream=stream or stack_multitail.DEFAULT_STREAM,
                columns=pane_columns or 1,
                lines=lines,
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


#: Its name before #1733: `show` is one record, `get` was the same thing.
alias_command(config_app, "get", "uqs config show", config_get, cls=renamed(_SHOW_RENAMES))
