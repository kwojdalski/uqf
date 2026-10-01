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
from uqs.cli.shared import ExportOpt, _die, _export, _paths, app, console, log
from uqs.model.registry import DEFAULT_BASE_PORT
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
                'Without it, `uqs query --proc <name> "<expr>"` still works over IPC.'
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


#: The hosts `--proc` can resolve against: this machine's.
_LOCAL_HOSTS = ("localhost", "127.0.0.1")


def _proc_port(procname: str, base_port: int) -> int:
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
    except Exception as exc:  # noqa: BLE001 - see comment above
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
    host: str, port: int, user: str, passwd: str, servers: str, render: str
) -> None:
    """The gateway's interactive session: every line through .gw.syncexec."""
    types = "".join("`" + t for t in servers.split())
    console.print(
        f"gateway1 at {host}:{port} - each line runs as .gw.syncexec[...;{types}]. "
        "\\\\ or Ctrl-D to leave; uqs query --raw for plain qcon.",
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
        _die(UqsError(f"could not open a session on gateway1 at {host}:{port}: {exc}"))


@app.command()
def query(
    expr: Annotated[
        str | None,
        typer.Argument(
            help='q expression, e.g. "select count i by sym from quote"; omit it for an '
            "interactive session on the process"
        ),
    ] = None,
    proc: Annotated[
        str | None,
        typer.Option(
            help=f"process to query, by name, e.g. rdb1 (default: {stack_gateway.DEFAULT_PROC})",
            autocompletion=completion.procname,
        ),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option(
            help="port to query directly, instead of naming --proc",
            autocompletion=completion.process_ports,
        ),
    ] = None,
    base_port: Annotated[
        int, typer.Option(help="stack base port --proc is resolved against")
    ] = DEFAULT_BASE_PORT,
    host: str = "localhost",
    user: str = "admin",
    passwd: str = "admin",
    servers: Annotated[
        str,
        typer.Option(help="with the gateway: the process types an expression is routed to"),
    ] = stack_gateway.DEFAULT_SERVERS,
    raw: Annotated[
        bool,
        typer.Option(
            "--raw",
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
) -> None:
    """Run a q expression against a running process - or, with no expression,
    open an interactive session on it.

    Name the process with `--proc rdb1`, or give its `--port`. With neither it
    asks the gateway, gateway1 - the fleet's front door, and the one process
    whose answer does not depend on which part of the day the data is in.

    On the gateway every expression is routed for you - it runs as
    `.gw.syncexec[expr;`rdb`hdb]`, across today's rows and history, and
    `--servers` names other process types - and so is every line of its
    interactive session. A `.gw.*` call or a `\\` command is sent as typed; an
    in-place `update`/`delete ... from `t` is refused (it would change live
    data); `--raw` sends anything as typed. Any other process gets qcon.

    Results print as q's console prints them; `--render kola` shows the
    Python objects kola makes of them instead. `--export` always writes the
    data itself.
    """
    try:
        render = stack_render.renderer(render)
    except UqsError as exc:
        _die(exc)
        return
    if proc is not None and port is not None:
        _die(UqsError("name the process to query once: --proc NAME or --port N, not both"))
        return
    if proc is None and port is None:
        proc = stack_gateway.DEFAULT_PROC
    routed = proc == stack_gateway.DEFAULT_PROC and not raw
    if proc is not None and host not in _LOCAL_HOSTS:
        # --proc reads this machine's registry and process list, so on another
        # host both the port and the up/down check would describe the wrong one.
        _die(
            UqsError(
                "--proc looks the port up in this machine's stack - give --port for another host"
            )
        )
        return
    if port is None:
        try:
            port = _proc_port(str(proc), base_port)
        except UqsError as exc:
            _die(exc)
            return
        log.debug("query {} -> {}:{}", proc, host, port)
    if expr is None:
        if export is not None:
            _die(UqsError("--export needs an expression whose result it can write"))
            return
        if routed:
            _gateway_session(host, port, user, passwd, servers, render)
            return
        _exec_qcon(host, port, user, passwd)
        return
    if routed:
        try:
            expr = stack_gateway.expression(expr, servers)
        except UqsError as exc:
            _die(exc)
            return
        log.debug("routed through the gateway: {}", expr)
    # --export needs the data, not its picture, so it always takes kola's.
    size = stack_render.console_size() if render == stack_render.Q and export is None else None
    try:
        result = runtime.query(expr, port, host=host, user=user, passwd=passwd, render=size)
    except Exception as exc:  # kola raises its own exception types on connect/query failure
        log.error("query failed: {}", exc)
        raise typer.Exit(code=1) from exc
    if size is not None:
        _show_q(result)
        return
    console.print(result)
    _export(result, export)
