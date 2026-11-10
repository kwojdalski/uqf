"""`uqs deploy status`, `rollback`, `prune` and `check`: commands about a
server that already runs a release, beside cli/deploy.py's build and push,
which make and install one. status, rollback and prune read what the server's
own pushes recorded; check runs ON the server, from inside a release.

status, rollback and prune name their server as a TARGET, or by the
connection flags for one no target declares (cli/deploy_connection.py, #1736).
"""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.cli import completion, safety
from uqs.cli import deploy_connection as conn
from uqs.cli import deploy_output as deploy_out
from uqs.cli import deploy_policy as policy
from uqs.cli.concepts import profile_opt
from uqs.cli.deploy import deploy_app
from uqs.cli.hdb_scope import tables_given
from uqs.cli.json_out import emit, emit_line, json_opt
from uqs.cli.renames import renamed
from uqs.cli.safety import ChangesState, OverrideOpt, ReadOnly, ReasonOpt, YesOpt
from uqs.cli.shared import _die, json_refusal
from uqs.cli.vocabulary import duration
from uqs.deploy import config, inspection, overrides, prune, rollback, status, verify
from uqs.deploy.prune import RELEASE_ID
from uqs.deploy.remote import Remote
from uqs.paths import UqsError, repo_root


def _server(ctx, target, flags: dict, unnamed: str | None = None, **fixed):
    """The Config and Remote for a command that reaches one server: TARGET's
    settings - its timeouts too (#1892) - under the `flags` given on the
    command line, under `fixed`, which this command alone decides. `unnamed`
    refuses a call that names no server (conn.server)."""
    settings = conn.server(ctx, target, unnamed=unnamed, **flags) | fixed
    # profile is unused: each release's own report names the profile it runs
    settings["profile"] = "unused"
    cfg = config.make_config(artifact="", **settings)
    return cfg, Remote(cfg.ssh, cfg.connect_timeout, remote_user=cfg.remote_user)


StatusJson = json_opt(
    '{"ok", "format", "ssh", "dest", "current", "previous", "releases", "last_deployment", '
    '"health": {checked, passed, reason, processes}, "lock"} - ok false when the health check '
    "ran and failed"
)


@deploy_app.command("status", cls=ReadOnly)
def status_cmd(
    ctx: typer.Context,
    target: conn.Target = None,
    as_json: StatusJson = False,
    health: Annotated[
        bool,
        typer.Option("--health/--no-health", help="Ask current's processes whether they answer"),
    ] = True,
    health_timeout: Annotated[
        int,
        duration(
            "--health-timeout", integer=True, help="How long (a DURATION) processes have to answer"
        ),
    ] = 10,
    ssh: conn.Ssh = None,
    dest: conn.Dest = None,
    remote_user: conn.RemoteUser = None,
    connect_timeout: conn.ConnectTimeout = conn.DEFAULT_CONNECT_TIMEOUT,
) -> None:
    """Show what a server runs: its releases, last deployment, health and lock.

    What a server runs: its current and previous releases, the last
    deployment's outcome, whether current's processes answer, and the lock.
    Read-only; it never takes the lock."""
    try:
        cfg, remote = _server(
            ctx,
            target,
            dict(ssh=ssh, dest=dest, remote_user=remote_user, connect_timeout=connect_timeout),
            verify_timeout=health_timeout,
        )
        result = status.status(cfg, remote, health=health)
    except config.DeployError as exc:
        deploy_out.failed("uqs deploy status", exc.stage, exc)
    h = result["health"]
    failing = h["checked"] and not h["passed"]
    if as_json:
        emit({"ok": not failing, **result})
    else:
        typer.echo(status.render(result))
    raise typer.Exit(code=1 if failing else 0)


