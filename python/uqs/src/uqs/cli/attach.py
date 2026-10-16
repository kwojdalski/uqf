"""`uqs job attach ...`: attaching a job to a TorQ stack that already exists (#1424).

plan, validate, status here; apply, verify, start and detach beside them. The
unit is a JOB - its publisher process and the tables it publishes - so this
lives under `job`; `connector` is for external publishers that are not q
processes. Nothing here reads uqf's own stack defaults: the stack id, the
discovery address and the storage locations come from the attachment's
persisted configuration, written once by `plan`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from uqs.attach import config, service
from uqs.attach.findings import Finding
from uqs.attach.plan import Plan
from uqs.cli.shared import JOB_OPERATIONS_PANEL, _die, _paths, console, job_app
from uqs.paths import UqsError

attach_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Attach a job to an EXISTING TorQ stack, reversibly: plan and validate "
    "(read-only), then the commands that change it.",
)
job_app.add_typer(attach_app, name="attach", rich_help_panel=JOB_OPERATIONS_PANEL)

NameArg = Annotated[str, typer.Argument(help="The attachment's name, e.g. piggybank-uat")]
JsonOpt = Annotated[bool, typer.Option("--json", help="Print JSON instead of tables")]
_STYLE = {"blocker": "red", "warning": "yellow", "info": "dim"}


def _findings(findings: list[Finding]) -> None:
    if not findings:
        return
    table = Table(title="Findings")
    for column in ("level", "code", "component", "message"):
        table.add_column(column, overflow="fold")
    for f in sorted(findings, key=lambda f: ("blocker", "warning", "info").index(f.level)):
        action = f"\n[dim]-> {f.action}[/]" if f.action else ""
        table.add_row(f"[{_STYLE[f.level]}]{f.level}[/]", f.code, f.component, f.message + action)
    console.print(table)


def _render(p: Plan) -> None:
    d = p.as_dict()
    st = d["stack"]
    console.print(
        f"[bold]{d['attachment']}[/]: job {d['job']['name']} ({d['job']['procname']}) -> "
        f"stack {st['id']} on {st['host']}, discovery {st['discovery']}"
    )
    console.print(
        f"credential: {st['user']} / {st['password_env']}   persist: {d['persist']}   "
        f"publisher port: {d['publisher_port']}"
    )
    for t in d["tables"]:
        console.print(
            f"table {t['name']}: " + ", ".join(f"{n}:{c.strip() or '*'}" for n, c in t["columns"])
        )
    comps = Table(title="Affected components")
    for column in ("role", "procname", "endpoint", "up", "effect"):
        comps.add_column(column)
    for c in d["components"]:
        comps.add_row(c["role"], c["procname"], c["endpoint"], str(c["up"]), c["effect"])
    console.print(comps)
    steps = Table(title="Changes apply would make (no restart of any process)")
    for column in ("process", "action", "table", "before publication", "after publication"):
        steps.add_column(column, overflow="fold")
    for s in d["changes"]:
        steps.add_row(
            s["process"], s["action"], s["table"], s["reversible_before"], s["reversible_after"]
        )
    console.print(steps)
    _findings(p.findings)
    console.print(
        "[green]no blockers[/]" if p.ok else "[red]blocked: apply would refuse this stack[/]"
    )


def _load(name: str):
    try:
        return config.load(name)
    except UqsError as exc:
        _die(exc)
        raise


@attach_app.command("plan")
def plan(
    name: NameArg,
    job: Annotated[str | None, typer.Option("--job", help="The streaming job to attach")] = None,
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
        str, typer.Option("--host", help="Where the stack's processes listen")
    ] = "localhost",
    discovery: Annotated[
        str | None,
        typer.Option("--discovery", help="HOST:PORT of its discovery (default: HOST:<stack-id>+1)"),
    ] = None,
    tickerplant: Annotated[
        str, typer.Option("--tickerplant", help="Its tickerplant, if several")
    ] = "",
    consumer: Annotated[
        list[str] | None, typer.Option("--consumer", help="An rdb/wdb to attach (default: all)")
    ] = None,
    user: Annotated[str, typer.Option("--user")] = config.DEFAULT_USER,
    password_env: Annotated[
        str,
        typer.Option(
            "--password-env",
            help="Environment variable holding the password (default: TorQ's own demo credential)",
        ),
    ] = "",
    runtime_only: Annotated[
        bool,
        typer.Option(
            "--runtime-only",
            help="Accept that restarting the tickerplant drops the registration "
            "(default: add a marked block to its schema file)",
        ),
    ] = False,
    tplogdir: Annotated[
        str, typer.Option("--tplogdir", help="Pin the tickerplant log directory")
    ] = "",
    schema_file: Annotated[str, typer.Option("--schema-file", help="Pin the schema file")] = "",
    replace: Annotated[
        bool, typer.Option("--replace", help="Rebuild a saved attachment from these options")
    ] = False,
    no_save: Annotated[bool, typer.Option("--no-save", help="Do not write the config")] = False,
    as_json: JsonOpt = False,
) -> None:
    """Identify the stack, the job and every affected component; change nothing.

    Writes the attachment's configuration (stack id, discovery, storage) so the
    later commands never fall back to uqf defaults; the stack itself is only
    ever read.
    """
    try:
        paths = _paths()
        saved = config.directory(name) / config.CONFIG_FILE
        if saved.is_file() and not replace:
            if job or stack_id is not None:
                raise UqsError(
                    f"attachment {name} is already saved - plan it without --job/--stack-id, "
                    "or add --replace to rebuild it"
                )
            att = config.load(name)
        else:
            if not job or stack_id is None:
                raise UqsError("a new attachment needs --job and --stack-id")
            att = service.create(
                name,
                job,
                bundle,
                stack_id,
                paths.repo_root,
                host=host,
                discovery=discovery,
                tickerplant=tickerplant,
                consumers=tuple(consumer or ()),
                user=user,
                password_env=password_env,
                runtime_only=runtime_only,
                storage=config.Storage(tplogdir=tplogdir, schema_file=schema_file),
                runtime=paths.runtime,
            )
        planned = service.make_plan(att, service.ipc_for(att), paths.repo_root)
        where = None if no_save else service.save_planned(att, planned)
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        console.print_json(json.dumps(planned.as_dict()))
    else:
        _render(planned)
        if where:
            console.print(f"[dim]saved {where}[/]")


@attach_app.command("validate")
def validate(name: NameArg, as_json: JsonOpt = False) -> None:
    """Check the saved attachment against the stack now. Exit 1 on any blocker."""
    try:
        att = _load(name)
        planned = service.make_plan(att, service.ipc_for(att), _paths().repo_root)
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        console.print_json(
            json.dumps({"ok": planned.ok, "findings": [f.as_dict() for f in planned.findings]})
        )
    else:
        _findings(planned.findings)
    raise typer.Exit(code=0 if planned.ok else 1)


@attach_app.command("status")
def status(name: NameArg, as_json: JsonOpt = False) -> None:
    """The saved attachment, and what the stack reports for its tables now."""
    try:
        att = _load(name)
        planned = service.make_plan(att, service.ipc_for(att), _paths().repo_root)
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        console.print_json(json.dumps(planned.as_dict()))
    else:
        _render(planned)
