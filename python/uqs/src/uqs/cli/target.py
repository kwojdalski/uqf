"""`uqs target ...` and `uqs deploy history`: the servers `uqs --target NAME`
drives, and what was deployed where (#956).

Targets are the entries `uqs deploy push ARTIFACT TARGET...` reads, in
deploy_targets.toml (deploy/targets.py): a server is declared once, then
pushed to and driven from that entry - and a push to a server no target
names registers one (deploy/history.py). `add` appends a [targets.NAME]
table rather than rewriting the file, so comments and other entries are never
touched; `remove` deletes that one table.

`uqs --target NAME <command>` itself is handled by the entry point before any
command parses (cli/entry.py), because what follows it is the SERVER's
command line, for the server's uqs to parse.
"""

from __future__ import annotations

import re
import sys
from typing import Annotated

import typer
from rich.table import Table

from uqs.cli.deploy import deploy_app
from uqs.cli.json_out import emit, json_opt
from uqs.cli.shared import _die, app, console
from uqs.deploy import control, history, targets
from uqs.deploy.config import DeployError
from uqs.paths import UqsError, repo_root

target_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="The servers `uqs --target NAME <command>` runs on: add, remove, check, shell. "
    "`uqs list targets` lists them.",
)
app.add_typer(target_app, name="target")

_ACCOUNT = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
_LABEL = re.compile(r"([A-Za-z0-9_.-]+)=(\S*)")
Name = Annotated[str, typer.Argument(help="A target, or a server: its latest deployment")]


@target_app.command("add")
def add(
    name: Annotated[str, typer.Argument(help="What to call it: `uqs --target NAME ...`")],
    ssh: Annotated[str, typer.Option("--ssh", help="ssh destination: user@host, or a Host alias")],
    dest: Annotated[str, typer.Option("--dest", help="Where `uqs deploy push` installs, absolute")],
    remote_user: Annotated[
        str | None,
        typer.Option("--remote-user", help="Run commands as this account (`sudo -n -iu`)"),
    ] = None,
    label: Annotated[
        list[str] | None,
        typer.Option("--label", help="key=value, shown by `uqs list targets`; repeatable"),
    ] = None,
) -> None:
    """Declare a server: its ssh destination and deployment root, nothing else.
    Processes, ports and configuration are read from the server when used. A
    push registers its server itself; this is for one deployed some other way."""
    labels: dict[str, str] = {}
    for pair in label or []:
        m = _LABEL.fullmatch(pair)
        if m is None:
            _die(UqsError(f"--label {pair!r}: give key=value"))
            return
        labels[m.group(1)] = m.group(2)
    if remote_user is not None and not _ACCOUNT.fullmatch(remote_user):
        _die(UqsError(f"--remote-user {remote_user!r} is not an account name"))
    root = repo_root()
    path = targets.declaration_path(root)
    before = path.read_text() if path.is_file() else None
    entry = {"host": ssh, "dest": dest, "remote_user": remote_user, "labels": labels or None}
    try:
        targets.append(root, name, entry)
        control.remote_for(name, root)
    except (DeployError, UqsError) as exc:
        if before is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(before)
        _die(UqsError(str(exc)))
        return
    console.print(f"[green]✓[/] target {name} added to {path}")
    console.print(f"  try: uqs --target {name} summary")


CheckJson = json_opt('{"ok": true, "target", "host", "dest", "remote_user", "release"}')


@target_app.command("check")
def check(name: Name, as_json: CheckJson = False) -> None:
    """Reach the target and name the release it runs. Says which part failed:
    ssh, or no release under its dest."""
    try:
        r = control.remote_for(name, repo_root())
        release = control.current_release(r)
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        emit({
            "ok": True, "target": r.name, "host": r.host, "dest": r.dest,
            "remote_user": r.remote_user, "release": release,
        })  # fmt: skip
        return
    console.print(f"[green]✓[/] {r.name} ({r.describe()}) runs release {release}")


