"""`uqs target ...` and `uqs deploy history`: the servers `uqs --target TARGET`
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

`uqs --target TARGET <command>` itself is handled by the entry point before any
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
from typer._click.exceptions import ClickException

from uqs.cli import hdb_scope, safety
from uqs.cli.concepts import LatestTargetArg, TargetArg, target_arg
from uqs.cli.json_out import emit, json_opt
from uqs.cli.safety import ChangesState, Effect, ReadOnly, dry_run_note, marked
from uqs.cli.shared import _die, app, console, log
from uqs.cli.skip_options import forwarded
from uqs.cli.target_confirm import (
    changes_the_server,
    for_the_server,
    may_change_the_server,
    parse,
    root_command,
)
from uqs.deploy import control, inspection, target_changelog, target_changes, target_file
from uqs.deploy.config import DeployError
from uqs.paths import UqsError, UqsUsageError, repo_root

target_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Deploy targets: add, set, unset, rename, remove, edit, list, check, log, revert, "
    "adopt, shell, config, tunnel.\n\nThe servers `uqs --target TARGET <command>` runs on.",
)
app.add_typer(target_app, name="target")

_ACCOUNT = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
_LABEL = re.compile(r"([A-Za-z0-9_.-]+)=(\S*)")
Name = LatestTargetArg


#: `target rename OLD NEW`'s OLD: a target there is.
_OldTarget = target_arg("The target to rename")

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
    else:
        console.print(result.entry["diff"].rstrip("\n"), markup=False, highlight=False)
    if dry_run:
        console.print(dry_run_note())
    elif result.entry is not None:
        number = len(target_changelog.read_log(repo_root()))
        console.print(f"[green]✓[/] {said} in {result.path} (change {number}, `uqs target log`)")


def _change(run: Callable[[], target_changes.Result], said: str, dry_run: bool) -> None:
    try:
        result = run()
    except (DeployError, UqsError) as exc:
        _die(UqsError(str(exc)))
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


@target_app.command("add", cls=ChangesState)
def add(
    name: Annotated[str, typer.Argument(metavar="TARGET", help="Its name, for `uqs --target`")],
    ssh: Ssh,
    dest: Dest,
    remote_user: RemoteUser = None,
    label: Annotated[
        list[str] | None,
        typer.Option("--label", help="`key=value`, shown by `uqs target list`; repeatable"),
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
    entry = {"ssh": ssh, "dest": dest, "remote_user": remote_user, "labels": labels or None}
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
        console.print(f"  try: uqs --target {name} status")


_PAIR = re.compile(r"([a-z_][a-z0-9_]*(?:\.[A-Za-z0-9_.-]+)?)=(.*)", re.S)
_KEY = re.compile(r"[a-z_][a-z0-9_]*(?:\.[A-Za-z0-9_.-]+)?")


@target_app.command("set", cls=ChangesState)
def set_(
    name: TargetArg,
    pairs: Annotated[
        list[str],
        typer.Argument(
            metavar="KEY=VALUE...",
            help="Settings, spelt as in the file: ssh=deploy@prod connect_timeout=20 "
            'launch_mode=source labels.tier=uat live_check=\'["a", "b"]\'. A VALUE is '
            'read as TOML when it is one (20, true, [..], "quoted"), else as text',
            show_default=False,
        ),
    ],
    reach: Reach = False,
    dry_run: DryRun = False,
) -> None:
    """Change settings of one target in place.

    Any key the file accepts,
    including launch_mode, launch_python, launch_env_file, connect_timeout
    and environment. The rest of the file, comments included, is untouched."""
    changes: dict[str, object] = {}
    for pair in pairs:
        m = _PAIR.fullmatch(pair)
        if m is None:
            _die(UqsError(f"{pair!r}: give KEY=VALUE, the key as the file spells it"))
        changes[m.group(1)] = target_file.parse_value(m.group(2))
    _change(
        lambda: target_changes.set_keys(
            repo_root(), name, changes, _command("set", name, *pairs),
            check=_reach if reach else None, dry_run=dry_run,
        ),
        f"target {name} set {', '.join(changes)}",
        dry_run,
    )  # fmt: skip


@target_app.command("unset", cls=ChangesState)
def unset(
    name: TargetArg,
    keys: Annotated[
        list[str],
        typer.Argument(metavar="KEY...", help="Settings to remove: launch_python labels.tier"),
    ],
    dry_run: DryRun = False,
) -> None:
    """Remove settings from one target.

    The target then takes the layer beneath's
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


@target_app.command("rename", cls=ChangesState)
def rename(
    old: _OldTarget,
    new: Annotated[str, typer.Argument(metavar="NEW", help="Its new name")],
    dry_run: DryRun = False,
) -> None:
    """Rename a target in place; `uqs deploy history NEW` then shows its deployments."""
    _change(
        lambda: target_changes.rename(
            repo_root(), old, new, _command("rename", old, new), dry_run=dry_run
        ),
        f"target {old} renamed {new}",
        dry_run,
    )


CheckJson = json_opt('{"ok": true, "target", "ssh", "dest", "remote_user", "release"}')


