"""`uqs query`: one q expression, or an interactive session, on a process.

Split out of cli/inspect.py when that file passed the 400-line budget adding
`--render`, the choice between q's own printing and kola's Python objects.
The line it was split on is the transport: everything here opens a
connection to a running process - kola, qcon or the gateway's session - while
what stays in inspect.py reads files or asks one fixed question.

Imported immediately before `inspect` in cli/entry.py, which keeps `--help`
listing `query` exactly where it was before the split.
"""

from __future__ import annotations

import os
import shutil
from typing import Annotated

import typer

from uqs.cli import completion
from uqs.cli import query_target as via_target
from uqs.cli.json_out import emit, json_opt
from uqs.cli.renames import renamed
from uqs.cli.shared import (
    ExportOpt,
    ProcArg,
    QHostOpt,
    QPasswdOpt,
    QUserOpt,
    _die,
    _export,
    _paths,
    _proc_first,
    app,
    console,
    log,
)
from uqs.paths import UqsError
from uqs.stack import alive, listing, runtime
from uqs.stack import gateway as stack_gateway
from uqs.stack import render as stack_render


def _exec_qcon(host: str, port: int, user: str, passwd: str) -> None:
    """Replace this process with an interactive qcon session on host:port.

    Returns only on a refusal (qcon not installed, or it would not start),
    after `_die` has already exited - so a caller's `return` after it is what
    stops a call that did return from falling through.
    """
    if shutil.which("qcon") is None:
        _die(
            UqsError(
                "qcon is not on PATH. It ships with kdb+ rather than with this "
                "repository (macOS: it is beside q in your KDB-X install). "
                'Without it, `uqs query <name> "<expr>"` still works over IPC.'
            )
        )
        return
    argv = runtime.qcon_command(host, port, user, passwd, rlwrap=shutil.which("rlwrap") is not None)
    log.debug("exec: {}", " ".join(argv))
    # execvp, not subprocess: qcon owns the terminal from here, and replacing
    # this process rather than wrapping it is what makes Ctrl-C, Ctrl-D and
    # the exit code behave as they would if you had typed `qcon` yourself.
    try:
        os.execvp(argv[0], argv)
    except OSError as exc:
        # `which` found it a moment ago, so this is a race or a broken binary -
        # either way a message beats a traceback.
        _die(UqsError(f"could not start {argv[0]}: {exc}"))


#: The hosts PROC can resolve against: this machine's.
_LOCAL_HOSTS = ("localhost", "127.0.0.1")


def _proc_port(procname: str, base_port: int | None) -> int:
    """The port `procname` listens on in the stack at `base_port`, refusing a
    process that is not declared or not running.

    A process that is down is refused with how to start it, rather than left
    to qcon's or kola's bare connection refusal, which reads the same as a
    wrong port.
    """
    paths = _paths()
    ports = listing.configured_ports(paths, base_port=base_port)
    if procname not in ports:
        raise UqsError(
            f"{procname} is not a declared process - `uqs list processes` shows them all"
        )
    # Advisory: a check that cannot answer does not stop the call, since the
    # connection will say for itself whether anything is listening.
    try:
        up = procname in alive.running(paths, base_port=base_port)
    except (UqsError, OSError) as exc:  # ps failed or timed out, or is missing
        log.debug("could not tell whether {} is running: {}", procname, exc)
        up = True
    if not up:
        raise UqsError(f"{procname} is not running - start it with `uqs start {procname}`")
    return int(ports[procname])


def _show_q(text: str) -> None:
    """Print what q's console would, exactly: no markup, no re-wrapping, and
    nothing at all for a result the console shows as nothing."""
    if text:
        typer.echo(text, nl=not text.endswith("\n"))


def _gateway_session(
    host: str,
    port: int,
    user: str,
    passwd: str,
    servers: str,
    render: str,
    via: via_target.Via | None = None,
) -> None:
    """The gateway's interactive session: every line through .gw.syncexec."""
    types = "".join("`" + t for t in servers.split())
    where = f"{host}:{port}" if via is None else via.where
    console.print(
        f"gateway1 at {where} - each line runs as .gw.syncexec[...;{types}]. "
        "\\\\ or Ctrl-D to leave; uqs query --no-gateway for plain qcon.",
        markup=False,
        style="dim",
    )
    try:
        stack_gateway.query_session(
            host,
            port,
            user,
            passwd,
            show=_show_q if render == stack_render.Q else console.print,
            fail=lambda msg: console.print(msg, markup=False, style="red"),
            servers=servers,
            render=stack_render.console_size if render == stack_render.Q else None,
        )
    except Exception as exc:  # kola's own exception types on connect
        said = str(exc) if via is None else via.scrub(exc)
        _die(UqsError(f"could not open a session on gateway1 at {where}: {said}"))


QueryJson = json_opt(
    '{"expression", "result"} - a table as [{column: value}], a dictionary as an object, '
    "a list as an array, a time as ISO text. Needs an expression"
)


