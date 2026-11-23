"""`--once`: run ONE unit of a job and report how it ended (#2798).

The q side is src/etl/core/once.q. A job's process, started with `-once`, runs one
unit of the job (one poll, one timer cycle, one input batch and its processing, one
planned window) through the framework's runner for the job's kind, writes
`<status_dir>/once_<procname>.json` and exits. This is the launcher's half: it
refuses to start one over a process that is already running, passes the flags,
and waits for the file.

THE FILE, NOT THE EXIT STATUS, because torq.sh detaches the process: the status
the launcher gets back is torq.sh's own, and says only that the process started.
The exit status the process itself leaves with is the same code, for whoever
runs q directly (`q scripts/jobs/run_backfill.q ... -once`).

THE OUTCOMES and their codes are q's (uqs.generated.q_facts, generated from
.qetl.once): done 0, failed 1, stopped 4, no_work 6. No work is a success the
scheduler can tell from a unit that ran: a poll that found nothing, a range
already covered (reason `all_covered`) or not yet complete at the source or end of
day (`not_complete`, #2929), a subscriber that saw no input before its timeout (reason
`input_timeout`, a wait that ended - never a cancelled query, which is a failure).

NEVER A SECOND UNIT. A process of the same job that is already running is not
stopped and is not reused: the launch is refused before torq.sh is asked for
anything, so a running service keeps running and is not run for a second unit.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from uqs.generated import q_facts
from uqs.model.declarations import read_declarations
from uqs.model.pipeline import PipelineKind
from uqs.model.registry import PIPELINES
from uqs.paths import UqsError, UqsPaths, repo_root
from uqs.stack import alive, runs

#: The outcomes, as q names them, and the code each exits with.
OUTCOMES: tuple[str, ...] = q_facts.ONCE_OUTCOMES
EXIT_DONE: int = q_facts.ONCE_EXIT_DONE
EXIT_FAILED: int = q_facts.ONCE_EXIT_FAILED
EXIT_STOPPED: int = q_facts.STOP_EXIT_CODE
EXIT_NO_WORK: int = q_facts.ONCE_EXIT_NO_WORK
CODES: dict[str, int] = {
    "done": EXIT_DONE,
    "failed": EXIT_FAILED,
    "stopped": EXIT_STOPPED,
    "no_work": EXIT_NO_WORK,
}

#: What a flag value may contain: torq.sh evals the start line, so a value is a number.
_NUMBER = re.compile(r"[0-9]+(\.[0-9]+)?")

#: How long a launched process has to write its first sign of life before its absence
#: means it died: torq.sh returns as soon as it has forked, and q loads the tree first.
STARTUP_GRACE = timedelta(seconds=30)


@dataclass(frozen=True)
class Outcome:
    """How a unit ended: q's document, or the launcher's account of a process that left none."""

    outcome: str
    code: int
    reason: str
    document: dict[str, Any]

    @property
    def ok(self) -> bool:
        """Done and no work are both a unit that did what it was asked."""
        return self.outcome in ("done", "no_work")


def flags(timeout: float | None = None) -> list[str]:
    """The flags a one-unit start line carries: `-once`, and `-once_timeout SECONDS` when given.

    A value torq.sh could not carry through its shell is refused here, not passed on."""
    out = ["-once"]
    if timeout is not None:
        text = f"{timeout:g}"
        if timeout <= 0 or not _NUMBER.fullmatch(text):
            raise UqsError(f"--once-timeout must be a positive number of seconds, got {timeout!r}")
        if timeout > q_facts.ONCE_MAX_TIMEOUT_SECONDS:
            raise UqsError(
                f"--once-timeout {text}s is past the longest wait, "
                f"{q_facts.ONCE_MAX_TIMEOUT_SECONDS}s"
            )
        out += ["-once_timeout", text]
    return out


def outcome_path(paths: UqsPaths, procname: str) -> Path:
    """Where q writes a process's outcome (`.qetl.once.path`)."""
    return (
        runs.status_dir(paths) / f"{q_facts.ONCE_FILE_PREFIX}{procname}{q_facts.ONCE_FILE_SUFFIX}"
    )


def job_processes() -> dict[str, str]:
    """procname -> the job it runs, for every process a streaming job runs.

    A bounded worker's process needs a range and a version, so it is `uqs job backfill`'s;
    tap1 and the infrastructure services (tickerplant, RDB, HDB, gateway) run no job."""
    return {
        d.procname: d.name
        for d in read_declarations(repo_root())
        if d.kind is not PipelineKind.BACKFILL
    }


