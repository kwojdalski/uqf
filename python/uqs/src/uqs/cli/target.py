"""`uqs target ...`: the servers `uqs --target NAME` drives (#956).

The same entries `uqs deploy push --target` reads, in deploy_targets.toml
(deploy/targets.py): a server is declared once, then pushed to and driven
from that one entry. `add` appends a [targets.NAME] table rather than
rewriting the file, so the operator's comments and other entries are never
touched; `remove` deletes that one table's lines.

`uqs --target NAME <command>` itself is handled by the entry point before
any command parses (cli/entry.py), because what follows it is the SERVER's
command line, for the server's uqs to parse.
"""

from __future__ import annotations

import json
import re
from typing import Annotated

import typer
from rich.table import Table

from uqs.cli.shared import _die, app, console
from uqs.deploy import control, targets
from uqs.deploy.config import DeployError
from uqs.paths import UqsError, repo_root

target_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="The servers `uqs --target NAME <command>` runs commands on: add, list, check.",
)
app.add_typer(target_app, name="target")

_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_ACCOUNT = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
_LABEL = re.compile(r"([A-Za-z0-9_.-]+)=(\S*)")


def _declared() -> dict[str, dict]:
    path = targets.declaration_path(repo_root())
    if not path.is_file():
        return {}
    try:
        return targets.read(repo_root())
    except DeployError as exc:
        _die(UqsError(str(exc)))
        return {}


def _toml(value: object) -> str:
    """A TOML value for what `add` writes: strings, ints and string tables.
    JSON's string escapes are TOML's basic-string escapes."""
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in value.items()) + " }"
    return json.dumps(value)


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
    Processes, ports and configuration are read from the server when used."""
    labels: dict[str, str] = {}
    for pair in label or []:
        m = _LABEL.fullmatch(pair)
        if m is None:
            _die(UqsError(f"--label {pair!r}: give key=value"))
            return
        labels[m.group(1)] = m.group(2)
    if not _NAME.fullmatch(name):
        _die(UqsError(f"target name {name!r}: lower-case letters, digits, - and _"))
    if remote_user is not None and not _ACCOUNT.fullmatch(remote_user):
        _die(UqsError(f"--remote-user {remote_user!r} is not an account name"))
    if name in _declared():
        _die(UqsError(f"target {name} is already declared - `uqs target remove {name}` first"))
    entry = {"host": ssh, "dest": dest, "remote_user": remote_user, "labels": labels or None}
    lines = [f"[targets.{name}]"] + [f"{k} = {_toml(v)}" for k, v in entry.items() if v]
    path = targets.declaration_path(repo_root())
    before = path.read_text() if path.is_file() else ""
    sep = "\n" if before.endswith("\n") else "\n\n" if before else ""
    if before.endswith("\n\n"):
        sep = ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(before + sep + "\n".join(lines) + "\n")
    try:
        control.remote_for(name, repo_root())
    except UqsError as exc:
        path.write_text(before)
        _die(exc)
    console.print(f"[green]✓[/] target {name} added to {path}")
    console.print(f"  try: uqs --target {name} summary")


@target_app.command("list")
def list_targets() -> None:
    """Every declared target: where it is, as whom, and its labels."""
    declared = _declared()
    if not declared:
        console.print(
            f"no targets in {targets.declaration_path(repo_root())} - add one with `uqs target add`"
        )
        return
    table = Table("target", "ssh", "dest", "as", "labels")
    for name, t in sorted(declared.items()):
        labels = t.get("labels") or {}
        table.add_row(
            name,
            str(t.get("host", "")),
            str(t.get("dest", "")),
            str(t.get("remote_user") or ""),
            ", ".join(f"{k}={v}" for k, v in labels.items()),
        )
    console.print(table)


@target_app.command("check")
def check(name: Annotated[str, typer.Argument(help="The target")]) -> None:
    """Reach the target and name the release it runs. Says which part failed:
    ssh, or no release under its dest."""
    try:
        r = control.remote_for(name, repo_root())
        release = control.current_release(r)
    except UqsError as exc:
        _die(exc)
        return
    console.print(f"[green]✓[/] {name} ({r.describe()}) runs release {release}")


@target_app.command("remove")
def remove(name: Annotated[str, typer.Argument(help="The target")]) -> None:
    """Delete one target's [targets.NAME] table; every other line stays."""
    if name not in _declared():
        _die(UqsError(f"no target {name!r} declared"))
    path = targets.declaration_path(repo_root())
    lines = path.read_text().splitlines(keepends=True)
    header = re.compile(rf"^\s*\[targets\.{re.escape(name)}\]\s*(#.*)?$")
    start = next((i for i, ln in enumerate(lines) if header.match(ln)), None)
    if start is None:
        _die(UqsError(f"target {name} is not a [targets.{name}] table in {path} - edit it by hand"))
        return
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines)
    )
    path.write_text("".join(lines[:start] + lines[end:]))
    console.print(f"[green]✓[/] target {name} removed from {path}")


def forward(name: str, yes: bool, args: list[str], *, interactive: bool) -> int:
    """`uqs --target NAME <args>`: run args on the target's release, after
    asking - or `--yes` - when they may change the server."""
    words = control.command_words(args)
    if words[:1] in (("target",),):
        raise UqsError("`uqs target ...` manages this machine's targets - run it without --target")
    r = control.remote_for(name, repo_root())
    shown = " ".join(["uqs", *args])
    if control.changes_the_server(args) and not yes:
        if not interactive:
            raise UqsError(
                f"`{shown}` may change {name} ({r.describe()}) - pass --yes "
                f"(`uqs --target {name} --yes ...`) to run it without a terminal to confirm on"
            )
        if not typer.confirm(f"Run `{shown}` on {name} ({r.describe()})?", default=False):
            raise UqsError("not run")
    return control.run(r, args, tty=interactive)