@target_app.command("shell")
def shell(
    name: Annotated[
        str | None,
        typer.Argument(help="A target or a server; default: the most recent deployment"),
    ] = None,
) -> None:
    """A shell on the server, in the deployed release - like `poetry shell`:
    its deploy.env loaded, its uqs first on PATH, as the target's account.
    `exit` comes back."""
    try:
        r = control.remote_for(name, repo_root())
        console.print(f"connecting to {r.name} ({r.describe()})")
        code = control.run(r, [], tty=True, command=control.shell_command(r))
    except UqsError as exc:
        _die(exc)
        return
    raise typer.Exit(code=code)


@target_app.command("remove")
def remove(name: Annotated[str, typer.Argument(help="The target")]) -> None:
    """Delete one target's [targets.NAME] table; every other line stays."""
    try:
        path = targets.remove(repo_root(), name)
    except DeployError as exc:
        _die(UqsError(str(exc)))
        return
    console.print(f"[green]✓[/] target {name} removed from {path}")


HistoryJson = json_opt(
    '{"deployments": [{default: bool, at, target, host, dest, release, profile, status, '
    "stage}]}, newest first"
)


@deploy_app.command("history")
def deploy_history(
    target: Annotated[
        str | None,
        typer.Argument(
            metavar="TARGET",
            help="Only this target, or server (host, with or without user@)",
            show_default=False,
        ),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Most recent N")] = 20,
    as_json: HistoryJson = False,
) -> None:
    """Show deployment history recorded on this machine, newest first.

    Every `uqs deploy push` made from THIS machine - not what a server has
    installed (`uqs deploy status`) nor the declared servers (`uqs list
    targets`). `*` marks each server's default: its latest successful
    deployment, which `uqs --target <server>` and `uqs target shell` use."""
    root = repo_root()
    try:
        entries = history.read(root)
    except DeployError as exc:
        _die(UqsError(str(exc)))
        return
    defaults = history.defaults(root)
    rows = [
        (i, e)
        for i, e in enumerate(entries)
        if target is None
        or target in (e.get("target"), e.get("host"), history.server_of(str(e.get("host"))))
    ]
    if as_json:
        keys = ("at", "target", "host", "dest", "release", "profile", "status", "stage")
        emit({
            "deployments": [
                {"default": i in defaults, **{k: e.get(k) for k in keys}}
                for i, e in reversed(rows[-limit:])
            ]
        })  # fmt: skip
        return
    if not rows:
        console.print(f"no deployments recorded in {history.history_path(root)}")
        return
    table = Table("", "when", "target", "server", "dest", "release", "profile", "status")
    for i, e in reversed(rows[-limit:]):
        status = str(e.get("status"))
        if status == "failed":
            status = f"[red]failed[/] at {e.get('stage')}"
        table.add_row(
            "*" if i in defaults else "",
            str(e.get("at", "")),
            str(e.get("target") or ""),
            str(e.get("host", "")),
            str(e.get("dest", "")),
            str(e.get("release", "")),
            str(e.get("profile") or ""),
            status,
        )
    console.print(table)


def forward(name: str, yes: bool, args: list[str], *, interactive: bool) -> int:
    """`uqs --target NAME <args>`: run args on the target's release, after
    asking - or `--yes` - when they may change the server."""
    words = control.command_words(args)
    if words[:1] == ("target",):
        raise UqsError("`uqs target ...` manages this machine's targets - run it without --target")
    if words[:2] == ("list", "targets"):
        raise UqsError("`uqs list targets` lists this machine's targets - run it without --target")
    tunnel = ("--tunnel", "--target")  # --target: query's old spelling (#1729)
    if words[:1] == ("query",) and any(a.partition("=")[0] in tunnel for a in args):
        # Each names a different machine to run the query on (#1561).
        raise UqsError(
            "give the target once: `uqs --target NAME query` runs the query on the server, "
            "`uqs query --tunnel NAME` runs it here through an ssh tunnel"
        )
    r = control.remote_for(name, repo_root())
    shown = " ".join(["uqs", *args])
    if control.changes_the_server(args) and not yes:
        if not interactive:
            raise UqsError(
                f"`{shown}` may change {r.name} ({r.describe()}) - pass --yes "
                f"(`uqs --target {name} --yes ...`) to run it without a terminal to confirm on"
            )
        if not typer.confirm(f"Run `{shown}` on {r.name} ({r.describe()})?", default=False):
            raise UqsError("not run")
    return control.run(r, args, tty=interactive and sys.stdout.isatty())
