"""What an attached backfill's coordination concludes, and what may be waived (#1827, #1828).

backfill_coordination asks the native writers; this holds its vocabulary, apart from the
asking: every finding id and its `POLICY` (whether it could ever be accepted), the one
guardrail that waives one (`eod-fence`, `GUARDRAILS`, the `--skip-eod-fence` of `uqs job
backfill`), the results - `Assessment`, `Coordination` and the refusal - and the words the
plan and the run print about a waiver: "EOD fence bypassed; no EOD write deadline", never a
deadline that is not there.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from uqs.attach.findings import Finding
from uqs.attach.overrides import Policy
from uqs.guardrails import Guardrail
from uqs.paths import UqsError

_EVIDENCE = (
    "missing evidence is not a risk an operator can weigh: what the native writers do, and "
    "when, would be unknown"
)
_UNSUPPORTED = (
    "the coordination this admission relies on does not hold here, so accepting it would be "
    "accepting a corrupted HDB, not a risk"
)

#: Every finding id `examine` can emit, and whether it could ever be accepted (#1827). Only
#: end-of-day timing is: near a roll the native writers' reloads, sorts and merges are
#: likely, not certain. Missing evidence and unsupported capabilities are not.
#: `test_every_finding_coordination_can_emit_has_a_policy` keeps this whole.
POLICY: dict[str, Policy] = {
    "eod-fence": Policy(
        True,
        "the run's writes may overlap the native end of day's reload, sort and merge of the "
        "HDB, or a roll that is overdue: readers may reload a half-written partition, and a "
        "native sort may rewrite the directory under the run",
        "wait until the window has passed (writes stop at the guard before the next roll), or "
        "confirm on the writer that its end of day has finished before accepting it",
    ),
    "writer-unreachable": Policy(False, _EVIDENCE),
    "active-date-unknown": Policy(False, _EVIDENCE),
    "clock-unknown": Policy(False, _EVIDENCE),
    "active-date": Policy(
        False,
        "native end of day writes the active date and every later one: a backfill there would "
        "be overwritten or would overwrite it",
    ),
    "reload-in-progress": Policy(
        False,
        "an end-of-day reload is open on a writer: the HDB is changing under it now, not possibly",
    ),
    "native-calendar": Policy(
        False,
        "the stack stamps `time` in a data zone this backfill does not stamp: its rows would "
        "sit hours away from the live rows of the same table",
    ),
    "remote-host": Policy(False, _UNSUPPORTED),
    "network-filesystem": Policy(False, _UNSUPPORTED),
    "segmented-hdb": Policy(False, _UNSUPPORTED),
    "sort-policy": Policy(
        False,
        "the partitions would be sorted and attributed in an order the stack's own end of day "
        "never makes",
    ),
    "in-place-maintenance": Policy(
        False,
        "a process works on partitions in place, outside any lock: a backfilled partition "
        "could be rewritten mid-write",
    ),
}


def blocker(code: str, component: str, message: str, **facts: object) -> Finding:
    """A coordination blocker: `code` must be in POLICY, `facts` what was observed."""
    return Finding(
        "blocker",
        code,
        component,
        message,
        action=POLICY[code].action,
        facts={k: "" if v is None else str(v) for k, v in facts.items()},
    )


#: The one coordination finding a backfill may waive, and the guardrail that waives it.
EOD_FENCE = "eod-fence"
#: What a waived fence means, in the words the plan and the run print.
EOD_BYPASSED = "EOD fence bypassed; no EOD write deadline"
#: `uqs job backfill --skip-eod-fence` (cli/skip_options.py builds the flag from this): its
#: risk and action are POLICY's, so the text lives in one place. Only the attached path
#: (`--attachment`, run, dry-run and plan) honours it; cli/backfill.py refuses it elsewhere.
GUARDRAILS = (
    Guardrail(
        EOD_FENCE,
        ("job backfill",),
        "admit an attached backfill (--attachment) within the end-of-day fence and give it no "
        "end-of-day write deadline; every other coordination check still refuses",
        risk=POLICY[EOD_FENCE].risk,
        action=POLICY[EOD_FENCE].action,
        overridable=POLICY[EOD_FENCE].overridable,
        skip_flag=True,
    ),
)


class CoordinationRefused(UqsError):
    """A backfill that cannot be coordinated with the native writers: every blocker at once.

    `findings` are the blockers, in the order the message lists them; `coordination` is
    what could still be established (None when the evidence for it is missing)."""

    def __init__(
        self,
        message: str,
        findings: list[Finding],
        coordination: Coordination | None,
        accepted: list[Finding] | None = None,
    ) -> None:
        super().__init__(message)
        self.findings = list(findings)
        self.coordination = coordination
        #: the findings a waiver lifted (level `accepted`): lifted, and still refused for the rest
        self.accepted = list(accepted or [])

    def json_details(self) -> dict[str, Any]:
        """What a --json refusal adds beside its message: each blocker's id, component,
        observed facts and whether its policy would ever allow accepting it; and the findings
        a waiver lifted, which did not admit the run."""
        return {
            "findings": [
                {**f.as_dict(), "overridable": POLICY[f.code].overridable} for f in self.findings
            ],
            "accepted": [f.as_dict() for f in self.accepted],
        }


@dataclass
class Assessment:
    """What `examine` found: the blockers, the warnings, and the coordination facts it
    could establish whether or not anything blocks (None when evidence is missing)."""

    findings: list[Finding]
    warnings: list[str]
    coordination: Coordination | None

    @property
    def blockers(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "blocker"]


@dataclass
class Coordination:
    """What coordination established for a destination."""

    wdb: str
    active_date: date
    now: datetime
    next_roll: datetime
    guard_minutes: int
    filesystem: str
    writers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: the end-of-day fence was waived (#1828): admitted within it, and no write deadline
    eod_fence_bypassed: bool = False
    #: the blockers a waiver lifted, at level `accepted` with their reason
    accepted: list[Finding] = field(default_factory=list)
    #: the wdb's calendar for the q runner (`off=<ns>,adj=<ns>`); '' when it files by UTC midnight
    calendar: str = ""

    @property
    def writes_stop_at(self) -> datetime | None:
        """The instant the run's writes must stop - the end-of-day fence - or None when the
        fence is waived. Never the completed-history bound: that one stays."""
        if self.eod_fence_bypassed:
            return None
        return self.next_roll - timedelta(minutes=self.guard_minutes)

    def as_dict(self) -> dict[str, Any]:
        stop = self.writes_stop_at
        return {
            "mechanism": "kdb+ serialises sym-file extension for every .Q.en writer (local "
            "filesystem); completed historical dates only; end-of-day fence. uqf adds no lock "
            "the native writers honour",
            "wdb": self.wdb,
            "filesystem": self.filesystem,
            "active_date": self.active_date.isoformat(),
            "last_writable_date": (self.active_date - timedelta(days=1)).isoformat(),
            "writes_stop_at": None if stop is None else stop.isoformat(),
            "eod_fence_bypassed": self.eod_fence_bypassed,
            "now": self.now.isoformat(),
            "next_roll": self.next_roll.isoformat(),
            "eod_guard_minutes": self.guard_minutes,
            "writers": self.writers,
            "calendar": self.calendar,
            "warnings": self.warnings,
            "accepted": [f.as_dict() for f in self.accepted],
        }


def check_waivers(waived: Mapping[str, str] | None) -> dict[str, str]:
    """`waived` as {finding id: reason}, refusing an id coordination cannot emit or may never
    accept, and an empty reason: what reaches `assess` was checked by safety.given, and this
    holds a library caller to the same."""
    out = dict(waived or {})
    for code, reason in out.items():
        policy = POLICY.get(code)
        if policy is None:
            raise UqsError(f"{code!r} is not a backfill coordination finding: nothing to waive")
        if not policy.overridable:
            raise UqsError(f"{code} is never waived: {policy.risk}")
        if not str(reason).strip():
            raise UqsError(f"waiving {code} needs a reason")
    return out


def lift(
    blockers: list[Finding], waivers: Mapping[str, str]
) -> tuple[list[Finding], list[Finding]]:
    """(the blockers still standing, the ones `waivers` lifted - at level `accepted`, with
    the reason): a lifted finding is kept and never shown as passing."""
    accepted = [
        dataclasses.replace(f, level="accepted", reason=waivers[f.code])
        for f in blockers
        if f.code in waivers
    ]
    return [f for f in blockers if f.code not in waivers], accepted


def lifted_note(waivers: Mapping[str, str], accepted: list[Finding]) -> str:
    """What a refusal adds when a waiver lifted some findings and others still refuse."""
    if not accepted:
        return ""
    head = f"\n  accepted, and lifting nothing else ({', '.join(sorted(waivers))} waived):"
    return head + "".join(f"\n    {f.message}" for f in accepted)


def overrides_of(
    waived: Mapping[str, str] | None, coordination: Coordination
) -> list[dict[str, Any]]:
    """One entry per waiver given (#1828; #1829 records them): it was `requested`, with its
    reason; `needed` is whether a finding of it fired at this admission - a waiver nobody
    needed still bypasses the fence's write deadline, so `effect` says what it did either
    way - and `accepted` holds the findings it lifted (level `accepted`, never passing)."""
    out = []
    for code, reason in sorted((waived or {}).items()):
        lifted = [f for f in coordination.accepted if f.code == code]
        out.append(
            {
                "override": code,
                "reason": reason,
                "requested": True,
                "needed": bool(lifted),
                "accepted": [f.as_dict() for f in lifted],
                "effect": EOD_BYPASSED if code == EOD_FENCE else "",
            }
        )
    return out


def coordination_line(co: dict[str, Any]) -> str:
    """The coordination facts in one line: the completed-history bound, then the end-of-day
    fence - its deadline, or that it was bypassed."""
    fence = (
        EOD_BYPASSED
        if co.get("eod_fence_bypassed")
        else f"writes stop at {co['writes_stop_at']}, {co['eod_guard_minutes']} min before "
        "end of day"
    )
    return (
        f"  coordination  active write date {co['active_date']} is never touched (last writable "
        f"date {co['last_writable_date']}); {fence}; {co['filesystem']} filesystem"
    )


def override_lines(plan: dict[str, Any]) -> list[str]:
    """Each waiver, and the findings it lifted: accepted, never passing."""
    lines = []
    for o in plan.get("overrides", []):
        needed = (
            "needed: it lifted the findings below"
            if o["needed"]
            else "requested but not needed: no finding of it fired at admission"
        )
        lines.append(f"  OVERRIDE      {o['override']} ({needed}); reason: {o['reason']}")
        if o["effect"]:
            lines.append(f"                {o['effect']}")
        lines.extend(f"    accepted    {f['component']}: {f['message']}" for f in o["accepted"])
    return lines
