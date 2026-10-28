"""`uqs target ...` and `uqs deploy history`: the servers `uqs --target NAME`
drives, and what was deployed where (#956).

Targets are the entries `uqs deploy push ARTIFACT TARGET...` reads, in
deploy_targets.toml (deploy/targets.py): a server is declared once, then
pushed to and driven from that entry - and a push to a server no target
names registers one (deploy/history.py).

Every change - add, set, unset, rename, remove, edit, revert, adopt, and a
push's registration - is one transaction (deploy/target_changes.py): locked,
made to the file's text so comments and order survive, validated whole,
written atomically and recorded in the change log `uqs target log` reads.
The file may still be edited by hand; the next change records that edit.

`uqs --target NAME <command>` itself is handled by the entry point before any
command parses (cli/entry.py), because what follows it is the SERVER's
command line, for the server's uqs to parse.
"""

from __future__ import annotations

import re
import shlex
import sys
from collections.abc import Callable
from typing import Annotated

import typer
from rich.table import Table

from uqs.cli.deploy import deploy_app
from uqs.cli.json_out import emit, json_opt
from uqs.cli.shared import _die, app, console
from uqs.deploy import control, history, target_changelog, target_changes, target_file
from uqs.deploy.config import DeployError
from uqs.paths import UqsError, repo_root

target_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="The servers `uqs --target NAME <command>` runs on: add, set, unset, rename, remove, "
    "edit, list, check, log, revert, adopt, shell.",
)
app.add_typer(target_app, name="target")

_ACCOUNT = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
_LABEL = re.compile(r"([A-Za-z0-9_.-]+)=(\S*)")
Name = Annotated[str, typer.Argument(help="A target, or a server: its latest deployment")]


Reach = Annotated[
    bool,
    typer.Option(
        "--reach",
        help="Reach the server first, as `uqs target check` does, and save nothing it cannot reach",
    ),
]
DryRun = Annotated[
    bool,
    typer.Option("--dry-run", "-n", help="Show the change and check it; write nothing"),
]


def _command(*words: str) -> str:
    """The command as the change log records it."""
    return shlex.join(["uqs", "target", *words])


def _reach(merged: dict, name: str | None) -> None:
    """--reach: the changed target, as it would be saved, answers over ssh."""
    if name is None or name not in merged:
        return
    try:
        r = control.remote_from(name, merged[name])
        release = control.current_release(r)
    except UqsError as exc:
        raise DeployError("arguments", f"not saved: {exc}") from None
    console.print(f"[green]✓[/] reached {name} ({r.describe()}): release {release}")


def _done(result: target_changes.Result, said: str, dry_run: bool) -> None:
    """Report a change: what it did, each changed table before and after,
    and its number in the log."""
    if result.outside is not None:
        verb = "will record" if dry_run else "recorded"
        console.print(
            f"[yellow]![/] {result.path} was edited outside uqs since its last change - "
            f"{verb} that first (`uqs target log`)"
        )
    if result.entry is None:
        console.print(f"nothing to change: {said}")
        return
    console.print(result.entry["diff"].rstrip("\n"), markup=False, highlight=False)
    if dry_run:
        console.print(f"dry run: {said} - nothing written")
        return
    number = len(target_changelog.read_log(repo_root()))
    console.print(f"[green]✓[/] {said} in {result.path} (change {number}, `uqs target log`)")


def _change(run: Callable[[], target_changes.Result], said: str, dry_run: bool) -> None:
    try:
        result = run()
    except (DeployError, UqsError) as exc:
        _die(UqsError(str(exc)))
        return
    _done(result, said, dry_run)


def _labels(label: list[str] | None) -> dict[str, str]:
    labels: dict[str, str] = {}
    for pair in label or []:
        m = _LABEL.fullmatch(pair)
        if m is None:
            _die(UqsError(f"--label {pair!r}: give key=value"))
        else:
            labels[m.group(1)] = m.group(2)
    return labels


Ssh = Annotated[str, typer.Option("--ssh", help="The ssh destination: user@host, or a Host alias")]
Dest = Annotated[str, typer.Option("--dest", help="Where `uqs deploy push` installs, absolute")]
RemoteUser = Annotated[
    str | None,
    typer.Option("--remote-user", help="Run commands as this account (`sudo -n -iu`)"),
]


