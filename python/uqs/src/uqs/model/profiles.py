"""Named start sets, each a closure over the dependency graph.

THE CONSTRAINT THIS EXISTS FOR. The licence caps a q process at
`LICENCE_CONNECTION_LIMIT` concurrent connections and `INBOUND_RESERVE` are
held back for ad-hoc handles, leaving fourteen tickerplant slots. The default
start holds thirteen. So the arbitrage chain - four processes - cannot run
without first stopping something, and the only dial was
`uqs config-set <proc> startwithall 0/1`: one process at a time, persisted to
disk, easy to forget you changed.

Past the cap nothing complains. The plant RESETS the extra connection, the
process wedges in its retry loop in scripts/processes/torq_stream.q, and
`uqs summary` still reports it `up` because that is a PID check. Which jobs
actually run then depends on which fourteen won the race to start (#285).

A PROFILE NAMES LEAVES, NOT PROCESSES. `arbitrage` is `crossarb1` and
`arbitrage1`; everything they need is derived from
`dependencies.depends_on_by_process()`, the same graph `summary`'s
"Depends on" column and its "up, but idle" warning are built from. A
hand-kept list of processes would be a second copy of that graph, free to
disagree with it the first time a job gains an input - which is the failure
this tree keeps finding and deleting. Name the thing you want; the tool works
out what it rests on.

CLOSURES STOP AT AN EXTERNALLY-FED TABLE, and that is not an optimisation.
`crypto_book` and `crypto_trades` have a declared in-stack producer,
`cryptomock1`, AND an external one - cryptorust's recorder, which the mock
stands in for. cryptomock1's own declaration says: "start it INSTEAD of them,
never as well as - it publishes onto the same two tables, and an invented
ladder or fill must not interleave with a real one". A mechanical closure
over `posbook1` pulls cryptomock1 in and would recommend exactly that
interleaving. So a table in `dependencies.EXTERNAL_PRODUCERS` counts as
satisfied, and the mock is reachable only by naming it - which is the
documented workflow, and why `crypto` is a profile of its own.

VERIFIED AGAINST A RUNNING STACK, 2026-09-24. The arithmetic here is
computed from the registry, so it was worth checking against the wire before
anyone relied on it. `lsof -nP -iTCP:6050 -sTCP:ESTABLISHED` on a stack
running the default set showed fifteen established connections against a
predicted thirteen, and both extras are outside what this counts:

    13  the clients this module predicts - name for name, no difference
     1  stp1 itself, which is the LISTENER rather than a client
     1  cryptorust's kdb-market-data-recorder, an external process that is
        in no Pipeline and so in no profile

So `plant_slots` is right about the processes it knows, and blind to
anything outside `PIPELINES` that opens a handle. That is a real limit
rather than a rounding error: an external recorder holds a slot the budget
cannot see, and the two held back by INBOUND_RESERVE are what absorb it.

WHY PYTHON AND NOT q. Every other fact about a process is declared in its q
file and read back by model/declarations.py, `autostart` included - so
"does this start by default" already lives in q. A profile does not: it spans
jobs, so no single job can own it, and nothing in q consumes it. Declaring it
in q would mean parsing it back out for a reader that is only ever the CLI.
"""

from __future__ import annotations

from collections.abc import Iterable

from uqs.model.dependencies import (
    EXTERNAL_PRODUCERS,
    inputs_by_process,
    producers_by_table,
)
from uqs.model.pipeline import PipelineKind
from uqs.model.pipeline_edges import (
    INBOUND_RESERVE,
    LICENCE_CONNECTION_LIMIT,
    VENDORED_PLANT_CLIENTS,
)
from uqs.model.registry import PIPELINES
from uqs.paths import UqsError

#: The vendored TorQ processes every profile needs: the plant itself, service
#: discovery, the databases, the writedown path, the gateway and the
#: monitoring that makes `summary`'s Heartbeat column answer.
#:
#: Taken from the vendored rows that start by default today rather than
#: chosen afresh, so a profile start produces the same infrastructure a
#: `start all` does and nothing moves underneath this change. Four of these
#: hold a plant slot (VENDORED_PLANT_CLIENTS); the rest do not subscribe.
CORE_INFRA: tuple[str, ...] = (
    "discovery1",
    "stp1",
    "rdb1",
    "hdb1",
    "hdb2",
    "wdb1",
    "sort1",
    "sortworker1",
    "sortworker2",
    "gateway1",
    "monitor1",
    "housekeeping1",
    "sctp1",
    "metrics1",
)

#: What each profile is FOR, keyed by name, valued by the leaves it wants.
#:
#: Leaves, not members: see the module docstring. A leaf is the process whose
#: output you actually came for, and adding one to a chain does not mean
#: editing whatever profile contains it.
PROFILES: dict[str, tuple[str, ...]] = {
    #: What a `start all` runs today, named so it can be asked about and
    #: diffed. Deliberately NOT the union of the others.
    "default": ("posbook1", "markout1", "fxpositions1", "quotesfeed1"),
    #: Positions, P&L and execution quality on the FX chain.
    "fx": ("posbook1", "markout1", "fxpositions1"),
    #: Cross-source and cross-currency opportunities, three processes deep.
    "arbitrage": ("arbitrage1", "crossarb1"),
    #: The depth-aware book path: a wide feed folded into vector columns, and
    #: synthetic crosses off the same quotes.
    "depth": ("vectorize1", "cross1"),
    #: cryptorust's recorders replaced by the in-tree mock. INSTEAD of the
    #: real ones, never alongside - see the module docstring.
    "crypto": ("cryptomock1",),
}

