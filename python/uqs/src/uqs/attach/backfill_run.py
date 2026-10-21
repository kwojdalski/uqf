"""Running a backfill into an attached stack's HDB (#1454): revalidate, then launch.

Everything `plan` decides is decided again here, immediately before anything runs
(`backfill_plan.plan_attached`: the attachment, the stack, the writers, the date and
end-of-day fences), because the plan an operator read may be minutes old and a stack
moves. What the fresh admission says becomes the run's two bounds - the last date it
may write and the instant its writes must stop - and they, the destination and its
state directory are handed to the runner as flags. Nothing is read from the runtime's
own `KDBHDB`.

The runner (scripts/dev/attached_backfill.q) is the bounded-worker lifecycle and the HDB
writer, not a second engine. This module only starts it, owns the attachment while it
runs (backfill_owner: detach refuses), and follows it to an outcome when asked.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from uqs.attach import backfill_owner, config
from uqs.attach.backfill import Destination, require_verified
from uqs.attach.backfill_plan import plan_attached
from uqs.attach.remote import Ipc
from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, UqsPaths
from uqs.stack import backfill as stack_backfill
from uqs.stack import runs
from uqs.stack.backfill_status import result_line

RUN_MODES = ("run", "dry-run")
#: How the runner is handed the password of the readers it is to ask to reload.
REACH_PASSWORD_ENV = "UQF_ATTACH_REACH_PASSWORD"
#: The runner's exit code for rows that are written and covered but not yet queryable.
EXIT_NOT_QUERYABLE = 3
RUNNER = Path("scripts") / "dev" / "attached_backfill.q"
LOGS = "logs"


@dataclass
class Launched:
    """A started run."""

    pid: int
    log: Path
    state_dir: Path
    owner: Path
    destination: Destination
    process: subprocess.Popen[bytes]

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "log": str(self.log),
            "state_dir": str(self.state_dir),
            "owner_record": str(self.owner),
            "destination": self.destination.as_dict(),
        }


def q_date(day: date) -> str:
    return day.strftime("%Y.%m.%d")


def bounds_of(plan: dict[str, Any]) -> tuple[date, datetime]:
    """(last date this run may write, instant its writes stop) from a fresh admission."""
    co = plan["coordination"]
    return date.fromisoformat(co["last_writable_date"]), datetime.fromisoformat(
        co["writes_stop_at"]
    )


def runner_flags(
    plan: dict[str, Any],
    dest: Destination,
    owner: Path,
    *,
    host: str,
    user: str,
    worker: str,
    version: str,
    range_from: datetime,
    range_to: datetime,
    mode: str,
    on_conflict: str | None,
    verbose: bool,
    trace: bool,
) -> list[str]:
    """The runner's command line: torq_backfill.q's flags, plus the destination."""
    last, deadline = bounds_of(plan)
    flags = stack_backfill.backfill_flags(
        worker,
        version,
        range_from,
        range_to,
        verbose=verbose,
        trace=trace,
        on_conflict=on_conflict,
        mode=mode,
    )
    return [
        *flags,
        "-hdb",
        str(dest.hdb_dir),
        "-destid",
        dest.identity,
        "-attachment",
        dest.attachment,
        "-max_date",
        q_date(last),
        "-deadline",
        runs.to_q_timestamp(deadline),
        "-owner",
        str(owner),
        "-readers",
        ",".join(f"{r['procname']}@{host}:{r['port']}" for r in plan["reload_targets"]) or "_none_",
        "-user",
        user,
    ]


def launch(
    paths: UqsPaths,
    attachment: str,
    worker: str,
    version: str,
    range_from: datetime,
    range_to: datetime,
    *,
    ipc: Ipc,
    mode: str,
    port: int | None = None,
    fixture: bool = False,
    on_conflict: str | None = None,
    verbose: bool = False,
    trace: bool = False,
    attach_root: Path | None = None,
) -> tuple[Launched, dict[str, Any]]:
    """Admit afresh and start the runner; returns the run and the admission it started under.

    Refused - nothing started, nothing created - when the admission refuses; refused when the
    attachment already has a run or there is no q. The state directory is created here and only
    here.
    """
    if mode not in RUN_MODES:
        raise UqsError(f"--mode {mode} is not an execution mode ({', '.join(RUN_MODES)})")
    q = q_interpreter(os.environ)
    if q is None:
        raise UqsError("no q interpreter - set $QCMD, or put q on PATH; the run is a q process")
    # Admitted BEFORE the attachment is locked: a refusal must leave nothing behind, and taking
    # the lock creates its file.
    plan = plan_attached(
        paths,
        attachment,
        worker,
        version,
        range_from,
        range_to,
        ipc=ipc,
        port=port,
        fixture=fixture,
        on_conflict=on_conflict,
        attach_root=attach_root,
    )
    with config.locked(attachment, attach_root):
        # Read again under the lock: a detach may have finished since admission (#1526).
        require_verified(attachment, attach_root)
        backfill_owner.refuse_while_active(attachment, attach_root)
        dest = Destination(**_destination(plan))
        owner = backfill_owner.owner_file(attachment, worker, attach_root)
        att = config.load(attachment, attach_root)
        flags = runner_flags(
            plan,
            dest,
            owner,
            host=att.stack.host,
            user=att.stack.user,
            worker=worker,
            version=version,
            range_from=range_from,
            range_to=range_to,
            mode=mode,
            on_conflict=on_conflict,
            verbose=verbose,
            trace=trace,
        )
        logs = dest.state_dir / LOGS
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / f"{worker}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}.log"
        # the readers' password goes to the child by environment, never by command line
        env = {
            **os.environ,
            "UQF_STATUS_DIR": str(dest.state_dir),
            REACH_PASSWORD_ENV: att.stack.password(),
        }
        with log.open("wb") as out:
            proc = subprocess.Popen(
                [str(q), str(paths.repo_root / RUNNER), *flags],
                cwd=paths.repo_root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        # the process exists: record it before the lock is released, so no second run or detach
        # can slip in between its start and its own claim
        backfill_owner.claim(attachment, worker, proc.pid, dest.identity, attach_root)
    return Launched(proc.pid, log, dest.state_dir, owner, dest, proc), plan


def _destination(plan: dict[str, Any]) -> dict[str, Any]:
    d = plan["destination"]
    return {
        "attachment": d["attachment"],
        "stack_id": d["stack_id"],
        "host": d["host"],
        "hdb_dir": Path(d["hdb_dir"]),
        "dataset": d["dataset"],
        "identity": d["identity"],
        "state_dir": Path(d["state_dir"]),
    }


def follow(run: Launched, *, poll: float = 0.5, timeout: float | None = None) -> tuple[int, dict]:
    """Wait for the runner; its exit code and what it reported. The code is the lifecycle's: 0
    for completed or idle, non-zero for anything that did not do its job, and EXIT_NOT_QUERYABLE
    for rows written and covered that a reader has not yet reloaded."""
    started = time.monotonic()
    while True:
        code = run.process.poll()
        if code is not None:
            return code, result_line(run.log)
        if timeout is not None and time.monotonic() - started > timeout:
            raise UqsError(f"the backfill (pid {run.pid}) is still running; log {run.log}")
        time.sleep(poll)
