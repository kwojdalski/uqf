"""A backfill extracted on one machine and written by the stack's own (#1650).

An attached backfill (backfill_run) writes the stack's HDB from the machine it
runs on, so the HDB must be on that machine. When the source's driver and
credentials must stay somewhere else, the two halves run apart:

  receive   on the STACK's machine: `uqs job receive --contract ID --attach NAME --listen-port P`.
            Takes the receiver contract approved there (uqs.attach.receiver_contract)
            - never the worker, whose code that machine need not have - and admits
            its destination exactly as an attached backfill does: the verified
            attachment, its pinned HDB, the live stack's writers, readers and
            end-of-day fence. Starts scripts/dev/remote_receiver.q, which grants that
            one (attachment; dataset) under the contract and commits every batch
            through the fenced HDB writer (.qetl.io.hdb_scoped). It stops at the fence.
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
The contract is exported on the source's machine (`uqs job attachment contract export W`) and
approved on the stack's (`uqs job attachment contract approve FILE --attach NAME`).
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

from uqs.attach import backfill_owner, config, receiver_contract
from uqs.attach.backfill import (
    Destination,
    declaration,
    identity_of,
    require_verified,
    resolve,
    state_dir_for,
)
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
#: --any-writer: the HDB a destination names when the attachment pins none - the
#: writer's own `.wdb.hdbdir`, resolved by that process - and the prefix that keeps a
#: forced destination's identity, and so its coverage, apart from a checked one.
FORCED_HDB = ".wdb.hdbdir"
FORCED_PREFIX = "forced:"
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
    force: bool = False,
    attach_root: Path | None = None,
) -> tuple[Destination, dict[str, Any]]:
    """The extractor's plan: the destination named, the windows against this
    machine's coverage, and the refusals that need no stack. The stack-side
    admission - writers, readers, the fence - is the receiver's."""
    parse_writer(writer)
    dest = (
        forced_destination(paths, attachment, worker, writer, attach_root)
        if force
        else resolve(paths, attachment, worker, attach_root=attach_root, on_this_machine=False)
    )
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
        "forced": force,
    }
    return dest, plan


def forced_destination(
    paths: UqsPaths,
    attachment: str,
    worker: str,
    writer: str,
    attach_root: Path | None = None,
) -> Destination:
    """`--any-writer`: the process at `writer` itself, with nothing about it checked.

    The attachment is read for its stack's credential and nothing else: it need not
    be verified, pin an HDB or publish the dataset. The rows go wherever that process
    keeps its HDB (`.wdb.hdbdir`, or the attachment's pinned HDB when it has one).
    Coverage is filed under the writer's address, apart from any checked destination.
    """
    host, port = parse_writer(writer)
    att = config.load(attachment, attach_root)
    dataset = declaration(paths, worker).dataset
    where = Path(att.storage.hdb_dir or FORCED_HDB)
    ident = identity_of(f"{FORCED_PREFIX}{host}", port, where)
    return Destination(
        att.name, att.stack.id, host, where, dataset, ident, state_dir_for(paths, att.name, ident)
    )


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
    force: bool = False,
    attach_root: Path | None = None,
) -> tuple[Launched, dict[str, Any]]:
    """Start the extractor: the runner with -writer, after the remote plan. With
    `force` the runner gets -force and writes through the process at `writer`
    directly (forced_destination): no receiver, contract or verification."""
    q = q_interpreter(os.environ)
    if q is None:
        raise UqsError("no q interpreter - set $QCMD, or put q on PATH; the run is a q process")
    dest, plan = remote_plan(
        paths, attachment, worker, version, range_from, range_to,
        writer=writer, on_conflict=on_conflict, force=force, attach_root=attach_root,
    )  # fmt: skip
    with config.locked(attachment, attach_root):
        if not force:
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
            *(["-force", "-force_hdb", att.storage.hdb_dir or "_own_"] if force else []),
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
    admission = admit(att, dest, ipc)
    assert admission.observation is not None
    co = assess(att, dest, admission.observation, admission.writers, ipc, []).as_dict()
    readers = ",".join(f"{r.procname}@{att.stack.host}:{r.port}" for r in admission.reload_targets)
    receiver = f"{contract.dataset}.receiver"
    with config.locked(attachment, attach_root):
        require_verified(attachment, attach_root)
        backfill_owner.refuse_while_active(attachment, attach_root)
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
            "-journal", str(dest.state_dir / JOURNAL), "-writers", ",".join(users),
            "-max_rows", str(int(binding.get("max_rows", receiver_contract.DEFAULT_MAX_ROWS))),
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
