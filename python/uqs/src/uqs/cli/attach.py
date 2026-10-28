"""`uqs job attach NAME` and `uqs job detach NAME`: a job on a TorQ stack that
already exists (#1424, #1457).

One command per direction. `attach` resolves the configuration, runs the
preflight, shows the plan, asks, then saves, applies, verifies and starts the
publisher - each stage the existing one in uqs.attach, run in order by
workflow.execute; `--dry-run` stops after the plan and changes nothing.
`detach` stops the publisher and reverses what is safe to reverse. The
inventory is `uqs job attachment list` (stack/attach_state.py).

Every string printed here passes through `redact` first: the plan, the stage
results, the errors and the debug detail, in tables and in `--json`.
"""

from __future__ import annotations

import getpass
import time
from dataclasses import asdict
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Annotated

import typer

from uqs.attach import config, service, workflow
from uqs.attach import detach as detaching
from uqs.attach import publisher as publishing
from uqs.cli import shared
from uqs.cli.attach_legacy import refuse_legacy
from uqs.cli.attach_output import (
    Output,
    checks_table,
    redact,
    render_plan,
    secrets_of,
    stages_table,
    steps_table,
    verdict,
)
from uqs.cli.shared import JOB_OPERATIONS_PANEL, _debug_requested, _paths, job_app
from uqs.paths import UqsError


def _saved_names() -> list[str]:
    try:
        return config.names()
    except Exception:  # noqa: BLE001 - completion must never raise
        return []


NameArg = Annotated[
    str,
    typer.Argument(help="The attachment's name, e.g. piggybank-uat", autocompletion=_saved_names),
]
#: Extra words are taken, not rejected by the parser, so the old `uqs job attach
#: <stage> NAME` is refused with the command that replaced it.
EXTRA_WORDS = {"allow_extra_args": True}
JsonOpt = Annotated[
    bool,
    typer.Option("--json", help="Print one JSON document. Executing needs --yes: it cannot ask."),
]
DryRunOpt = Annotated[
    bool, typer.Option("--dry-run", help="Run every check and show the plan; change nothing")
]
YesOpt = Annotated[
    bool,
    typer.Option(
        "--yes",
        help="Do not ask before changing the stack (the plan and results are still shown)",
    ),
]


# ----------------------------------------------------------------- uqs job attach


