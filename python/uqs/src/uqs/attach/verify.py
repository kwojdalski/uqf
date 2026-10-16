"""Verify: can the consumers receive and persist the table - without publishing?

A canary row would be the obvious test and the wrong one: it lands in the real
table, is written to the real tickerplant log, and reaches the HDB at end of
day, so it would turn "before publication", when everything is still takeable
back, into "after", when nothing is. So verification only READS: every piece
of state that a row would pass through is checked to be there and consistent,
and the one thing a row cannot be shown to do without sending one - arrive - is
covered by checking the pieces it arrives through (the subscriber handle on
the tickerplant, the consumer's own subscription and table, its table list at
the next save, and the log a replaying subscriber would read).

On success the attachment becomes `verified`, which is the only state in which
the publisher may start (stack/attach_state.py). A failed re-verification of
a verified attachment takes it back to `applied`: a gate that stays open after
the stack stopped matching would be worse than none.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from uqs.attach import config, persist, plan
from uqs.attach.config import Attachment, AttachmentError
from uqs.attach.facts import Facts
from uqs.attach.manifest import CREATED, DONE, Manifest
from uqs.attach.preflight import identity
from uqs.attach.remote import Ipc
from uqs.attach.schema import TableSpec, differences
from uqs.attach.topology import Observation, Proc, observe
from uqs.paths import repo_root


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class VerifyResult:
    checks: list[Check]
    state: str

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


def _tickerplant(
    att: Attachment, m: Manifest, tp: Proc, spec: TableSpec, wanted: int
) -> list[Check]:
    p, t, who = tp.probe or {}, spec.name, tp.procname
    seen = p.get("schemas", {}).get(t, [])
    shape = differences(spec, seen) if seen else ["absent"]
    logs = p.get("logfiles", {}).get(t, [])
    holders = int(p.get("subscribers", {}).get(t, 0))
    out = [
        Check(f"{who}: {t} is a pub/sub table", t in p.get("published", [])),
        Check(f"{who}: {t} has the planned shape", not shape, "; ".join(shape)),
        Check(f"{who}: {t} is logged and rolled with the rest", bool(p.get("logged", {}).get(t))),
        Check(f"{who}: {t} gets its time stamp", bool(p.get("updtab", {}).get(t))),
        Check(
            f"{who}: {t}'s log is open and on disk",
            bool(logs) and bool(logs[0]["open"]) and logs[0]["size"] >= 0,
            ", ".join(f"{f['name']} ({f['size']} bytes)" for f in logs) or "no log",
        ),
        Check(
            f"{who}: a replaying subscriber would find {t}'s log",
            int(p.get("replay", {}).get(t, 0)) >= 1,
        ),
        Check(
            f"{who}: {wanted} consumer(s) hold a subscription to {t}",
            holders >= wanted,
            f"{holders} subscriber handle(s)",
        ),
    ]
    op = m.find("stp_register", tp.procname, t)
    if op is not None and op.ownership == CREATED:
        mine = p.get("owned", {}).get(t, {}).get("token") == m.token
        out.append(Check(f"{who}: {t} carries this attachment's ownership record", mine))
    return out


def _consumer(m: Manifest, c: Proc, spec: TableSpec) -> list[Check]:
    p, t, who = c.probe or {}, spec.name, c.procname
    seen = p.get("schemas", {}).get(t, [])
    shape = differences(spec, seen) if seen else ["absent"]
    out = [
        Check(f"{who}: {t} exists with the planned shape", not shape, "; ".join(shape)),
        Check(f"{who}: subscribed to {t}", t in p.get("subscribed", [])),
        Check(f"{who}: does not ignore {t}", t not in p.get("ignorelist", [])),
    ]
    if c.proctype == "rdb":
        out.append(
            Check(
                f"{who}: {t} is in .rdb.subtables (reload drops its rows)",
                t in p.get("subtables", []),
            )
        )
    else:
        out.append(
            Check(f"{who}: {t} is in the next save's table list", t in p.get("tablelist", []))
        )
    op = m.find("consumer_attach", who, t)
    if op is not None and op.ownership == CREATED:
        ledger = p.get("owned", {}).get(t)
        # a consumer that restarted rebuilt the table from the tickerplant and lost its
        # in-memory record: the plant's record is then the one that counts
        mine = ledger is None or ledger.get("token") == m.token
        out.append(Check(f"{who}: {t} is not another attachment's", mine))
    return out


def _schema_file(m: Manifest, att: Attachment, specs: tuple[TableSpec, ...]) -> list[Check]:
    block = next((o for o in m.operations if o.op == "schema_block" and o.status == DONE), None)
    if att.persist != "schema-file" or block is None:
        return []
    created = tuple(s for s in specs if s.name in block.table.split(","))
    found = persist.problems(persist.BlockRecord(**block.post), att, m.token, created)
    return [
        Check(
            "the schema file still holds this attachment's block, unchanged",
            not found,
            "; ".join(found),
        )
    ]


def run(att: Attachment, obs: Observation, m: Manifest) -> list[Check]:
    out: list[Check] = []
    for p in obs.procs():
        if not p.up:
            out.append(Check(f"{p.procname} answers", False, p.error))
        elif found := [f for f in identity(att, p) if f.code != "TORQ_VERSION"]:
            out.append(Check(f"{p.procname} is on this stack", False, found[0].message))
    tp = obs.tickerplant
    if tp is None or not tp.up:
        return out + [Check("the tickerplant answers", False)]
    for spec in att.tables:
        out += _tickerplant(att, m, tp, spec, len(obs.consumers))
        for c in obs.consumers:
            if c.up:
                out += _consumer(m, c, spec)
    return out + _schema_file(m, att, att.tables)


def accepted_risks(att: Attachment, ipc: Ipc, root: Path, m: Manifest) -> list[Check]:
    """Every accepted risk, re-evaluated against the stack as it is NOW.

    An acceptance is for the facts it was made against. If they changed - another
    TorQ version, another missing sort column, another process on the port - it is
    void, and verification fails rather than keep permitting what was never
    accepted. A still-valid one passes, and says in the check's name that it is a
    risk accepted, not a thing that works."""
    now = plan.build(att, ipc, root, Facts(token=m.token))
    out = [
        Check(f"accepted risk {f.code}:{f.component} still holds as accepted", True, f.reason)
        for f in now.accepted
    ]
    out += [
        Check(f"{f.component} runs a TorQ this procedure was tested on", False, f.message)
        for f in now.findings
        if f.level == "blocker" and f.code == "TORQ_VERSION"
    ]
    out += [
        Check("an accepted risk is still valid", False, problem)
        for problem in now.override_problems
        if "stale" in problem or "not overridable" in problem
    ]
    return out


def verify(
    att: Attachment, ipc: Ipc, state_dir: Path | None = None, root: Path | None = None
) -> VerifyResult:
    """Read-only checks; records `verified` (or takes it back) in the manifest."""
    with config.locked(att.name, state_dir):
        if not Manifest.exists(att.name, state_dir):
            raise AttachmentError(f"attachment {att.name} has not been applied")
        m = Manifest.load(att.name, state_dir)
        if m.state not in ("applied", "verified"):
            raise AttachmentError(
                f"attachment {att.name} is {m.state}: verify checks an applied attachment "
                f"(`uqs job attach apply {att.name}`)"
            )
        obs, structural = observe(att, ipc)
        checks = [Check(f.component, False, f.message) for f in structural] + run(att, obs, m)
        checks += accepted_risks(att, ipc, root or repo_root(), m)
        result = VerifyResult(checks, m.state)
        if result.ok and m.state != "verified":
            m.set_state("verified", f"{len(checks)} check(s) passed")
        elif not result.ok and m.state == "verified":
            m.set_state(
                "applied",
                "re-verification failed: " + checks[[c.ok for c in checks].index(False)].name,
            )
        result.state = m.state
        return result
