"""`uqs job attach apply|verify|start`: the commands that change the stack, or
let the publisher near it. plan, validate and status are in attach.py."""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.table import Table

from uqs.attach import apply as applying
from uqs.attach import publisher as publishing
from uqs.attach import service
from uqs.attach import verify as verifying
from uqs.cli.attach import JsonOpt, NameArg, _load, _render, attach_app
from uqs.cli.shared import _die, _paths, console
from uqs.paths import UqsError

_OUTCOME_STYLE = {"created": "green", "adopted": "cyan", "shared": "cyan", "already": "dim"}


def _steps(steps) -> None:
    table = Table(title="Steps")
    for column in ("step", "process", "table", "outcome", "detail"):
        table.add_column(column, overflow="fold")
    for s in steps:
        style = _OUTCOME_STYLE.get(s.outcome, "red")
        table.add_row(s.op, s.process, s.table, f"[{style}]{s.outcome}[/]", s.detail)
    console.print(table)


@attach_app.command("apply")
def apply(
    name: NameArg,
    yes: Annotated[
        bool, typer.Option("--yes", help="Do not ask before changing the stack")
    ] = False,
    as_json: JsonOpt = False,
) -> None:
    """Register the tables and subscribe the consumers; the publisher stays stopped.

    Changes only what the plan showed, with no restart of any process, and
    journals each step in the manifest. Re-running it is safe: a stack that
    already matches is left alone. A failure part way is reported exactly, not
    rolled back behind your back - run apply again, or detach.
    """
    try:
        att = _load(name)
        planned = service.make_plan(att, service.ipc_for(att), _paths().repo_root)
        if not as_json:
            _render(planned)
        if not planned.ok:
            raise applying.ApplyRefused([f for f in planned.findings if f.level == "blocker"])
        if not yes and not as_json:
            typer.confirm(
                f"Change stack {att.stack.id} as above? (no process is restarted)", abort=True
            )
        result = applying.apply(att, service.ipc_for(att), _paths().repo_root)
    except applying.ApplyRefused as exc:
        _die(exc)
        return
    except applying.ApplyFailed as exc:
        _steps(exc.result.steps)
        _die(exc)
        return
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        console.print_json(
            json.dumps({"state": result.state, "steps": [s.__dict__ for s in result.steps]})
        )
        return
    _steps(result.steps)
    console.print(
        f"[green]applied[/]: {name} is {result.state}. The publisher "
        f"({att.publisher.procname}) is still stopped; `uqs job attach verify {name}` next."
    )


@attach_app.command("verify")
def verify(name: NameArg, as_json: JsonOpt = False) -> None:
    """Check, without publishing a row, that the consumers can receive and persist
    the table. Passing permits the publisher to start."""
    try:
        att = _load(name)
        result = verifying.verify(att, service.ipc_for(att))
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        console.print_json(
            json.dumps(
                {
                    "ok": result.ok,
                    "state": result.state,
                    "checks": [c.__dict__ for c in result.checks],
                }
            )
        )
    else:
        table = Table(title=f"Verify {name}")
        for column in ("check", "ok", "detail"):
            table.add_column(column, overflow="fold")
        for c in result.checks:
            table.add_row(c.name, "[green]yes[/]" if c.ok else "[red]NO[/]", c.detail)
        console.print(table)
        console.print(
            f"[green]verified[/]: {att.publisher.procname} may start "
            f"(`uqs job attach start {name}`)"
            if result.ok
            else f"[red]not verified[/]: {name} is {result.state}; the publisher stays refused"
        )
    raise typer.Exit(code=0 if result.ok else 1)


@attach_app.command("start")
def start(name: NameArg) -> None:
    """Start the attached job's publisher under the attachment's stack id.

    Verifies again first, so a stack that changed since the last verify does not
    get a publisher. `uqs start <procname> --port <stack id>` is the same start
    and is gated the same way; this one cannot be run with the wrong port.
    """
    try:
        att = _load(name)
        result = verifying.verify(att, service.ipc_for(att))
        if not result.ok:
            raise UqsError(
                f"{name} no longer verifies ({result.state}); run `uqs job attach verify {name}`"
            )
        publishing.UqsPublisher(_paths()).start(att)
    except UqsError as exc:
        _die(exc)
        return
    console.print(f"[green]started[/] {att.publisher.procname} on stack {att.stack.id}")
