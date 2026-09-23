"""`uqs summary`: the status table, and the graph beside it.

Its own module because it is the largest single command in the CLI by a
distance - a status table, a derived process graph, a shared timeout budget
and the messaging that distinguishes an unreachable monitor from a stopped
one. See cli/lifecycle.py for why the split is shaped this way.
"""

from __future__ import annotations

import math
import time
from typing import Annotated

import typer
from rich.table import Table

from uqs.cli.shared import (
    ExportOpt,
    PortOpt,
    _die,
    _export,
    _lines,
    _paths,
    app,
    console,
    log,
)
from uqs.model import dependencies
from uqs.model.pipeline_edges import LICENCE_CONNECTION_LIMIT
from uqs.model.registry import DEFAULT_BASE_PORT
from uqs.paths import UqsError
from uqs.stack import listing, runtime
from uqs.stack.listing import (
    MONITOR_PROCNAME,
    SUMMARY_ALL_COLUMNS,
    SUMMARY_COLUMNS,
    SUMMARY_GRAPH_COLUMNS,
)

_STATUS_STYLE = {"up": "bold green", "down": "bold red"}

#: Seconds `summary` will spend gathering its table before giving up.
#:
#: It is a read-only command an operator runs to find out what is going on,
#: which makes it the worst possible thing to hang: the one time you run it
#: is when something is already wrong. Both of its blocking steps can hang
#: indefinitely without this - `torq.sh summary` shells out and had no
#: timeout at all, and the heartbeat query talks to a process that, at its
#: licence connection cap, accepts the TCP connection and then does not
#: answer.
#:
#: A BUDGET for the whole command rather than a per-call limit, because two
#: calls each given ten seconds is a twenty-second hang, which is not what
#: anyone means by a ten-second timeout.
SUMMARY_TIMEOUT_SECONDS = 10.0

#: How many table names go on one line of a graph cell before it wraps.
#:
#: Rich would wrap these on its own, but on whitespace and at whatever width
#: is left over - so `fx_position` and `fx_limit_breach` could break as
#: `fx_position, fx_` / `limit_breach`, splitting a name across lines. These
#: cells are lists, and a reader scans them by counting entries, so the break
#: belongs between entries and nowhere else.
GRAPH_CELL_ITEMS_PER_LINE = 2


def _graph_cell(items: tuple[str, ...] | list[str]) -> str:
    """A list of table or process names, broken across lines at the commas.

    Empty renders as a dim dash rather than blank: "this process declares no
    inputs" and "this column has nothing to say about it" look identical
    otherwise, and the first is a real fact about a feed.
    """
    if not items:
        return "[dim]-[/]"
    lines = [
        ", ".join(items[i : i + GRAPH_CELL_ITEMS_PER_LINE])
        for i in range(0, len(items), GRAPH_CELL_ITEMS_PER_LINE)
    ]
    # Every line but the last keeps its trailing comma, so a wrapped cell
    # still reads as one list rather than as separate values per line.
    return "\n".join(line + "," if i < len(lines) - 1 else line for i, line in enumerate(lines))


def _resolve_columns(requested: str | None) -> list[str]:
    """The columns to render, from a comma-separated `--columns` value.

    The graph columns are ON by default. They were opt-in first, on the
    grounds that nine columns do not fit an eighty-column terminal - which is
    true, and was still the wrong trade: a column nobody knows about answers
    nothing, and "what feeds this" is the question that follows "is it
    running" almost every time. A reader on a narrow terminal can say
    `--columns status`; a reader who never learns the columns exist has no
    such move.
    """
    if not requested:
        return list(SUMMARY_ALL_COLUMNS)
    if requested.strip().lower() == "all":
        return list(SUMMARY_ALL_COLUMNS)
    if requested.strip().lower() == "status":
        return list(SUMMARY_COLUMNS)
    wanted = [c.strip() for c in requested.split(",") if c.strip()]
    known = {c.lower(): c for c in SUMMARY_ALL_COLUMNS}
    resolved, unknown = [], []
    for column in wanted:
        match = known.get(column.lower())
        if match is None:
            unknown.append(column)
        elif match not in resolved:
            resolved.append(match)
    if unknown:
        _die(
            UqsError(
                f"unknown summary column(s): {', '.join(unknown)}. "
                f"Available: {', '.join(SUMMARY_ALL_COLUMNS)}, "
                "or `all` / `status`"
            )
        )
    return resolved