@app.command(cls=renamed({"--raw": "--no-gateway", "--target": "--tunnel", "--proc": "PROC"}))
def query(
    ctx: typer.Context,
    proc: ProcArg = None,
    expr: Annotated[
        str | None,
        typer.Argument(
            help='q expression, e.g. "select count i by sym from quote"; omit it for an '
            "interactive session on the process"
        ),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option(
            help="port to query directly, instead of naming PROC",
            autocompletion=completion.process_ports,
        ),
    ] = None,
    base_port: Annotated[
        int | None,
        typer.Option(help="stack base port PROC is resolved against (default: the runtime's)"),
    ] = None,
    host: QHostOpt = "localhost",
    target: Annotated[
        str | None,
        typer.Option(
            "--tunnel",
            metavar="TARGET",
            help="deployment target (deploy_targets.toml) to query from here, through an "
            "ssh tunnel opened for the call; ports are the target's release's own",
        ),
    ] = None,
    user: QUserOpt = "admin",
    passwd: QPasswdOpt = "admin",
    servers: Annotated[
        str,
        typer.Option(help="with the gateway: the process types an expression is routed to"),
    ] = stack_gateway.DEFAULT_SERVERS,
    raw: Annotated[
        bool,
        typer.Option(
            "--no-gateway",
            help="with the gateway: send as typed, not through .gw.syncexec "
            "(and, with no expression, open plain qcon)",
        ),
    ] = False,
    render: Annotated[
        str | None,
        typer.Option(
            help="q: show results as q prints them (the default); kola: as Python "
            f"objects. Default from ${stack_render.RENDER_ENV}",
        ),
    ] = None,
    export: ExportOpt = None,
    as_json: QueryJson = False,
) -> None:
    """Run a q expression against a running process - or, with no expression,
    open an interactive session on it.

    Name the process first, `uqs query rdb1 'count quote'`, or give its
    `--port`. A lone word that names a process is PROC; any other is the
    expression. With neither it
    asks the gateway, gateway1 - the fleet's front door, and the one process
    whose answer does not depend on which part of the day the data is in.

    On the gateway every expression is routed for you - it runs as
    `.gw.syncexec[expr;`rdb`hdb]`, across today's rows and history, and
    `--servers` names other process types - and so is every line of its
    interactive session. A `.gw.*` call or a `\\` command is sent as typed; an
    in-place `update`/`delete ... from `t` is refused (it would change live
    data); `--no-gateway` sends anything as typed. Any other process gets qcon.

    Results print as q's console prints them; `--render kola` shows the
    Python objects kola makes of them instead. `--export` always writes the
    data itself.

    `--tunnel TARGET` asks a deployed target instead, from this machine: an
    ssh tunnel to it opens on a free loopback port for the call and closes
    after, and PROC (or the gateway) is resolved to a port by the
    target's own release. `--port` is then a port on the target. `uqs
    --target TARGET query` runs the whole command on the target instead; with
    --tunnel, `--export` writes here.
    """
    try:
        render = stack_render.renderer(render)
        proc, expr = _proc_first(ctx, proc, expr, port is not None)
    except UqsError as exc:
        _die(exc)
        return
    if proc is not None and port is not None:
        _die(UqsError("name the process to query once: PROC or --port N, not both"))
        return
    if as_json and expr is None:
        _die(UqsError("--json needs an expression whose result it can print"))
        return
    if proc is None and port is None:
        proc = stack_gateway.DEFAULT_PROC
    routed = proc == stack_gateway.DEFAULT_PROC and not raw
    if target is not None:
        if getattr(ctx.get_parameter_source("host"), "name", None) != "DEFAULT":
            _die(
                UqsError("--tunnel names the machine to query - give --tunnel or --host, not both")
            )
            return
        try:
            with via_target.connect(target, proc, port, base_port, user, passwd) as via:
                _ask(expr, routed, via_target.LOOPBACK, via.port, user, passwd,
                     servers, render, export, via, as_json=as_json)  # fmt: skip
        except UqsError as exc:
            _die(exc)
        return
    if proc is not None and host not in _LOCAL_HOSTS:
        # PROC reads this machine's registry and process list, so on another
        # host both the port and the up/down check would describe the wrong one.
        _die(UqsError("PROC is looked up in this machine's stack - give --port for another host"))
        return
    if port is None:
        try:
            port = _proc_port(str(proc), base_port)
        except UqsError as exc:
            _die(exc)
            return
        log.debug("query {} -> {}:{}", proc, host, port)
    _ask(expr, routed, host, port, user, passwd, servers, render, export, as_json=as_json)


def _ask(
    expr: str | None,
    routed: bool,
    host: str,
    port: int,
    user: str,
    passwd: str,
    servers: str,
    render: str,
    export,
    via: via_target.Via | None = None,
    *,
    as_json: bool = False,
) -> None:
    """Run `expr` - or, with none, a session - on host:port: the gateway's
    routing when `routed`. `via` is the target reached through a tunnel."""
    if expr is None:
        if export is not None:
            _die(UqsError("--export needs an expression whose result it can write"))
            return
        if routed:
            _gateway_session(host, port, user, passwd, servers, render, via)
            return
        if via is not None:
            via_target.run_qcon(host, port, user, passwd)
            return
        _exec_qcon(host, port, user, passwd)
        return
    # --export and --json need the data, not its picture, so they take kola's.
    picture = render == stack_render.Q and export is None and not as_json
    size = stack_render.console_size() if picture else None
    # Who lays the answer out: runtime.query wraps it for a process asked
    # directly, while a routed expression carries its own layout - the
    # gateway's deferred reply skips any wrapper (stack/gateway.expression).
    wrap_size, shown = size, expr
    if routed:
        try:
            expr = stack_gateway.expression(expr, servers, size)
        except UqsError as exc:
            _die(exc)
            return
        wrap_size = None
        log.debug("routed through the gateway: {}", expr)
    try:
        result = runtime.query(expr, port, host=host, user=user, passwd=passwd, render=wrap_size)
    except Exception as exc:  # kola raises its own exception types on connect/query failure
        if via is not None:
            _die(via.failed(exc))
            return
        _die(UqsError(f"query failed: {exc}"))
        return
    if as_json:
        _export(result, export)  # first: a result it cannot write is refused alone
        emit({"expression": shown, "result": result})
        return
    if size is not None:
        _show_q(stack_render.text(result))
        return
    console.print(result)
    _export(result, export)