def process_of(job: str) -> str:
    """The process `uqs job run JOB --once` starts for JOB, or a refusal saying what it takes.

    JOB is a job's name as `uqs job list` shows it. A bounded worker needs a range, so it
    is `uqs job backfill`'s; a process name is answered with its job's, and anything else
    - a service such as the tickerplant, an RDB, an HDB or the gateway - runs no job."""
    by_job = {name: procname for procname, name in job_processes().items()}
    if job in by_job:
        return by_job[job]
    workers = {p.worker for p in PIPELINES if p.worker}
    if job in workers:
        raise UqsError(
            f"{job} is a bounded worker, which needs a range: "
            f"use `uqs job backfill {job} ... --once`"
        )
    if job in job_processes():
        raise UqsError(
            f"{job} is a process, not a job: it runs {job_processes()[job]}, "
            f"so run `uqs job run {job_processes()[job]} --once`"
        )
    raise UqsError(
        f"{job} is not a job (`uqs job list` names them, e.g. {sorted(by_job)[0]}): "
        "--once runs one unit of a job, and infrastructure such as the tickerplant, an RDB, "
        "an HDB or the gateway has no unit to run"
    )


def refuse_if_running(
    paths: UqsPaths, procname: str, base_port: int | None, *, once: bool = True
) -> None:
    """Refuse, rather than stop or reuse, a process of this job that is already up.

    Stopping it would end a service someone is running, and a second unit run through it would
    not be the one this launch was asked for: either way the answer is to leave it alone.

    Not only for --once (`once` False): torq.sh's `start` of a running process only echoes
    "already running" and exits 0, so a backfill of a second range was reported started and
    never ran (#2862)."""
    if procname not in alive.running(paths, base_port):
        return
    if once:
        raise UqsError(
            f"{procname} is already running: --once never stops or reuses a running process. "
            f"Stop it first (`uqs stop {procname}`) if one unit in a fresh process is what you want"
        )
    raise UqsError(
        f"{procname} is already running, so this range would not start: a worker runs one "
        f"range at a time. Wait for it (`uqs job runs list --unfinished`), or stop it "
        f"(`uqs stop {procname}`), then run this again"
    )


def _stamp(value: str) -> datetime:
    """A q timestamp from JSON (nanoseconds, no offset, UTC) as a datetime."""
    stamp, _, fraction = value.partition(".")
    return datetime.fromisoformat(f"{stamp}.{(fraction or '0')[:6].ljust(6, '0')}").replace(
        tzinfo=UTC
    )


def read(path: Path, launched_at: datetime) -> Outcome | None:
    """The outcome this launch's process wrote, or None: no file yet, a half-read one, or a
    file an earlier launch left (its unit started before this launch)."""
    try:
        doc = json.loads(path.read_text())
        if _stamp(str(doc["started_at"])) < launched_at.astimezone(UTC):
            return None
        outcome = str(doc["outcome"])
        code = int(doc["code"])
    except OSError, ValueError, KeyError:
        return None
    if outcome not in CODES or CODES[outcome] != code:
        raise UqsError(
            f"{path} records outcome {outcome!r} with code {code}, which is not q's contract "
            f"({CODES}) - the q tree and uqs disagree"
        )
    return Outcome(outcome, code, str(doc.get("reason") or ""), doc)


def wait(
    paths: UqsPaths,
    procname: str,
    launched_at: datetime,
    *,
    base_port: int | None = None,
    poll_seconds: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    is_up: Callable[[], bool] | None = None,
) -> Outcome:
    """Follow the process to its outcome.

    A process that is gone without writing one - the tree failed to load, a refusal, a kill - is
    `failed`, with the reason, once STARTUP_GRACE has passed since the launch: until then it may
    simply not have got as far as its first unit. The wait is bounded by the process, not a clock:
    a unit that is still running is waited for, and a subscriber's own input timeout (q's) ends
    the process whatever the plant does."""
    path = outcome_path(paths, procname)
    if is_up is None:

        def is_up() -> bool:
            return procname in alive.running(paths, base_port)

    while True:
        found = read(path, launched_at)
        if found is not None:
            return found
        if now() - launched_at >= STARTUP_GRACE and not is_up():
            found = read(path, launched_at)  # it may have written and exited since
            if found is not None:
                return found
            return Outcome(
                "failed",
                EXIT_FAILED,
                f"its process exited without recording an outcome - see `uqs logs {procname}`",
                {},
            )
        sleep(poll_seconds)


def as_document(procname: str, job: str, outcome: Outcome) -> dict[str, Any]:
    """The `--json` document: what ran, how it ended, and the exit code the command leaves with."""
    doc = outcome.document
    return {
        "once": True,
        "process": procname,
        "job": job,
        "kind": doc.get("kind", ""),
        "outcome": outcome.outcome,
        "reason": outcome.reason,
        "units": doc.get("units", 0),
        "rows": doc.get("rows", 0),
        "detail": doc.get("detail", ""),
        "exit_code": outcome.code,
        "ok": outcome.ok,
    }
