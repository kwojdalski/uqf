"""What a native writer's `writer_state` says about its clock and the dates it holds (#1454, #1914).

Split from backfill_coordination.py, which reads these readings and decides. Nothing here
decides: a reading that is missing or unreadable comes back as None / flagged unreadable, and
the caller refuses it rather than read it as safe.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from uqs import timerange
from uqs.paths import UqsError

#: The digits of a q timestamp's fraction past the microsecond.
_PAST_MICROSECOND = re.compile(r"(\.\d{6})\d+$")


def moment(text: str) -> datetime | None:
    """A writer's clock reading, to the microsecond.

    The writer sends `.z.p`, which on Linux carries nanoseconds (macOS's clock
    stops at the microsecond, so a run there never showed it). parse_bound
    refuses those digits in a bound someone typed; a clock reading nobody typed
    is cut to the microsecond instead, which no fence measured in minutes can
    feel (#1650: the remote-stack lane's wdb on Linux was refused for it)."""
    return (
        timerange.parse_bound("writer clock", _PAST_MICROSECOND.sub(r"\1", text)) if text else None
    )


def day(text: str) -> date | None:
    """The date of a writer's reading, or None when it sent none."""
    reading = moment(text)
    return reading.date() if reading else None


def partitions(state: dict[str, Any]) -> tuple[list[date], bool]:
    """The dates a writer reports it is, or is still, holding - and whether any was unreadable.

    A wdb reports `partition` (TorQ's `.wdb.getpartition[]`, its maintained current write
    partition); an rdb reports `partitions` (`.rdb.getpartition[]`, its outstanding
    partition list, including dates awaiting reload). `.eodtime.d` is deliberately not
    evidence: TorQ sets it at process start and neither end-of-day handler advances it
    (#1914). Anything else - another proctype, a missing key, a null or malformed date - is
    unreadable, which the caller refuses rather than reads as safe."""
    if state.get("proctype") == "wdb":
        raw: Any = [state.get("partition", "")]
    elif state.get("proctype") == "rdb":
        raw = state.get("partitions", [])
    else:
        return [], True
    if not isinstance(raw, list):
        return [], True
    days: list[date] = []
    unreadable = False
    for item in raw:
        try:
            found = day(item) if isinstance(item, str) else None
        except UqsError:
            found = None
        if found is None:
            unreadable = True
        else:
            days.append(found)
    return days, unreadable


def boundary(state: dict[str, Any], held: list[date]) -> str:
    """The observed partition state, in the words of the writer that reported it."""
    who = state.get("procname", "a writer")
    if state.get("proctype") == "wdb":
        return f"{who}'s current write partition is {held[0]}"
    listed = ", ".join(d.isoformat() for d in sorted(held))
    return f"{who}'s outstanding partitions are {listed}, the earliest {min(held)}"
