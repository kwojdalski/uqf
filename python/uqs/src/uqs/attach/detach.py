"""Detach: stop the publisher and reverse what this attachment created - when,
and only when, that is provably safe.

The contract (issue #1424), as code:

  * A pre-existing table, subscription, process or log configuration is never
    removed or modified. Reversal acts on operations the manifest records as
    `created`, and on nothing else.
  * BEFORE ANY PUBLICATION, newly created empty state may be removed where
    verified safe: each q operation re-verifies, inside the process, that what
    it is about to remove is empty and ours.
  * AFTER PUBLICATION nothing is deleted. The publisher is stopped and every
    registration, log, row and schema-file block stays - removing a
    registration would break replay of the log and lose the rows in the
    consumers. Detachment is not data deletion.
  * "Published" is decided from evidence read off the stack, never from a flag
    this tool set; evidence that cannot be read counts as publication.
  * Drift or unknown ownership REFUSES the reversal and lists what a person has
    to do. A refused detach changes nothing but the publisher, which is stopped
    first and stays stopped.
  * Once the publisher is stopped the manifest is no longer `verified`, so a detach
    that stops part way (stack unreadable, an IPC error) leaves the start gate
    closed until a retry finishes (#1443). A step apply never heard back from is
    probed on the stack, never assumed pre-existing (#1447).
  * A failure part way is reported as exactly that; no atomicity across
    processes is claimed, and every step is safe to repeat.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path

from uqs.attach import backfill_owner, config, persist, sorting
from uqs.attach.apply import Step
from uqs.attach.config import Attachment, AttachmentError
from uqs.attach.drift import Blocker, drift, plant_ours
from uqs.attach.evidence import EMPTY_LOG, Evidence, evidence
from uqs.attach.facts import Host
from uqs.attach.manifest import (
    CREATED,
    DONE,
    FAILED,
    PENDING,
    RETAINED,
    REVERTED,
    SHARED,
    Manifest,
    Op,
)
from uqs.attach.outcome import BLOCKS, CONSUMER_OPS, INVERSE, interrupted, unresolved
from uqs.attach.publisher import Publisher, stop_publisher
from uqs.attach.remote import Ipc, IpcError
from uqs.attach.topology import Observation, Proc, observe


@dataclass
class DetachResult:
    #: rolled_back | detached | refused | already
    outcome: str
    steps: list[Step] = field(default_factory=list)
    evidence: Evidence = field(default_factory=Evidence)
    blockers: list[Blocker] = field(default_factory=list)
    #: what stays, and why (after publication), or notes
    retained: list[str] = field(default_factory=list)
    publisher_stopped: bool = False
    dry_run: bool = False
    #: set while the reversal is in progress: an error then leaves the gate closed
    interrupted: bool = False


# ------------------------------------------------------------------ steps


def _subscribers_added(m: Manifest, table: str) -> int:
    """Handles our consumers added to `table` alone, as stp_unregister.q counts (#1471, #1459);
    reverted ones too: the tickerplant lists them until the table is unregistered, last."""
    added = (o for o in m.operations if o.op in CONSUMER_OPS and o.table == table)
    return sum(
        max(0, int(o.post.get("subscribers_after", 0)) - int(o.post.get("subscribers_before", 0)))
        for o in added
        if o.ownership == CREATED and o.status != FAILED
    )


def _request(m: Manifest, op: Op, dry_run: bool, ours: set[str]) -> dict:
    req: dict = {"table": op.table, "token": m.token, "dry_run": dry_run}
    if op.op == "consumer_attach" and op.table in ours:
        req["follows_plant"] = True
    if op.op in CONSUMER_OPS:
        req["tickerplant"] = next((o.process for o in m.operations if o.op == "stp_register"), "")
    if op.op == "consumer_subscribe":
        req["subtables"] = bool(op.post.get("subtables"))
    if op.op == "stp_register":
        req |= {"empty_size": EMPTY_LOG, "expect_subscribers": _subscribers_added(m, op.table)}
    return req


def _removable(m: Manifest, procs: dict[str, Proc]) -> list[Op]:
    """What a rollback removes, newest first: created, not yet reverted, on a process we reach."""
    return [
        o
        for o in reversed(m.operations)
        if o.ownership == CREATED
        and o.status in (DONE, PENDING)
        and o.op in INVERSE
        and o.process in procs
    ]


def _dry_runs(ipc: Ipc, m: Manifest, procs: dict[str, Proc], ours: set[str]) -> list[Blocker]:
    """Ask each process to run its own removal checks, changing nothing."""
    out: list[Blocker] = []
    for op in _removable(m, procs):
        answer = ipc.call(procs[op.process].endpoint, INVERSE[op.op], _request(m, op, True, ours))
        out += [
            Blocker(op.process, problem, "nothing was removed; resolve it, then detach again")
            for problem in answer.get("problems", [])
        ]
    return out


def _leave(ipc: Ipc, procs: dict[str, Proc], m: Manifest, op: Op) -> None:
    proc = procs.get(op.process)
    if proc is not None and proc.up:
        ipc.call(proc.endpoint, "ledger_leave", {"table": op.table, "attachment": m.attachment})


def detach(
    att: Attachment,
    ipc: Ipc,
    publisher: Publisher,
    *,
    host: Host | None = None,
    state_dir: Path | None = None,
    dry_run: bool = False,
) -> DetachResult:
    """Stop the publisher and reverse what is safe to reverse.

    `dry_run` runs each process's own removal checks and changes nothing, not even the publisher.
    """
    host = host or Host()
    backfill_owner.refuse_while_active(att.name, state_dir)  # a run owns it (#1454)
    # A dry run writes nothing - not even the lock file - and needs no lock: it
    # only reads, and the manifest is replaced whole, never seen half-written.
    with nullcontext() if dry_run else config.locked(att.name, state_dir):
        if not Manifest.exists(att.name, state_dir):
            raise AttachmentError(f"attachment {att.name} has not been applied: nothing to detach")
        m = Manifest.load(att.name, state_dir)
        if m.state in ("rolled_back", "detached"):
            return DetachResult("already", retained=[f"{att.name} is already {m.state}"])
        result = DetachResult("refused", dry_run=dry_run)
        if not dry_run:
            result.publisher_stopped = stop_publisher(att, publisher)
        previous = m.state
        if not dry_run and previous == "verified":
            # The publisher is stopped, so the gate must not stay open on a manifest
            # that still says `verified` whatever the rest of this detach does (#1443).
            m.set_state("detaching", "detach started: the publisher is stopped")
        try:
            return _reverse(att, ipc, m, result, host, previous, dry_run, state_dir)
        except IpcError as exc:
            raise AttachmentError(interrupted(att, m, str(exc), dry_run)) from None
        except AttachmentError as exc:
            if result.interrupted:
                raise AttachmentError(interrupted(att, m, str(exc), dry_run)) from None
            raise


def _reverse(
    att: Attachment,
    ipc: Ipc,
    m: Manifest,
    result: DetachResult,
    host: Host,
    previous: str,
    dry_run: bool,
    state_dir: Path | None = None,
) -> DetachResult:
    result.interrupted = True
    obs, structural = observe(att, ipc)
    if obs.tickerplant is None or not obs.tickerplant.up:
        raise AttachmentError(
            "the stack cannot be read, so nothing can be verified or reversed: "
            + "; ".join(f.message for f in structural)
        )
    procs = {p.procname: p for p in obs.procs() if p.up}
    unknown = [
        Blocker(*found) for found in unresolved(m, plant_ours(obs, m), procs, save=not dry_run)
    ]
    unknown += [Blocker(*found) for found in sorting.resolve_pending(m, save=not dry_run)]
    result.evidence = evidence(att, obs, host)
    if result.evidence.published:
        return _retain(att, ipc, procs, m, result)
    ours = plant_ours(obs, m)
    result.blockers = (
        unknown
        + drift(att, obs, m)
        + [Blocker(*b) for b in sorting.blockers(att, obs, ipc, m, state_dir, host)]
        + _dry_runs(ipc, m, procs, ours)
    )
    if result.blockers:
        if not dry_run:
            why = "; ".join(b.reason for b in result.blockers)
            m.set_state(
                "applied" if previous in ("verified", "detaching") else previous,
                f"detach refused: {why}",
            )
        result.interrupted = False
        return result
    result.outcome = "rolled_back"
    if dry_run:
        result.retained = ["dry run: nothing was changed"]
        result.interrupted = False
        return result
    result.interrupted = False  # _rollback reports its own failures, and sets its own state
    backups = config.directory(att.name, state_dir) / "backups"
    return _rollback(att, ipc, procs, m, result, ours, obs, backups)


def _retain(
    att: Attachment, ipc: Ipc, procs: dict[str, Proc], m: Manifest, result: DetachResult
) -> DetachResult:
    """After publication: keep every registration, row, log and block - except the
    `+uses` block, which only says this attachment still depends on a table."""
    result.outcome = "detached"
    for op in m.operations:
        if op.ownership == CREATED and op.status in (DONE, PENDING):
            result.retained.append(f"{op.op} {op.process}: {op.table}")
            if not result.dry_run:
                m.finish(op, RETAINED, note="published: kept for replay and restart")
        elif op.ownership == SHARED and op.status == DONE and not result.dry_run:
            if op.op == "schema_uses":
                # no longer a user, so a restart must not make it one again
                try:
                    result.steps.append(_unblock(att, m, op))
                except (AttachmentError, OSError) as exc:
                    result.retained.append(f"{op.op} {op.process}: {op.table} ({exc})")
                continue
            _leave(ipc, procs, m, op)
            m.finish(op, REVERTED, note="no longer a user of the table")
            result.steps.append(
                Step("ledger_leave", op.process, op.table, "left", "left the users")
            )
    if not result.dry_run:
        m.set_state("detached", "; ".join(result.evidence.reasons + result.evidence.unknown))
    return result


def _unblock(att: Attachment, m: Manifest, op: Op) -> Step:
    specs = tuple(s for s in att.tables if s.name in op.table.split(","))
    persist.remove(persist.BlockRecord(**op.post), att, m.token, specs)
    m.finish(op, REVERTED, note="block removed")
    return Step(op.op, op.process, op.table, "removed", "block removed")


def _rollback(
    att: Attachment,
    ipc: Ipc,
    procs: dict[str, Proc],
    m: Manifest,
    result: DetachResult,
    ours: set[str],
    obs: Observation | None = None,
    backups: Path | None = None,
) -> DetachResult:
    m.set_state("detaching", "pre-publication rollback")
    try:
        for op in [o for o in reversed(m.operations) if o.status in (DONE, PENDING)]:
            if op.op in sorting.SORT_OPS and op.ownership == CREATED:
                assert obs is not None and backups is not None
                steps = sorting.revert(att, obs, ipc, m, op, backups)
                result.steps += [Step(*s) for s in steps]
            elif op.op in BLOCKS and op.ownership in (CREATED, SHARED):
                result.steps.append(_unblock(att, m, op))
            elif op.ownership == SHARED:
                _leave(ipc, procs, m, op)
                m.finish(op, REVERTED, note="no longer a user of the table")
                result.steps.append(Step("ledger_leave", op.process, op.table, "left", ""))
            elif op.ownership == CREATED:
                if op.process not in procs:
                    raise AttachmentError(f"{op.process} cannot be reached to remove {op.table}")
                answer = ipc.call(
                    procs[op.process].endpoint, INVERSE[op.op], _request(m, op, False, ours)
                )
                if answer.get("status") not in ("removed", "absent"):
                    m.finish(op, PENDING, note=f"{answer.get('status')}: {answer.get('problems')}")
                    raise AttachmentError(
                        f"{op.process}: {INVERSE[op.op]}: {answer.get('status')}: "
                        f"{answer.get('problems')}"
                    )
                m.finish(op, REVERTED, note=answer["status"])
                result.steps.append(
                    Step(INVERSE[op.op], op.process, op.table, answer["status"], "")
                )
            else:
                m.finish(op, REVERTED, note="pre-existing: left as it was")
    except (IpcError, AttachmentError, OSError) as exc:
        m.set_state("rollback_partial", str(exc))
        left = [
            f"{o.op} {o.process}: {o.table}" for o in m.operations if o.status in (DONE, PENDING)
        ]
        raise AttachmentError(
            f"rollback stopped: {exc}. "
            f"Done: {[s.op + ' ' + s.process for s in result.steps] or 'nothing'}; "
            f"still in place: {left or 'nothing'}. Cross-process atomicity is not claimed: run "
            f"`uqs job detach {att.name}` again (every step is safe to repeat), or remove "
            "the rest by hand."
        ) from None
    m.set_state("rolled_back", f"{len(result.steps)} step(s)")
    return result