@deploy_app.command("rollback", cls=renamed(policy.ROLLBACK_OLD_SPELLINGS, ChangesState))
def rollback_cmd(
    ctx: typer.Context,
    target: conn.NamedTarget = None,
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
    ssh: conn.Ssh = None,
    dest: conn.Dest = None,
    remote_user: conn.RemoteUser = None,
    connect_timeout: conn.ConnectTimeout = conn.DEFAULT_CONNECT_TIMEOUT,
    command_timeout: conn.CommandTimeout = None,
    verify_timeout: conn.VerifyTimeout = None,
    override: OverrideOpt = None,
    reason: ReasonOpt = None,
    as_json: deploy_out.RollbackJson = False,
) -> None:
    """Put a server back on an earlier release.

    Stops current's processes, starts and verifies the target's, then moves
    `current`. A target that fails to verify is stopped and current's
    processes are started again.

    `--dry-run` prints the plan, then the dry-run note (on stderr under
    --json). It reads the deploy lock and does not take it: it changes
    nothing, so it warns when another deployment holds the lock rather than
    writing one."""
    if to is not None and not RELEASE_ID.fullmatch(to):
        deploy_out.failed(
            "uqs deploy rollback", "arguments", ValueError(f"--release {to!r} is not a release id")
        )
    try:
        waived = policy.waived("deploy rollback", override, reason)
        cfg, remote = _server(
            ctx,
            target,
            dict(
                ssh=ssh,
                dest=dest,
                remote_user=remote_user,
                connect_timeout=connect_timeout,
                command_timeout=command_timeout,
                verify_timeout=verify_timeout,
            ),
            unnamed="name the server to roll back: `uqs deploy rollback TARGET`",
            break_lock=waived.get(overrides.BREAK_LOCK),
        )
        inspection.refuse_deploy(
            repo_root(), "`uqs deploy rollback`",
            name=target, ssh=cfg.ssh, dest=cfg.dest, dry_run=dry_run,
        )  # fmt: skip
        code, result = rollback.rollback(cfg, remote, to=to, dry_run=dry_run)
    except config.DeployError as exc:
        deploy_out.failed("uqs deploy rollback", exc.stage, exc)
        return
    deploy_out.show(result, rollback.render, as_json=as_json, dry_run=dry_run)
    raise typer.Exit(code=code)


@deploy_app.command("prune", cls=ChangesState)
def prune_cmd(
    ctx: typer.Context,
    target: conn.NamedTarget = None,
    keep: Annotated[
        int | None,
        typer.Option(
            "--keep",
            min=0,
            help="How many of the newest releases to keep; default: TARGET's `keep`, "
            "as `deploy push` prunes to",
            show_default=False,
        ),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="List what would be removed; remove nothing")
    ] = False,
    ssh: conn.Ssh = None,
    dest: conn.Dest = None,
    remote_user: conn.RemoteUser = None,
    connect_timeout: conn.ConnectTimeout = conn.DEFAULT_CONNECT_TIMEOUT,
    command_timeout: conn.CommandTimeout = None,
    yes: YesOpt = False,
    as_json: deploy_out.PruneJson = False,
) -> None:
    """Remove the oldest releases beyond the newest --keep, under the deploy lock.

    Names what would go and asks first: a removed release cannot be brought
    back. `--yes` does not ask.

    Never removes the release `current` names, the one a rollback would return
    to, or one whose push is still running - whatever --keep is. Prints what
    stayed and why, what went, and the bytes freed.

    `--dry-run` takes no lock - it reads it, and warns when another deployment
    holds it - and ends with the dry-run note (on stderr under --json)."""
    try:
        cfg, remote = _server(
            ctx,
            target,
            dict(
                ssh=ssh,
                dest=dest,
                remote_user=remote_user,
                connect_timeout=connect_timeout,
                command_timeout=command_timeout,
                keep=keep,
            ),
            unnamed="name the server to prune: `uqs deploy prune TARGET`",
        )
        if cfg.keep is None:
            raise config.DeployError(
                "arguments",
                "--keep is required: "
                + ("the target sets no keep" if target else "no TARGET gives one"),
            )
        inspection.refuse_deploy(
            repo_root(), "`uqs deploy prune`",
            name=target, ssh=cfg.ssh, dest=cfg.dest, dry_run=dry_run,
        )  # fmt: skip
        if not dry_run:
            try:
                safety.json_needs_yes(as_json, yes, "remove releases")
            except UqsError as exc:  # before the server is reached: nothing was removed
                raise config.DeployError("confirm", str(exc)) from None
        code, result = prune.prune(
            cfg, remote, keep=cfg.keep, dry_run=dry_run, confirm=None if yes else _confirm_prune
        )
    except config.DeployError as exc:
        deploy_out.failed("uqs deploy prune", exc.stage, exc)
        return
    deploy_out.show(result, prune.render, as_json=as_json, dry_run=dry_run)
    raise typer.Exit(code=code)


