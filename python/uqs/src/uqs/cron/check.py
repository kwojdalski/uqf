"""What `uqs cron add` refuses (#2987): a command that is not a finite unit, and a
time inside an attachment's end-of-day fence.

Schedulable are the two finite units: `job backfill JOB` (a bounded worker -
coverage skips windows already done, so runs that overlap cost nothing) and
`job run JOB --once` (one unit of a streaming job). A streaming job run any
other way is long-running and supervised by TorQ, and a reaction runs when a
worker publishes the window it reacts to: both are refused, with why. The job
is looked up in the declarations, as `uqs job list` reads them
(uqs.model.jobs.job_rows); the attachment in the saved attachments.

The CLI parses `run` with the real command tree and hands this module the
command's path and the values it gave (`Parsed`), so nothing here knows Typer.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from uqs.cron import schedule
from uqs.cron.entries import Entry
from uqs.paths import UqsError

BACKFILL = ("job", "backfill")
RUN = ("job", "run")
SCHEDULABLE = "`job backfill JOB ...` and `job run JOB --once`"
#: Options a scheduled command may not carry: cron runs the command, waits for it
#: and logs it, so a rehearsal, a stream of its log or a document instead are refused.
NOT_SCHEDULED = {
    "--dry-run": "a schedule runs the command; --dry-run would run nothing",
    "--mode": "a schedule runs the command; `uqs cron add` already runs it once with --mode plan",
    "--follow": "cron has no terminal to follow on: `uqs cron run` waits and logs",
    "--json": "`uqs cron run` keeps the command's own output in the entry's log",
}
#: How many of an entry's next runs are held to the fence: two weeks of a daily entry.
FENCE_HORIZON = 14


@dataclass(frozen=True)
class Parsed:
    """`run` as the CLI parsed it: the command's path and the options it was given."""

    path: tuple[str, ...]
    job: str | None = None
    attachment: str | None = None
    given: frozenset[str] = field(default_factory=frozenset)


def refuse_unschedulable(
    parsed: Parsed, rows: Sequence[dict[str, Any]], attachments: Sequence[str]
) -> None:
    """Refuse what cron must not run, naming why; return when it may."""
    if parsed.path not in (BACKFILL, RUN):
        raise UqsError(
            f"`uqs {' '.join(parsed.path)}` cannot be scheduled: uqs cron runs {SCHEDULABLE}, "
            "the finite units. A streaming job is long-running and supervised by TorQ; a "
            "reaction runs when its window is published"
        )
    for option, why in NOT_SCHEDULED.items():
        if option in parsed.given:
            raise UqsError(f"{option} is not for a scheduled command: {why}")
    job = parsed.job or ""
    row = next((r for r in rows if job in (r["job"], str(r.get("procname", "")))), None)
    if row is None:
        raise UqsError(f"{job} is not a job (`uqs job list` names them)")
    kind = row["kind"]
    if kind == "reaction":
        raise UqsError(
            f"{job} is a reaction: it runs when a worker publishes the window it reacts to, "
            "not on a schedule"
        )
    if parsed.path == BACKFILL and kind != "backfill":
        raise UqsError(
            f"{job} is a streaming job: it is long-running and supervised by TorQ, not "
            f"scheduled. One unit of it is `job run {job} --once`"
        )
    if parsed.path == RUN and "--once" not in parsed.given:
        raise UqsError(f"`job run {job}` is scheduled with --once: one unit, then it exits")
    if parsed.path == RUN and kind == "backfill":
        raise UqsError(f"{job} is a bounded worker: schedule `job backfill {job} --last ...`")
    if parsed.attachment is not None and parsed.attachment not in attachments:
        raise UqsError(
            f"{parsed.attachment} is not an attachment (`uqs job attachment list` names them)"
        )


#: Runs uqs with these words in the checkout, returning (exit code, stdout, stderr).
Runner = Callable[[list[str]], tuple[int, str, str]]


def plan_args(e: Entry, parsed: Parsed) -> list[str] | None:
    """The command `add` runs once to check the entry: `--mode plan` where it has one."""
    if parsed.path != BACKFILL:
        return None
    return [*e.args, "--mode", "plan", *(["--json"] if parsed.attachment else [])]


def run_plan(e: Entry, parsed: Parsed, runner: Runner) -> dict[str, Any] | None:
    """Run the entry once with --mode plan; refuse when that is refused. The plan's
    document for an attachment (it carries the end-of-day fence), else None."""
    args = plan_args(e, parsed)
    if args is None:
        return None
    code, out, err = runner(args)
    if code:
        raise UqsError(
            f"`uqs {' '.join(args)}` was refused (exit {code}), so the entry would be too:\n"
            + (err or out).strip()
        )
    if not parsed.attachment:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        raise UqsError(f"`uqs {' '.join(args)}` printed no plan document") from None


def refuse_in_fence(e: Entry, plan: dict[str, Any], now: datetime) -> None:
    """Refuse an entry whose next runs fall within the attachment's end-of-day fence:
    `eod_guard_minutes` either side of its writers' daily roll, as the plan reports it."""
    co = plan.get("plan", plan).get("coordination") or {}
    roll_text, guard_min = co.get("next_roll"), co.get("eod_guard_minutes")
    if not roll_text or guard_min is None:
        raise UqsError(
            f"the plan for {e.name} does not report its writers' next roll, so whether the "
            "entry's time is inside the end-of-day fence cannot be told"
        )
    roll = datetime.fromisoformat(str(roll_text).replace("Z", "+00:00"))
    guard = timedelta(minutes=int(guard_min))
    for run in schedule.next_runs(e.parsed, e.tz, now, FENCE_HORIZON):
        days = round((run - roll).total_seconds() / 86_400)
        nearest = roll + timedelta(days=days)
        if abs(run - nearest) < guard:
            raise UqsError(
                f"{e.name} runs at {run.isoformat()}, within {int(guard_min)} minutes of its "
                f"attachment's end of day ({nearest.isoformat()}): a backfill there is refused. "
                "Choose a time outside the fence"
            )


def default_runner(repo: Path) -> Runner:
    """uqs itself, as a child in the checkout - the command line the crontab will run."""
    import subprocess
    import sys

    def run(args: list[str]) -> tuple[int, str, str]:
        done = subprocess.run(
            [sys.executable, "-m", "uqs", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        return done.returncode, done.stdout, done.stderr

    return run