@target_app.command("check", cls=ReadOnly)
def check(name: Name = None, as_json: CheckJson = False) -> None:
    """Reach the target and name its release - or which failed: ssh, or no release."""
    try:
        r = control.remote_for(name, repo_root())
        release = control.current_release(r)
    except UqsError as exc:
        _die(exc)
    if as_json:
        emit({
            "ok": True, "target": r.name, "ssh": r.host, "dest": r.dest,
            "remote_user": r.remote_user, "release": release,
        })  # fmt: skip
        return
    console.print(f"[green]✓[/] {r.name} ({r.describe()}) runs release {release}")


@target_app.command(
    "shell",
    cls=marked(Effect.CHANGES_STATE, own_output="opens an interactive shell on the server"),
)
def shell(
    name: LatestTargetArg = None,
) -> None:
    """Open a shell on the server, in the deployed release.

    Like `poetry shell`:
    its deploy.env loaded, its uqs first on PATH, as the target's account.
    `exit` comes back."""
    try:
        r = control.remote_for(name, repo_root())
        # a shell can change anything there (#2032)
        inspection.refuse(repo_root(), "`uqs target shell`", name=r.name, ssh=r.host, dest=r.dest)
        console.print(f"connecting to {r.name} ({r.describe()})")
        code = control.run(r, [], tty=True, command=control.shell_command(r))
    except UqsError as exc:
        _die(exc)
    raise typer.Exit(code=code)


#: The one command --target and --hdb reach together (#2219).
REPORT_ON_TARGET = ("data", "compress")


def _report_here(name: str, args: list[str]) -> int | None:
    """`uqs --target TARGET data compress --report --hdb DIR`, run HERE: DIR is
    read on the target by a fixed helper over its ssh (stack/hdb_compression.py),
    so the target needs q and the HDB, not uqf. None for any other command line,
    which forward sends to the release as before.

    Not refused on an inspection-only target: the helper is a program this tree
    wrote, and it reads - file headers and sort columns - and writes nothing
    but its own temporary directory, which it removes. `--apply` is refused
    before anything reaches the target: compressing an arbitrary directory
    would bypass the writer lock, the coverage hold, the policy and the reload a
    managed deployment's housekeeping1 runs it under."""
    p = parse(args)
    if p is None or p.help or p.path != REPORT_ON_TARGET or p.given("--hdb") is None:
        return None
    if p.given("--apply"):
        raise UqsUsageError(
            f"`--apply` is not run on a directory --hdb names on target {name}: only "
            "`--report` reads one. Compressing goes through a managed deployment's "
            f"writer lock, policy and reload - `uqs --target {name} data compress --apply`, "
            "without --hdb, on its configured HDB"
        )
    token = hdb_scope.ON_TARGET.set(name)
    try:
        code = root_command().main(args=list(args), prog_name="uqs", standalone_mode=False)
        return int(code or 0)
    except ClickException as exc:
        exc.show()
        return exc.exit_code
    except typer.Exit as exc:
        return exc.exit_code
    finally:
        hdb_scope.ON_TARGET.reset(token)


def forward(name: str, yes: bool, args: list[str], *, interactive: bool) -> int:
    """`uqs --target TARGET <args>`: run args on the target's release, after
    asking - or `--yes` - when they may change the server."""
    words = control.command_words(args)
    if words[:1] == ("target",):
        raise UqsUsageError(
            "`uqs target ...` manages this machine's targets - run it without --target"
        )
    if words[:2] == ("list", "targets"):
        raise UqsUsageError(
            "`uqs target list` lists this machine's targets - run it without --target"
        )
    tunnel = ("--tunnel", "--target")  # --target: query's old spelling (#1729)
    if words[:1] in (("query",), ("schema",)) and any(a.partition("=")[0] in tunnel for a in args):
        # Each names a different machine to run the query on (#1561, #1858).
        raise UqsError(
            f"give the target once: `uqs --target NAME {words[0]}` runs it on the server, "
            f"`uqs {words[0]} --tunnel NAME` runs it here through an ssh tunnel"
        )
    if (code := _report_here(name, args)) is not None:
        return code
    # a --skip-<id> goes as its --override, which an older server's uqs takes (#1826)
    args = for_the_server(forwarded(args), yes)
    r = control.remote_for(name, repo_root())
    shown = " ".join(["uqs", *args])
    if may_change_the_server(args):
        # before --yes is read: inspection mode is not a question (#2032)
        inspection.refuse(repo_root(), f"`{shown}`", name=r.name, ssh=r.host, dest=r.dest)
    if changes_the_server(args) and not yes:
        if not interactive:
            raise UqsError(
                f"`{shown}` may change {r.name} ({r.describe()}) - pass --yes "
                f"(`uqs --target {name} --yes ...`) to run it without a terminal to confirm on"
            )
        if not safety.ask(f"Run `{shown}` on {r.name} ({r.describe()})?"):
            log.warning("not confirmed - nothing was run")
            return safety.EXIT_DECLINED
    return control.run(r, args, tty=interactive and sys.stdout.isatty())
