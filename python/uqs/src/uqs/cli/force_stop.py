"""`uqs stop --force`'s option and report; the killing is uqs.stack.force_stop."""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.cli.shared import _die, _paths, console
from uqs.paths import UqsError
from uqs.stack import force_stop

ForceOpt = Annotated[
    bool,
    typer.Option(
        "--force",
        help="SIGKILL every instance, found by command line rather than by its port - "
        "for a process stuck before it opened its port, a duplicate, or one that ignores "
        "a normal stop. The last resort: no clean shutdown, and a tickerplant may leave "
        "its log's last message torn",
    ),
]


def _force_stop(names: str, port: int | None) -> None:
    """Kill `names` outright, print what happened to each pid, and exit:
    0 when every one is gone, 1 when any survives."""
    try:
        results = force_stop.kill(_paths(), names, base_port=port)
    except UqsError as exc:
        _die(exc)
        return
    if not results:
        console.print("nothing to kill: none of them is running")
    for r in results:
        verdict = "[green]killed[/]" if r.gone else "[bold red]still running[/]"
        console.print(f"{r.name}: {verdict} pid {r.pid}")
    raise typer.Exit(code=0 if all(r.gone for r in results) else 1)
