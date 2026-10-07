"""`uqs summary`'s table: gathering its rows, and rendering them.

Apart from the command so the same two steps can run again - `summary -i`
re-reads the fleet after each start, stop or restart it makes - and because
cli/summary.py had reached the package's module budget.
"""

from __future__ import annotations

import math
import subprocess
import time
from dataclasses import dataclass

from rich.table import Table

from uqs.cli.shared import _lines, _paths, _sorted_items, log
from uqs.cli.summary_graph import attach_graph_columns
from uqs.paths import UqsError
from uqs.stack import listing, probe, runtime
from uqs.stack.listing import SUMMARY_GRAPH_COLUMNS

_STATUS_STYLE = {"up": "bold green", "down": "bold red"}


@dataclass
class Gathered:
    """What one pass over the fleet found."""

    result: subprocess.CompletedProcess[str]
    rows: list[dict[str, str]]
    #: None when monitor1 could not be reached - a monitoring gap, not a verdict.
    heartbeats: dict[str, str] | None
    #: Up processes that did not answer the probe.
    silent: list[str]


def gather(
    port: int | None,
    needed: list[str],
    timeout: float,
    probe_timeout: float,
    sort_column: str | None,
    reverse: bool,
) -> Gathered:
    """Ask the fleet for every row the table needs, within one `timeout` budget.

    @throws UqsError when the process listing itself fails; every other
    lookup degrades to a blank column rather than failing the table.
    """
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
    result = runtime.summary(paths, base_port=port, timeout=remaining())
    log.debug("process listing: {} line(s)", _lines(result))

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

    if any(col in SUMMARY_GRAPH_COLUMNS for col in needed):
        attach_graph_columns(rows)
    silent = (
        probe.attach_probe_column(rows, probe_timeout, deadline) if "Responds" in needed else []
    )
    rows = _sorted_items(rows, sort_column, reverse)

    return Gathered(result, rows, heartbeats, silent)


def render(rows: list[dict[str, str]], chosen: list[str], port: int | None) -> Table:
    """The rows as the coloured Rich table `summary` prints, titled with
    the runtime and base port they describe."""
    declared = _paths().runtime_declaration
    base = declared.resolve_base_port(port)
    table = Table(title=f"uqs summary ({declared.name} runtime, base port {base})")
    for col in chosen:
        # The graph cells are pre-wrapped at their commas by graph_cell, so
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
        responds = row.get("Responds", "")
        rendered = {
            **row,
            "Status": f"[{status_style}]{row['Status']}[/]" if status_style else row["Status"],
            "Port": port_cell,
            "Heartbeat": hb_cell,
            "Responds": responds
            if responds.endswith("ms") or responds in ("", "-")
            else f"[bold red]{responds}[/]",
        }
        table.add_row(*(rendered.get(col, "") for col in chosen))
    return table
