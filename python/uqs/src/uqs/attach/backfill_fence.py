"""One native writer's active date and end-of-day fence, read from its `writer_state` (#1454).

Split from backfill_coordination.py, which asks every writer and decides admission; the
forced path (backfill_forced.py) reads the same rule for its date bound. This reads one
answer: the date it is filling (only dates before it are completed history), its calendar,
and how near its clock is to the next roll. Each problem is appended as a blocker; nothing
here refuses.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from uqs.attach import writer_calendar, writer_clock
from uqs.attach.backfill_policy import blocker
from uqs.attach.findings import Finding


def fence(
    state: dict[str, Any], dates: list[date], guard: timedelta, blockers: list[Finding]
) -> tuple[date | None, datetime | None, datetime | None]:
    """One writer's active date and end-of-day fence: blockers appended, facts returned."""
    who = state.get("procname", "a writer")
    held, unreadable = writer_clock.partitions(state)
    if not held or unreadable:
        what = (
            "current write partition"
            if state.get("proctype") == "wdb"
            else "outstanding partitions"
        )
        blockers.append(
            blocker(
                "active-date-unknown",
                who,
                f"{who} does not report which date it is writing: its {what} are missing, "
                "empty or unreadable, so the active write date cannot be excluded",
                proctype=state.get("proctype"),
                partition=state.get("partition"),
                partitions=state.get("partitions"),
            )
        )
    active = min(held) if held and not unreadable else None
    now, roll = (
        writer_clock.moment(state.get("now", "")),
        writer_clock.moment(state.get("next_roll", "")),
    )
    cal = writer_calendar.of(state)
    if cal is None or cal.data_adj_ns:
        blockers.append(
            blocker(
                "native-calendar",
                who,
                f"{who} stamps `time` in a data zone other than UTC "
                f"({cal.describe if cal else 'unreadable calendar'}): a backfill stamps no zone, "
                "so its rows would sit hours away from the live rows of the same table",
                datatz_adj=state.get("datatz_adj"),
            )
        )
    if active is not None:
        touched = [d.isoformat() for d in writer_calendar.touched_days(state, dates, active, roll)]
        if touched:
            blockers.append(
                blocker(
                    "active-date",
                    who,
                    f"{', '.join(touched)} is not completed history: "
                    f"{writer_clock.boundary(state, held)} and native end of day will write it. "
                    "Only dates before it can be backfilled",
                    active_date=active.isoformat(),
                    touched_dates=",".join(touched),
                    partitions=",".join(d.isoformat() for d in held),
                )
            )
    if now is None or roll is None:
        blockers.append(
            blocker(
                "clock-unknown",
                who,
                f"{who} does not report its clock and next roll: the end-of-day fence is unknown",
                now=state.get("now"),
                next_roll=state.get("next_roll"),
            )
        )
        return active, now, roll
    minutes = int(guard.total_seconds() // 60)
    to_roll = int((roll - now).total_seconds() // 60)
    if roll - now < guard:
        blockers.append(
            blocker(
                "eod-fence",
                who,
                f"end of day is at {roll.isoformat()}, {to_roll} minutes away on {who}'s clock; "
                f"reloads, sorts and merges cluster there (guard {minutes} minutes)",
                window="overdue" if roll <= now else "before-roll",
                now=now.isoformat(),
                next_roll=roll.isoformat(),
                minutes_to_roll=to_roll,
                guard_minutes=minutes,
            )
        )
    since = now - (roll - timedelta(days=1))
    if since < guard:
        blockers.append(
            blocker(
                "eod-fence",
                who,
                f"{who}'s last end of day was {int(since.total_seconds() // 60)} minutes ago: "
                "its sort, merge and reload may still be running",
                window="after-roll",
                now=now.isoformat(),
                next_roll=roll.isoformat(),
                minutes_since_roll=int(since.total_seconds() // 60),
                guard_minutes=minutes,
            )
        )
    if state.get("reloadcomplete") is False:
        blockers.append(
            blocker(
                "reload-in-progress",
                who,
                f"{who} has an end-of-day reload in progress",
                reloadcomplete=False,
            )
        )
    return active, now, roll
