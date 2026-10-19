"""Apply: register the tables, subscribe the consumers, persist - journalled.

The order is the safety argument, as in the bounded worker's publish-before-
claim: tickerplant first (nothing can subscribe to a table it lacks), then each
consumer, then the schema file. An interruption at any point leaves a stack
that is either as it was or holds a table nothing publishes to yet, never one
that claims more than exists - and the schema file, the only step that changes
what a RESTART does, is last, so an interrupted apply restarts back to the
pre-attachment stack.

Each step is journalled `pending` before its call and finished after it
(manifest.py). A step whose call fails with the connection - outcome unknown -
stays `pending`; the next apply asks the process (our token is on what we
created) instead of guessing. A step the process reports as failed is `failed`
and the process undid its own partial work in the same call (stp_register.q).
Nothing here rolls other steps back on a failure: that would be a second
sequence of remote changes with failures of its own, and the issue is explicit
that atomicity across processes is not claimed. The result says exactly what
is done, what is pending and what was not started.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from uqs.attach import config, manifest, persist, plan
from uqs.attach.config import Attachment, AttachmentError
from uqs.attach.facts import Facts
from uqs.attach.findings import Finding, blockers
from uqs.attach.manifest import CREATED, DONE, EXISTING, FAILED, SHARED, Manifest, Op
from uqs.attach.remote import Ipc, IpcError
from uqs.attach.schema import TableSpec
from uqs.attach.topology import Proc


class ApplyRefused(AttachmentError):
    """The preflight found a blocker; nothing was changed and no manifest written."""

    def __init__(self, findings: list[Finding]) -> None:
        self.findings = findings
        super().__init__(
            "refused: "
            + "; ".join(f"{f.code} ({f.component})" for f in findings)
            + " - `uqs job attach NAME --dry-run` shows the detail"
        )


class RisksNotConfirmed(AttachmentError):
    """The plan carries accepted risks and nobody confirmed applying with them.

    `--yes` skips the question "change the stack as above?"; it is not an answer to
    "apply with these risks accepted?", which is its own confirmation."""

    def __init__(self, accepted: list[Finding]) -> None:
        self.accepted = accepted
        super().__init__(
            "apply would proceed with accepted risks: "
            + "; ".join(f"{f.code} ({f.component})" for f in accepted)
            + " - confirm them explicitly (--accept-risks, or answer the prompt); "
            "--yes alone does not"
        )


class ApplyFailed(AttachmentError):
    """A step failed after others had been applied. `result` says exactly where."""

    def __init__(self, message: str, result: ApplyResult) -> None:
        super().__init__(message)
        self.result = result


@dataclass
class Step:
    op: str
    process: str
    table: str
    #: created | adopted | subscribed | shared | already | failed | pending | not-started
    outcome: str
    detail: str = ""


@dataclass
class ApplyResult:
    state: str
    steps: list[Step] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def mutations(self) -> int:
        return sum(
            1 for s in self.steps if s.outcome in ("created", "adopted", "subscribed", "shared")
        )


def _columns(spec: TableSpec) -> list[list[str]]:
    return spec.as_pairs()


def _request(m: Manifest, att: Attachment, spec: TableSpec) -> dict:
    return {
        "table": spec.name,
        "columns": _columns(spec),
        "token": m.token,
        "attachment": att.name,
        "join": True,
    }


def refuse_detaching(name: str, state: str) -> None:
    """Refuse to attach over a detach that is in progress or was interrupted (#1443)."""
    if state == "detaching":
        raise AttachmentError(
            f"attachment {name} is detaching: a detach is in progress or was interrupted. "
            "The publisher is stopped and its start gate is closed. Run "
            f"`uqs job detach {name}` to finish it, then attach again."
        )


def _fresh(att: Attachment, state_dir: Path | None) -> Manifest:
    path = Manifest.path_for(att.name, state_dir)
    if not path.is_file():
        return Manifest.new(att, path)
    m = Manifest.load(att.name, state_dir)
    refuse_detaching(att.name, m.state)
    if m.state == "rolled_back":
        # Everything it made was removed: a new run is a new attachment, with a new token
        # (the old one must not match anything left behind), and the history stays.
        fresh = Manifest.new(att, path)
        fresh.history = [*m.history, *fresh.history]
        return fresh
    if set(m.tables) != {t.name for t in att.tables}:
        raise AttachmentError(
            f"attachment {att.name} was applied for tables {sorted(m.tables)}, not "
            f"{sorted(t.name for t in att.tables)}: detach it first"
        )
    return m


def _mine(proc: Proc, table: str, token: str) -> bool:
    owned = (proc.probe or {}).get("owned", {})
    return table in owned and owned[table].get("token") == token


def _in_place(proc: Proc, spec: TableSpec, token: str, consumer: bool) -> bool:
    """The step's effect is visible on `proc` right now - what makes skipping its call safe."""
    p = proc.probe or {}
    if not p.get("schemas", {}).get(spec.name):
        return False
    if consumer and spec.name not in p.get("subscribed", []):
        return False
    return True


def _joined(proc: Proc, table: str, name: str) -> bool:
    return name in (proc.probe or {}).get("owned", {}).get(table, {}).get("users", [])


def _register(m: Manifest, ipc: Ipc, att: Attachment, tp: Proc, spec: TableSpec) -> Step:
    prior = m.done("stp_register", tp.procname, spec.name)
    if prior is not None and _in_place(tp, spec, m.token, False):
        if prior.ownership == SHARED and not _joined(tp, spec.name, att.name):
            pass  # the plant forgot we use it (a restart without our +uses block): join again
        elif prior.ownership != CREATED or _mine(tp, spec.name, m.token):
            return Step("stp_register", tp.procname, spec.name, "already")
    probe = tp.probe or {}
    op = m.begin(
        "stp_register",
        tp.procname,
        spec.name,
        {"table_present": bool(probe.get("schemas", {}).get(spec.name)),
         "multilog": probe.get("multilog"), "tables": probe.get("published", [])},
    )  # fmt: skip
    try:
        answer = ipc.call(tp.endpoint, "stp_register", _request(m, att, spec))
    except IpcError as exc:
        m.finish(op, manifest.PENDING, note=f"outcome unknown: {exc.reason}")
        raise
    return _interpret(m, op, answer, tp.procname, spec.name)


def _interpret(m: Manifest, op: manifest.Op, answer: dict, process: str, table: str) -> Step:
    status = answer.get("status")
    if status in ("created", "owned"):
        post = {k: answer[k] for k in ("logs", "handle", "replayed") if k in answer}
        m.finish(
            op, DONE, CREATED, post, "resumed: our token was already on it" * (status == "owned")
        )
        return Step(op.op, process, table, "created" if status == "created" else "already",
                    "" if status == "created" else "our token was already on it")  # fmt: skip
    if status == "shared":
        m.finish(op, DONE, SHARED, {"owner": answer.get("owner"), "users": answer.get("users")})
        return Step(op.op, process, table, "shared", f"created by attachment {answer.get('owner')}")
    if status == "existing" and answer.get("matches", True):
        m.finish(op, DONE, EXISTING, {}, "pre-existing; never removed")
        return Step(op.op, process, table, "adopted", "pre-existing, same shape")
    if status == "subscribed":
        m.finish(op, DONE, EXISTING, {}, "pre-existing table; never removed")
        return Step(op.op, process, table, "subscribed", "pre-existing table, subscription added")
    detail = answer.get("detail") or "the table exists with another shape"
    m.finish(op, FAILED, note=str(detail))
    raise AttachmentError(f"{process}: {table}: {status}: {detail}")


def _attach(m, ipc, att, c: Proc, spec: TableSpec, tp: Proc, depends: list[int]) -> Step:
    prior = m.done("consumer_attach", c.procname, spec.name)
    if prior is not None and _in_place(c, spec, m.token, True):
        if prior.ownership != CREATED or _mine(c, spec.name, m.token):
            return Step("consumer_attach", c.procname, spec.name, "already")
    before = _subscribers(ipc, tp, spec.name)
    earlier = m.find("consumer_attach", c.procname, spec.name)
    was = earlier.ownership if earlier is not None else ""
    op = m.begin(
        "consumer_attach", c.procname, spec.name,
        {"table_present": bool((c.probe or {}).get("schemas", {}).get(spec.name)),
         "subscribers_before": before}, depends,
    )  # fmt: skip
    try:
        answer = ipc.call(c.endpoint, "consumer_attach", _request(m, att, spec))
        after = _subscribers(ipc, tp, spec.name)
    except IpcError as exc:
        m.finish(op, manifest.PENDING, note=f"outcome unknown: {exc.reason}")
        raise
    plant = m.find("stp_register", tp.procname, spec.name)
    ours = plant is not None and plant.ownership == CREATED
    rebuilt = ours and (was == CREATED or not op.pre.get("table_present"))
    if answer.get("status") == "subscribed" and ours and not rebuilt:
        # Saved with the consumer_attach step, in one write, so the subscription is never
        # left unrecorded (#1459).
        _subscription(m, op, answer, c.procname, spec.name, before, after)
    step = _interpret(m, op, answer, c.procname, spec.name)
    if op.ownership == EXISTING and rebuilt:
        # The tickerplant's table is ours, and the consumer's copy did not exist before
        # this attachment (the first attempt found it absent) or an earlier run already
        # made it: a restart rebuilt it from our table. A copy the consumer held BEFORE
        # the attachment is also answered `existing` and must stay so (#1437) - detach
        # never removes pre-existing state.
        op.ownership = CREATED
        m.save()
    op.post = {**op.post, "subscribers_before": before, "subscribers_after": after}
    m.save()
    return step


def _subscription(m: Manifest, attach: Op, answer: dict, process: str, table: str,
                  before: int, after: int) -> None:  # fmt: skip
    """Record, as this attachment's own, the subscription it added to a table the
    consumer already held (#1459). The table stays `existing` and is never removed;
    the subscription is `created`, so a rollback takes back exactly it
    (consumer_unsubscribe.q) and the tickerplant's unregister counts its handle."""
    entry = m.find("consumer_subscribe", process, table)
    if entry is None:
        entry = Op(len(m.operations) + 1, "consumer_subscribe", process, table)
        m.operations.append(entry)
    entry.status, entry.ownership, entry.at = DONE, CREATED, manifest.now()
    entry.depends_on, entry.pre = [attach.id], {"table_present": True}
    entry.post = {
        "subscribers_before": before,
        "subscribers_after": after,
        "subtables": bool(answer.get("subtables")),
    }
    entry.note = "subscription added to a pre-existing table"


def _subscribers(ipc: Ipc, tp: Proc, table: str) -> int:
    return int(ipc.call(tp.endpoint, "probe", {"tables": [table]})["subscribers"].get(table, 0))


def _persist(
    m: Manifest, att: Attachment, tp: Proc, specs: list[TableSpec], deps: list[int],
    uses: bool = False,
) -> Step:  # fmt: skip
    """The schema-file block for the tables this attachment created - or, with
    `uses`, the `+uses` block for the ones it shares, so the dependency is back
    after a restart too (#1446)."""
    kind, ownership = ("schema_uses", SHARED) if uses else ("schema_block", CREATED)
    block = persist.uses_name(att.name) if uses else att.name
    path = (tp.probe or {}).get("schemafile", "")
    name = ",".join(s.name for s in specs)
    prior = m.done(kind, "schema-file", name)
    if prior is not None:
        record = persist.BlockRecord(**prior.post)
        if not persist.problems(record, att, m.token, tuple(specs)):
            return Step(kind, path, name, "already")
    op = m.begin(kind, "schema-file", name, {"path": path}, deps)
    try:
        present = persist.present(path, block)
        if uses:
            record = persist.write_uses(att, m.token, tuple(s.name for s in specs), path)
        else:
            record = persist.write(att, m.token, tuple(specs), path)
    except (OSError, AttachmentError) as exc:
        m.finish(op, FAILED, note=str(exc))
        raise AttachmentError(f"the schema file {path}: {exc}") from None
    m.finish(op, DONE, ownership, asdict(record))
    if present:
        return Step(kind, path, name, "already", "the block was already there")
    return Step(kind, path, name, "created", f"block added to {path}")


def apply(
    att: Attachment,
    ipc: Ipc,
    root: Path,
    *,
    facts: Facts | None = None,
    state_dir: Path | None = None,
    confirmed_risks: str = "",
) -> ApplyResult:
    """Register `att`'s tables on the stack it names. Idempotent: on a stack it
    already converged, no call that changes anything is made.

    Raises ApplyRefused (before touching anything) when the preflight has a
    blocker, ApplyFailed when a step fails after others were applied.
    """
    with config.locked(att.name, state_dir):
        m = _fresh(att, state_dir)
        facts = facts or Facts()
        facts.token = m.token
        planned = plan.build(att, ipc, root, facts)
        if not planned.ok:
            raise ApplyRefused(blockers(planned.findings))
        if planned.accepted and not confirmed_risks:
            raise RisksNotConfirmed(planned.accepted)
        obs = planned.observation
        tp = obs.tickerplant
        assert tp is not None  # no blocker means one was found
        m.risk_acceptance = {}
        if planned.accepted:
            m.risk_acceptance = {
                "confirmed_by": confirmed_risks,
                "at": manifest.now(),
                "overrides": [o.as_dict() for o in att.overrides],
                "findings": [f.as_dict() for f in planned.accepted],
            }
            m.event(
                "risks accepted", ", ".join(f"{f.code}:{f.component}" for f in planned.accepted)
            )
        m.set_state("applying", f"{len(att.tables)} table(s), {len(obs.consumers)} consumer(s)")
        result = ApplyResult("applying", findings=planned.findings)
        try:
            deps: list[int] = []
            for spec in att.tables:
                result.steps.append(_register(m, ipc, att, tp, spec))
                entry = m.find("stp_register", tp.procname, spec.name)
                deps.append(entry.id if entry else 0)
            for spec in att.tables:
                for c in obs.consumers:
                    result.steps.append(_attach(m, ipc, att, c, spec, tp, deps))
            made = [
                s for s in att.tables
                if (e := m.find("stp_register", tp.procname, s.name)) and e.ownership == CREATED
            ]  # fmt: skip
            used = [
                s for s in att.tables
                if (e := m.find("stp_register", tp.procname, s.name)) and e.ownership == SHARED
            ]  # fmt: skip
            if att.persist == "schema-file" and made:
                result.steps.append(_persist(m, att, tp, made, deps))
            if att.persist == "schema-file" and used:
                result.steps.append(_persist(m, att, tp, used, deps, uses=True))
        except (IpcError, AttachmentError) as exc:
            m.set_state("partial", str(exc))
            result.state = "partial"
            raise ApplyFailed(_failure(m, result, exc), result) from None
        m.set_state("applied", f"{result.mutations()} change(s)")
        result.state = "applied"
        return result


def _failure(m: Manifest, result: ApplyResult, exc: Exception) -> str:
    done = [f"{o.op} {o.process} {o.table}" for o in m.operations if o.status == DONE]
    pending = [
        f"{o.op} {o.process} {o.table}" for o in m.operations if o.status == manifest.PENDING
    ]
    failed = [f"{o.op} {o.process} {o.table}: {o.note}" for o in m.operations if o.status == FAILED]
    return (
        f"apply stopped: {exc}. The stack is partially changed (no cross-process atomicity is "
        f"claimed). done: {done or 'nothing'}; outcome unknown: {pending or 'none'}; "
        f"failed: {failed or 'none'}. Re-run `uqs job attach {m.attachment}` (it is "
        f"idempotent and resolves unknown steps by asking the process), or "
        f"`uqs job detach {m.attachment}` to reverse."
    )
