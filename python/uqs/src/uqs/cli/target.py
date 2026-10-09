"""`uqs target ...` and `uqs deploy list`: the servers `uqs --target NAME`
drives, and what was deployed where (#956).

Targets are the entries `uqs deploy push --target` reads, in
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
from uqs.cli.shared import _die, app, console
from uqs.deploy import control, history, targets
from uqs.deploy.config import DeployError
from uqs.paths import UqsError, repo_root

target_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="The servers `uqs --target NAME <command>` runs on: add, list, check, shell.",
)
app.add_typer(target_app, name="target")

_ACCOUNT = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
_LABEL = re.compile(r"([A-Za-z0-9_.-]+)=(\S*)")
Name = Annotated[str, typer.Argument(help="A target, or a server: its latest deployment")]


def _declared() -> dict[str, dict]:
    if not targets.declaration_path(repo_root()).is_file():
        return {}
    try:
        return targets.read(repo_root())
    except DeployError as exc:
        _die(UqsError(str(exc)))
        return {}


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
        list[str] | None, typer.Option("--label", help="key=value, shown by `list`; repeatable")
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


@target_app.command("list")
def list_targets() -> None:
    """Every declared target: where it is, as whom, its labels, and the
    release last deployed there from this machine."""
    declared = _declared()
    if not declared:
        console.print(
            f"no targets in {targets.declaration_path(repo_root())} - add one with "
            "`uqs target add`, or `uqs deploy push` to a server"
        )
        return
    deployed = {
        (e.get("host"), e.get("dest")): e
        for e in history.read(repo_root())
        if e.get("status") == "deployed"
    }
    table = Table("target", "ssh", "dest", "as", "labels", "last deployed")
    for name, t in sorted(declared.items()):
        last = deployed.get((t.get("host"), t.get("dest")))
        table.add_row(
            name,
            str(t.get("host", "")),
            str(t.get("dest", "")),
            str(t.get("remote_user") or ""),
            ", ".join(f"{k}={v}" for k, v in (t.get("labels") or {}).items()),
            f"{last['release']} ({last['at']})" if last else "",
        )
    console.print(table)


@target_app.command("check")
def check(name: Name) -> None:
    """Reach the target and name the release it runs. Says which part failed:
    ssh, or no release under its dest."""
    try:
        r = control.remote_for(name, repo_root())
        release = control.current_release(r)
    except UqsError as exc:
        _die(exc)
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


@deploy_app.command("list")
def deploy_list(
    server: Annotated[
        str | None, typer.Argument(help="Only this target or server (host, with or without user@)")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Most recent N")] = 20,
) -> None:
    """Every `uqs deploy push` made from this machine, newest first. `*` marks
    each server's default: its latest successful deployment, which
    `uqs --target <server>` and `uqs target shell` use."""
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
        if server is None
        or server in (e.get("target"), e.get("host"), history.server_of(str(e.get("host"))))
    ]
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
    if control.command_words(args)[:1] == ("target",):
        raise UqsError("`uqs target ...` manages this machine's targets - run it without --target")
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
