"""`uqs job gaps`: where a streaming job was down, and how to refill it (#630)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

import typer
from rich.table import Table

from uqs.cli.shared import (
    JOB_OPERATIONS_PANEL,
    InteractiveOpt,
    _die,
    _paths,
    _show,
    console,
    job_app,
)
from uqs.paths import UqsError
from uqs.stack import backfill as stack_backfill
from uqs.stack import runs as stack_runs
from uqs.stack import uptime as stack_uptime


def _utc_today() -> date:
    return datetime.now(UTC).date()


def refillable(holes: list[dict], today: date) -> tuple[list[dict], bool]:
    """The part of each gap a backfill can refill, and whether any was cut.

    A backfill writes through the HDB writer, which refuses a row dated today
    or later: today belongs to the tickerplant and end of day (#770). So each
    gap is clipped to today's UTC midnight, and one lying wholly in today is
    dropped. The ledger's timestamps are ISO strings of one width, so they
    order as strings.
    """
    midnight = f"{today.isoformat()}T00:00:00.000000000"
    kept = [
        {**hole, "range_to": min(str(hole["range_to"]), midnight)}
        for hole in holes
        if str(hole["range_from"]) < midnight
    ]
    cut = len(kept) < len(holes) or any(str(h["range_to"]) > midnight for h in holes)
    return kept, cut


#: 2000.01.01, q's epoch, in Unix nanoseconds: what `"j"$` of a timestamp
#: counts from, and so the origin of every grid a worker's `align` is held to.
Q_EPOCH_NS = 946_684_800 * 1_000_000_000


def widen_to_grain(ledger_value: str, align_ns: int | None, *, upper: bool = False) -> str:
    """A ledger timestamp moved outwards onto a bar grid (#1323).

    A worker that declares `align` refuses a bound that is not a multiple of
    it (#1306), and an uptime edge is a wall-clock instant. So a range's
    start is floored to the grid and its end (`upper`) ceiled - the same
    widening `_as_bound` does for the microsecond, safe for the same reason:
    covered windows are skipped. A worker without `align` is left as is.
    """
    if not align_ns:
        return ledger_value
    stamp, _, fraction = ledger_value.partition(".")
    whole = datetime.fromisoformat(stamp).replace(tzinfo=UTC)
    # On q's grid, not the Unix one (#1345): require_aligned tests `"j"$ts`,
    # nanoseconds since 2000.01.01, and the epochs are 946,684,800 s apart -
    # they agree only for an align that divides that, which nothing enforces.
    nanos = int(whole.timestamp()) * 1_000_000_000 + int(fraction.ljust(9, "0")[:9]) - Q_EPOCH_NS
    low = nanos - nanos % align_ns
    if upper and low != nanos:
        low += align_ns
    seconds, sub = divmod(low + Q_EPOCH_NS, 1_000_000_000)
    return f"{datetime.fromtimestamp(seconds, UTC):%Y-%m-%dT%H:%M:%S}.{sub:09d}"


@job_app.command("gaps", rich_help_panel=JOB_OPERATIONS_PANEL)
def gaps(
    job: Annotated[str, typer.Argument(help="The streaming job, e.g. markout")],
    range_from: Annotated[str, typer.Option("--from", help="Start of the range to check")],
    range_to: Annotated[str, typer.Option("--to", help="End of the range, exclusive")],
    interactive: InteractiveOpt = False,
) -> None:
    """Where in [--from, --to) a streaming job was not up and subscribed.

    A restarted job rebuilds its state from the day's log but does not publish
    again what it missed, so these are the holes in its output. Each is
    followed by the backfill that refills it, when a bounded worker fills
    what the job publishes (its "twin"); a job without one cannot be
    refilled from here.

    The record is uptime, not output: a job that was up but received nothing
    shows no gap. Each session's end is its last beat, so a gap can be up to
    a minute wider than the real outage - never narrower.
    """
    try:
        bound_from = stack_backfill.parse_bound("--from", range_from)
        bound_to = stack_backfill.parse_bound("--to", range_to)
        holes, twins, sessions = stack_uptime.gaps(_paths(), job, bound_from, bound_to)
    except UqsError as exc:
        _die(exc)
        return
    if not holes:
        console.print(f"[green]{job} was up throughout[/] - no gaps in the range")
        return
    table = Table(title=f"{job}: not up and subscribed")
    for col in ("range_from", "range_to"):
        table.add_column(col, no_wrap=True)
    for hole in holes:
        table.add_row(str(hole["range_from"]), str(hole["range_to"]))
    _show(table, interactive)
    if sessions == 0:
        console.print(
            f"[yellow]no uptime recorded for {job} at all[/] - it never ran here, or ran "
            "before uptime was recorded, so the whole range reads as down"
        )
    if not twins:
        console.print(
            f"no bounded worker fills what {job} publishes, so these gaps cannot be "
            "refilled from here"
        )
        return
    advised, cut = refillable(holes, _utc_today())
    if advised:
        console.print("\n[bold]refill[/] - re-running is idempotent: covered windows are skipped")
    for worker, align in twins.items() if advised else ():
        try:
            version = f" --version {stack_backfill.resolve_version(worker, None)}"
        except UqsError:
            # No declared default: the source release is the operator's to
            # name, so it is shown as one to fill in, not guessed.
            version = " --version <V>"
        for hole in advised:
            start = widen_to_grain(str(hole["range_from"]), align)
            end = widen_to_grain(str(hole["range_to"]), align, upper=True)
            command = (
                f"uqs job backfill {worker} --from {stack_runs._as_bound(start)} "
                f"--to {stack_runs._as_bound(end, upper=True)}{version}"
            )
            console.print(f"  {command}", soft_wrap=True)
    if cut:
        console.print(
            "[yellow]today's part of these gaps is not refillable yet[/]: a backfill cannot "
            "write rows dated today, which belong to the tickerplant until end of day. "
            "Run this same `uqs job gaps` command after end of day for its refill."
        )
