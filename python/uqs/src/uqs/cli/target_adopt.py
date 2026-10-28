"""`uqs target adopt`: take over a release copied onto a server by hand
(#1756). What it asks the server and changes is deploy/adopt.py's; this is
the command: a dry run by default, `--apply` to do it, `--yes` not to ask.
"""

from __future__ import annotations

import os
from typing import Annotated

import typer

from uqs.cli.shared import _die, console
from uqs.cli.target import _ACCOUNT, RemoteUser, Ssh, _command, _reach, target_app
from uqs.deploy import (
    adopt,
    control,
    history,
    target_changelog,
    target_changes,
    target_file,
    targets,
)
from uqs.deploy.config import DeployError
from uqs.paths import UqsError, repo_root

Dest = Annotated[str, typer.Option("--dest", help="The deployment root on the server, absolute")]


def _say(s: adopt.Survey, r: control.Remote) -> None:
    console.print(
        f"{r.name}: {r.host}:{s.dest}" + (f", as {r.remote_user}" if r.remote_user else "")
    )
    if s.current is not None:
        console.print(f"  current      links to {s.current} ({s.release_dir}) - left as it is")
    else:
        console.print(f"  current      none; {s.release_dir} would become the release")
        for step in adopt.server_changes(s):
            console.print(f"               would {step}")
    if s.mode == "release":
        console.print("  launches     as pushed: .venv/bin/uqs and deploy.env")
        return
    console.print("  launches     from source: no .venv/bin/uqs with a deploy.env")
    env = f"{s.env_file} (from {s.env_source})" if s.env_file else "none found"
    console.print(f"  environment  {env}")
    for p in s.probes:
        verdict = "[green]imports uqf[/]" if p.ok else f"[red]{p.said or 'fails'}[/]"
        where = p.path or p.given
        console.print(f"  python       {p.source}: {where} - {verdict}", highlight=False)


@target_app.command("adopt")
def adopt_(
    name: Annotated[str, typer.Argument(help="What to call it: `uqs --target NAME ...`")],
    ssh: Ssh,
    dest: Dest,
    remote_user: RemoteUser = None,
    release: Annotated[
        str | None,
        typer.Option(
            "--release",
            help="The copied tree, absolute; default: DEST itself. Moved to "
            "DEST/releases/adopted-<time> with DEST/current linked to it",
        ),
    ] = None,
    launch_python: Annotated[
        str | None,
        typer.Option(
            "--launch-python",
            help="A Python on the server that imports uqf's dependencies; default: the "
            "release's .venv/bin/python, else python3",
        ),
    ] = None,
    launch_env_file: Annotated[
        str | None,
        typer.Option(
            "--launch-env-file",
            help="The environment file to load; default: the release's deploy.env, else "
            "its output/uqs/setenv.sh",
        ),
    ] = None,
    do_it: Annotated[
        bool,
        typer.Option(
            "--apply",
            help="Make the changes - move and link on the server, save the target; "
            "without it, a dry run that writes nothing on either side",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", "-n", help="Show what it would do and change nothing (the default)"
        ),
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Do not ask before changing the server")
    ] = False,
) -> None:
    """Take over a release copied onto a server by hand (scp, rsync): say
    what the server holds, how it would launch uqs and the target it would
    save. --apply links it as DEST/current, saves the target - checked by
    reaching it - and records it in `uqs deploy history`."""
    if dry_run and do_it:
        _die(UqsError("--dry-run and --apply contradict each other - give one"))
        return
    root = repo_root()
    try:
        declared = targets.read(root) if targets.declaration_path(root).is_file() else {}
        if name in declared:
            raise UqsError(
                f"target {name} is already declared - pick another name, or "
                f"`uqs target remove {name}` first"
            )
        if not targets.NAME.fullmatch(name):
            raise UqsError(f"target name {name!r}: lower-case letters, digits, - and _")
        if remote_user is not None and not _ACCOUNT.fullmatch(remote_user):
            raise UqsError(f"--remote-user {remote_user!r} is not an account name")
        targets.remote_shape(name, {"host": ssh, "dest": dest})
        r = control.Remote(name, ssh, dest.rstrip("/") or "/", remote_user)
        survey = adopt.survey(r, release, launch_python, launch_env_file)
    except UqsError as exc:
        _die(exc)
        return
    _say(survey, r)
    refused = adopt.problems(survey)
    if refused:
        _die(UqsError("cannot adopt: " + "; ".join(refused)))
    entry = adopt.block(survey, r)
    console.print("  target       would save:")
    console.print(
        "\n".join(f"    {ln}" for ln in target_file.render(("targets", name), entry).splitlines()),
        markup=False,
        highlight=False,
    )
    words = [name, "--ssh", ssh, "--dest", dest]
    for flag, value in (("--remote-user", remote_user), ("--release", release),
                        ("--launch-python", launch_python),
                        ("--launch-env-file", launch_env_file)):  # fmt: skip
        words += [flag, value] if value else []
    command = _command("adopt", *words, "--apply")
    try:
        # the target is checked whole before the server is touched
        target_changes.add(root, name, entry, command, kind=target_changelog.ADOPTED, dry_run=True)
    except DeployError as exc:
        _die(UqsError(str(exc)))
    if not do_it:
        console.print("dry run: nothing changed on the server or here - --apply to adopt it")
        return
    changes = adopt.server_changes(survey)
    if changes and not yes:
        asked = f"On {r.host}: {'; '.join(changes)}?"
        try:
            agreed = typer.confirm(asked, default=False)
        except typer.Abort:
            agreed = False
        if not agreed:
            _die(UqsError("not adopted: nothing was changed (--yes to run without asking)"))
    try:
        adopt.apply(r, survey)
        done = target_changes.add(
            root, name, entry, command, kind=target_changelog.ADOPTED, check=_reach
        )
    except (UqsError, DeployError) as exc:
        moved = " - the server WAS changed: current is linked" if changes else ""
        _die(UqsError(f"{exc}{moved}"))
        return
    current = survey.release_name or os.path.basename(str(survey.current).rstrip("/"))
    history.record_adopted(root, name, r.host, r.dest, r.remote_user, current)
    number = len(target_changelog.read_log(root))
    console.print(
        f"[green]✓[/] adopted {current} as target {name} in {done.path} "
        f"(change {number}, `uqs target log`)"
    )
    console.print(f"  try: uqs --target {name} status")
