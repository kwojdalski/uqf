"""`uqs deploy ...`: build a release, put it on a server, verify it there (#835).

Thin over uqs.deploy, whose package docstring describes the whole run. The
push command's parameters are named exactly as uqs.deploy.config.make_config's,
which checks them, so the options and their validation cannot drift apart.

Everything here prints plainly rather than through the rich console: push
prints its report and verify its result as JSON, which another program reads -
verify's last two lines are parsed by the push that ran it.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Annotated

import typer

from uqs.cli.shared import app
from uqs.deploy import build as release_build
from uqs.deploy import config, driver, verify
from uqs.deploy.artifact import PLATFORMS, ReleaseError
from uqs.deploy.remote import Remote

deploy_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Deploy uqf onto a server with an existing TorQ: build a release, push it, verify it.",
)
app.add_typer(deploy_app, name="deploy")


def _failed(prefix: str, stage: str, exc: Exception) -> None:
    print(f"{prefix}: FAILED at {stage}: {config.redact(str(exc))}", file=sys.stderr, flush=True)
    raise typer.Exit(code=1)


@deploy_app.command("build")
def build(
    output: Annotated[Path, typer.Option("--output", help="Directory to write the artifact into")],
    arch: Annotated[
        str, typer.Option("--arch", help=f"The servers' architecture: {', '.join(PLATFORMS)}")
    ] = "x86_64",
    python: Annotated[
        str | None,
        typer.Option(
            "--python", help="The servers' Python, major.minor; default requires-python's"
        ),
    ] = None,
    allow_dirty: Annotated[
        bool, typer.Option("--allow-dirty", help="Build uncommitted changes (recorded)")
    ] = False,
    bundle: Annotated[
        list[str] | None,
        typer.Option(
            "--bundle",
            metavar="DIR",
            help="A sidecar bundle (a folder with bundle.json) to install into the release; "
            "repeatable",
        ),
    ] = None,
) -> None:
    """Build a release artifact once, for `uqs deploy push` to put on any number of servers."""
    if shutil.which("uv") is None:
        _failed("uqs deploy build", "arguments", RuntimeError("uv is not on PATH"))
    try:
        artifact = release_build.build(
            output, arch=arch, python=python, allow_dirty=allow_dirty, bundles=bundle or ()
        )
    except ReleaseError as exc:
        _failed("uqs deploy build", exc.stage, exc)
    print(artifact.path)


@deploy_app.command("push")
def push(
    artifact: Annotated[str, typer.Argument(help="The release, from `uqs deploy build`")],
    host: Annotated[
        str, typer.Option("--host", help="ssh destination, as your ssh config knows it")
    ],
    dest: Annotated[str, typer.Option("--dest", help="Absolute directory on the server")],
    profile: Annotated[
        str, typer.Option("--profile", help="The uqs profile to start, e.g. essential")
    ],
    remote_user: Annotated[
        str | None,
        typer.Option(
            "--remote-user",
            help="Run the deployment as this account through `sudo -n -iu`; ssh and scp "
            "stay the --host login",
        ),
    ] = None,
    torq_home: Annotated[
        str | None, typer.Option("--torq-home", help="An existing TorQ on the server (TORQHOME)")
    ] = None,
    torq_app_home: Annotated[
        str | None,
        typer.Option("--torq-app-home", help="An existing finance starter pack (TORQAPPHOME)"),
    ] = None,
    torq_launcher: Annotated[
        str | None,
        typer.Option(
            "--torq-launcher",
            help="A site's own TorQ launcher, an absolute path, run instead of "
            "<torq-home>/torq.sh; TORQHOME still names the core holding torq.q",
        ),
    ] = None,
    launcher_env: Annotated[
        list[str] | None,
        typer.Option(
            "--launcher-env",
            metavar="NAME=VALUE",
            help="A variable the site launcher reads, persisted in deploy.env; repeatable. "
            "TORQDATAHOME defaults to the runtime data directory when --torq-launcher is given",
        ),
    ] = None,
    qcmd: Annotated[
        str | None,
        typer.Option(
            "--qcmd",
            help="The q executable on the server, an absolute path. Default: the deploying "
            "account's $QCMD there, else q on that account's PATH - never this machine's",
        ),
    ] = None,
    qhome: Annotated[
        str | None,
        typer.Option(
            "--qhome",
            help="QHOME on the server, where q finds its licence. Default: the deploying "
            "account's $QHOME there; refused when that is unset too",
        ),
    ] = None,
    data_dir: Annotated[
        str | None,
        typer.Option("--data-dir", help="Runtime data directory; default: <dest>/shared/data"),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Change nothing; show what would run")
    ] = False,
    restart: Annotated[
        bool,
        typer.Option(
            "--restart",
            help="Replace a deployment already there: stop its processes, start the new ones",
        ),
    ] = False,
    init_data: Annotated[
        bool,
        typer.Option(
            "--init-data", help="Create the runtime data directory if it does not exist yet"
        ),
    ] = False,
    jobs: Annotated[
        str,
        typer.Option(
            "--jobs",
            metavar="NAME,...",
            help="Sidecar streaming jobs to start beside the profile, with what they need; "
            "from the artifact's bundles (`uqs deploy build --bundle`)",
        ),
    ] = "",
    live: Annotated[
        bool,
        typer.Option(
            "--live", help="Refuse a source with no credential instead of reading its fixture"
        ),
    ] = False,
    odbc_home: Annotated[
        str | None,
        typer.Option(
            "--odbc-home",
            help="A private ODBC setup on the server (uqs odbc install), loaded by every "
            "process the release starts",
        ),
    ] = None,
    live_check: Annotated[
        str,
        typer.Option(
            "--live-check",
            metavar="SOURCE,...",
            help="Check these sources live after verification and before activation; "
            "a failure rolls the deployment back",
        ),
    ] = "",
    live_check_timeout: Annotated[
        int, typer.Option("--live-check-timeout", help="Every live check together, seconds")
    ] = 120,
    connect_timeout: Annotated[
        int, typer.Option("--connect-timeout", help="ssh/scp, seconds")
    ] = 10,
    command_timeout: Annotated[
        int, typer.Option("--command-timeout", help="Each remote step, seconds")
    ] = 900,
    smoke_timeout: Annotated[
        int, typer.Option("--smoke-timeout", help="The offline check, seconds")
    ] = 120,
    verify_timeout: Annotated[
        int, typer.Option("--verify-timeout", help="Readiness deadline, seconds")
    ] = 180,
) -> None:
    """Deploy a release onto a server with an existing TorQ, and verify it there."""
    options = dict(locals())
    try:
        cfg = config.make_config(**options)
        remote = Remote(cfg.host, cfg.connect_timeout, remote_user=cfg.remote_user)
        code = driver.deploy(cfg, remote)
    except config.DeployError as exc:
        _failed("uqs deploy push", exc.stage, exc)
    raise typer.Exit(code=code)


@deploy_app.command("verify")
def verify_cmd(
    profile: Annotated[str, typer.Option("--profile", help="The profile the release started")],
    deadline: Annotated[float, typer.Option("--deadline", help="Seconds")] = 180.0,
    query_timeout: Annotated[int, typer.Option("--query-timeout", help="Seconds per query")] = 5,
    port: Annotated[int | None, typer.Option("--port", help="The stack's base port")] = None,
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
) -> None:
    """Run on the server, from a release: does every process the profile promises answer?"""
    code = verify.run(
        profile,
        deadline=deadline,
        query_timeout=query_timeout,
        port=port,
        procs=[p for p in procs.split(",") if p],
        tables=[t for t in tables.split(",") if t],
        live=live,
        ports_free=ports_free,
    )
    raise typer.Exit(code=code)
