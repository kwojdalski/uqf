"""`uqs gaps`: where a streaming job was down, and how to refill it (#630)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

import typer
from rich.table import Table

from uqs.cli.shared import InteractiveOpt, _die, _paths, _show, app, console
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


@app.command()
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
    for worker in twins if advised else ():
        try:
            version = f" --version {stack_backfill.resolve_version(worker, None)}"
        except UqsError:
            # No declared default: the source release is the operator's to
            # name, so it is shown as one to fill in, not guessed.
            version = " --version <V>"
        for hole in advised:
            command = (
                f"uqs backfill {worker} --from {stack_runs._as_bound(str(hole['range_from']))} "
                f"--to {stack_runs._as_bound(str(hole['range_to']))}{version}"
            )
            console.print(f"  {command}", soft_wrap=True)
    if cut:
        console.print(
            "[yellow]today's part of these gaps is not refillable yet[/]: a backfill cannot "
            "write rows dated today, which belong to the tickerplant until end of day. "
            "Run this same `uqs gaps` command after end of day for its refill."
        )