@job_app.command("attach", rich_help_panel=JOB_OPERATIONS_PANEL, context_settings=EXTRA_WORDS)
def attach(
    ctx: typer.Context,
    name: NameArg,
    job: Annotated[
        str | None, typer.Option("--job", help="The streaming job to attach (new attachment)")
    ] = None,
    bundle: Annotated[
        Path | None,
        typer.Option("--bundle", help="The sidecar bundle holding the job (default: this tree)"),
    ] = None,
    stack_id: Annotated[
        int | None,
        typer.Option(
            "--stack-id",
            help="The existing stack's base port (-stackid / KDBBASEPORT). No default: "
            "the runtime's own base port is exactly the wrong guess.",
        ),
    ] = None,
    host: Annotated[
        str | None,
        typer.Option("--host", help="Where the stack's processes listen (default: localhost)"),
    ] = None,
    discovery: Annotated[
        str | None,
        typer.Option(
            "--discovery",
            help="HOST:PORT of its discovery, on the --host machine (default: HOST:<stack-id>+1)",
        ),
    ] = None,
    tickerplant: Annotated[
        str | None, typer.Option("--tickerplant", help="Its tickerplant, if several")
    ] = None,
    consumer: Annotated[
        list[str] | None, typer.Option("--consumer", help="An rdb/wdb to attach (default: all)")
    ] = None,
    user: Annotated[
        str | None, typer.Option("--user", help=f"default: {config.DEFAULT_USER}")
    ] = None,
    password_env: Annotated[
        str | None,
        typer.Option(
            "--password-env",
            help="Environment variable holding the password (default: TorQ's own demo "
            "credential). The password itself is never saved or printed.",
        ),
    ] = None,
    runtime_only: Annotated[
        bool,
        typer.Option(
            "--runtime-only",
            help="Accept that restarting the tickerplant drops the registration "
            "(default: add a marked block to its schema file)",
        ),
    ] = False,
    remote_schema_file: Annotated[
        bool,
        typer.Option(
            "--remote-schema-file",
            help="Have the tickerplant append the marked block to its own schema file, "
            "for a stack on another machine (default: uqs appends it, on the stack's machine)",
        ),
    ] = False,
    tplogdir: Annotated[
        str, typer.Option("--tplogdir", help="Pin the tickerplant log directory")
    ] = "",
    schema_file: Annotated[str, typer.Option("--schema-file", help="Pin the schema file")] = "",
    ssh: Annotated[
        str | None,
        typer.Option(
            "--ssh",
            help="USER@HOST to reach the stack's files over ssh, for `uqs data hdb-check` and "
            "`hdb-alter --attach` (default: --host as the current user). Changes a saved "
            "attachment without --replace",
        ),
    ] = None,
    replace: Annotated[
        bool,
        typer.Option(
            "--replace",
            help="Rebuild a saved attachment from these options. Keeps its manifest and "
            "accepted risks; refused while the manifest holds changes on another stack.",
        ),
    ] = False,
    override: Annotated[
        list[str] | None,
        typer.Option(
            "--override",
            help="Accept ONE blocker, as CODE:COMPONENT (e.g. TORQ_VERSION:stp1); repeatable. "
            "It accepts a risk, it does not establish compatibility. Needs --override-reason.",
        ),
    ] = None,
    override_reason: Annotated[
        str, typer.Option("--override-reason", help="Why the accepted blocker is acceptable")
    ] = "",
    accept_risks: Annotated[
        bool,
        typer.Option(
            "--accept-risks",
            help="Confirm attaching with the accepted risks the plan lists. Separate from "
            "--yes, which never confirms them; --json needs it when there are any.",
        ),
    ] = False,
    clear_overrides: Annotated[
        bool,
        typer.Option(
            "--clear-overrides",
            help="Withdraw every accepted risk (saved even when that leaves a blocker)",
        ),
    ] = False,
    dry_run: DryRunOpt = False,
    no_start: Annotated[
        bool,
        typer.Option("--no-start", help="Apply and verify, and leave the publisher stopped"),
    ] = False,
    yes: YesOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Attach a job to an EXISTING TorQ stack, verify it, and start its publisher.

    Shows the plan (identity, tables, affected components, changes, findings),
    asks, then registers the tables, subscribes the consumers, persists the
    schema block, verifies - publishing nothing - and starts the publisher.
    Every check runs again at execution, whatever a preview showed. A failed
    apply or verify never starts the publisher. Re-running is safe: what is in
    place is left alone. With --dry-run nothing is saved or changed.

    A new attachment needs --job and --stack-id; a saved one is reused by name.
    Replaces the stage commands plan, validate, status, apply, verify and start.
    """
    debug = _debug_requested(ctx)
    out = Output(as_json, debug)
    out.doc = {"attachment": name, "dry_run": dry_run}
    refuse_legacy(out, "job attach", name, ctx.args)
    began = time.monotonic()
    given = workflow.Given(
        job=job, bundle=bundle, stack_id=stack_id, host=host, discovery=discovery,
        tickerplant=tickerplant, consumers=tuple(consumer or ()), user=user,
        password_env=password_env, runtime_only=runtime_only,
        remote_schema_file=remote_schema_file, tplogdir=tplogdir,
        schema_file=schema_file, ssh=ssh,
    )  # fmt: skip
    try:
        paths = _paths()
        resolved = workflow.resolve(
            name, given, paths.repo_root, replace_saved=replace, runtime=paths.runtime
        )
        out.secrets = secrets_of(resolved.att)
        att = dc_replace(resolved.att, overrides=()) if clear_overrides else resolved.att
        resolved = dc_replace(resolved, att=att)
        ipc = service.ipc_for(att)
        att = service.accept(att, ipc, paths.repo_root, list(override or ()), override_reason)
        resolved = dc_replace(resolved, att=att)
        planned = service.make_plan(att, ipc, paths.repo_root)
        manifest = service.manifest_of(name)
    except UqsError as exc:
        out.fail(exc)
        return
    out.detail(
        f"configuration: {resolved.source} ({resolved.path})"
        + (f"; replaced {'; '.join(resolved.changed)}" if resolved.changed else "")
    )
    out.detail(
        f"resolved: stack {att.stack.id} on {att.stack.host}, discovery "
        f"{att.stack.discovery_host}:{att.stack.discovery_port}, storage {asdict(att.storage)}"
    )
    out.detail(f"preflight: {len(planned.findings)} finding(s) in {time.monotonic() - began:.3f}s")
    d = redact(planned.as_dict(manifest), out.secrets)
    running = workflow.already_running(att, planned)
    out.doc["plan"] = d
    render_plan(out, d)
    verdict(out, d, running)
    if dry_run:
        out.text("[dim]dry run: nothing was saved or changed. Run it again without --dry-run.[/]")
        out.finish(ok=planned.ok or running)
    if not planned.ok and not running:
        if clear_overrides and resolved.source != "new":
            service.save_planned(att, planned)
            out.text("[yellow]the accepted risks were withdrawn and saved[/]")
        out.fail(
            "refused: "
            + "; ".join(
                f"{f.code} ({f.component})" for f in planned.findings if f.level == "blocker"
            )  # fmt: skip
            + ". Nothing was changed; resolve the blockers above and run it again"
        )
    confirmed = ""
    if not running:
        if as_json and not yes:
            out.fail("--json cannot ask for confirmation: add --yes to change the stack")
        if not yes:
            typer.confirm(
                f"Attach {name} to stack {att.stack.id} as above? (no process is restarted)",
                abort=True,
            )
        if planned.accepted:
            if not accept_risks:
                if as_json:
                    out.fail(
                        f"{len(planned.accepted)} accepted risk(s) need --accept-risks; "
                        "--yes does not confirm them"
                    )
                typer.confirm(
                    f"Attach with {len(planned.accepted)} ACCEPTED RISK(S) "
                    "(" + ", ".join(f"{f.code}:{f.component}" for f in planned.accepted) + ")? "
                    "--yes does not answer this",
                    abort=True,
                )
            confirmed = getpass.getuser()
    outcome = workflow.execute(
        resolved, planned, ipc, paths.repo_root, publishing.UqsPublisher(paths),
        start=not no_start, confirmed_risks=confirmed,
    )  # fmt: skip
    stages = [asdict(s) | {"stage": s.name} for s in outcome.stages]
    for s in stages:
        s.pop("name")
    applied = (
        {"state": outcome.applied.state, "steps": [asdict(s) for s in outcome.applied.steps]}
        if outcome.applied
        else None
    )
    verified = (
        {
            "ok": outcome.verified.ok,
            "state": outcome.verified.state,
            "checks": [asdict(c) for c in outcome.verified.checks],
            "warnings": outcome.verified.warnings,
        }
        if outcome.verified
        else None
    )
    out.doc |= {"stages": stages, "apply": applied, "verify": verified}
    if applied:
        steps_table(out, "Apply", redact(applied["steps"], out.secrets))
    if verified and (debug or not verified["ok"]):
        checks_table(out, redact(verified["checks"], out.secrets))
    stages_table(out, redact(stages, out.secrets))
    out.detail(f"total {time.monotonic() - began:.3f}s")
    if outcome.ok:
        last = outcome.stages[-1]
        out.text(f"[green]attached[/]: {name} is verified; {last.detail}")
        out.finish(ok=True, recovery=[])
    for line in outcome.recovery:
        out.text(f"[yellow]->[/] {line}")
    failed = next(s for s in outcome.stages if s.status == "failed")
    message = redact(f"attach {name}: {failed.name} failed - {failed.detail}", out.secrets)
    shared.log.error("{}", message)
    out.finish(ok=False, recovery=outcome.recovery, error=message)


# ----------------------------------------------------------------- uqs job detach


@job_app.command("detach", rich_help_panel=JOB_OPERATIONS_PANEL, context_settings=EXTRA_WORDS)
def detach(
    ctx: typer.Context,
    name: NameArg,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Run every check, including each process's own removal checks, and change "
            "nothing: the publisher is not stopped and no file is written",
        ),
    ] = False,
    yes: YesOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Stop an attachment's publisher and reverse what it created, if that is safe.

    Before any publication, the empty state the attachment created is removed
    (each process re-checks that it is empty and its own). After publication
    nothing is deleted: the publisher is stopped and every registration, row,
    log and the schema block stay, so replay and restart keep working. If the
    stack drifted or ownership cannot be shown, nothing is removed and what a
    person has to do is listed.
    """
    out = Output(as_json, _debug_requested(ctx))
    out.doc = {"attachment": name, "dry_run": dry_run}
    refuse_legacy(out, "job detach", name, ctx.args)
    began = time.monotonic()
    try:
        att = config.load(name)
        out.secrets = secrets_of(att)
        if not dry_run:
            if as_json and not yes:
                out.fail("--json cannot ask for confirmation: add --yes to change the stack")
            if not yes:
                typer.confirm(
                    f"Stop {att.publisher.procname} and detach {name} from stack {att.stack.id}?",
                    abort=True,
                )
        result = detaching.detach(
            att, service.ipc_for(att), publishing.UqsPublisher(_paths()), dry_run=dry_run
        )
    except UqsError as exc:
        out.fail(exc)
        return
    steps = [asdict(s) for s in result.steps]
    out.doc |= {
        "outcome": result.outcome,
        "publisher_stopped": result.publisher_stopped,
        "steps": steps,
        "published": result.evidence.reasons + result.evidence.unknown,
        "retained": result.retained,
        "blockers": [asdict(b) for b in result.blockers],
    }
    steps_table(out, "Steps", redact(steps, out.secrets))
    for reason in result.evidence.reasons + result.evidence.unknown:
        out.text(f"[yellow]published:[/] {reason}")
    for line in result.retained:
        out.text(f"[cyan]kept:[/] {line}")
    for b in result.blockers:
        out.text(f"[red]refused[/] {b.component}: {b.reason}\n  -> {b.action}")
    out.detail(f"total {time.monotonic() - began:.3f}s")
    out.text(f"{name}: {result.outcome}" + (" (dry run: nothing was changed)" if dry_run else ""))
    out.finish(ok=result.outcome != "refused")
