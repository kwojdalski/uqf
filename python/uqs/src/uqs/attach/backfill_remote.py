"""A backfill extracted on one machine and written by the stack's own (#1650).

An attached backfill (backfill_run) writes the stack's HDB from the machine it
runs on, so the HDB must be on that machine. When the source's driver and
credentials must stay somewhere else, the two halves run apart:

  receive   on the STACK's machine: `uqs job receive W --attach NAME --port P`.
            Resolves the destination exactly as an attached backfill does - the
            verified attachment, its pinned HDB, the live stack's writers, readers
            and end-of-day fence - and starts scripts/dev/remote_receiver.q, which
            grants that one (attachment; dataset) and commits every batch through
            the fenced HDB writer (.qetl.io.hdb_scoped). It stops at the fence.
  extract   on the SOURCE's machine: `uqs job backfill W --attach NAME --writer HOST:P`.
            Resolves the same attachment WITHOUT the HDB (it is the stack's), plans
            the windows against this machine's coverage, and starts
            scripts/dev/attached_backfill.q with -writer: each window's batch goes to
            the receiver, and coverage here moves only once the receiver committed
            it - and is released as queryable only once its readers reloaded.

The receiver authenticates with q's own -U file, written for the attachment's
stack user and password and readable only by this account. The extractor
connects as that user: one attachment, one credential, on both machines.
The attachment must carry the same name on both, because the grant is by name.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uqs.attach import backfill_owner, config
from uqs.attach.backfill import Destination, require_verified, resolve
from uqs.attach.backfill_coordination import assess
from uqs.attach.backfill_plan import admit, evaluate
from uqs.attach.backfill_run import LOGS, REACH_PASSWORD_ENV, RUNNER, Launched, q_date
from uqs.attach.remote import Ipc
from uqs.interpreter import q_interpreter
from uqs.liveness import on_this_host
from uqs.paths import UqsError, UqsPaths
from uqs.stack import backfill as stack_backfill
from uqs.stack import runs

RECEIVER = Path("scripts") / "dev" / "remote_receiver.q"
#: How the runner is handed the password it reaches the receiver with.
WRITER_PASSWORD_ENV = "UQF_ATTACH_WRITER_PASSWORD"
#: The receiver's batch journal and access file, in the destination's state directory.
JOURNAL = "remote_journal"
ACCESS = "receiver.access"
_WRITER = re.compile(r"(?P<host>[A-Za-z0-9_][A-Za-z0-9._-]*):(?P<port>\d{1,5})")


def parse_writer(text: str) -> tuple[str, int]:
    """--writer HOST:PORT, refused when it is anything else."""
    found = _WRITER.fullmatch(text.strip())
    if not found or not 0 < int(found["port"]) < 65536:
        raise UqsError(f"--writer must be HOST:PORT, the receiver's address - not {text!r}")
    return found["host"], int(found["port"])


def remote_plan(
    paths: UqsPaths,
    attachment: str,
    worker: str,
    version: str,
    range_from: datetime,
    range_to: datetime,
    *,
    writer: str,
    on_conflict: str | None = None,
    attach_root: Path | None = None,
) -> tuple[Destination, dict[str, Any]]:
    """The extractor's plan: the destination named, the windows against this
    machine's coverage, and the refusals that need no stack. The stack-side
    admission - writers, readers, the fence - is the receiver's."""
    parse_writer(writer)
    dest = resolve(paths, attachment, worker, attach_root=attach_root, on_this_machine=False)
    facts = evaluate(paths, dest, worker, version, range_from, range_to, on_conflict)
    if facts["custom_io"]:
        raise UqsError(f"{worker} declares its own IO manager, which would write where it likes")
    if not facts["live"]:
        raise UqsError(
            f"{worker}'s source {facts['source']} has no credential configured here: without "
            "one the run would send the source's FIXTURE into a native HDB"
        )
    plan = {
        "mode": "plan",
        "worker": worker,
        "attachment": attachment,
        "destination": dest.as_dict(),
        "writer": writer,
        "dataset": facts["dataset"],
        "source_version": version,
        "windows": facts["windows"],
        "mutates": False,
    }
    return dest, plan


