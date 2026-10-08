"""`uqs deploy status`, `uqs deploy rollback` and `uqs deploy prune`: commands
about a server that already runs a release, beside cli/deploy.py's build, push
and verify, which make and install one. Each reads what the server's own pushes
recorded."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from uqs.cli.deploy import _failed, deploy_app
from uqs.deploy import config, prune, rollback, status
from uqs.deploy.prune import RELEASE_ID
from uqs.deploy.remote import Remote

Host = Annotated[str, typer.Option("--host", help="ssh destination, as your ssh config knows it")]
Dest = Annotated[str, typer.Option("--dest", help="Absolute directory on the server")]
RemoteUser = Annotated[
    str | None,
    typer.Option("--remote-user", help="Run the steps as this account (`sudo -n -iu`)"),
]
ConnectTimeout = Annotated[int, typer.Option("--connect-timeout", help="ssh/scp, seconds")]


def _server(host: str, dest: str, remote_user: str | None, connect_timeout: int, **timeouts):
    # profile is unused: each release's own report names the profile it runs
    cfg = config.make_config(
        artifact="",
        host=host,
        dest=dest,
        profile="unused",
        remote_user=remote_user,
        connect_timeout=connect_timeout,
        **timeouts,
    )
    return cfg, Remote(cfg.host, cfg.connect_timeout, remote_user=cfg.remote_user)


@deploy_app.command("status")
def status_cmd(
    host: Host,
    dest: Dest,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the status as JSON, to script against")
    ] = False,
    health: Annotated[
        bool,
        typer.Option("--health/--no-health", help="Ask current's processes whether they answer"),
    ] = True,
    health_timeout: Annotated[
        int, typer.Option("--health-timeout", help="How long processes have to answer, seconds")
    ] = 10,
    remote_user: RemoteUser = None,
    connect_timeout: ConnectTimeout = 10,
) -> None:
    """What a server runs: its current and previous releases, the last
    deployment's outcome, whether current's processes answer, and the lock.
    Read-only; it never takes the lock."""
    try:
        cfg, remote = _server(
            host, dest, remote_user, connect_timeout, verify_timeout=health_timeout
        )
        result = status.status(cfg, remote, health=health)
    except config.DeployError as exc:
        _failed("uqs deploy status", exc.stage, exc)
    typer.echo(json.dumps(result, indent=2) if as_json else status.render(result))
    h = result["health"]
    raise typer.Exit(code=1 if h["checked"] and not h["passed"] else 0)


@deploy_app.command("rollback")
def rollback_cmd(
    host: Host,
    dest: Dest,
    to: Annotated[
        str | None,
        typer.Option(
            "--to",
            metavar="RELEASE",
            help="The release to return to; default: the one current replaced",
        ),
    ] = None,
    remote_user: RemoteUser = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print what would stop and start; change nothing")
    ] = False,
    connect_timeout: ConnectTimeout = 10,
    command_timeout: Annotated[
        int, typer.Option("--command-timeout", help="Each remote step, seconds")
    ] = 900,
    verify_timeout: Annotated[
        int, typer.Option("--verify-timeout", help="Readiness deadline, seconds")
    ] = 180,
    break_lock: Annotated[
        bool,
        typer.Option(
            "--break-lock",
            help="Remove a deploy lock whose holder stopped beating; refused while it beats",
        ),
    ] = False,
) -> None:
    """Put a server back on an earlier release: stop current's processes, start
    and verify the target's, then move `current`. A target that fails to verify
    is stopped and current's processes are started again."""
    if to is not None and not RELEASE_ID.fullmatch(to):
        _failed("uqs deploy rollback", "arguments", ValueError(f"--to {to!r} is not a release id"))
    try:
        cfg, remote = _server(
            host,
            dest,
            remote_user,
            connect_timeout,
            command_timeout=command_timeout,
            verify_timeout=verify_timeout,
            break_lock=break_lock,
        )
        code = rollback.rollback(cfg, remote, to=to, dry_run=dry_run)
    except config.DeployError as exc:
        _failed("uqs deploy rollback", exc.stage, exc)
    raise typer.Exit(code=code)


@deploy_app.command("prune")
def prune_cmd(
    host: Host,
    dest: Dest,
    keep: Annotated[
        int, typer.Option("--keep", min=0, help="How many of the newest releases to keep")
    ],
    remote_user: RemoteUser = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="List what would be removed; remove nothing")
    ] = False,
    connect_timeout: ConnectTimeout = 10,
    command_timeout: Annotated[
        int, typer.Option("--command-timeout", help="Each remote step, seconds")
    ] = 900,
) -> None:
    """Remove the oldest releases beyond the newest --keep, under the deploy lock.

    Never removes the release `current` names, the one a rollback would return
    to, or one whose push is still running - whatever --keep is. Prints what
    stayed and why, what went, and the bytes freed."""
    try:
        cfg, remote = _server(
            host, dest, remote_user, connect_timeout, command_timeout=command_timeout
        )
        code = prune.prune(cfg, remote, keep=keep, dry_run=dry_run)
    except config.DeployError as exc:
        _failed("uqs deploy prune", exc.stage, exc)
    raise typer.Exit(code=code)
