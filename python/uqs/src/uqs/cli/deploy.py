"""`uqs deploy ...`: build a release, put it on a server, verify it there (#835).

Thin over uqs.deploy, whose package docstring describes the whole run. The
push command's parameters are named exactly as uqs.deploy.config.make_config's,
which checks them, so the options and their validation cannot drift apart.

Progress goes through uqs.logger on stderr, like every other command's (#1881):
stdout carries what another program reads - build's artifact path, and
check's result, whose last two lines the push that ran it parses. push,
rollback and prune print what happened as text, or with --json as one
document through json_out.emit like every other command (#2125).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated

import typer

from uqs.cli import completion
from uqs.cli import deploy_components as components
from uqs.cli import deploy_connection as conn
from uqs.cli import deploy_dry_run as dry
from uqs.cli import deploy_output as deploy_out
from uqs.cli import deploy_policy as policy
from uqs.cli.choices import ChoiceOpt
from uqs.cli.concepts import profile_opt
from uqs.cli.safety import ChangesState, DryRunOpt, OverrideOpt, ReasonOpt
from uqs.cli.shared import app
from uqs.deploy import build as release_build
from uqs.deploy import (
    config,
    driver,
    frontend,
    history,
    inspection,
    kafka,
    overrides,
    portable,
    target_keys,
    targets,
)
from uqs.deploy.artifact import PLATFORMS, ReleaseError
from uqs.deploy.remote import Remote
from uqs.logger import get_logger
from uqs.paths import repo_root, runtime_from_env
from uqs.stack import runtime_bundles

deploy_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Deploy releases: build, component, push, check, status, history, rollback, prune.\n\n"
    "Deploy uqf onto a server with an existing TorQ: build a release (and each "
    "component), push it, check it, see its status and history, rollback, prune.",
)
app.add_typer(deploy_app, name="deploy")


#: The servers' architecture a release is built for when --arch is not given.
DEFAULT_ARCH = "x86_64"
#: `--arch`, shared by `deploy build` and `deploy component build` (#1884).
ArchOpt = ChoiceOpt(
    PLATFORMS, "--arch", "The servers' architecture", default=DEFAULT_ARCH, required=True
)
log = get_logger()


@deploy_app.command("build", cls=ChangesState)
def build(
    output: Annotated[
        Path | None,
        typer.Option(
            "--output-dir",
            help="Directory to write the artifact into; default: dist/ at the repo root",
        ),
    ] = None,
    arch: ArchOpt = DEFAULT_ARCH,
    python: Annotated[
        str | None,
        typer.Option(
            "--python", help="The servers' Python, major.minor; default requires-python's"
        ),
    ] = None,
    bundle: Annotated[
        list[Path] | None,
        typer.Option(
            "--bundle",
            help="A sidecar bundle (a folder with bundle.json) to install into the release, "
            "beside those the runtime declares; repeatable",
        ),
    ] = None,
    dry_run: dry.BuildDryRun = False,
    q_target: components.ConvertFor = None,
    q_exclude: Annotated[
        list[str] | None,
        typer.Option(
            "--q-exclude",
            metavar="PATTERN",
            help="With --q-version: a folder, file or glob to ship as written; repeatable",
        ),
    ] = None,
    override: OverrideOpt = None,
    reason: ReasonOpt = None,
    kafka_home: Annotated[
        Path | None, typer.Option("--kfk-home", metavar="DIR", help=kafka.HOME_HELP)
    ] = None,
    with_kafka_connector: Annotated[
        bool, typer.Option("--kafka-connector/--no-kafka-connector", help=kafka.CONNECTOR_HELP)
    ] = False,
    python_component: components.CarriedPythonRuntime = None,
    frontend_dist: Annotated[
        Path | None, typer.Option("--frontend", metavar="DIST", help=frontend.HELP)
    ] = None,
) -> None:
    """Build a release artifact once, for `uqs deploy push` to put on any number of servers.

    It carries the selected runtime's bundles (`uqs --runtime crypto deploy build`):
    those runtime_bundles.json declares for it, plus any --bundle."""
    runtime = runtime_from_env()
    try:
        waived = policy.waived("deploy build", override, reason)
    except config.DeployError as exc:
        deploy_out.failed("uqs deploy build", exc.stage, exc)
        return
    if dry_run:
        try:
            members = release_build.composition_members(
                runtime, repo_root(), [str(b) for b in bundle or ()]
            )
        except ReleaseError as exc:
            deploy_out.failed("uqs deploy build", exc.stage, exc)
            return
        dry.say_composition(runtime_bundles.composition(runtime, members))
        return
    if shutil.which("uv") is None:
        deploy_out.failed("uqs deploy build", "prerequisites", RuntimeError("uv is not on PATH"))
    try:
        artifact = release_build.build(
            output,
            arch=arch,
            python=python,
            allow_dirty=overrides.ALLOW_DIRTY in waived,
            bundles=[str(b) for b in bundle or ()],
            runtime=runtime,
            q_target=q_target,
            q_exclude=q_exclude or (),
            allow_computed_names=portable.ALLOW_COMPUTED_NAMES in waived,
            kafka_home=kafka_home,
            kafka_connector=with_kafka_connector,
            python_runtime_file=python_component,
            frontend_dist=frontend_dist,
        )
    except ReleaseError as exc:
        deploy_out.failed("uqs deploy build", exc.stage, exc)
    print(artifact.path)


_ProfileOpt = profile_opt("The uqs profile to start, e.g. essential")


@deploy_app.command("push", cls=ChangesState)
def push(
    ctx: typer.Context,
    artifact: Annotated[
        str,
        typer.Argument(
            metavar="ARTIFACT",
            help="The release: a file from `uqs deploy build`, an https:// URL to one, or "
            "a release tag CI published (.github/workflows/release.yml)",
        ),
    ],
    target_names: Annotated[
        list[str] | None,
        typer.Argument(
            metavar="TARGET...",
            autocompletion=completion.targets,
            help=f"Targets in {targets.DECLARATION} (or ${targets.DECLARATION_ENV}), deployed in "
            "order, stopping at the first failure; a flag given here wins over a target's "
            "value. None: --ssh, --dest and --profile say it all",
            show_default=False,
        ),
    ] = None,
    ssh: conn.Ssh = None,
    dest: conn.Dest = None,
    profile: _ProfileOpt = None,
    remote_user: conn.RemoteUser = None,
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
    process_fields: Annotated[
        str,
        typer.Option(
            "--process-fields",
            metavar="NAME,...",
            help="Columns the site launcher reads that TorQ's process.csv lacks, such as "
            "taskset_no; every row carries them, empty unless `uqs config set` gives a value. "
            "Persisted in deploy.env as UQS_PROCESS_FIELDS",
        ),
    ] = "",
    q_cmd: components.Qcmd = None,
    q_home: components.Qhome = None,
    data_dir: Annotated[
        str | None,
        typer.Option("--data-dir", help="Runtime data directory; default: <dest>/shared/data"),
    ] = None,
    dry_run: DryRunOpt = False,
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
    fixture: conn.FixtureOpt = True,
    odbc_home: Annotated[
        Path | None,
        typer.Option(
            "--odbc-home",
            help="A private ODBC setup on the server (uqs setup odbc install), loaded by every "
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
            autocompletion=completion.source_list,
        ),
    ] = "",
    live_check_timeout: conn.LiveCheckTimeout = 120,
    connect_timeout: conn.ConnectTimeout = conn.DEFAULT_CONNECT_TIMEOUT,
    command_timeout: conn.CommandTimeout = None,
    smoke_timeout: conn.SmokeTimeout = 120,
    verify_timeout: conn.VerifyTimeout = None,
    override: OverrideOpt = None,
    reason: ReasonOpt = None,
    release_repo: Annotated[
        str | None,
        typer.Option(
            "--release-repo",
            metavar="OWNER/NAME",
            help="Whose GitHub releases a tag ARTIFACT names; default: this checkout's origin",
        ),
    ] = None,
    expected_sha256: policy.ExpectedSha256 = None,
    q_version: policy.QVersion = None,
    q_mode: components.QMode = components.DEFAULT,
    torq_mode: components.TorqMode = components.DEFAULT,
    python_mode: components.PythonMode = components.DEFAULT,
    torq_component: components.TorqRuntime = None,
    torq_component_sha256: components.TorqRuntimeSha256 = None,
    q_component: components.QRuntime = None,
    q_component_sha256: components.QRuntimeSha256 = None,
    python_component: components.PythonRuntime = None,
    python_component_sha256: components.PythonRuntimeSha256 = None,
    keep: Annotated[
        int | None,
        typer.Option(
            "--keep",
            min=0,
            help="After activating, remove all but the newest N releases (`uqs deploy prune`)",
        ),
    ] = None,
    soak: conn.Soak = None,
    register: conn.Register = True,
    as_json: deploy_out.PushJson = False,
) -> None:
    """Deploy a release onto a server, on its TorQ and q or bundled ones, and verify it there."""
    options = conn.config_options(dict(locals()))
    del options["ctx"], options["target_names"]
    del options["override"], options["reason"]
    code = 0
    shown = deploy_out.PushOutput(as_json, dry_run)
    try:
        bypass = policy.bypasses(override, reason)
        options.update(bypass)
        names = ",".join(target_names or [])
        if names:
            given = {
                k: v
                for k, v in options.items()
                if k not in targets.GIVEN_SEPARATELY
                and conn.on_command_line(ctx, {"live": "fixture"}.get(k, k))
            } | {k: v for k, v in bypass.items() if v is not None}
            cfgs = targets.configs(names, artifact, given, dry_run, repo_root())
        else:
            for name in ("ssh", "dest", "profile"):
                if options[name] is None:
                    raise config.DeployError(
                        "arguments",
                        f"{target_keys.flag(name)} is required, or a TARGET after ARTIFACT",
                    )
            # an option left unset (None) takes make_config's default
            cfgs = [config.make_config(**{k: v for k, v in options.items() if v is not None})]
        inspection.refuse_push(repo_root(), cfgs, dry_run=dry_run)  # #2032
        for cfg in cfgs:
            if cfg.target:
                log.info("target {}: {}:{}", cfg.target, cfg.ssh, cfg.dest)
            remote = Remote(cfg.ssh, cfg.connect_timeout, remote_user=cfg.remote_user)
            code = driver.deploy(
                cfg,
                remote,
                out=shown.plan,
                on_report=lambda rep, c=cfg: history.record(
                    repo_root(), c, rep, register=register, say=shown.say
                ),
                on_result=shown.on_result(cfg),
            )
            if code:
                if len(cfgs) > 1:
                    log.error(
                        "target {} failed - the targets after it were not deployed", cfg.target
                    )
                break
    except config.DeployError as exc:
        deploy_out.failed("uqs deploy push", exc.stage, exc, {"deployments": shown.deployments})
    shown.end(code)
    raise typer.Exit(code=code)
