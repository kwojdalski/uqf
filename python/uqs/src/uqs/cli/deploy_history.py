"""`uqs deploy history`: the deployments pushed from this machine (#1756),
with every listing's flags (#1858) - moved out of cli/target.py beside them.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from uqs.cli import completion
from uqs.cli.deploy import deploy_app
from uqs.cli.json_out import emit, json_opt
from uqs.cli.safety import ReadOnly
from uqs.cli.shared import ExportOpt, InteractiveOpt, _die, _export, _show, console
from uqs.cli.sorting import ReverseOpt, SortOpt, _sorted_items
from uqs.deploy import history
from uqs.deploy.config import DeployError
from uqs.paths import UqsError, repo_root
from uqs.timefmt import timestamp

HistoryJson = json_opt(
    '{"deployments": [{default: bool, at, target, ssh, dest, release, profile, status, '
    "stage}]}, newest first"
)
_KEYS = ("at", "target", "ssh", "dest", "release", "profile", "status", "stage")


@deploy_app.command("history", cls=ReadOnly)
def deploy_history(
    target: Annotated[
        str | None,
        typer.Argument(
            metavar="TARGET",
            help="Only this target, or server (host, with or without user@)",
            show_default=False,
            autocompletion=completion.targets,
        ),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, help="Most recent N")] = 20,
    export: ExportOpt = None,
    sort: SortOpt = None,
    reverse: ReverseOpt = False,
    interactive: InteractiveOpt = False,
    as_json: HistoryJson = False,
) -> None:
    """Show deployment history recorded on this machine, newest first.

    Every `uqs deploy push` made from THIS machine - not what a server has
    installed (`uqs deploy status`) nor the declared servers (`uqs target
    list`). `*` marks each server's default: its latest successful
    deployment, which `uqs --target <server>` and `uqs target shell` use."""
    root = repo_root()
    try:
        entries = history.read(root)
    except DeployError as exc:
        _die(UqsError(str(exc)))
    defaults = history.defaults(root)
    found = [
        (i, e)
        for i, e in enumerate(entries)
        if target is None
        or target in (e.get("target"), e.get("ssh"), history.server_of(str(e.get("ssh"))))
    ]
    rows = [
        {"default": i in defaults, **{k: e.get(k) for k in _KEYS}}
        for i, e in reversed(found[len(found) - limit :])
    ]
    try:
        rows = _sorted_items(rows, sort, reverse)
    except UqsError as exc:
        _die(exc)
    _export(rows, export)
    if as_json:
        # the time ISO-8601 with its offset, as every document's (#1882)
        emit({"deployments": [r | {"at": timestamp(r["at"], naive="utc")} for r in rows]})
        return
    if not rows:
        console.print(f"no deployments recorded in {history.history_path(root)}")
        return
    table = Table("", "when", "target", "server", "dest", "release", "profile", "status")
    for r in rows:
        status = str(r.get("status"))
        if status == "failed":
            status = f"[red]failed[/] at {r.get('stage')}"
        table.add_row(
            "*" if r["default"] else "",
            str(r.get("at") or ""),
            str(r.get("target") or ""),
            str(r.get("ssh") or ""),
            str(r.get("dest") or ""),
            str(r.get("release") or ""),
            str(r.get("profile") or ""),
            status,
        )
    _show(table, interactive)