def launch_remote(
    paths: UqsPaths,
    attachment: str,
    worker: str,
    version: str,
    range_from: datetime,
    range_to: datetime,
    *,
    writer: str,
    mode: str,
    on_conflict: str | None = None,
    verbose: bool = False,
    trace: bool = False,
    attach_root: Path | None = None,
) -> tuple[Launched, dict[str, Any]]:
    """Start the extractor: the runner with -writer, after the remote plan."""
    q = q_interpreter(os.environ)
    if q is None:
        raise UqsError("no q interpreter - set $QCMD, or put q on PATH; the run is a q process")
    dest, plan = remote_plan(
        paths, attachment, worker, version, range_from, range_to,
        writer=writer, on_conflict=on_conflict, attach_root=attach_root,
    )  # fmt: skip
    with config.locked(attachment, attach_root):
        require_verified(attachment, attach_root)
        backfill_owner.refuse_while_active(attachment, attach_root)
        att = config.load(attachment, attach_root)
        owner = backfill_owner.owner_file(attachment, worker, attach_root)
        flags = [
            *stack_backfill.backfill_flags(
                worker, version, range_from, range_to,
                verbose=verbose, trace=trace, on_conflict=on_conflict, mode=mode,
            ),
            "-destid", dest.identity, "-attachment", dest.attachment, "-owner", str(owner),
            "-writer", writer, "-writer_user", att.stack.user,
        ]  # fmt: skip
        logs = dest.state_dir / LOGS
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / f"{worker}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}.log"
        # the receiver's password goes to the child by environment, never by command line
        env = {
            **os.environ,
            "UQF_STATUS_DIR": str(dest.state_dir),
            WRITER_PASSWORD_ENV: att.stack.password(),
        }
        with log.open("wb") as out:
            proc = subprocess.Popen(
                [str(q), str(paths.repo_root / RUNNER), *flags],
                cwd=paths.repo_root, env=env, stdin=subprocess.DEVNULL,
                stdout=out, stderr=subprocess.STDOUT, start_new_session=True,
            )  # fmt: skip
        backfill_owner.claim(attachment, worker, proc.pid, dest.identity, attach_root)
    return Launched(proc.pid, log, dest.state_dir, owner, dest, proc), plan


def _access_file(dest: Destination, user: str, password: str) -> Path:
    """q's -U file for the one writer, readable by this account alone."""
    path = dest.state_dir / ACCESS
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(f"{user}:{password}\n")
    os.chmod(path, 0o600)
    return path


def receive(
    paths: UqsPaths,
    attachment: str,
    worker: str,
    *,
    port: int,
    ipc: Ipc,
    max_rows: int = 100_000,
    attach_root: Path | None = None,
) -> dict[str, Any]:
    """Admit the destination against the live stack and start its receiver;
    what it serves, until when, and where it logs."""
    q = q_interpreter(os.environ)
    if q is None:
        raise UqsError(
            "no q interpreter - set $QCMD, or put q on PATH; the receiver is a q process"
        )
    if not 0 < port < 65536:
        raise UqsError(f"--port {port} is not a port")
    dest = resolve(paths, attachment, worker, attach_root=attach_root)
    att = config.load(attachment, attach_root)
    admission = admit(att, dest, ipc)
    assert admission.observation is not None
    co = assess(att, dest, admission.observation, admission.writers, ipc, []).as_dict()
    readers = ",".join(f"{r.procname}@{att.stack.host}:{r.port}" for r in admission.reload_targets)
    receiver = f"{worker}.receiver"
    with config.locked(attachment, attach_root):
        require_verified(attachment, attach_root)
        backfill_owner.refuse_while_active(attachment, attach_root)
        access = _access_file(dest, att.stack.user, att.stack.password())
        flags = [
            "-p", str(port), "-U", str(access),
            "-worker", worker, "-attachment", dest.attachment, "-destid", dest.identity,
            "-hdb", str(dest.hdb_dir),
            "-max_date", q_date(datetime.fromisoformat(co["last_writable_date"]).date()),
            "-deadline", runs.to_q_timestamp(datetime.fromisoformat(co["writes_stop_at"])),
            "-readers", readers or "_none_", "-user", att.stack.user,
            "-journal", str(dest.state_dir / JOURNAL), "-writers", att.stack.user,
            "-max_rows", str(max_rows),
        ]  # fmt: skip
        logs = dest.state_dir / LOGS
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / f"{receiver}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}.log"
        env = {**os.environ, REACH_PASSWORD_ENV: att.stack.password()}
        with log.open("wb") as out:
            proc = subprocess.Popen(
                [str(q), str(paths.repo_root / RECEIVER), *flags],
                cwd=paths.repo_root, env=env, stdin=subprocess.DEVNULL,
                stdout=out, stderr=subprocess.STDOUT, start_new_session=True,
            )  # fmt: skip
        backfill_owner.claim(attachment, receiver, proc.pid, dest.identity, attach_root)
    return {
        "pid": proc.pid,
        "port": port,
        "attachment": dest.attachment,
        "dataset": dest.dataset,
        "hdb_dir": str(dest.hdb_dir),
        "last_writable_date": co["last_writable_date"],
        "stops_at": co["writes_stop_at"],
        "readers": readers.split(",") if readers else [],
        "log": str(log),
    }


def stop_receivers(attachment: str, attach_root: Path | None = None) -> list[dict[str, Any]]:
    """Stop this machine's receivers for `attachment` (`uqs job receive --stop`), so a
    detach here - which refuses while one runs - can go ahead. An extractor elsewhere
    then fails its next window without covering it, and nothing it sent half-way is
    written (a staged batch is committed only whole). Returns the receivers stopped."""
    stopped = []
    for run in backfill_owner.active(attachment, attach_root):
        if not str(run.get("worker", "")).endswith(".receiver") or not on_this_host(
            str(run.get("host", ""))
        ):
            continue
        with contextlib.suppress(ProcessLookupError):
            os.kill(int(run["pid"]), signal.SIGTERM)
        backfill_owner.release(attachment, str(run["worker"]), attach_root)
        stopped.append(run)
    return stopped
