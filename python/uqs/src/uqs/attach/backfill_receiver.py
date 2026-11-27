"""The receiving half of a remote backfill (#1650): `uqs job receiver start|stop`.

On the STACK's machine, the receiver takes a contract approved there
(uqs.attach.receiver_contract), admits its destination exactly as an attached backfill does,
and starts scripts/jobs/attached_receiver.q, which commits each batch the extractor sends
through the fenced HDB writer. The extracting half, and the design of the two, is
uqs.attach.backfill_remote.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uqs.attach import backfill_owner, config, receiver_contract
from uqs.attach.backfill import Destination, require_verified
from uqs.attach.backfill_admission import admit
from uqs.attach.backfill_coordination import assess
from uqs.attach.backfill_run import LOG_STAMP, LOGS, REACH_PASSWORD_ENV, q_date
from uqs.attach.remote import Ipc
from uqs.interpreter import q_interpreter
from uqs.liveness import on_this_host
from uqs.paths import UqsError, UqsPaths
from uqs.process_stop import DEFAULT_GRACE_SECONDS, stop_pids
from uqs.stack import runs

RECEIVER = Path("scripts") / "jobs" / "attached_receiver.q"
#: The receiver's batch journal and access file, in the destination's state directory.
JOURNAL = "remote_journal"
ACCESS = "receiver.access"


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
    contract_id: str,
    *,
    port: int,
    ipc: Ipc,
    attach_root: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Start the receiver for the contract `contract_id` approved for `attachment`,
    after admitting its destination against the live stack; what it serves, until
    when, and where it logs. Reads no worker: the contract and its binding are all
    it knows of the batches. With `dry_run`, every check runs and nothing starts:
    the pid and the log are None."""
    q = q_interpreter(os.environ)
    if q is None:
        raise UqsError(
            "no q interpreter - set $QCMD, or put q on PATH; the receiver is a q process"
        )
    if not 0 < port < 65536:
        raise UqsError(f"--listen-port {port} is not a port")
    contract, binding, dest, record = receiver_contract.approved(
        paths, attachment, contract_id, attach_root
    )
    att = config.load(attachment, attach_root)
    users = [str(u) for u in binding.get("users", [])]
    if att.stack.user not in users:
        raise UqsError(
            f"contract {contract.identity} was approved for writer(s) "
            f"{', '.join(users) or 'none'}, "
            f"but attachment {attachment} connects as {att.stack.user}: approve it again"
        )
    admission = admit(att, dest, ipc, attach_root)
    assert admission.observation is not None
    # never waived: `uqs job receiver start` takes no override, so the receiver always has its
    # end-of-day deadline (an extractor's --skip-eod-fence is refused before it gets here)
    co = assess(att, dest, admission.observation, admission.writers, ipc, []).as_dict()
    readers = ",".join(f"{r.procname}@{r.host}:{r.port}" for r in admission.reload_targets)
    receiver = f"{contract.dataset}.receiver"
    with config.locked(attachment, attach_root):
        require_verified(attachment, attach_root)
        backfill_owner.refuse_while_active(attachment, attach_root, then="start the receiver again")
        if dry_run:
            return _served(None, port, dest, contract, binding, co, readers, None)
        access = _access_file(dest, att.stack.user, att.stack.password())
        flags = [
            "-p", str(port), "-U", str(access),
            "-contract", str(record), "-attachment", dest.attachment, "-destid", dest.identity,
            "-hdb", str(dest.hdb_dir),
            "-max_date", q_date(datetime.fromisoformat(co["last_writable_date"]).date()),
            "-deadline", runs.to_q_timestamp(datetime.fromisoformat(co["writes_stop_at"])),
            "-readers", readers or "_none_", "-user", att.stack.user,
            *(["-calendar", co["calendar"]] if co.get("calendar") else []),
            "-journal", str(dest.state_dir / JOURNAL), "-writers", ",".join(users),
            "-max_rows", str(int(binding.get("max_rows", receiver_contract.DEFAULT_MAX_ROWS))),
        ]  # fmt: skip
        logs = dest.state_dir / LOGS
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / f"{receiver}-{datetime.now(UTC).strftime(LOG_STAMP)}.log"
        env = {**os.environ, REACH_PASSWORD_ENV: att.stack.password()}
        with log.open("wb") as out:
            proc = subprocess.Popen(
                [str(q), str(paths.repo_root / RECEIVER), *flags],
                cwd=paths.repo_root, env=env, stdin=subprocess.DEVNULL,
                stdout=out, stderr=subprocess.STDOUT, start_new_session=True,
            )  # fmt: skip
        backfill_owner.claim(attachment, receiver, proc.pid, dest.identity, attach_root)
    return _served(proc.pid, port, dest, contract, binding, co, readers, str(log))


def _served(pid, port, dest, contract, binding, co, readers, log) -> dict[str, Any]:
    """What a receiver serves - or, with no pid, would serve."""
    return {
        "pid": pid,
        "port": port,
        "attachment": dest.attachment,
        "contract": contract.identity,
        "dataset": dest.dataset,
        "hdb_dir": str(dest.hdb_dir),
        "conflict_policies": binding.get("conflict_policies", contract.conflict_policies),
        "last_writable_date": co["last_writable_date"],
        "stops_at": co["writes_stop_at"],
        "readers": readers.split(",") if readers else [],
        "log": log,
    }


def stop_receivers(
    attachment: str,
    attach_root: Path | None = None,
    *,
    dry_run: bool = False,
    kill: bool = False,
    grace: float = DEFAULT_GRACE_SECONDS,
) -> list[dict[str, Any]]:
    """Stop this machine's receivers for `attachment` (`uqs job receiver stop`), so a
    detach here - which refuses while one runs - can go ahead. An extractor elsewhere
    then fails its next window without covering it, and nothing it sent half-way is
    written (a staged batch is committed only whole).

    Each gets the one stop policy (uqs/process_stop.py): SIGTERM, a grace to exit,
    SIGKILL only with `kill`. Its ownership is released only once it is gone - one
    still running keeps its record, so a detach still refuses (#2689). Returns each
    receiver's record with its `outcome`: stopped, killed or still running. A record is
    released only while it still names the stopped pid, under the attachment lock (#2984)."""
    runs = [
        run
        for run in backfill_owner.active(attachment, attach_root)
        if str(run.get("worker", "")).endswith(".receiver")
        and on_this_host(str(run.get("host", "")), run.get("pid_space"))
    ]
    if dry_run:  # the ones that would stop: nothing signalled, nothing released
        return runs
    done = stop_pids([int(run["pid"]) for run in runs], kill=kill, grace=grace)
    gone = []
    for run, result in zip(runs, done, strict=True):
        run["outcome"] = result.verdict
        if result.gone:
            gone.append(run)
    if gone:
        # under the lock `receive` claims under, and only a record still naming the pid that
        # was stopped: while this waited, a `receiver start` may have seen the stopped run as
        # dead and claimed its name for a new receiver, whose record must stay (#2984)
        with config.locked(attachment, attach_root):
            for run in gone:
                backfill_owner.release(
                    attachment, str(run["worker"]), attach_root, pid=int(run["pid"])
                )
    return runs
