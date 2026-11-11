"""`uqs job attachment add|remove|show NAME`: a job on a TorQ stack that
already exists (#1424, #1457, #1737).

One command per direction. `add` resolves the configuration, runs the
preflight, shows the plan, asks, then saves, applies, verifies and starts the
publisher - each stage the existing one in uqs.attach, run in order by
workflow.execute; `--dry-run` stops after the plan and changes nothing.
`remove` stops the publisher and reverses what is safe to reverse. `show`
(attachment_show.py) is one attachment; the inventory is `uqs job attachment
list` (stack/attach_state.py).

`add` and `remove` were `uqs job attach` and `uqs job detach`, verbs whose
noun was spelled three other ways (`list attachments`, `--attachment`, `job
contract`); attachment is the noun now (#1737), and the old spellings stay for
one release, hidden, each logging the new one.

Every string printed here passes through `redact` first: the plan, the stage
results, the errors and the debug detail, in tables and in `--json`.
"""

from __future__ import annotations

import getpass
import time
from dataclasses import asdict
from dataclasses import replace as dc_replace
from typing import Annotated

import typer
from rich.markup import escape

from uqs.attach import config, service, workflow
from uqs.attach import detach as detaching
from uqs.attach import publisher as publishing
from uqs.cli import attach_launch, attach_next, safety, shared
from uqs.cli import attach_options as o
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
from uqs.cli.attachment import OLD_REASON, attachment_app
from uqs.cli.concepts import AttachmentArg
from uqs.cli.json_out import json_opt
from uqs.cli.launch_options import FollowOpt, LevelOpt, StopOnExitOpt, TraceOpt
from uqs.cli.shared import _debug_requested, _paths, log
from uqs.cli.stop_options import KillOpt
from uqs.guardrails import spelled
from uqs.paths import UqsError

NameArg = AttachmentArg
#: Extra words are taken, not rejected by the parser, so the old `uqs job attach
#: <stage> NAME` is refused with the command that replaced it.
EXTRA_WORDS = {"allow_extra_args": True}
JsonOpt = json_opt('{"plan", "stages", "verdict", ...}', "Executing needs --yes: it cannot ask")
DryRunOpt = Annotated[
    bool, typer.Option("--dry-run", "-n", help="Run every check and show the plan; change nothing")
]
YesOpt = Annotated[
    bool, typer.Option("--yes", "-y", help="Do not ask first; the plan and results are still shown")
]


# ------------------------------------------------------- uqs job attachment add


def waivers(override: list[str] | None, reason: str) -> list[str]:
    """The `--override NAME:COMPONENT`s and the generated `--skip-<name>
    COMPONENT`s (`--skip-torq-version stp1` is `TORQ_VERSION:stp1`, #1918), as
    one list, checked as every command's are (safety.given, #1852): a code this
    command does not take, one that is never overridden, a missing component or
    reason, or a reason with no override, is refused here. A name is matched as
    the attachment's findings name it - `torq-version` is `TORQ_VERSION`, and the
    code's own spelling still parses for one release, warning (#1884).
    service.accept then checks each against what the stack shows, so a component
    with no such finding is refused there."""
    specs = []
    for c, sep, rest in (s.partition(":") for s in override or ()):
        if c != spelled(c):
            log.warning(
                "--override {}: spell it {} - the finding-code spelling goes in the next "
                "release (#1884)",
                c,
                spelled(c),
            )
        specs.append(f"{c.upper().replace('-', '_')}{sep}{rest}")
    return list(safety.given("job attachment add", specs or None, reason or None))


def _refuse_extra(out: Output, command: str, extra: list[str]) -> None:
    """Refuse words after NAME: the command takes one attachment name."""
    if extra:
        out.fail(f"unexpected argument(s) {extra}: `uqs {command}` takes one attachment name")