#: Standing processes no profile reaches, and why each is deliberate.
#:
#: The same shape as EXTERNAL_PRODUCERS and the catalog's hidden list: an
#: exemption carries its reason, so "nobody got round to it" cannot pass as
#: "deliberately not in a set". A process that is in neither a profile nor
#: here fails `test_every_standing_process_is_reachable`.
#:
#: Backfills are not here and do not need to be: they are bounded, hold no
#: plant connection and belong to no standing set.
UNPROFILED: dict[str, str] = {
    "databento1": (
        "a live external feed - its rows come from the Databento handler "
        "(`uqs databento-feed start`), so it is started with that or not at all"
    ),
    "tap1": (
        "a diagnostic subscriber chosen at runtime: which table it taps is an "
        "argument, so there is no standing set it belongs to"
    ),
}

#: Plant slots a profile may hold.
ALLOWANCE = LICENCE_CONNECTION_LIMIT - INBOUND_RESERVE


def _procnames() -> set[str]:
    return {pipeline.procname for pipeline in PIPELINES}


def closure(leaves: Iterable[str]) -> set[str]:
    """Every uqf process `leaves` need, themselves included.

    Walks inputs to the processes that publish them, transitively. A table in
    `EXTERNAL_PRODUCERS` is treated as already fed and its in-stack producer
    is NOT pulled in - the module docstring says why that matters rather than
    merely being tidy.

    Vendored infrastructure is not here: it is `CORE_INFRA`, needed by every
    profile and not derivable from a graph that only knows uqf's own jobs.
    """
    inputs = inputs_by_process()
    producers = producers_by_table()
    seen: set[str] = set()
    pending = list(leaves)
    while pending:
        procname = pending.pop()
        if procname in seen:
            continue
        seen.add(procname)
        for table in inputs.get(procname, ()):
            if table in EXTERNAL_PRODUCERS:
                continue
            pending.extend(sorted(producers.get(table, ())))
    return seen


def plant_slots(procnames: Iterable[str]) -> int:
    """Tickerplant connections `procnames` would hold, with the vendored ones.

    Two exclusions, both load-bearing:

    * A BACKFILL is bounded - it registers with discovery, runs its window
      and exits, and never subscribes to the plant. The same distinction
      `verify_pipeline_edges` draws when it counts the default start.
    * A VENDORED process is counted only if it is in
      `VENDORED_PLANT_CLIENTS`. Most of `CORE_INFRA` opens no plant handle:
      the gateway queries the databases, discovery is registered WITH, and
      stp1 is the plant. Counting every infrastructure process as a client
      put a bare `crypto` profile over a budget it uses five slots of.
    """
    kinds = {pipeline.procname: pipeline.kind for pipeline in PIPELINES}
    subscribers = {
        procname
        for procname in procnames
        if procname in kinds and kinds[procname] is not PipelineKind.BACKFILL
    }
    return len(subscribers | VENDORED_PLANT_CLIENTS)


def resolve(names: Iterable[str]) -> tuple[str, ...]:
    """Every process to start for `names`, infrastructure first.

    Order matters to nothing here - torq.sh starts what it is given - but
    infrastructure leads so the printed list reads as the stack does, and a
    reader can see the plant is included.

    Raises rather than guessing on an unknown name: a typo that silently
    resolved to a smaller set would start a subset and look like it worked.
    """
    wanted = list(names)
    unknown = [name for name in wanted if name not in PROFILES]
    if unknown:
        raise UqsError(
            f"unknown profile(s) {', '.join(sorted(unknown))} - "
            f"known profiles are {', '.join(sorted(PROFILES))}"
        )
    leaves = [leaf for name in wanted for leaf in PROFILES[name]]
    members = closure(leaves)
    return CORE_INFRA + tuple(sorted(members))


def over_budget(names: Iterable[str]) -> str | None:
    """Why `names` cannot be started together, or None when they fit.

    A REFUSAL rather than the advisory warning a positional start gets: the
    operator named a set by a name this tree defined, so a set that cannot
    run is this tree's mistake to report, not theirs to discover when a
    handle is reset.
    """
    wanted = sorted(names)
    held = plant_slots(resolve(wanted))
    if held <= ALLOWANCE:
        return None
    return (
        f"profile(s) {', '.join(wanted)} need {held} tickerplant connections, "
        f"and only {ALLOWANCE} are available ({LICENCE_CONNECTION_LIMIT} on this "
        f"licence, {INBOUND_RESERVE} held back for ad-hoc handles). The plant "
        f"resets the extras rather than refusing them, so the processes past the "
        f"cap wedge in their retry loop while still reporting `up`. Start fewer "
        f"profiles, or stop what you are not using."
    )
