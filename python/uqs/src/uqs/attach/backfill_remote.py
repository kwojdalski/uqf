"""A backfill extracted on one machine and written by the stack's own (#1650).

An attached backfill (backfill_run) writes the stack's HDB from the machine it
runs on, so the HDB must be on that machine. When the source's driver and
credentials must stay somewhere else, the two halves run apart:

  receive   on the STACK's machine:
            `uqs job receiver start --contract ID --attachment NAME --listen-port P`.
            Takes the receiver contract approved there (uqs.attach.receiver_contract)
            - never the worker, whose code that machine need not have - and admits
            its destination exactly as an attached backfill does: the verified
            attachment, its pinned HDB, the live stack's writers, readers and
            end-of-day fence. Starts scripts/jobs/attached_receiver.q, which grants that
            one (attachment; dataset) under the contract and commits every batch
            through the fenced HDB writer (.qetl.io.hdb_scoped). It stops at the fence.
  extract   on the SOURCE's machine: `uqs job backfill W --attachment NAME --writer HOST:P`.
            Resolves the same attachment WITHOUT the HDB (it is the stack's), plans
            the windows against this machine's coverage, and starts
            scripts/jobs/attached_backfill.q with -writer: each window's batch goes to
            the receiver, and coverage here moves only once the receiver committed
            it - and is released as queryable only once its readers reloaded.

The receiver authenticates with q's own -U file, written for the attachment's
stack user and password and readable only by this account. The extractor
connects as that user: one attachment, one credential, on both machines.
The attachment must carry the same name on both, because the grant is by name.
The contract is exported on the source's machine (`uqs job attachment contract export W`) and
approved on the stack's (`uqs job attachment contract approve FILE --attachment NAME`).

This module is the extracting half; the receiving half is uqs.attach.backfill_receiver.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from uqs.attach import (
    backfill_audit,
    backfill_forced,
    backfill_owner,
    config,
    service,
)
from uqs.attach.backfill import (
    Destination,
    declaration,
    identity_of,
    require_verified,
    resolve,
    state_dir_for,
)
from uqs.attach.backfill_plan import evaluate
from uqs.attach.backfill_run import (
    AGAIN,
    LOG_STAMP,
    LOGS,
    RUNNER,
    Launched,
    q_date,
    stop_file_for,
)
from uqs.attach.remote import Ipc
from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, UqsPaths
from uqs.stack import backfill as stack_backfill
from uqs.stack import runs

#: Each --writer run's writer, in its state directory (note_writer).
WRITERS = "writers.jsonl"
#: How the runner is handed the password it reaches the receiver with.
WRITER_PASSWORD_ENV = "UQF_ATTACH_WRITER_PASSWORD"
#: --override any-writer: the HDB a destination names when the attachment pins none - the
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
    """`--override any-writer`: the process at `writer` itself, with nothing about it checked.

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
    reason: str | None = None,
    attach_root: Path | None = None,
    ipc: Ipc | None = None,
) -> tuple[Launched, dict[str, Any]]:
    """Start the extractor: the runner with -writer, after the remote plan. With
    `force` the runner gets -force and writes through the process at `writer`
    directly (forced_destination): no receiver, contract or verification - but
    still completed history only: `-max_date` is asked of the writer (backfill_forced).
    A forced run is recorded with its `reason` in backfill_audit's log before the runner is
    started, and refused - nothing started - when it cannot be (#2928)."""
    if force and not (reason or "").strip():
        raise UqsError(
            f"--override {backfill_forced.ANY_WRITER} needs a --reason: it is recorded before "
            "the run starts, and an override that is not recorded is not used"
        )
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
        backfill_owner.refuse_while_active(attachment, attach_root, then=AGAIN)
        att = config.load(attachment, attach_root)
        owner = backfill_owner.owner_file(attachment, worker, attach_root)
        forced_bound: list[str] = []
        last = None
        if force:
            host, wport = parse_writer(writer)
            last, calendar = backfill_forced.completed_bounds(
                host, wport, ipc or service.ipc_for(att)
            )
            forced_bound = [
                "-max_date",
                q_date(last),
                *(["-calendar", calendar] if calendar else []),
            ]
        flags = [
            *stack_backfill.backfill_flags(
                worker, version, range_from, range_to,
                verbose=verbose, trace=trace, on_conflict=on_conflict, mode=mode,
            ),
            "-destid", dest.identity, "-attachment", dest.attachment, "-owner", str(owner),
            "-writer", writer, "-writer_user", att.stack.user,
            *(["-force", "-force_hdb", att.storage.hdb_dir or "_own_"] if force else []),
            *forced_bound,
        ]  # fmt: skip
        logs = dest.state_dir / LOGS
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / f"{worker}-{datetime.now(UTC).strftime(LOG_STAMP)}.log"
        # the receiver's password goes to the child by environment, never by command line
        env = {
            **os.environ,
            "UQF_STATUS_DIR": str(dest.state_dir),
            WRITER_PASSWORD_ENV: att.stack.password(),
        }
        # a forced write is on record, durably, before the runner exists to write (#2928)
        audit = None
        if force:
            assert last is not None and reason is not None
            audit = backfill_audit.record(
                dest.state_dir,
                [_forced_entry(dest, worker, version, range_from, range_to, writer=writer,
                               mode=mode, reason=reason, last=last, log_path=log)],
            )  # fmt: skip
        try:
            with log.open("wb") as out:
                proc = subprocess.Popen(
                    [str(q), str(paths.repo_root / RUNNER), *flags],
                    cwd=paths.repo_root, env=env, stdin=subprocess.DEVNULL,
                    stdout=out, stderr=subprocess.STDOUT, start_new_session=True,
                )  # fmt: skip
        except OSError as exc:
            backfill_audit.outcome(audit, log.stem, outcome="not-started", error=str(exc))
            raise UqsError(f"could not start the backfill runner: {exc}") from None
        # an earlier process's leftover stop request for this pid stops nothing new (#2930)
        stop_file_for(dest.state_dir, worker, proc.pid).unlink(missing_ok=True)
        note_writer(dest.state_dir, proc.pid, writer, forced=force)
        backfill_owner.claim(attachment, worker, proc.pid, dest.identity, attach_root)
    return Launched(proc.pid, log, dest.state_dir, owner, dest, proc, audit, worker), plan