@attachment_app.command("add", context_settings=EXTRA_WORDS, cls=OLD_REASON)
def attach(
    ctx: typer.Context,
    name: NameArg,
    job: o.JobOpt = None,
    bundle: o.BundleOpt = None,
    stack_id: o.BasePortOpt = None,
    host: o.HostOpt = None,
    discovery: o.DiscoveryOpt = None,
    tickerplant: o.TickerplantOpt = None,
    consumer: o.ConsumerOpt = None,
    user: o.UserOpt = None,
    password_env: o.PasswordEnvOpt = None,
    runtime_only: o.TransientOpt = False,
    schema_writer: o.SchemaWriterOpt = None,
    remote_schema_file: o.RetiredRemoteSchemaOpt = False,
    process_file: o.ProcessFileOpt = None,
    start_with_all: o.StartWithAllOpt = None,
    tplogdir: o.TplogdirOpt = "",
    schema_file: o.SchemaFileOpt = "",
    ssh: o.SshOpt = None,
    replace: o.ReplaceOpt = False,
    override: o.OverrideOpt = None,
    override_reason: o.ReasonOpt = "",
    accept_risks: o.AcceptRisksOpt = False,
    clear_overrides: o.ClearOverridesOpt = False,
    dry_run: DryRunOpt = False,
    start: o.StartOpt = True,
    follow: FollowOpt = False,
    stop_on_exit: StopOnExitOpt = False,
    level: LevelOpt = None,
    trace: TraceOpt = False,
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

    With --process-file the publisher is also registered in the stack's own
    TorQ process file, after verify, so a native torq.sh start runs it through
    the same start gate; --start-with-all puts it in `start all`.

    The publisher it starts takes `uqs start`'s launch options, meaning what
    they mean there: --trace starts it with -trace, --follow (-f) streams its
    log until Ctrl-C (--level filters it), and --stop-on-exit stops it then.
    None applies with --no-start.

    A new attachment needs --job and --base-port; a saved one is reused by name.
    Replaces the stage commands plan, validate, status, apply, verify and start.
    """
    debug = _debug_requested(ctx)
    out = Output(as_json, debug)
    out.doc = {"attachment": name, "dry_run": dry_run}
    _refuse_extra(out, "job attachment add", ctx.args)
    refused = attach_launch.refusal(
        start=start, follow=follow, level=level, stop_on_exit=stop_on_exit, trace=trace,
        as_json=as_json, dry_run=dry_run,
    )  # fmt: skip
    if refused is not None:
        out.fail(refused, code=shared.EXIT_USAGE)
    if remote_schema_file:
        out.fail(o.REMOTE_SCHEMA_RETIRED)
    began = time.monotonic()
    given = workflow.Given(
        job=job, bundle=bundle, stack_id=stack_id, host=host, discovery=discovery,
        tickerplant=tickerplant, consumers=tuple(consumer or ()), user=user,
        password_env=password_env, runtime_only=runtime_only,
        schema_writer=schema_writer.value if schema_writer else None, tplogdir=tplogdir,
        schema_file=schema_file, ssh=ssh, process_file=process_file, start_with_all=start_with_all,
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
        specs = waivers(override, override_reason)
        att = service.accept(att, ipc, paths.repo_root, specs, override_reason)
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
        out.text(safety.dry_run_note())
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
        try:
            safety.json_needs_yes(as_json, yes, "change the stack")
            safety.confirm(
                f"Attach {name} to stack {att.stack.id} as above? (no process is restarted)",
                yes=yes,
            )
        except UqsError as exc:
            out.fail(exc)
        if planned.accepted:
            if not accept_risks:
                if as_json:
                    out.fail(
                        f"{len(planned.accepted)} accepted risk(s) need --accept-risks; "
                        "--yes does not confirm them"
                    )
                try:
                    safety.confirm(
                        f"Attach with {len(planned.accepted)} ACCEPTED RISK(S) "
                        "(" + ", ".join(f"{f.code}:{f.component}" for f in planned.accepted) + ")? "
                        "--yes does not answer this",
                        yes=False,
                    )
                except UqsError as exc:
                    out.fail(exc)
            confirmed = getpass.getuser()
    publisher = publishing.UqsPublisher(paths, trace=trace)
    attach_launch.run(
        att, publisher,
        lambda: _execute(out, name, resolved, planned, ipc, paths, publisher, start,
                         confirmed, began, follow),
        follow=follow, level=level, stop_on_exit=stop_on_exit, trace=trace, as_json=as_json,
    )  # fmt: skip


def _execute(
    out, name, resolved, planned, ipc, paths, publisher, start, confirmed, began, follow
) -> None:
    """Save, apply, verify and start, then report each stage and exit: `add`'s execution.
    A success that is not followed ends with what to run next (attach_next.py): the
    follow and query commands, or with --no-start the query alone."""
    debug = out.debug
    outcome = workflow.execute(
        resolved, planned, ipc, paths.repo_root, publisher,
        start=start, confirmed_risks=confirmed,
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
        if not follow:
            nxt = attach_next.commands(resolved.att, attach_next.rdb_of(planned.observation))
            if not start:
                nxt.pop("follow")
            out.doc["next"] = nxt
            for line in attach_next.lines(nxt, follow=start):
                out.text(escape(line))
        out.finish(ok=True, recovery=[])
    for line in outcome.recovery:
        out.text(f"[yellow]->[/] {line}")
    failed = next(s for s in outcome.stages if s.status == "failed")
    message = redact(f"attach {name}: {failed.name} failed - {failed.detail}", out.secrets)
    shared.log.error("{}", message)
    out.finish(ok=False, recovery=outcome.recovery, error=message)


# ---------------------------------------------------- uqs job attachment remove


@attachment_app.command("remove", context_settings=EXTRA_WORDS, cls=safety.ChangesState)
def detach(
    ctx: typer.Context,
    name: NameArg,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            "-n",
            help="Run every check, including each process's own removal checks, and change "
            "nothing: the publisher is not stopped and no file is written",
        ),
    ] = False,
    yes: YesOpt = False,
    as_json: JsonOpt = False,
    kill: KillOpt = False,
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
    _refuse_extra(out, "job attachment remove", ctx.args)
    began = time.monotonic()
    try:
        att = config.load(name)
        out.secrets = secrets_of(att)
        if not dry_run:
            safety.json_needs_yes(as_json, yes, "change the stack")
            safety.confirm(
                f"Stop {att.publisher.procname} and detach {name} from stack {att.stack.id}?",
                yes=yes,
            )
        result = detaching.detach(
            att, service.ipc_for(att), publishing.UqsPublisher(_paths(), kill=kill), dry_run=dry_run
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
    out.text(f"{name}: {result.outcome}")
    if dry_run:
        out.text(safety.dry_run_note())
    out.finish(ok=result.outcome != "refused")
