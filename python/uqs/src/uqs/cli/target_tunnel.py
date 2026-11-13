"""`uqs target tunnel plan` and `uqs target tunnel check`: a target's rdb,
hdb, wdb and gateway forwarded to this machine (#2030, #2031).

`plan` prints the mappings - each process, its proctype, its production endpoint
and the local one - and the one ssh command that opens them all, with no
credential in it. `check` proves each mapping layer by layer and names the
layer that failed. Both are deploy/tunnel_plan.py's and tunnel_check.py's,
the same functions the frontend's Services view calls; this is only their
command line.
"""

from __future__ import annotations

import os
from typing import Annotated, Any

import typer
from rich.table import Table

from uqs import runtimes
from uqs.cli.concepts import TargetArg
from uqs.cli.json_out import emit, json_opt
from uqs.cli.q_login import DEFAULT_USER, QPasswordEnvOpt, QUserOpt, q_password
from uqs.cli.safety import ReadOnly
from uqs.cli.shared import EXIT_REFUSED, _die, console
from uqs.cli.target import target_app
from uqs.deploy import tunnel_check, tunnel_plan
from uqs.paths import UqsError, repo_root

tunnel_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Tunnels to a target's processes: plan, check.\n\nThe rdb, hdb, wdb and gateway a "
    "target's release declares, forwarded to this machine over ssh.",
)
target_app.add_typer(tunnel_app, name="tunnel")

Procs = Annotated[
    list[str] | None,
    typer.Argument(
        metavar="PROC...",
        help="Processes to forward (default: every rdb, hdb, wdb and gateway it declares)",
        show_default=False,
    ),
]
Local = Annotated[
    list[str] | None,
    typer.Option(
        "--local",
        metavar="PROC:PORT",
        help="Forward PROC to this local port instead of its remote port plus the offset; "
        "repeatable",
    ),
]
Offset = Annotated[
    int,
    typer.Option(
        "--local-offset",
        help="Added to each remote port to give its local port, unless --local names one",
    ),
]
PlanJson = json_opt(
    '{"target", "ssh", "release", "mappings": [{process, proctype, remote_host, remote_port, '
    "local_host, local_port, production_endpoint, local_endpoint, query_command, state, "
    'last_success, listening}], "launch_command", "launch_argv", "available"}'
)
CheckJson = json_opt(
    '{"ok", "target", "ssh", "release", "checked_at", "category", "error", "launch_command", '
    '"endpoints": [{process, proctype, production_endpoint, local_endpoint, category, error, '
    'layers, connected, reported_process, gateway_servers, last_success}], "categories"}'
)


def _runtime() -> str | None:
    return os.environ.get(runtimes.RUNTIME_ENV) or None


@tunnel_app.command("plan", cls=ReadOnly)
def plan(
    name: TargetArg,
    procs: Procs = None,
    local: Local = None,
    offset: Offset = tunnel_plan.LOCAL_OFFSET,
    as_json: PlanJson = False,
) -> None:
    """Show which processes a tunnel to TARGET forwards, and the command that opens it.

    Processes and ports are read from the target's own release over ssh;
    nothing is forwarded. The launch command holds no credential: run it
    yourself, then `uqs target tunnel check` proves each mapping."""
    root = repo_root()
    try:
        p = tunnel_plan.for_target(
            name, root, procs or (), tunnel_plan.parse_local(local or ()), offset,
            runtime=_runtime(),
        )  # fmt: skip
    except UqsError as exc:
        _die(exc)
    doc = tunnel_plan.annotate(p, tunnel_plan.read_checks(root).get(p.target, {}))
    if as_json:
        emit(doc)
        return
    table = Table(title=f"tunnel plan for {p.target} ({p.ssh})")
    for column in ("process", "proctype", "production", "local", "state", "last success"):
        table.add_column(column)
    for m in doc["mappings"]:
        last = (m["last_success"] or {}).get("at") or "-"
        table.add_row(m["process"], m["proctype"], m["production_endpoint"], m["local_endpoint"],
                      m["state"], last)  # fmt: skip
    console.print(table)
    console.print("open it with:")
    console.print(p.launch_command, markup=False, highlight=False, soft_wrap=True)
    if p.available:
        console.print(f"also forwardable: {', '.join(p.available)}")


@tunnel_app.command("check", cls=ReadOnly)
def check(
    name: TargetArg,
    procs: Procs = None,
    local: Local = None,
    offset: Offset = tunnel_plan.LOCAL_OFFSET,
    user: QUserOpt = DEFAULT_USER,
    password_env: QPasswordEnvOpt = None,
    as_json: CheckJson = False,
) -> None:
    """Check each mapping of TARGET's tunnel plan, and name the layer that fails.

    ssh, the remote port, the q login, the process's identity, a gateway's
    servers, then the local port - each endpoint separately, so one failure
    hides no other. A mapping that passes is recorded with when, for the
    plan's `last success`. Exits non-zero when any endpoint fails."""
    try:
        passwd = q_password(password_env)
        pairs = tunnel_plan.parse_local(local or ())
        report = tunnel_check.run(name, repo_root(), procs or (), pairs, offset,
                                  user=user, passwd=passwd, runtime_name=_runtime())  # fmt: skip
    except UqsError as exc:
        _die(exc)
    if as_json:
        emit(report)
    else:
        _show(report)
    if not report["ok"]:
        raise typer.Exit(code=EXIT_REFUSED)


def _show(report: dict[str, Any]) -> None:
    where = f"{report['target']} ({report['ssh']})"
    if report["error"]:
        console.print(f"[red]✗[/] {where}: {report['category']}: {report['error']}",
                      highlight=False)  # fmt: skip
        return
    console.print(f"{where} runs release {report['release']}")
    table = Table()
    for column in ("process", "production", "local", "status", "reported", "last success"):
        table.add_column(column)
    for e in report["endpoints"]:
        status = "[green]ok[/]" if e["category"] is None else f"[red]{e['category']}[/]"
        if e["connected"]:
            status += " (connected)"
        last = (e["last_success"] or {}).get("at") or "never"
        table.add_row(
            e["process"], e["production_endpoint"], e["local_endpoint"], status,
            e["reported_process"] or "-", last,
        )  # fmt: skip
    console.print(table)
    for e in report["endpoints"]:
        if e["error"]:
            console.print(f"  {e['process']}: {e['error']}", markup=False, highlight=False)