def note_writer(state_dir: Path, pid: int, writer: str, *, forced: bool) -> None:
    """Record that the run of process `pid` writes through `writer` (#2950). The ledger's row
    names the run's host and pid but not its writer, and a re-run without --writer would take
    the local attached path, which writes the HDB directly or refuses it as not here."""
    note = {
        "pid": pid,
        "host": socket.gethostname(),
        "writer": writer,
        "forced": forced,
        "started": datetime.now(UTC).isoformat(),
    }
    with (state_dir / WRITERS).open("a") as out:
        out.write(json.dumps(note, sort_keys=True) + "\n")


def writer_of(state_dir: Path, run: dict[str, Any]) -> dict[str, Any] | None:
    """The `note_writer` record of ledger row `run`, or None for a run that wrote directly:
    the latest one for its host and pid that is not later than the run's start, since a pid
    is reused."""
    try:
        lines = (state_dir / WRITERS).read_text().splitlines()
    except OSError:
        return None
    started = str(run.get("started_at") or "")
    if not started:
        return None
    began = runs.ledger_moment(started, upper=True)
    found = None
    for line in lines:
        try:
            note = json.loads(line)
            same = int(note["pid"]) == int(run.get("pid") or -1) and (
                str(note["host"]).casefold() == str(run.get("host", "")).casefold()
            )
            if same and datetime.fromisoformat(note["started"]) <= began:
                found = note
        except ValueError, KeyError, TypeError:
            continue
    return found


def _forced_entry(
    dest: Destination,
    worker: str,
    version: str,
    range_from: datetime,
    range_to: datetime,
    *,
    writer: str,
    mode: str,
    reason: str,
    last: date,
    log_path: Path,
) -> dict[str, Any]:
    """The audit entry of a forced run, its command rebuilt from its own arguments."""
    how = "--dry-run" if mode == "dry-run" else f"--mode {mode}"
    command = (
        f"uqs job backfill {worker} --attachment {dest.attachment} --writer {writer} {how} "
        f"--from {range_from.isoformat()} --to {range_to.isoformat()} "
        f"--source-version {version} --override {backfill_forced.ANY_WRITER}"
    )
    return backfill_audit.forced_entry(
        run=log_path.stem, mode=mode, reason=reason,
        effect=backfill_forced.GUARDRAILS[0].description, command=command, job=worker,
        writer=writer, destination=dest.as_dict(), range_from=range_from.isoformat(),
        range_to=range_to.isoformat(), last_writable_date=last.isoformat(), log_path=log_path,
    )  # fmt: skip
