"""What an attached backfill's coordination concludes (#1827).

backfill_coordination asks the native writers; this holds its vocabulary, apart from the
asking: every finding id and its `POLICY` (whether it could ever be accepted), and the
results - `Assessment`, `Coordination` and the refusal, `CoordinationRefused`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from uqs.attach.findings import Finding
from uqs.attach.overrides import Policy
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
        "confirm on the writer that its end of day has finished",
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


class CoordinationRefused(UqsError):
    """A backfill that cannot be coordinated with the native writers: every blocker at once.

    `findings` are the blockers, in the order the message lists them; `coordination` is
    what could still be established (None when the evidence for it is missing)."""

    def __init__(
        self, message: str, findings: list[Finding], coordination: Coordination | None
    ) -> None:
        super().__init__(message)
        self.findings = list(findings)
        self.coordination = coordination

    def json_details(self) -> dict[str, Any]:
        """What a --json refusal adds beside its message: each blocker's id, component,
        observed facts and whether its policy would ever allow accepting it."""
        return {
            "findings": [
                {**f.as_dict(), "overridable": POLICY[f.code].overridable} for f in self.findings
            ]
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "mechanism": "kdb+ serialises sym-file extension for every .Q.en writer (local "
            "filesystem); completed historical dates only; end-of-day fence. uqf adds no lock "
            "the native writers honour",
            "wdb": self.wdb,
            "filesystem": self.filesystem,
            "active_date": self.active_date.isoformat(),
            "last_writable_date": (self.active_date - timedelta(days=1)).isoformat(),
            "writes_stop_at": (self.next_roll - timedelta(minutes=self.guard_minutes)).isoformat(),
            "now": self.now.isoformat(),
            "next_roll": self.next_roll.isoformat(),
            "eod_guard_minutes": self.guard_minutes,
            "writers": self.writers,
            "warnings": self.warnings,
        }