def _attach_graph_columns(rows: list[dict[str, str]]) -> None:
    """Fill the graph columns on each row, in place.

    Derived from the same `Pipeline` declarations `verify_pipeline_edges`
    checks and `database.q` is generated from, so a process's row here cannot
    claim an edge the build would reject.

    A vendored TorQ process has no `Pipeline` entry and so no declared edges;
    it gets the same dash as a uqf process that genuinely has none, because
    this table is not the place to explain the difference.
    """
    inputs = dependencies.inputs_by_process()
    outputs = dependencies.outputs_by_process()
    depends = dependencies.depends_on_by_process()
    for row in rows:
        name = row["Process"]
        row["Depends on"] = _graph_cell(depends.get(name, ()))
        row["Inputs"] = _graph_cell(inputs.get(name, ()))
        row["Outputs"] = _graph_cell(outputs.get(name, ()))


@app.command()
def summary(
    port: PortOpt = DEFAULT_BASE_PORT,
    export: ExportOpt = None,
    columns: Annotated[
        str | None,
        typer.Option(
            "--columns",
            help=(
                "Comma-separated columns, `all` (the default), or `status` for "
                "just up/down/pid/port - narrower, for an 80-column terminal."
            ),
        ),
    ] = None,
    timeout: Annotated[
        float,
        typer.Option(
            "--timeout",
            help=(
                f"Seconds to spend gathering the table before giving up "
                f"(default {SUMMARY_TIMEOUT_SECONDS:g}). 0 waits forever."
            ),
        ),
    ] = SUMMARY_TIMEOUT_SECONDS,
) -> None:
    """Status table for every process in process.csv, with its declared graph.

    The graph columns - what each process subscribes to, what it publishes,
    and which processes it therefore needs running - answer the question
    behind every `up, but idle` process, from the same declarations
    `verify_pipeline_edges` checks.

    Nine columns need a wide terminal. `--columns status` gives the original
    six, and any subset can be named explicitly.

    Run with `--debug` (or LOG_LEVEL=DEBUG) to see where each column came
    from: the two lookups below degrade rather than fail, so on the default
    level a missing port map and an unreachable monitor1 look the same as a
    stack that simply has nothing to report.
    """
    chosen = _resolve_columns(columns)
    # One budget shared across both blocking steps, spent in order. `remaining`
    # is what is left when each is reached; 0 means no limit, as it does on
    # the option itself.
    deadline = None if timeout <= 0 else time.monotonic() + timeout

    def remaining() -> float | None:
        if deadline is None:
            return None
        # Never hand a caller zero or a negative: subprocess treats <=0 as
        # "already expired" and kola refuses a zero duration outright, so a
        # budget that has just run out would raise something less legible
        # than the timeout it is. A floor of one second lets the last step
        # fail on its own terms.
        return max(1.0, deadline - time.monotonic())

    paths = _paths()
    log.debug("summary base_port={} torqdata={}", port, paths.torqdata)
    try:
        result = runtime.summary(paths, base_port=port, timeout=remaining())
    except UqsError as exc:
        _die(exc)
        return
    log.debug("torq.sh summary returncode={} stdout_lines={}", result.returncode, _lines(result))

    # TorQ reports a port only for a process that is UP, so every `down` row
    # used to show a blank - for exactly the processes whose port a reader is
    # most likely looking up. The port is declared in process.csv either way,
    # so fill it from there and mark where it came from.
    try:
        ports = listing.configured_ports(paths, base_port=port)
        log.debug("configured ports for {} process(es)", len(ports))
    except UqsError as exc:
        # A summary that still prints beats one that dies because the port
        # map could not be built - the reported ports are unaffected. The
        # reason is worth keeping even so: without it, every `down` row shows
        # a blank port and nothing on screen says why.
        log.debug("configured port map unavailable, every down row loses its port: {}", exc)
        ports = {}

    # Heartbeat state, which answers a different question from Status: the
    # latter comes from torq.sh's PID lookup, and a hung process still has a
    # PID. None here means monitor1 could not be reached, which is a gap in
    # MONITORING rather than a verdict on the fleet - rendered as such below.
    try:
        # kola's timeout is whole seconds, so the remaining budget is rounded
        # UP: rounding down could hand it 0, which kola reads as "wait
        # forever" - the exact opposite of a spent budget.
        left = remaining()
        heartbeats = listing.heartbeat_states(
            paths, base_port=port, timeout=0 if left is None else max(1, math.ceil(left))
        )
    except UqsError as exc:
        # Two distinct failures reach the same printed sentence below, and it
        # names only the commoner one. This branch is monitor1 missing from
        # the registry entirely; a None return (handled next) is monitor1
        # declared but unreachable - a refused connection, a bad password or a
        # timeout. Only DEBUG tells the reader which of the two they have.
        log.debug("heartbeat states unavailable, Heartbeat column is a monitoring gap: {}", exc)
        heartbeats = None
    if heartbeats is None:
        log.debug("monitor1 not reached; Heartbeat column is a monitoring gap, not a verdict")
    else:
        log.debug("monitor1 reported heartbeats for {} process(es)", len(heartbeats))

    rows = listing.summary_rows(result.stdout, ports, heartbeats)
    log.debug(
        "parsed {} row(s): {} up, {} down",
        len(rows),
        sum(1 for r in rows if r["Status"] == "up"),
        sum(1 for r in rows if r["Status"] == "down"),
    )

    if any(col in SUMMARY_GRAPH_COLUMNS for col in chosen):
        _attach_graph_columns(rows)

    table = Table(title=f"uqs summary (base port {port})")
    for col in chosen:
        # The graph cells are pre-wrapped at their commas by _graph_cell, so
        # Rich must not wrap them again at whatever width is left over - that
        # is what splits a table name across two lines.
        table.add_column(col, overflow="fold" if col in SUMMARY_GRAPH_COLUMNS else "ellipsis")

    for row in rows:
        status_style = _STATUS_STYLE.get(row["Status"], "")
        # A configured port is a different claim from a reported one -
        # "will listen here" rather than "is listening here" - so it is dimmed
        # rather than printed as though the process were up.
        port_cell = row["Port"]
        if row["PortSource"] == "configured" and port_cell:
            port_cell = f"[dim]{port_cell}[/]"
        # A heartbeat error is the most serious thing this table can show -
        # the process answers ps but not its own monitor - so it is the only
        # cell in red besides a `down` status.
        hb = row["Heartbeat"]
        hb_cell = {
            "ok": "[green]ok[/]",
            "warning": "[yellow]warning[/]",
            "error": "[bold red]error[/]",
            "not collected": "[dim]not collected[/]",
        }.get(hb, hb)
        rendered = {
            **row,
            "Status": f"[{status_style}]{row['Status']}[/]" if status_style else row["Status"],
            "Port": port_cell,
            "Heartbeat": hb_cell,
        }
        table.add_row(*(rendered.get(col, "") for col in chosen))
    console.print(table)

    # Up and fed are different questions, and the table above only answers
    # the first. A process can hold a PID, heartbeat `ok`, and still be
    # receiving nothing because whatever publishes its input is stopped -
    # which has no other symptom at all (#290).
    starved = dependencies.starved_processes(
        {row["Process"] for row in rows if row["Status"] == "up"}
    )
    log.debug("starved process(es): {}", ", ".join(sorted(starved)) or "none")
    if starved:
        console.print(
            f"\n[yellow]{len(starved)} running process(es) have an input nothing "
            "running publishes - up, but idle:[/]"
        )
        for reason in (r for reasons in starved.values() for r in reasons):
            console.print(f"  [yellow]·[/] {reason}")

    if heartbeats is None:
        # This used to assert "monitor1 is not running", which is one cause
        # and not the common one. Saturation looks identical from here and is
        # what actually happens on a full stack: monitor1 opens a handle to
        # every process it monitors, the licence caps a q process at
        # LICENCE_CONNECTION_LIMIT concurrent connections, and once it is at
        # the cap it cannot accept the inbound handle this query needs - so a
        # monitor that is running perfectly, and collecting heartbeats
        # correctly, is unreachable. Telling the reader to restart it then
        # sends them to fix a process that has nothing wrong with it.
        monitor_up = any(r["Process"] == MONITOR_PROCNAME and r["Status"] == "up" for r in rows)
        cause = (
            "It is up, so it is most likely at its connection cap "
            f"({LICENCE_CONNECTION_LIMIT} on this licence) and cannot accept "
            "another handle - check `err_monitor1.log`, which will still be "
            "recording the heartbeats it collected"
            if monitor_up
            else "It is not running - it starts with the stack, so it died or was "
            "stopped. Run `uqs start monitor1`"
        )
        console.print(
            f"[dim]Heartbeats not collected: monitor1 could not be reached. {cause}. "
            "Status above is a PID check, which cannot tell a hung process from a "
            "working one.[/]"
        )
    else:
        # A "-" on an `up` process is not an all-clear and not a fault: it
        # means monitor1 holds no subscription to it. On the KDB-X community
        # edition that is the usual cause - the licence caps concurrent
        # connections (~16 in practice), so monitor1 saturates partway
        # through the fleet and the rest are simply never subscribed. Saying
        # so beats leaving a dash the reader has to guess at.
        unheard = [r["Process"] for r in rows if r["Status"] == "up" and r["Heartbeat"] == "-"]
        if unheard:
            console.print(
                f"[dim]No heartbeat collected for {len(unheard)} running "
                f"process(es): {', '.join(unheard)}. monitor1 holds no "
                "subscription to them - on the KDB-X community edition its "
                "connection count is licence-capped, so it cannot reach the "
                "whole fleet. This is a monitoring gap, not a fault in those "
                "processes.[/]"
            )
    if any(r["PortSource"] == "configured" for r in rows):
        console.print(
            "[dim]Dimmed ports come from process.csv: that is where the process "
            "will listen, not where it is listening.[/]"
        )
    _export(rows, export)
    raise typer.Exit(code=result.returncode)
