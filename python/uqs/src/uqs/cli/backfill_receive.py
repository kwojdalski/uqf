"""The two halves of a backfill whose HDB is on another machine (#1650).

`uqs job receiver start --contract CONTRACT --attachment ATTACHMENT --listen-port P`, on the
stack's machine,
starts the receiver for a receiver contract approved there (`uqs job attachment contract`);
`uqs job backfill JOB --attachment ATTACHMENT --writer HOST:P ...`, on the source's
machine, extracts and sends to it. What each decides is in
uqs.attach.backfill_remote.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import typer

from uqs.attach import backfill_remote, backfill_run, config, service
from uqs.cli.concepts import attach_opt
from uqs.cli.json_out import emit, json_opt
from uqs.cli.renames import ATTACHMENT, alias_command, renamed, warn_renamed
from uqs.cli.safety import ChangesState, dry_run_note
from uqs.cli.shared import JOB_OPERATIONS_PANEL, _die, _paths, console, job_app
from uqs.paths import UqsError, UqsUsageError
from uqs.stack.run_modes import READ_ONLY_MODES


def extract(
    attachment: str,
    worker: str,
    version: str,
    bound_from: datetime,
    bound_to: datetime,
    *,
    writer: str,
    mode: str,
    port: int | None,
    fixture: bool,
    on_conflict: str | None,
    as_json: bool,
    wait: bool,
    verbose: bool,
    trace: bool,
    force: bool = False,
) -> None:
    """`uqs job backfill --attachment ATTACHMENT --writer HOST:PORT`: plan or start the extractor.
    With `force`, HOST:PORT is any q process (a wdb), written to with nothing checked."""
    from uqs.cli.backfill_attached import _outcome_text, _report, exit_code, logs_command, outcome

    if fixture:
        _die(
            UqsUsageError(
                "--fixture is refused with --attachment: a fixture in a native HDB is data"
            )
        )
        return
    if port is not None:
        _die(
            UqsUsageError(
                "--base-port is not taken with --writer: the receiver's address is the stack's"
            )
        )
        return
    if force:
        if on_conflict not in (None, "append"):
            _die(
                UqsError(
                    f"--override any-writer only appends: --on-conflict {on_conflict} would "
                    "need to read what the writer holds, and nothing is read there"
                )
            )
            return
        on_conflict = "append"
    try:
        paths = _paths()
        if mode in READ_ONLY_MODES:
            _, plan = backfill_remote.remote_plan(
                paths, attachment, worker, version, bound_from, bound_to,
                writer=writer, on_conflict=on_conflict, force=force,
            )  # fmt: skip
            text = (
                f"{worker} -> attachment {attachment} "
                f"{'FORCED straight into' if force else 'through the receiver at'} {writer}: "
                f"{len(plan['windows'])} window(s) (nothing was written)"
            )
            _report(plan, as_json, text)
            raise typer.Exit(code=0)
        run_, plan = backfill_remote.launch_remote(
            paths, attachment, worker, version, bound_from, bound_to,
            writer=writer, mode=mode, on_conflict=on_conflict, verbose=verbose, trace=trace,
            force=force,
        )  # fmt: skip
    except UqsError as exc:
        _die(exc)
        return
    info = {"mode": mode, "job": worker, "writer": writer, "plan": plan, **run_.as_dict()}
    if not wait:
        _report(
            info,
            as_json,
            f"started {worker} (pid {run_.pid}) -> {writer}; log {run_.log}"
            f"\n  follow it with `{logs_command(attachment, worker)} -f`",
        )
        raise typer.Exit(code=0)
    if not as_json:  # with --json, stdout is the one document below (#1685)
        console.print(
            f"[dim]waiting for {worker} (pid {run_.pid}); Ctrl-C stops waiting, not the run[/]"
        )
    runner, result = backfill_run.follow(run_)
    code = exit_code(runner)
    _report(
        {**info, "result": result, **outcome(result.get("state") or "unknown", code)},
        as_json,
        _outcome_text(worker, run_, code, result),
    )
    raise typer.Exit(code=code)


#: `uqs job receiver start|stop` (#1858): the receiver is a noun with two
#: verbs, as a connector's is, where `job receive --stop` turned the verb into
#: its opposite with a flag. `job receive` stays one release, hidden.
receiver_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Receivers of another machine's backfill: start, stop.\n\nA backfill's HDB writer "
    "on the stack's machine, for an extractor on another.",
)
job_app.add_typer(receiver_app, name="receiver", rich_help_panel=JOB_OPERATIONS_PANEL)

AttachmentOpt = attach_opt(
    "The saved, verified attachment whose HDB it writes",
    required=True,
)
ContractOpt = Annotated[
    str | None,
    typer.Option(
        "--contract",
        help="The identity of the receiver contract approved for the attachment "
        "(`uqs job attachment contract approve`); a unique prefix will do",
    ),
]
ListenPortOpt = Annotated[
    int | None, typer.Option("--listen-port", help="The port the extractor connects to")
]
_STARTED = '{"attachment", "dataset", "contract", "port", "hdb_dir", "pid", "log", ...}'
ReceiverJson = json_opt(_STARTED)
ReceiveJson = json_opt(_STARTED, 'With --stop, {"stopped": [...]}')
StoppedJson = json_opt('{"stopped": [{job, pid, ...}], "dry_run"}')


@receiver_app.command("start", cls=ChangesState)
def receiver_start(
    attach: AttachmentOpt,
    contract: ContractOpt = None,
    port: ListenPortOpt = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            "-n",
            help="Admit the destination and show what it would serve; start nothing",
        ),
    ] = False,
    as_json: ReceiverJson = False,
) -> None:
    """Start the receiver for a backfill extracted on another machine.

    Run on the STACK's machine, which needs no code of the job: it serves the
    receiver contract --contract that `uqs job attachment contract approve` bound to the
    attachment here, and takes the row limit and the permitted conflict
    policies from that approval. It admits the attachment's destination against the
    live stack as `uqs job backfill --attachment` does - the pinned HDB, its writers and
    readers, the end-of-day fence - then listens on --listen-port for the extractor
    (`uqs job backfill JOB --attachment ATTACHMENT --writer THIS-HOST:PORT ...` on the
    source's machine). Every batch is checked against the contract - dataset,
    columns and types, window column, row key, partition, conflict policy - and
    committed through the fenced HDB writer; the readers are asked to reload as rows
    finish. It authenticates the attachment's stack user, and stops itself where the
    end-of-day fence begins. `uqs job receiver stop` stops it.
    """
    _start(attach, contract, port, dry_run, as_json)


_AttachOpt = attach_opt(
    "The attachment whose receivers on this machine stop",
    required=True,
)


@receiver_app.command("stop", cls=ChangesState)
def receiver_stop(
    attach: _AttachOpt,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Name the receivers that would stop; stop nothing"),
    ] = False,
    as_json: StoppedJson = False,
) -> None:
    """Stop this machine's receivers for an attachment, so a detach can go ahead.

    An extractor then fails its next window without covering it."""
    stopped = backfill_remote.stop_receivers(attach, dry_run=dry_run)
    names = ", ".join(f"{r['worker']} (pid {r['pid']})" for r in stopped) or "none was running"
    if as_json:
        emit({"stopped": stopped, "dry_run": dry_run})
    elif dry_run:
        console.print(f"would stop: {names}", markup=False)
        console.print(dry_run_note())
    else:
        console.print(f"stopped: {names}", markup=False)
    raise typer.Exit(code=0)


def receive(
    attach: AttachmentOpt,
    contract: ContractOpt = None,
    port: ListenPortOpt = None,
    stop: Annotated[
        bool,
        typer.Option(
            "--stop",
            help="Stop this machine's receiver for the attachment instead, so a detach can go "
            "ahead; an extractor then fails its next window without covering it",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            "-n",
            help="Admit the destination and show what it would serve; start nothing",
        ),
    ] = False,
    as_json: ReceiveJson = False,
) -> None:
    """`uqs job receive`, before #1858: `job receiver start`, or with --stop
    `job receiver stop`."""
    if stop:
        warn_renamed("uqs job receive --stop", "uqs job receiver stop")
        receiver_stop(attach, dry_run, as_json)
    _start(attach, contract, port, dry_run, as_json)


def _start(attach: str, contract: str | None, port: int | None, dry_run: bool, as_json: bool):
    """Admit the destination and start the receiver - `job receiver start`."""
    if port is None:
        _die(
            UqsUsageError(
                "--listen-port is required to start a receiver: the port the extractor connects to"
            )
        )
        return
    if contract is None:
        _die(
            UqsUsageError(
                "--contract is required to start a receiver: the identity of a contract "
                "approved for the attachment "
                f"(`uqs job attachment contract list --attachment {attach}`)"
            )
        )
        return
    try:
        att = config.load(attach)
        out = backfill_remote.receive(
            _paths(), attach, contract, port=port, ipc=service.ipc_for(att), dry_run=dry_run
        )
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        emit(out)
    elif dry_run:
        console.print(
            f"would receive {out['dataset']} (contract {out['contract']}) for attachment "
            f"{out['attachment']} on port {out['port']} into {out['hdb_dir']}: dates to "
            f"{out['last_writable_date']}, until {out['stops_at']}",
            markup=False,
        )
        console.print(dry_run_note())
    else:
        console.print(
            f"receiving {out['dataset']} (contract {out['contract']}) for attachment "
            f"{out['attachment']} on port {out['port']} "
            f"(pid {out['pid']}) into {out['hdb_dir']}: dates to {out['last_writable_date']}, "
            f"until {out['stops_at']}; log {out['log']}\n  follow it with "
            f"`uqs logs --attachment {out['attachment']} --worker {out['dataset']}.receiver -f`",
            markup=False,
        )
    raise typer.Exit(code=0)


alias_command(
    job_app,
    "receive",
    "uqs job receiver start",
    receive,
    cls=renamed(ATTACHMENT, ChangesState),
)
