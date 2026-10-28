"""The two halves of a backfill whose HDB is on another machine (#1650).

`uqs job receive --contract ID --attach NAME --listen-port P`, on the stack's machine,
starts the receiver for a receiver contract approved there (`uqs job attachment contract`);
`uqs job backfill WORKER --attach NAME --writer HOST:P ...`, on the source's
machine, extracts and sends to it. What each decides is in
uqs.attach.backfill_remote.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated

import typer

from uqs.attach import backfill_remote, backfill_run, config, service
from uqs.cli.renames import renamed
from uqs.cli.shared import JOB_OPERATIONS_PANEL, _die, _paths, console, job_app
from uqs.paths import UqsError


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
    """`uqs job backfill --attach NAME --writer HOST:PORT`: plan or start the extractor.
    With `force`, HOST:PORT is any q process (a wdb), written to with nothing checked."""
    from uqs.cli.backfill_attached import _outcome_text, _report, logs_command

    if fixture:
        _die(UqsError("--fixture is refused with --attach: a fixture in a native HDB is data"))
        return
    if port is not None:
        _die(
            UqsError(
                "--base-port is not taken with --writer: the receiver's address is the stack's"
            )
        )
        return
    if force:
        if on_conflict not in (None, "append"):
            _die(
                UqsError(
                    f"--any-writer only appends: --on-conflict {on_conflict} would need to "
                    "read what the writer holds, and nothing is read there"
                )
            )
            return
        on_conflict = "append"
    try:
        paths = _paths()
        if mode in ("validate", "plan"):
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
    info = {"mode": mode, "worker": worker, "writer": writer, "plan": plan, **run_.as_dict()}
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
            f"[dim]following {worker} (pid {run_.pid}); Ctrl-C stops waiting, not the run[/]"
        )
    code, result = backfill_run.follow(run_)
    _report(
        {**info, "result": result, "exit_code": code},
        as_json,
        _outcome_text(worker, run_, code, result),
    )
    raise typer.Exit(code=code)


@job_app.command(
    "receive", rich_help_panel=JOB_OPERATIONS_PANEL, cls=renamed({"--port": "--listen-port"})
)
def receive(
    attach: Annotated[
        str, typer.Option("--attach", help="The saved, verified attachment whose HDB it writes")
    ],
    contract: Annotated[
        str | None,
        typer.Option(
            "--contract",
            help="The identity of the receiver contract approved for the attachment "
            "(`uqs job attachment contract approve`); a unique prefix will do",
        ),
    ] = None,
    port: Annotated[
        int | None, typer.Option("--listen-port", help="The port the extractor connects to")
    ] = None,
    stop: Annotated[
        bool,
        typer.Option(
            "--stop",
            help="Stop this machine's receiver for the attachment instead, so a detach can go "
            "ahead; an extractor then fails its next window without covering it",
        ),
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print what started as JSON")] = False,
) -> None:
    """Start the receiver for a backfill extracted on another machine (#1650).

    Run on the STACK's machine, which needs no code of the job: it serves the
    receiver contract --contract that `uqs job attachment contract approve` bound to the
    attachment here (#1717), and takes the row limit and the permitted conflict
    policies from that approval. It admits the attachment's destination against the
    live stack as `uqs job backfill --attach` does - the pinned HDB, its writers and
    readers, the end-of-day fence - then listens on --listen-port for the extractor
    (`uqs job backfill WORKER --attach NAME --writer THIS-HOST:PORT ...` on the
    source's machine). Every batch is checked against the contract - dataset,
    columns and types, window column, row key, partition, conflict policy - and
    committed through the fenced HDB writer; the readers are asked to reload as rows
    finish. It authenticates the attachment's stack user, and stops itself where the
    end-of-day fence begins.
    """
    if stop:
        stopped = backfill_remote.stop_receivers(attach)
        names = ", ".join(f"{r['worker']} (pid {r['pid']})" for r in stopped) or "none was running"
        if as_json:
            typer.echo(json.dumps({"stopped": stopped}, indent=2, sort_keys=True))
        else:
            console.print(f"stopped: {names}", markup=False)
        raise typer.Exit(code=0)
    if port is None:
        _die(
            UqsError(
                "--listen-port is required to start a receiver: the port the extractor connects to"
            )
        )
        return
    if contract is None:
        _die(
            UqsError(
                "--contract is required to start a receiver: the identity of a contract "
                "approved for the attachment "
                f"(`uqs job attachment contract list --attach {attach}`)"
            )
        )
        return
    try:
        att = config.load(attach)
        out = backfill_remote.receive(
            _paths(), attach, contract, port=port, ipc=service.ipc_for(att)
        )
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        typer.echo(json.dumps(out, indent=2, sort_keys=True))
    else:
        console.print(
            f"receiving {out['dataset']} (contract {out['contract']}) for attachment "
            f"{out['attachment']} on port {out['port']} "
            f"(pid {out['pid']}) into {out['hdb_dir']}: dates to {out['last_writable_date']}, "
            f"until {out['stops_at']}; log {out['log']}\n  follow it with "
            f"`uqs logs --attach {out['attachment']} --worker {out['dataset']}.receiver -f`",
            markup=False,
        )
    raise typer.Exit(code=0)
