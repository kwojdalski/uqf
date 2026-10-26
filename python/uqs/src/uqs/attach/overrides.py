"""Explicit, narrow, auditable acceptance of an attachment blocker (#1435).

A blocker is the workflow refusing to continue. Sometimes the operator knows
better - the site runs a TorQ the procedure was never tested on - and editing
the validation code is neither appropriate nor auditable. So a blocker may be
ACCEPTED, under rules that keep the acceptance honest:

  * Scoped to one finding CODE on one COMPONENT (`TORQ_VERSION:stp1`). There is
    no blanket force, and an acceptance for stp1 says nothing about rdb1.
  * Only codes the central POLICY table marks overridable. Proven schema
    incompatibility, ambiguous ownership, anything that needs a restart of a
    shared process, and every destructive-cleanup protection are not: an
    override accepts a RISK, it never establishes that something works.
  * A mandatory reason, and who accepted it, when, and against which OBSERVED
    FACTS. The facts are what make it safe to keep: the acceptance is void the
    moment they change (another TorQ version, another missing sort column, another
    process on the port), and is re-evaluated at every plan, validate, apply and
    verify.
  * The finding stays visible - level `accepted`, with its reason and the
    concrete failure risk and operator action - and is never shown as passing.
  * Applying with accepted risks needs its own confirmation (apply.py), which
    `--yes` does not give.

Accepting an untested TorQ version does NOT add it to `SUPPORTED_TORQ` and says
nothing about compatibility: the finding is kept, only the refusal is lifted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from uqs.attach.findings import Finding
from uqs.paths import UqsError


class OverrideError(UqsError):
    """An override that is not allowed, not understood, or no longer valid."""


@dataclass(frozen=True)
class Policy:
    overridable: bool
    #: overridable: the concrete failure this risks; otherwise why it cannot be accepted
    risk: str
    #: overridable: what the operator must do about it
    action: str = ""


_NO_RESTART = "fixing it needs a restart of a shared process, which this workflow never does"

#: Every finding code a preflight can raise, and whether it may be accepted. Test
#: `test_every_code_the_preflight_raises_has_a_policy` keeps this complete.
POLICY: dict[str, Policy] = {
    "TORQ_VERSION": Policy(
        True,
        "the TorQ internals this procedure drives (.stpps, .stplg, .stpm, .sub) are unverified on "
        "this version: registration may misbehave or fail part way. Accepting does NOT add the "
        "version to the tested list and does not establish compatibility",
        "run the attach-stack suite against this version; prefer --runtime-only until a restart "
        "has been shown to reload the registration; run verify after apply",
    ),
    "SORT_CONFIG": Policy(
        True,
        "the wdb's end-of-day sort of this table will fail or sort it wrongly, so the day's rows "
        "stay unsorted in the wdb and do not reach the HDB",
        "add a sort.csv row for the table (or the column it names) on the wdb before end of day",
    ),
    "PORT_CONFLICT": Policy(
        True,
        "the publisher cannot bind its port, so it fails to start, or it takes the port of "
        "another process in the stack",
        "free the port, or resolve the clashing name, before starting the publisher; the start "
        "gate still needs verify to pass",
    ),
    "PUBLISHER_RUNNING": Policy(
        True,
        "rows published while the table is being registered may be discarded or routed to some "
        "consumers only, and detach cannot rely on a stopped publisher",
        "stop the publisher before apply (or accept that rows published meanwhile may be lost); "
        "run verify afterwards",
    ),
    "SCHEMA_INCOMPATIBLE": Policy(
        False, "a proven schema mismatch: publishers send columns by position"
    ),
    "UNTYPED_COLUMN": Policy(
        False,
        "an untyped column can change its stored type from one batch to the next, which makes "
        "the wdb's write fail and aborts its end of day for every table",
    ),
    "NAME_RESERVED": Policy(
        False,
        "the name is already bound to something else on the process (a TorQ global, a view, a "
        "keyword); registering the table would overwrite it for every table",
    ),
    "NOT_PUBLISHED": Policy(False, "the table is not a pub/sub table; ownership of it is unclear"),
    "STACK_MISMATCH": Policy(
        False, "the process belongs to another stack; identity is not negotiable"
    ),
    "LOGGING_MODE": Policy(
        False, "a shared log cannot be attributed to a table or taken back; " + _NO_RESTART
    ),
    "LOGS_DISABLED": Policy(False, "the table would not be replayable; " + _NO_RESTART),
    "CHAINED_TP": Policy(False, "a chained tickerplant takes its tables from its parent"),
    "CONSUMER_FILTERED": Policy(
        False, "the table would never be persisted; its filter is stack configuration"
    ),
    "CONSUMER_IGNORES": Policy(
        False, "the table would never be persisted; the ignore list is stack configuration"
    ),
    "WDB_MODE": Policy(False, "this wdb does not save, so nothing would persist the table"),
    "STORAGE_MISMATCH": Policy(
        False, "a recorded location is not where the stack keeps it; plan again"
    ),
    "PERSIST_UNAVAILABLE": Policy(
        False, "use --runtime-only to accept losing the registration on a restart"
    ),
    "DISCOVERY_UNREACHABLE": Policy(False, "nothing can be verified without discovery"),
    "UNREACHABLE": Policy(False, "a process that cannot be asked cannot be verified"),
    "ADVERTISED_LOOPBACK": Policy(
        False, "the stack would connect back to its own machine, never to the publisher"
    ),
    "ADVERTISED_UNRESOLVABLE": Policy(
        False, "the stack cannot connect back to a publisher whose name it cannot resolve"
    ),
    "PUBLISHER_UNREACHABLE_FROM_STACK": Policy(
        False,
        "the stack tried to connect back to the running publisher and could not: discovery "
        "would list a process it cannot use, and no acceptance makes the path exist",
    ),
    "NO_TICKERPLANT": Policy(False, "there is nothing to publish to"),
    "AMBIGUOUS_TICKERPLANT": Policy(False, "name the tickerplant with --tickerplant"),
    "NO_CONSUMER": Policy(False, "nothing would receive and persist the table"),
    "UNKNOWN_CONSUMER": Policy(False, "the named consumer does not exist"),
    "SORT_DECLARATION": Policy(False, "the declared sort rule is one TorQ cannot apply"),
    "SORT_CONFLICT": Policy(
        False, "an attachment never replaces an operator's sort policy for a table"
    ),
    "SORT_FILE": Policy(False, "the declared rows cannot be written to the sort.csv from here"),
    "SORT_DRIFT": Policy(
        False, "a reload would also apply an edit to sort.csv that is not this attachment's"
    ),
    "SORT_RELOAD_UNAVAILABLE": Policy(
        False, "rows in a file that no process has loaded are not configuration"
    ),
}

#: Codes that are not refusals at all - warnings and what the plan will do.
NOT_BLOCKERS = frozenset(
    {
        "WILL_CREATE", "WILL_ADOPT", "ALREADY_APPLIED", "SHARED_TABLE", "WILL_PERSIST",
        "ALREADY_PERSISTED", "RUNTIME_ONLY", "PUBLISHER_PORT_UNKNOWN", "UNMANAGED_CONSUMER",
        "CHAINED_UNMANAGED", "HDB_HISTORY", "SORT_RULES_ADD", "SORT_RULES_PRESENT",
        "SORT_FILE_UNCHECKED", "SORT_READER_STOPPED",
    }
)  # fmt: skip


@dataclass(frozen=True)
class Override:
    """One accepted risk, as saved in attachment.json and the manifest."""

    code: str
    component: str
    reason: str
    operator: str
    at: str
    #: the observed facts it was accepted against; the acceptance is void if they change
    facts: dict[str, str]

    @property
    def key(self) -> str:
        return f"{self.code}:{self.component}"

    def as_dict(self) -> dict:
        return asdict(self)


def parse_spec(spec: str) -> tuple[str, str]:
    """`CODE:COMPONENT` -> (code, component), refusing anything else."""
    code, sep, component = spec.partition(":")
    if not sep or not code or not component or ":" in component or " " in spec:
        raise OverrideError(
            f"--override takes CODE:COMPONENT, e.g. TORQ_VERSION:stp1 - not {spec!r}. "
            "There is no blanket override; name the finding and the process it is about"
        )
    return code.upper(), component


def observed(findings: list[Finding], code: str, component: str) -> dict[str, str]:
    """The facts behind every `code` finding on `component`, merged: one component
    can raise a code several times (a sort.csv problem per table), and an
    acceptance covers what was seen at the time, all of it."""
    merged: dict[str, set[str]] = {}
    for f in findings:
        if f.code == code and f.component == component:
            for k, v in f.facts.items():
                merged.setdefault(k, set()).add(v)
    return {k: ",".join(sorted(v)) for k, v in merged.items()}


def create(specs: list[str], reason: str, findings: list[Finding], operator: str) -> list[Override]:
    """The overrides `specs` ask for, validated against what was just observed.

    Refuses: no reason; a code that is unknown; a code policy does not allow
    (naming why); and a finding that is not there - an acceptance has to be for
    something observed, or it would be a standing waiver for whatever comes.
    """
    if specs and not reason.strip():
        raise OverrideError('an override needs a reason: pass --override-reason "..."')
    made: list[Override] = []
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    for spec in specs:
        code, component = parse_spec(spec)
        policy = POLICY.get(code)
        if policy is None:
            known = ", ".join(sorted(c for c, p in POLICY.items() if p.overridable))
            raise OverrideError(f"{code} is not a finding code ({known} can be accepted)")
        if not policy.overridable:
            raise OverrideError(f"{code} cannot be overridden: {policy.risk}")
        present = [f for f in findings if f.code == code and f.component == component]
        if not present:
            seen = sorted({f.component for f in findings if f.code == code})
            raise OverrideError(
                f"nothing to accept: the stack shows no {code} finding for {component!r}"
                + (f" (it shows one for {', '.join(seen)})" if seen else "")
            )
        facts = observed(findings, code, component)
        made.append(Override(code, component, reason.strip(), operator, now, facts))
    return made


def merge(current: tuple[Override, ...], new: list[Override]) -> tuple[Override, ...]:
    """`new` replaces a current override for the same code and component."""
    keep = [o for o in current if o.key not in {n.key for n in new}]
    return tuple(keep + new)


def evaluate(
    overrides: tuple[Override, ...], findings: list[Finding]
) -> tuple[list[Finding], list[str]]:
    """`findings` with every validly accepted blocker turned into `accepted`.

    Returns the findings and the problems with the overrides themselves. A
    blocker stays a blocker - with a message saying why - when its override is
    stale (the facts differ), not allowed by policy, or for another component.
    A blocker no override names is untouched.
    """
    out: list[Finding] = []
    problems: list[str] = []
    used: set[str] = set()
    for f in findings:
        match = next(
            (o for o in overrides if o.code == f.code and o.component == f.component), None
        )
        if f.level != "blocker" or match is None:
            out.append(f)
            continue
        used.add(match.key)
        policy = POLICY.get(f.code)
        if policy is None or not policy.overridable:
            problems.append(f"{match.key}: not overridable")
            out.append(f)
        elif match.facts != (now := observed(findings, f.code, f.component)):
            problems.append(
                f"{match.key}: the acceptance is stale - accepted against {match.facts}, "
                f"the stack now shows {now}"
            )
            out.append(
                Finding(
                    "blocker",
                    f.code,
                    f.component,
                    f"{f.message} [an earlier acceptance no longer applies: it was for "
                    f"{match.facts}, the stack now shows {now}]",
                    f.action,
                    f.facts,
                )
            )
        else:
            out.append(accepted(f, match, policy))
    for o in overrides:
        if o.key not in used:
            problems.append(f"{o.key}: no longer needed - the stack shows no such finding")
    return out, problems


def accepted(f: Finding, o: Override, policy: Policy) -> Finding:
    return Finding(
        "accepted",
        f.code,
        f.component,
        f"ACCEPTED RISK - {f.message}. Risk: {policy.risk}. Reason given by {o.operator} "
        f"({o.at}): {o.reason}",
        policy.action,
        f.facts,
        o.reason,
    )


def stale(problems: list[str]) -> list[str]:
    return [p for p in problems if "stale" in p]
