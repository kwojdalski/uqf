"""How long each process took to load, read from its own log.

TorQ prints the same 80-column banner twice in a normal start: once when
torq.q redirects stdout into the new log file (`.proc.createlog`), and once at
the very end of torq.q, immediately before it sets `.proc.initialised` - after
every code directory, the `-load` file, pubsub, `.servers.startup[]` and the
init list have run. So a process's load time is the span from the first
timestamped line in the file - torq.q logs the alias it creates just before
the opening banner - to the last one before the closing banner, and both ends
are stamped by the same `.proc.cp[]` clock.

Read from the log rather than asked over IPC on purpose. Asking each process
for `.proc.starttimeUTC` would open a handle per process, and a process at its
licence connection cap accepts the TCP connection and never answers - the
same hang `uqs summary` already budgets a timeout against for monitor1.

WHICH FILE. `out_<proc>.log` is an alias onto the CURRENT file, and TorQ rolls
the log daily by default (`.proc.logroll`), so after midnight the alias points
at a continuation that holds no start at all. The start is found instead in
the newest timestamped `out_<proc>_<stamp>.log` that contains one: a rolled
file carries only the opening banner and no `init`/`fileload` lines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from uqs.stack.logs import parse_log_line

#: torq.q's `.lg.banner` border: `-1 80#"#"`, printed raw with no timestamp.
BANNER_BORDER = "#" * 80

#: `.proc.logtimestamp`'s file suffix: `.z.z` with `.`, `:` and `T` as `_`.
_STAMPED = re.compile(r"_\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}_\d{3}\.log$")

#: The ids torq.q logs under while it is loading. A rolled file has none.
_LOAD_IDS = frozenset({"init", "fileload"})

#: How far into a file to look for the closing banner. A start is at the TOP
#: of its file, and a process that never rolls its log (the starter pack's
#: tickerplant sets logroll:0b) can have hundreds of MB after it, so the file
#: is read only until the closing banner begins - or this many lines, for a
#: start that never finished. A real load logs a few hundred.
MAX_HEAD_LINES = 20_000


@dataclass(frozen=True)
class Startup:
    """One process's most recent start, as its log records it."""

    procname: str
    #: The first timestamped line in the start's file, in the process's own
    #: clock (GMT unless it ran with -localtime).
    started: datetime | None
    #: Seconds from `started` to the last line before the closing banner.
    #: None while the closing banner has not been written.
    seconds: float | None
    #: Why `seconds` is missing, or empty when it is not.
    note: str = ""


def parse_kdb_timestamp(text: str) -> datetime | None:
    """`2026.09.24D08:12:33.123456789` -> a datetime, truncated to microseconds."""
    date, d, clock = text.strip().partition("D")
    if not d:
        return None
    whole, _, frac = clock.partition(".")
    try:
        base = datetime.strptime(f"{date} {whole}", "%Y.%m.%d %H:%M:%S")
    except ValueError:
        return None
    return base + timedelta(microseconds=int((frac + "000000")[:6]))


def _head(path: Path) -> list[str]:
    """The file up to the top border of its closing banner, the third border
    line in it, or its first MAX_HEAD_LINES lines."""
    lines: list[str] = []
    borders = 0
    with path.open(errors="replace") as f:
        for line in f:
            lines.append(line.rstrip("\n"))
            borders += lines[-1] == BANNER_BORDER
            if borders == 3 or len(lines) >= MAX_HEAD_LINES:
                break
    return lines


def _banner_starts(lines: list[str]) -> list[int]:
    """Line indexes where a banner begins. Each banner has TWO border lines,
    its top and its bottom, so every other border starts one."""
    borders = [i for i, line in enumerate(lines) if line == BANNER_BORDER]
    return borders[0::2]


def _stamped_times(lines: list[str]) -> list[datetime]:
    times = []
    for line in lines:
        rec = parse_log_line(line)
        stamp = parse_kdb_timestamp(rec["time"]) if rec else None
        if stamp is not None:
            times.append(stamp)
    return times


def _has_load_lines(lines: list[str]) -> bool:
    return any((rec := parse_log_line(line)) and rec["id"] in _LOAD_IDS for line in lines)


def startup_files(log_dir: Path, procname: str) -> list[Path]:
    """Every timestamped out_ file this process has written, oldest first.

    The stamp sorts as text, and the exact-suffix match keeps `out_hdb1_…`
    from also collecting `out_hdb12_…` or a `deals_backfill1` under `deals`.
    """
    prefix = f"out_{procname}"
    return sorted(
        path
        for path in log_dir.glob(f"{prefix}_*.log")
        if _STAMPED.fullmatch(path.name[len(prefix) :])
    )


def read_startup(log_dir: Path, procname: str) -> Startup:
    """The most recent start of `procname`, measured from its log."""
    for path in reversed(startup_files(log_dir, procname)):
        lines = _head(path)
        if not _has_load_lines(lines):
            continue  # a daily roll: the start is in an older file
        banners = _banner_starts(lines)
        times = _stamped_times(lines)
        started = times[0] if times else None
        if len(banners) < 2:
            return Startup(
                procname,
                started,
                None,
                "no closing banner yet - still loading, or it stopped while loading",
            )
        loaded = _stamped_times(lines[: banners[1]])
        if started is None or not loaded:
            return Startup(procname, started, None, "no timestamped lines around the banners")
        return Startup(procname, started, (loaded[-1] - started).total_seconds())
    return Startup(procname, None, None, "no startup log - never started, or started -noredirect")


def read_startups(log_dir: Path, procnames: list[str]) -> list[Startup]:
    """`read_startup` for each process, in the order given."""
    return [read_startup(log_dir, name) for name in procnames]