def _confirm_prune(planned: dict) -> bool:
    """Ask before `deploy prune` removes anything, naming each release and runtime."""
    runtimes = planned.get("runtimes", {}).get("removed", [])
    # on stderr, with the question: stdout is the result's alone (#2125)
    for name in planned["removed"]:
        typer.echo(f"  release {name}", err=True)
    for name in runtimes:
        typer.echo(f"  runtime {name}", err=True)
    try:
        return safety.ask(
            f"Remove these {len(planned['removed'])} release(s) and {len(runtimes)} runtime(s) "
            "from the server? This cannot be undone"
        )
    except UqsError as exc:  # no terminal to ask on: refused, removing nothing
        raise config.DeployError("confirm", str(exc)) from None


VerifyJson = json_opt(
    '{"ok", "profile", "passed", "reason", "processes": [{process, port, pipeline, ok, error, '
    '...}]} - with --ports-free, {"ok", "profile", "busy": {process: port}}; no marker line'
)


#: The readiness wait is `--verify-timeout`, as `deploy push` and `rollback`
#: name it, and a process beyond the profile `--proc`, repeatable (#1857).
#: `deploy push` asks the release which it takes (deploy/selection.py).
_CHECK_RENAMES = {
    "--deadline": "--verify-timeout",
    "--procs": "--proc",
    "--tables": "--table",  # tables are a repeatable --table on every command (#1856)
}


_ProfileOpt = profile_opt("The profile the release started", required=True)


@deploy_app.command("check", cls=renamed(_CHECK_RENAMES, ReadOnly))
def verify_cmd(
    profile: _ProfileOpt,
    deadline: Annotated[float, conn.verify_timeout_option()] = config.DEFAULT_VERIFY_TIMEOUT,
    query_timeout: Annotated[
        int,
        duration("--query-timeout", integer=True, help="How long (a DURATION) each query may take"),
    ] = 5,
    port: Annotated[int | None, typer.Option("--base-port", help="The stack's base port")] = None,
    procs: Annotated[
        list[str] | None,
        typer.Option(
            "--proc",
            metavar="PROC",
            help="A process the check expects beyond the profile's; repeatable "
            "(PROC,PROC also works)",
        ),
    ] = None,
    tables: Annotated[
        list[str] | None,
        typer.Option(
            "--table",
            metavar="TABLE",
            help=f"Check {verify.PLANT} carries this table; repeatable, and TABLE,TABLE also works",
            autocompletion=completion.plant_table,
        ),
    ] = None,
    fixture: conn.FixtureOpt = True,
    ports_free: Annotated[
        bool,
        typer.Option(
            "--ports-free", help="Only check that nothing listens on the profile's ports yet"
        ),
    ] = False,
    as_json: VerifyJson = False,
) -> None:
    """Check, on the server, that every process the profile promises answers.

    Run from a release on the server.

    Prints one JSON object, then DEPLOY_VERIFY_OK or DEPLOY_VERIFY_FAILED on a
    line of its own, which `uqs deploy push` reads; `--json` leaves the marker
    out, so stdout is the one document."""
    try:
        code, result = verify.run(
            profile,
            deadline=deadline,
            query_timeout=query_timeout,
            port=port,
            procs=[p for word in procs or () for p in word.split(",") if p],
            tables=tables_given(tables),
            live=not fixture,
            ports_free=ports_free,
        )
    except UqsError as exc:  # an unknown profile or process
        _die(exc)
        return
    except SystemExit as exc:  # a profile process with no port: verify's own refusal
        if not as_json:
            raise
        json_refusal(str(exc.code))
        raise typer.Exit(code=1) from None
    if as_json:
        emit(result)
    else:
        emit_line(result)
        print(verify.OK_MARKER if code == 0 else verify.FAILED_MARKER)
    raise typer.Exit(code=code)
