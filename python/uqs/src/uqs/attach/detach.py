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
  * A failure part way is reported as exactly that; no atomicity across
    processes is claimed, and every step is safe to repeat.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path

from uqs.attach import config, persist
from uqs.attach.apply import Step
from uqs.attach.config import Attachment, AttachmentError
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
from uqs.attach.publisher import Publisher
from uqs.attach.remote import Ipc, IpcError
from uqs.attach.schema import differences
from uqs.attach.topology import Observation, Proc, observe

#: The q operation that undoes each forward one.
INVERSE = {"consumer_attach": "consumer_detach", "stp_register": "stp_unregister"}
#: The schema-file blocks: the created tables' (`created`) and the shared ones' `+uses` (`shared`).
BLOCKS = ("schema_block", "schema_uses")


@dataclass(frozen=True)
class Blocker:
    component: str
    reason: str
    action: str


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


# -------------------------------------------------------------------- drift


def plant_ours(obs: Observation, m: Manifest) -> set[str]:
    """The tables whose tickerplant record carries this attachment's token. A
    consumer's copy of such a table is ours too, even with no record of its own:
    a consumer that restarted rebuilt it from the tickerplant."""
    tp = (obs.tickerplant.probe or {}) if obs.tickerplant else {}
    return {t for t, led in tp.get("owned", {}).items() if led.get("token") == m.token}


def drift(att: Attachment, obs: Observation, m: Manifest) -> list[Blocker]:
    """Everything that makes a reversal unsafe or unprovable right now."""
    found: list[Blocker] = []
    procs = {p.procname: p for p in obs.procs()}
    for op in m.operations:
        if op.status not in (DONE, PENDING) or op.ownership not in (CREATED, SHARED):
            continue
        if op.op in BLOCKS:
            found += _block(att, m, op)
        elif (proc := procs.get(op.process)) is None or not proc.up:
            found.append(
                Blocker(
                    op.process,
                    "not reachable, so what this attachment created there cannot be verified",
                    f"start {op.process}, or remove {op.table} by hand once it is safe",
                )
            )
        elif op.ownership == CREATED:
            found += _table_state(att, m, op, proc, plant_ours(obs, m))
    stp = next((o for o in m.operations if o.op == "stp_register"), None)
    for op in m.operations:
        if op.op == "consumer_attach" and op.ownership == CREATED and op.status in (DONE, PENDING):
            if stp is None or stp.ownership != CREATED:
                found.append(
                    Blocker(
                        op.process,
                        f"{op.table} was attached here through a tickerplant table this "
                        "attachment does not own",
                        "removing that subscription needs a tickerplant-side change this "
                        "workflow does not make; unsubscribe by hand",
                    )
                )
    return found


def _block(att: Attachment, m: Manifest, op: Op) -> list[Blocker]:
    specs = tuple(s for s in att.tables if s.name in op.table.split(","))
    problems = persist.problems(persist.BlockRecord(**op.post), att, m.token, specs)
    where = op.post.get("path")
    return [
        Blocker(
            "schema file",
            p,
            f"restore {where} to its recorded state, or edit the block out by hand",
        )
        for p in problems
    ]


def _table_state(att: Attachment, m: Manifest, op: Op, proc: Proc, ours: set[str]) -> list[Blocker]:
    p, t = proc.probe or {}, op.table
    seen = p.get("schemas", {}).get(t, [])
    if not seen:
        return []  # already gone: a retry after a partial rollback; the q operation says "absent"
    out: list[Blocker] = []
    if differences(att.table(t), seen):
        out.append(
            Blocker(
                proc.procname,
                f"{t} no longer has the shape this attachment created",
                "it was altered by someone else; leave it, or drop it by hand",
            )  # fmt: skip
        )
    ledger = p.get("owned", {}).get(t)
    follows = op.op == "consumer_attach" and op.table in ours
    if (ledger is None and not follows) or (ledger is not None and ledger.get("token") != m.token):
        out.append(
            Blocker(
                proc.procname,
                f"{t} carries no ownership record of this attachment",
                "ownership cannot be established; leave it",
            )  # fmt: skip
        )
    elif ledger is not None and ledger.get("users"):
        out.append(
            Blocker(
                proc.procname,
                f"{t} is used by attachment(s) {', '.join(ledger['users'])}",
                "detach those first, or keep the table",
            )  # fmt: skip
        )
    return out


# ------------------------------------------------------------------ steps


def _subscribers_added(m: Manifest) -> int:
    """The tickerplant subscriber handles this attachment's consumers added.
    Reverted consumers count: the tickerplant still lists their handles until
    the table itself is unregistered, which is the last step."""
    return sum(
        max(0, int(o.post.get("subscribers_after", 0)) - int(o.post.get("subscribers_before", 0)))
        for o in m.operations
        if o.op == "consumer_attach" and o.ownership == CREATED and o.status != FAILED
    )


def _request(m: Manifest, op: Op, dry_run: bool, ours: set[str]) -> dict:
    req: dict = {"table": op.table, "token": m.token, "dry_run": dry_run}
    if op.op == "consumer_attach" and op.table in ours:
        req["follows_plant"] = True
    if op.op == "consumer_attach":
        stp = next((o.process for o in m.operations if o.op == "stp_register"), "")
        req["tickerplant"] = stp
    if op.op == "stp_register":
        req |= {"empty_size": EMPTY_LOG, "expect_subscribers": _subscribers_added(m)}
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


def _stop_publisher(att: Attachment, publisher: Publisher) -> bool:
    if not publisher.running(att):
        return False
    publisher.stop(att)
    if publisher.running(att):
        raise AttachmentError(
            f"{att.publisher.procname} is still listening after stop - detach will not touch the "
            "stack while the publisher may be publishing"
        )
    return True


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

    `dry_run` reads everything and runs each process's own removal checks, and
    changes nothing - it does not even stop the publisher.
    """
    host = host or Host()
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
            result.publisher_stopped = _stop_publisher(att, publisher)
        previous = m.state
        obs, structural = observe(att, ipc)
        if obs.tickerplant is None or not obs.tickerplant.up:
            raise AttachmentError(
                "the stack cannot be read, so nothing can be verified or reversed: "
                + "; ".join(f.message for f in structural)
            )
        procs = {p.procname: p for p in obs.procs() if p.up}
        result.evidence = evidence(att, obs, host)
        if result.evidence.published:
            return _retain(att, ipc, procs, m, result)
        ours = plant_ours(obs, m)
        result.blockers = drift(att, obs, m) + _dry_runs(ipc, m, procs, ours)
        if result.blockers:
            if not dry_run:
                why = "; ".join(b.reason for b in result.blockers)
                m.set_state(
                    "applied" if previous == "verified" else previous, f"detach refused: {why}"
                )
            return result
        result.outcome = "rolled_back"
        if dry_run:
            result.retained = ["dry run: nothing was changed"]
            return result
        return _rollback(att, ipc, procs, m, result, ours)


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
) -> DetachResult:
    m.set_state("detaching", "pre-publication rollback")
    try:
        for op in [o for o in reversed(m.operations) if o.status in (DONE, PENDING)]:
            if op.op in BLOCKS and op.ownership in (CREATED, SHARED):
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
