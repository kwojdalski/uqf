"""Starting, stopping and inspecting the fleet as processes.

The commands that drive torq.sh, plus the two advisory warnings that run
before a start. Split from cli/entry.py, which had grown to 1270 lines across
twenty-four commands - past the 400 this package holds its modules to, and
past the point where the file could be read in one sitting.

Commands register onto the shared `app` here rather than onto a sub-Typer,
so `uqf-stack start` stays `uqf-stack start`: the split is about where the
code lives, not about what anyone types.
"""

from __future__ import annotations

import typer

from torq_orchestrator import core
from torq_orchestrator.cli.shared import (
    PortOpt,
    ProcsArg,
    _die,
    _paths,
    _run_streaming,
    app,
    console,
    log,
)
from torq_orchestrator.model import dependencies


def _warn_about_unfed_inputs(procs: str, port: int) -> None:
    """Say so when what is being started subscribes to a table nothing
    running publishes.

    A subscriber started without its producer subscribes SUCCESSFULLY - the
    table is defined on the plant either way - then heartbeats and reports
    `up` while receiving nothing, with no error and no symptom but an
    output table that stays empty (#290).

    Advisory only, and deliberately so: starting a subscriber before its
    feed is how you avoid missing the first batch, and some tables come
    from outside the process list entirely. Processes named in this same
    invocation count as present, so starting a whole chain is silent.

    Never fails the command. A warning that cannot be produced - the fleet
    is unreachable, the registry cannot be read - must not stop a start.
    """
    try:
        names = [p for p in procs.split() if p != "all"]
        if not names:
            return  # `start all` brings up every startwithall=1 producer too
        running = {
            row["Process"]
            for row in core.summary_rows(core.summary(_paths(), base_port=port).stdout, {}, None)
            if row["Status"] == "up"
        }
        warnings = [
            line for name in names for line in dependencies.unfed_inputs(name, running | set(names))
        ]
    except Exception as exc:  # noqa: BLE001 - see docstring: never block a start
        # loguru formats with str.format, not %-interpolation: the `%s` this
        # used to carry printed literally and the exception was dropped, so
        # the one line explaining why the warning was skipped explained
        # nothing.
        log.debug("dependency warning skipped: {}", exc)
        return
    for line in warnings:
        console.print(f"[yellow]warning[/] {line}")


def _warn_about_connection_cap(procs: str, port: int) -> None:
    """Say so when the fleet this start produces is bigger than the licence
    lets one process hold handles for.

    The licence caps a q process at `PLANT_CONNECTION_BUDGET` concurrent
    connections. Every streaming job opens a handle to stp1 and monitor1
    opens one per process it watches, so past that count the cap - not the
    configuration - decides what works. The plant does not complain: it
    resets the extra connection, the process wedges in its retry loop, and
    `summary` still reports it `up` because that is a PID check.

    Advisory only, and never fails the command: the limit is a property of
    the licence, not a mistake, and a bigger fleet is a legitimate thing to
    want on a full kdb+/KDB-X licence.
    """
    try:
        running = {
            row["Process"]
            for row in core.summary_rows(core.summary(_paths(), base_port=port).stdout, {}, None)
            if row["Status"] == "up"
        }
        if procs.strip() == "all":
            starting = {
                row["procname"]
                for row in core.list_items(_paths(), "processes", base_port=port)
                if row.get("startwithall") == "1"
            }
        else:
            starting = {p for p in procs.split() if p != "all"}
        total = len(running | starting)
    except Exception as exc:  # noqa: BLE001 - see docstring: never block a start
        log.debug("connection-cap warning skipped: {}", exc)
        return
    if total <= core.PLANT_CONNECTION_BUDGET:
        return
    console.print(
        f"[yellow]warning[/] this start leaves {total} processes running, past the "
        f"{core.PLANT_CONNECTION_BUDGET} concurrent connections this licence allows "
        "one q process. Handles past the cap are reset, not refused: the process "
        "wedges in its retry loop and still reports `up`, and monitor1 may become "
        "unreachable so the Heartbeat column empties. Start a subset, or stop what "
        "you are not using."
    )


@app.command()
def start(procs: ProcsArg = "all", port: PortOpt = core.DEFAULT_BASE_PORT) -> None:
    """Start every startwithall=1 process (or specific process name(s))."""
    _warn_about_unfed_inputs(procs, port)
    _warn_about_connection_cap(procs, port)
    _run_streaming(core.start, procs, base_port=port)


@app.command()
def stop(procs: ProcsArg = "all", port: PortOpt = core.DEFAULT_BASE_PORT) -> None:
    """Stop every running process (or specific process name(s))."""
    _run_streaming(core.stop, procs, base_port=port)


@app.command()
def restart(procs: ProcsArg = "all", port: PortOpt = core.DEFAULT_BASE_PORT) -> None:
    """Restart every startwithall=1 process (or specific process name(s))."""
    _warn_about_unfed_inputs(procs, port)
    _warn_about_connection_cap(procs, port)
    _run_streaming(core.restart, procs, base_port=port)


@app.command("print")
def print_startlines(procs: ProcsArg = "all", port: PortOpt = core.DEFAULT_BASE_PORT) -> None:
    """Show the exact startup command line(s) without starting anything."""
    try:
        result = core.print_procs(_paths(), procs, base_port=port)
    except core.UqfStackError as exc:
        _die(exc)
        return
    console.print(result.stdout)
    raise typer.Exit(code=result.returncode)


@app.command()
def clean() -> None:
    """Wipe scripts/output/uqf-stack/ (logs, tplogs, wdb, the copied sample data)."""
    core.clean(_paths())


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def raw(ctx: typer.Context, port: PortOpt = core.DEFAULT_BASE_PORT) -> None:
    """Pass any other torq.sh verb straight through, e.g.:
    `raw -- debug rdb1`, `raw -- qcon gateway1 admin:admin`, `raw -- top feed1`.
    """
    try:
        result = core.run_torq_sh(_paths(), ctx.args, base_port=port, capture=False)
    except core.UqfStackError as exc:
        _die(exc)
        return
    raise typer.Exit(code=result.returncode)
