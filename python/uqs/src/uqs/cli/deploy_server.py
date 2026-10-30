"""`uqs deploy status`, `rollback`, `prune` and `verify`: commands about a
server that already runs a release, beside cli/deploy.py's build and push,
which make and install one. status, rollback and prune read what the server's
own pushes recorded; verify runs ON the server, from inside a release.

status, rollback and prune name their server as a TARGET, or by the
connection flags for one no target declares (cli/deploy_connection.py, #1736).
"""

from __future__ import annotations

import json
from typing import Annotated

import typer

from uqs.cli import deploy_connection as conn
from uqs.cli.deploy import _failed, deploy_app
from uqs.cli.json_out import json_opt
from uqs.cli.renames import alias_command, renamed
from uqs.cli.safety import ChangesState, ReadOnly
from uqs.cli.shared import _die, json_refusal
from uqs.deploy import config, prune, rollback, status, verify
from uqs.deploy.prune import RELEASE_ID
from uqs.deploy.remote import Remote
from uqs.paths import UqsError


def _server(ctx, target, host, dest, remote_user, connect_timeout, **timeouts):
    connection = conn.server(
        ctx, target, host=host, dest=dest, remote_user=remote_user, connect_timeout=connect_timeout
    )
    # profile is unused: each release's own report names the profile it runs
    cfg = config.make_config(artifact="", profile="unused", **connection, **timeouts)
    return cfg, Remote(cfg.host, cfg.connect_timeout, remote_user=cfg.remote_user)


@deploy_app.command("status", cls=renamed(conn.OLD_SPELLINGS, ReadOnly))
def status_cmd(
    ctx: typer.Context,
    target: conn.Target = None,
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
    host: conn.Host = None,
    dest: conn.Dest = None,
    remote_user: conn.RemoteUser = None,
    connect_timeout: conn.ConnectTimeout = conn.DEFAULT_CONNECT_TIMEOUT,
) -> None:
    """What a server runs: its current and previous releases, the last
    deployment's outcome, whether current's processes answer, and the lock.
    Read-only; it never takes the lock."""
    try:
        cfg, remote = _server(
            ctx, target, host, dest, remote_user, connect_timeout, verify_timeout=health_timeout
        )
        result = status.status(cfg, remote, health=health)
    except config.DeployError as exc:
        _failed("uqs deploy status", exc.stage, exc)
    typer.echo(json.dumps(result, indent=2) if as_json else status.render(result))
    h = result["health"]
    raise typer.Exit(code=1 if h["checked"] and not h["passed"] else 0)


#: `--to` names a release here, and only here, while everywhere else it ends a
#: time range: `--release` since #1768, the old spelling parsing for one release.
@deploy_app.command(
    "rollback", cls=renamed(conn.OLD_SPELLINGS | {"--to": "--release"}, ChangesState)
)
def rollback_cmd(
    ctx: typer.Context,
    target: conn.Target = None,
    to: Annotated[
        str | None,
        typer.Option(
            "--release",
            metavar="RELEASE",
            help="The release to return to; default: the one current replaced",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Print what would stop and start; change nothing"),
    ] = False,
    host: conn.Host = None,
    dest: conn.Dest = None,
    remote_user: conn.RemoteUser = None,
    connect_timeout: conn.ConnectTimeout = conn.DEFAULT_CONNECT_TIMEOUT,
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
        _failed(
            "uqs deploy rollback", "arguments", ValueError(f"--release {to!r} is not a release id")
        )
    try:
        cfg, remote = _server(
            ctx,
            target,
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


@deploy_app.command("prune", cls=renamed(conn.OLD_SPELLINGS, ChangesState))
def prune_cmd(
    ctx: typer.Context,
    keep: Annotated[
        int, typer.Option("--keep", min=0, help="How many of the newest releases to keep")
    ],
    target: conn.Target = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="List what would be removed; remove nothing")
    ] = False,
    host: conn.Host = None,
    dest: conn.Dest = None,
    remote_user: conn.RemoteUser = None,
    connect_timeout: conn.ConnectTimeout = conn.DEFAULT_CONNECT_TIMEOUT,
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
            ctx, target, host, dest, remote_user, connect_timeout, command_timeout=command_timeout
        )
        code = prune.prune(cfg, remote, keep=keep, dry_run=dry_run)
    except config.DeployError as exc:
        _failed("uqs deploy prune", exc.stage, exc)
    raise typer.Exit(code=code)


VerifyJson = json_opt(
    '{"profile", "passed", "reason", "processes": [{process, port, pipeline, ok, error, ...}]} '
    '- with --ports-free, {"profile", "busy": {process: port}}; no marker line'
)


@deploy_app.command("check", cls=renamed({"--port": "--base-port"}, ReadOnly))
def verify_cmd(
    profile: Annotated[str, typer.Option("--profile", help="The profile the release started")],
    deadline: Annotated[float, typer.Option("--deadline", help="Seconds")] = 180.0,
    query_timeout: Annotated[int, typer.Option("--query-timeout", help="Seconds per query")] = 5,
    port: Annotated[int | None, typer.Option("--base-port", help="The stack's base port")] = None,
    procs: Annotated[
        str,
        typer.Option("--procs", metavar="NAME,...", help="Processes beyond the profile"),
    ] = "",
    tables: Annotated[
        str, typer.Option("--tables", metavar="NAME,...", help="Tables stp1 must carry")
    ] = "",
    live: Annotated[bool, typer.Option("--live", help="Fixtures must be refused")] = False,
    ports_free: Annotated[
        bool,
        typer.Option(
            "--ports-free", help="Only check that nothing listens on the profile's ports yet"
        ),
    ] = False,
    as_json: VerifyJson = False,
) -> None:
    """Run on the server, from a release: does every process the profile promises answer?

    Prints one JSON object, then DEPLOY_VERIFY_OK or DEPLOY_VERIFY_FAILED on a
    line of its own, which `uqs deploy push` reads; `--json` leaves the marker
    out, so stdout is the one document."""
    try:
        code = verify.run(
            profile,
            deadline=deadline,
            query_timeout=query_timeout,
            port=port,
            procs=[p for p in procs.split(",") if p],
            tables=[t for t in tables.split(",") if t],
            live=live,
            ports_free=ports_free,
            marker=not as_json,
        )
    except UqsError as exc:  # an unknown profile or process
        _die(exc)
        return
    except SystemExit as exc:  # a profile process with no port: verify's own refusal
        if not as_json:
            raise
        json_refusal(str(exc.code))
        code = 1
    raise typer.Exit(code=code)


#: Its name before #1733: `check` actively probes and exits non-zero on a problem.
#: An older release on a server has only `verify`, which a rollback runs
#: (deploy/selection.py's verify_command asks for whichever it has).
alias_command(
    deploy_app, "verify", "uqs deploy check", verify_cmd, cls=renamed({"--port": "--base-port"})
)