@target_app.command("add")
def add(
    name: Annotated[str, typer.Argument(help="What to call it: `uqs --target NAME ...`")],
    ssh: Ssh,
    dest: Dest,
    remote_user: RemoteUser = None,
    label: Annotated[
        list[str] | None,
        typer.Option("--label", help="key=value, shown by `uqs target list`; repeatable"),
    ] = None,
    reach: Reach = False,
    dry_run: DryRun = False,
) -> None:
    """Declare a server: its ssh destination and deployment root, nothing else.
    Processes, ports and configuration are read from the server when used. A
    push registers its server itself; this is for one deployed some other way.
    Other settings: `uqs target set`."""
    labels = _labels(label)
    if remote_user is not None and not _ACCOUNT.fullmatch(remote_user):
        _die(UqsError(f"--remote-user {remote_user!r} is not an account name"))
    entry = {"host": ssh, "dest": dest, "remote_user": remote_user, "labels": labels or None}
    words = [name, "--ssh", ssh, "--dest", dest]
    words += ["--remote-user", remote_user] if remote_user else []
    words += [w for pair in label or [] for w in ("--label", pair)]
    _change(
        lambda: target_changes.add(
            repo_root(), name, entry, _command("add", *words),
            check=_reach if reach else None, dry_run=dry_run,
        ),
        f"target {name} added",
        dry_run,
    )  # fmt: skip
    if not dry_run:
        console.print(f"  try: uqs --target {name} summary")


_PAIR = re.compile(r"([a-z_][a-z0-9_]*(?:\.[A-Za-z0-9_.-]+)?)=(.*)", re.S)
_KEY = re.compile(r"[a-z_][a-z0-9_]*(?:\.[A-Za-z0-9_.-]+)?")


@target_app.command("set")
def set_(
    name: Annotated[str, typer.Argument(help="The target")],
    pairs: Annotated[
        list[str],
        typer.Argument(
            metavar="KEY=VALUE...",
            help="Settings, spelt as in the file: host=deploy@prod connect_timeout=20 "
            'launch_mode=source labels.tier=uat live_check=\'["a", "b"]\'. A VALUE is '
            'read as TOML when it is one (20, true, [..], "quoted"), else as text',
            show_default=False,
        ),
    ],
    reach: Reach = False,
    dry_run: DryRun = False,
) -> None:
    """Change settings of one target in place - any key the file accepts,
    including launch_mode, launch_python, launch_env_file, connect_timeout
    and environment. The rest of the file, comments included, is untouched."""
    changes: dict[str, object] = {}
    for pair in pairs:
        m = _PAIR.fullmatch(pair)
        if m is None:
            _die(UqsError(f"{pair!r}: give KEY=VALUE, the key as the file spells it"))
            return
        changes[m.group(1)] = target_file.parse_value(m.group(2))
    _change(
        lambda: target_changes.set_keys(
            repo_root(), name, changes, _command("set", name, *pairs),
            check=_reach if reach else None, dry_run=dry_run,
        ),
        f"target {name} set {', '.join(changes)}",
        dry_run,
    )  # fmt: skip


@target_app.command("unset")
def unset(
    name: Annotated[str, typer.Argument(help="The target")],
    keys: Annotated[
        list[str],
        typer.Argument(metavar="KEY...", help="Settings to remove: launch_python labels.tier"),
    ],
    dry_run: DryRun = False,
) -> None:
    """Remove settings from one target, so it takes the layer beneath's
    value ([defaults], its environment) or push's default."""
    for key in keys:
        if not _KEY.fullmatch(key):
            _die(UqsError(f"{key!r} is not a key as the file spells it"))
    changes = dict.fromkeys(keys, target_file.UNSET)
    _change(
        lambda: target_changes.set_keys(
            repo_root(), name, changes, _command("unset", name, *keys),
            kind="unset", dry_run=dry_run,
        ),
        f"target {name} unset {', '.join(keys)}",
        dry_run,
    )  # fmt: skip


@target_app.command("rename")
def rename(
    old: Annotated[str, typer.Argument(metavar="OLD", help="The target")],
    new: Annotated[str, typer.Argument(metavar="NEW", help="Its new name")],
    dry_run: DryRun = False,
) -> None:
    """Rename a target in place. The deployments recorded under its old name
    are recorded under the new one, so `uqs deploy history NEW` shows them."""
    _change(
        lambda: target_changes.rename(
            repo_root(), old, new, _command("rename", old, new), dry_run=dry_run
        ),
        f"target {old} renamed {new}",
        dry_run,
    )


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
def remove(
    name: Annotated[str, typer.Argument(help="The target")], dry_run: DryRun = False
) -> None:
    """Delete one target's table, and the comment directly above it; every
    other line stays. `uqs target revert` brings it back."""
    _change(
        lambda: target_changes.remove(repo_root(), name, _command("remove", name), dry_run=dry_run),
        f"target {name} removed",
        dry_run,
    )


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
    args = control.confirmed_on_server(args, yes)
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
