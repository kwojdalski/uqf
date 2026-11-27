"""`uqs cron run ENTRY` - what the crontab calls - and what `uqs cron status` reads back (#2987).

A run is the entry's command as a child uqs in the checkout, with `--wait`
added to a backfill (without it the exit code says only whether the worker
started; `job run --once` waits already), and `UQS_CRON_ENTRY` in its
environment. Its output is appended to the entry's log, under a line naming
the entry, and one outcome record per run - the entry, when it started and
ended, the command and its exit code - to the entry's outcomes file. The run
exits with the command's own code. A firing that is not due (guard.py) records
nothing: that is cron's hourly or per-minute line, not a run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from uqs.cron import entries, schedule
from uqs.cron.check import BACKFILL
from uqs.cron.crontab import ENTRY_ENV
from uqs.cron.entries import Entry

#: How late a run may be before status calls it overdue: a backfill takes time, and
#: its outcome is written when it ends.
GRACE = timedelta(hours=1)


def command(e: Entry, path: tuple[str, ...]) -> list[str]:
    """The words the child uqs is given: the entry's, with --wait for a backfill."""
    args = e.args
    if path == BACKFILL and "--wait" not in args:
        args.append("--wait")
    return args


def run(e: Entry, path: tuple[str, ...], repo: Path, root: Path | None = None) -> int:
    """Run the entry once, logging and recording it; its exit code."""
    args = command(e, path)
    log = entries.log_path(e.name, root)
    log.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC)
    env = {**os.environ, ENTRY_ENV: e.name}
    with log.open("a") as out:
        out.write(f"--- {started.isoformat()} uqs cron run {e.name}: uqs {' '.join(args)}\n")
        out.flush()
        try:
            code = subprocess.run(
                [sys.executable, "-m", "uqs", *args],
                cwd=repo,
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
                check=False,
            ).returncode
        except OSError as exc:
            out.write(f"uqs cron run {e.name}: cannot start uqs: {exc}\n")
            code = 1
        finished = datetime.now(UTC)
        out.write(f"--- {finished.isoformat()} uqs cron run {e.name}: exit {code}\n")
    record = {
        "entry": e.name,
        "started": started.isoformat(),
        "finished": finished.isoformat(),
        "command": ["uqs", *args],
        "exit_code": code,
    }
    with entries.outcomes_path(e.name, root).open("a") as f:
        f.write(json.dumps(record) + "\n")
    return code


def last_outcome(name: str, root: Path | None = None) -> dict[str, Any] | None:
    """The entry's last outcome record, or None when it never ran (or it is unreadable)."""
    try:
        lines = entries.outcomes_path(name, root).read_text().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def status(e: Entry, now: datetime, root: Path | None = None) -> dict[str, Any]:
    """One entry's state: its last outcome and when, and ok, failing, overdue or never run."""
    last = last_outcome(e.name, root)
    row: dict[str, Any] = {
        "entry": e.name,
        "cron": e.cron,
        "tz": e.tz,
        "last_started": None,
        "last_exit_code": None,
        "state": "never run",
    }
    if last is None:
        return row
    started = datetime.fromisoformat(last["started"])
    row["last_started"] = last["started"]
    row["last_exit_code"] = last["exit_code"]
    due = schedule.next_runs(e.parsed, e.tz, started, 1)
    if due and due[0] < now - GRACE:
        row["state"] = "overdue"
    elif last["exit_code"] != 0:
        row["state"] = "failing"
    else:
        row["state"] = "ok"
    return row
