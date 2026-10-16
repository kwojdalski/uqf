"""`uqs job backfill --attach NAME`: the attached-destination branch (#1454).

Apart from cli/backfill.py, which would otherwise carry two commands: the
fleet-path backfill starts a process and returns; this resolves a saved
attachment, asks its stack read-only questions and prints what a run would do.
Everything it decides is in uqs.attach.backfill.
"""

from __future__ import annotations

import json
from datetime import datetime

import typer

from uqs.attach import backfill as attached
from uqs.attach import backfill_plan as planning
from uqs.attach import config, service
from uqs.cli.shared import _die, _paths, console
from uqs.paths import UqsError
from uqs.stack import backfill as stack_backfill


def run(
    attachment: str,
    worker: str,
    version: str,
    bound_from: datetime,
    bound_to: datetime,
    *,
    mode: str | None,
    port: int | None,
    fixture: bool,
    on_conflict: str | None,
    as_json: bool,
) -> None:
    """Plan (or validate) a backfill into `attachment`'s native HDB; exit non-zero on a refusal."""
    try:
        attached.refuse_execution(mode)
        paths = _paths()
        if mode == "validate":
            dest = attached.resolve(paths, attachment, worker, port=port, fixture=fixture)
            stack_backfill.backfill_flags(
                worker, version, bound_from, bound_to, on_conflict=on_conflict, mode=mode
            )
            out = {
                "mode": "validate",
                "worker": worker,
                "destination": dest.as_dict(),
                "mutates": False,
            }
            text = (
                f"{worker} -> attachment {attachment}: destination {dest.hdb_dir} "
                "resolves (nothing opened)"
            )
        else:
            att = config.load(attachment)
            out = planning.plan_attached(
                paths,
                attachment,
                worker,
                version,
                bound_from,
                bound_to,
                ipc=service.ipc_for(att),
                port=port,
                fixture=fixture,
                on_conflict=on_conflict,
            )
            text = planning.render(out)
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        typer.echo(json.dumps(out, indent=2, sort_keys=True))
    else:
        console.print(text, markup=False)
    raise typer.Exit(code=0)
