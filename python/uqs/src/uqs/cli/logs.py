"""`uqs logs`: reading processes' logs (split from cli/config.py, which holds the
process-configuration commands this one sat beside).

The command, its `--json` document and the refusals of the spellings it
retired (`--id`, and `--channel` in its old subscription sense, #2274).
Imported right after `config` in cli/entry.py, which keeps `--help` listing it
where it was.
"""

from __future__ import annotations

import shlex
from typing import Annotated

import typer
from typer._click.exceptions import UsageError

from uqs.cli.choices import ChoiceOpt
from uqs.cli.concepts import attach_opt
from uqs.cli.json_out import emit, json_opt, records
from uqs.cli.renames import ATTACHMENT, refused, renamed
from uqs.cli.safety import ReadOnly
from uqs.cli.shared import ProcsArg, _die, _paths, _procs, app, console
from uqs.paths import UqsError, UqsUsageError
from uqs.stack import attached_logs, log_follow
from uqs.stack import channel as stack_channel
from uqs.stack import logs as stack_logs
from uqs.stack import multitail as stack_multitail
from uqs.timefmt import timestamp

LogsJson = json_opt(
    '{"lines": [{time, host, process, proctype, level, channel, message}]} - the history '
    "only: not with --follow, --subscribe or --multitail; time ISO-8601 with its offset"
)
#: A log record's keys as `parse_log_line` reads them. TorQ's `id` column is the
#: record's channel (#2274), and a document says so.
_LINE_KEYS = ("time", "host", "procname", "proctype", "loglevel", "id", "message")


def lines_document(rows: list[dict]) -> dict:
    """`logs --json`'s document for log records - uqs-mcp's `uqs_logs` returns it too.
    A time is q's local .z.P, given this machine's offset."""
    lines = records([{("channel" if k == "id" else k): r.get(k) for k in _LINE_KEYS} for r in rows])
    return {"lines": [line | {"time": timestamp(line["time"])} for line in lines]}


def _json_lines(rows: list[dict]) -> None:
    emit(lines_document(rows))


_LevelOpt = ChoiceOpt(
    stack_logs.LEVEL_CHOICES, "--level", stack_logs.LEVEL_HELP, case_sensitive=False
)
#: `--id` was TorQ's `id` column, which is the channel now, and `--channel` was
#: the subscription (#1067); neither is accepted in its old sense (#2274).
_ID_MOVED = (
    "`--id` was renamed `--channel` (#2274): the id column is the subsystem channel now, "
    f"one of {', '.join(stack_logs.CHANNELS)}. Several `--channel` match any of them"
)
_CHANNEL_MOVED = (
    "`--channel` now filters by subsystem channel (#2274), one of "
    f"{', '.join(stack_logs.CHANNELS)}; subscribing to the published lines was renamed "
    "`--subscribe`"
)


_LogsRefused = refused({"--id": _ID_MOVED}, ReadOnly)


def _logs_parse_args(self, ctx, args):
    """`logs`: a bare `--channel` - the old subscription flag, which took no value - is
    answered with where it went, not with "requires an argument"."""
    try:
        return _LogsRefused.parse_args(self, ctx, args)
    except UsageError as exc:
        if "--channel" in exc.message and "requires an argument" in exc.message:
            raise UsageError(_CHANNEL_MOVED, ctx=ctx) from None
        raise


_LogsCommand = type("LogsCommand", (_LogsRefused,), {"parse_args": _logs_parse_args})


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
            "--run": "--stamp",  # a log's stamp, not the run_id "run" means (#1890)
            **ATTACHMENT,
        },
        _LogsCommand,
    )
)
def logs(
    procs: ProcsArg = None,
    follow: Annotated[
        bool, typer.Option("--follow", "-f", help="Keep streaming new lines (Ctrl-C to stop)")
    ] = False,
    lines: Annotated[
        int,
        typer.Option("--lines", "-l", min=0, help="Lines per process log to show (history)"),
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
    subscribe: Annotated[
        bool,
        typer.Option(
            "--subscribe",
            help="Subscribe to the lines the processes publish (WARNING and up by default) "
            "instead of reading their files - live, until Ctrl-C",
        ),
    ] = False,
    channels: Annotated[
        list[str] | None,
        typer.Option("--channel", help=stack_logs.CHANNEL_HELP),
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

    A line's channel is the subsystem it concerns (lifecycle, ipc, source, ...).
    `--channel` shows only those, and repeats to match any - e.g. `logs rdb1 -f
    --channel source --channel ipc --level DEBUG`. It filters what you VIEW; it
    does not stop the lines being written, or save the cost of writing them.

    `--subscribe` subscribes instead of reading files: every line the processes
    publish, filtered by `--channel` and `--level` - e.g. `logs all --subscribe
    --level ERROR`, `logs fxpositions1 --subscribe --channel source`.

    `--attachment` reads an attached backfill's or receiver's run instead, which is
    no TorQ process: `logs --attachment pb-uat --worker piggybank_ledger_backfill -f`.
    Its q errors and RESULT line are shown whatever `--level` says.

    What the options combine with: the PROCs are what is read, every
    option a filter on how. `--attachment ATTACHMENT` replaces PROC - one attachment's
    run, narrowed by `--worker` (a worker, or <dataset>.receiver) and `--stamp`,
    which need it. `--subscribe` and `--multitail` each read PROC another way:
    `--stream`, `--pane-columns` and `--show-command` need `--multitail`.
    `--follow`, `--lines` and `--level` go with the files and with `--attachment`;
    `--channel` goes with the files and `--subscribe`.
    """
    if as_json:
        live = {"--follow": follow, "--subscribe": subscribe, "--multitail": multitail}
        for option, used in live.items():
            if used:
                _die(UqsUsageError(f"--json prints the history: {option} does not apply with it"))
    if attach is None:
        for option, used in {"--worker": worker, "--stamp": run}.items():
            if used is not None:
                _die(UqsUsageError(f"{option} needs --attachment"))
    else:
        clash = {
            "a process name": bool(procs),
            "--subscribe": subscribe,
            "--channel": bool(channels),
            "--multitail": multitail,
        }
        for option, used in clash.items():
            if used:
                _die(
                    UqsUsageError(
                        f"{option} does not apply with --attachment, which reads one run's log"
                    )
                )
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
    if subscribe:
        for option, used in {"--follow": follow, "--multitail": multitail}.items():
            if used:
                _die(
                    UqsUsageError(f"{option} does not apply with --subscribe, which is always live")
                )
    try:
        channels = list(stack_logs.channel_names(channels))
    except UqsError as exc:
        _die(exc)
    given = {
        "--stream": stream is not None,
        "--pane-columns": pane_columns is not None,
        "--show-command": print_only,
    }
    if multitail:
        given = {
            "--follow": follow,
            "--level": level is not None,
            "--channel": bool(channels),
        }
    for option, used in given.items():
        if used:
            relation = "does not apply with" if multitail else "needs"
            _die(UqsError(f"{option} {relation} --multitail"))
    try:
        if subscribe:
            stack_channel.follow_channel(
                _paths(), _procs(procs), channels=channels or (), min_level=level
            )
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
            _json_lines(stack_logs.get_recent_logs(_paths(), _procs(procs), lines, level, channels))
        elif follow:
            log_follow.follow_logs(
                _paths(), _procs(procs), min_level=level, lines=lines, channels=channels
            )
        else:
            stack_logs.print_recent_logs(
                _paths(), _procs(procs), lines=lines, min_level=level, channels=channels
            )
    except UqsError as exc:
        _die(exc)
