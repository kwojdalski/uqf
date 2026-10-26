"""The two halves of a backfill whose HDB is on another machine (#1650).

`uqs job receive WORKER --attach NAME --port P`, on the stack's machine, starts
the receiver; `uqs job backfill WORKER --attach NAME --writer HOST:P ...`, on the
source's machine, extracts and sends to it. What each decides is in
uqs.attach.backfill_remote.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated

import typer

from uqs.attach import backfill_remote, backfill_run, config, service
from uqs.cli import completion
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
) -> None:
    """`uqs job backfill --attach NAME --writer HOST:PORT`: plan or start the extractor."""
    from uqs.cli.backfill_attached import _outcome_text, _report

    if fixture:
        _die(UqsError("--fixture is refused with --attach: a fixture in a native HDB is data"))
        return
    if port is not None:
        _die(UqsError("--port is not taken with --writer: the receiver's address is the stack's"))
        return
    try:
        paths = _paths()
        if mode in ("validate", "plan"):
            _, plan = backfill_remote.remote_plan(
                paths, attachment, worker, version, bound_from, bound_to,
                writer=writer, on_conflict=on_conflict,
            )  # fmt: skip
            text = (
                f"{worker} -> attachment {attachment} through the receiver at {writer}: "
                f"{len(plan['windows'])} window(s) (nothing was written)"
            )
            _report(plan, as_json, text)
            raise typer.Exit(code=0)
        run_, plan = backfill_remote.launch_remote(
            paths, attachment, worker, version, bound_from, bound_to,
            writer=writer, mode=mode, on_conflict=on_conflict, verbose=verbose, trace=trace,
        )  # fmt: skip
    except UqsError as exc:
        _die(exc)
        return
    info = {"mode": mode, "worker": worker, "writer": writer, "plan": plan, **run_.as_dict()}
    if not wait:
        _report(info, as_json, f"started {worker} (pid {run_.pid}) -> {writer}; log {run_.log}")
        raise typer.Exit(code=0)
    console.print(f"[dim]following {worker} (pid {run_.pid}); Ctrl-C stops waiting, not the run[/]")
    code, result = backfill_run.follow(run_)
    _report(
        {**info, "result": result, "exit_code": code},
        as_json,
        _outcome_text(worker, run_, code, result),
    )
    raise typer.Exit(code=code)


@job_app.command("receive", rich_help_panel=JOB_OPERATIONS_PANEL)
def receive(
    worker: Annotated[
        str,
        typer.Argument(
            help="The bounded worker whose dataset the receiver writes",
            autocompletion=completion.backfill_workers,
        ),
    ],
    attach: Annotated[
        str, typer.Option("--attach", help="The saved, verified attachment whose HDB it writes")
    ],
    port: Annotated[int, typer.Option("--port", help="The port the extractor connects to")],
    max_rows: Annotated[
        int, typer.Option("--max-rows", help="The most rows one batch may carry")
    ] = 100_000,
    as_json: Annotated[bool, typer.Option("--json", help="Print what started as JSON")] = False,
) -> None:
    """Start the receiver for a backfill extracted on another machine (#1650).

    Run on the STACK's machine. It admits the attachment's destination against the
    live stack as `uqs job backfill --attach` does - the pinned HDB, its writers and
    readers, the end-of-day fence - then listens on --port for the extractor
    (`uqs job backfill WORKER --attach NAME --writer THIS-HOST:PORT ...` on the
    source's machine). Every batch is admitted and committed through the fenced
    HDB writer; the readers are asked to reload as rows finish. It authenticates the
    attachment's stack user, and stops itself where the end-of-day fence begins.
    """
    try:
        att = config.load(attach)
        out = backfill_remote.receive(
            _paths(), attach, worker, port=port, ipc=service.ipc_for(att), max_rows=max_rows
        )
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        typer.echo(json.dumps(out, indent=2, sort_keys=True))
    else:
        console.print(
            f"receiving {out['dataset']} for attachment {out['attachment']} on port {out['port']} "
            f"(pid {out['pid']}) into {out['hdb_dir']}: dates to {out['last_writable_date']}, "
            f"until {out['stops_at']}; log {out['log']}",
            markup=False,
        )
    raise typer.Exit(code=0)
