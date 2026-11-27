"""The not-due check of `uqs cron run ENTRY`, before anything slow loads (#2987).

When the host's zone is not the entry's, the crontab line fires every hour (or
every minute) and most firings are not the entry's. Each one would otherwise
load the whole command line, `.env` and `.envrc` (direnv, possibly a command)
only to exit; this answers first, from schedule.py and the entries file alone.

It may only ever say "not due". When the entry cannot be found here - the
directory `.env` would move is not yet loaded - it says nothing, and the
command's own check, after `.env`, decides. `now` is taken as the process
starts, so a slow start does not slip the firing into the next minute.
"""

from __future__ import annotations

from datetime import UTC, datetime

from uqs.cron import entries, schedule

#: `not_due`'s host zone when none is given: this machine's, looked up.
LOCAL = "local"


def not_due(name: str, now: datetime | None = None, host: str | None = LOCAL) -> bool:
    """Whether entry `name` is certainly not due now: a guarded line firing off its time."""
    now = now or datetime.now(UTC)
    try:
        e = entries.load().get(name)
    except Exception:  # anything odd: let the command say what is wrong
        return False
    if e is None:
        return False
    host_tz = schedule.host_zone() if host == LOCAL else host
    if not schedule.guarded(e.parsed, e.tz, host_tz, now):
        return False  # the line is the entry's own: cron has already decided
    return not schedule.due(e.parsed, e.tz, now)


def exit_if_not_due(argv: list[str], now: datetime) -> None:
    """`argv` = uqs's words: exit 0 at once for `cron run ENTRY` when ENTRY is not due."""
    if len(argv) >= 3 and argv[0] == "cron" and argv[1] == "run" and not argv[2].startswith("-"):
        if not_due(argv[2], now):
            raise SystemExit(0)
