"""Who feeds whom: the process graph, and whether it is actually satisfied.

The graph already existed in three places before this module - the
`subscribe_to`/`publishes` fields on each `Pipeline`, the `.qetl.dag` registry in
q that `src/etl/generated/pipeline_dag.q` feeds, and the diagrams derived
from both. What none of them did was answer the question an operator
actually has, which is not "what is the shape of the graph" but "is the
thing I just started going to receive anything".

It will not tell you on its own. A subscriber started without its producer
subscribes SUCCESSFULLY - the table is defined on the plant whether or not
anybody publishes to it - then heartbeats, reports `up`, and receives
nothing for as long as you leave it. There is no error and no symptom
except an output table that stays empty (#290). That became easy to hit
rather than theoretical once seven processes went `startwithall=0` (#285)
and the direct-arbitrage chain landed three processes deep and entirely on
demand (#289).

WARNINGS, NOT REFUSALS. Everything here reports; nothing blocks. Starting a
subscriber before its feed is a legitimate order - it is how you avoid
missing the first batch - and several tables are published by things that
are not processes in this list at all. A guard that refused would make
those workflows impossible, which is a worse failure than the one it
prevents.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from uqs.logger import get_logger
from uqs.model.plant_schema import _publishers
from uqs.model.registry import PIPELINES

log = get_logger(__name__)

#: Tables that ALSO come from something outside the process list, and what
#: that something is.
#:
#: Additive, not a fallback. `crypto_book` and `crypto_trades` have a
#: declared producer (`cryptomock1`) *and* an external one (cryptorust's
#: recorder, which the mock exists to stand in for), and the recorder is
#: the normal case - cryptomock1 is off by default precisely so the two do
#: not interleave. Treating the declared producer as the only one would
#: make `marks1` and `executions1` warn on every healthy stack, and a
#: warning that is usually wrong is one an operator learns to scroll past,
#: which costs more than it saves.
#:
#: Every key must be a table something actually subscribes to, or the entry
#: is dead - held by a test, the same way the other exemption lists in this
#: tree are.
EXTERNAL_PRODUCERS: dict[str, str] = {
    "databento_mbp10": (
        "the external Databento feed handler (`uqs databento-feed start`) "
        "or a `databento_backfill1` run"
    ),
    "kafka_client_flow": (
        "the external Kafka consumer (`uqs kafka start`). There is no backfill "
        "alternative: replaying a topic from offset 0 is Kafka's own answer to "
        "that, and it needs the same broker"
    ),
    "crypto_book": "cryptorust's kdb recorder, which cryptomock1 stands in for",
    "crypto_trades": "cryptorust's kdb recorder, which cryptomock1 stands in for",
}


def producers_by_table(pipelines: Iterable[Any] = PIPELINES) -> dict[str, set[str]]:
    """{table: the procnames declared to publish onto it}.

    The same `_publishers` the generated `database.q` is built from, so the
    graph an operator is warned about and the schema the plant loads cannot
    describe different systems.
    """
    return _publishers(pipelines)


def inputs_by_process(pipelines: Iterable[Any] = PIPELINES) -> dict[str, tuple[str, ...]]:
    """{procname: the tables it subscribes to}.

    A process whose subscription is chosen at runtime (`tap1`, via
    `-tables`) has no fixed input to check and is absent rather than
    reported as depending on nothing.
    """
    out: dict[str, tuple[str, ...]] = {}
    for pipeline in pipelines:
        if pipeline.subscribes_dynamic:
            continue
        if subscribe_to := pipeline.subscribed_tables:
            out[pipeline.procname] = subscribe_to
    return out


def outputs_by_process(pipelines: Iterable[Any] = PIPELINES) -> dict[str, tuple[str, ...]]:
    """{procname: the tables it publishes onto}.

    Resolved the same way `_publishers` resolves them - an explicit
    `publishes` if the pipeline declares one, otherwise its single `table` -
    so this and the generated `database.q` cannot describe different systems.
    """
    out: dict[str, tuple[str, ...]] = {}
    for pipeline in pipelines:
        if declared := pipeline.published_tables:
            out[pipeline.procname] = declared
    return out


def depends_on_by_process(pipelines: Iterable[Any] = PIPELINES) -> dict[str, tuple[str, ...]]:
    """{procname: the processes that publish what it subscribes to}.

    The edge an operator actually reasons about. `inputs_by_process` answers
    "what table does this need"; this answers "and who do I have to start to
    get it", which is the question behind every `up, but idle` process.

    A table produced from outside the process list entirely - Databento's
    feed handler, cryptorust's recorder - resolves to the name in
    EXTERNAL_PRODUCERS rather than being dropped, because "nothing in this
    list provides it" and "nothing provides it" are different facts and only
    one of them is a problem. A process never lists itself: a normalizer that
    republishes onto a table it also reads would otherwise appear to be its
    own dependency.
    """
    producers = producers_by_table(pipelines)
    out: dict[str, tuple[str, ...]] = {}
    for procname, tables in inputs_by_process(pipelines).items():
        names: list[str] = []
        for table in tables:
            for source in sorted(producers.get(table, set())):
                if source != procname and source not in names:
                    names.append(source)
            if table in EXTERNAL_PRODUCERS and table not in names:
                names.append(f"({table}: external)")
        if names:
            out[procname] = tuple(names)
    return out


def dependency_rows(pipelines: Iterable[Any] = PIPELINES) -> list[dict[str, str]]:
    """One row per (process, input table): who needs what, and who provides it.

    The listing behind `uqs list dependencies`. Sorted by consumer so
    a process's inputs read together.
    """
    producers = producers_by_table(pipelines)
    rows: list[dict[str, str]] = []
    for procname, tables in sorted(inputs_by_process(pipelines).items()):
        for table in tables:
            sources = sorted(producers.get(table, set()))
            if table in EXTERNAL_PRODUCERS:
                sources.append(EXTERNAL_PRODUCERS[table])
            rows.append(
                {
                    "process": procname,
                    "needs": table,
                    "published_by": " or ".join(sources) if sources else "(nothing declares it)",
                }
            )
    return rows


def unfed_inputs(
    procname: str,
    running: Iterable[str],
    pipelines: Iterable[Any] = PIPELINES,
) -> list[str]:
    """Human-readable lines for *procname*'s inputs that nothing running publishes.

    Empty means every table it subscribes to has a declared producer that is
    up. `running` is the caller's own view of the fleet - the summary's
    status column, plus whatever the same command is about to start, so
    `uqs start marketdata1 superbook1 arbitrage1` is silent rather
    than warning about a process it starts in the same breath.

    A table with no declared producer at all is reported with what to start
    instead, from EXTERNAL_PRODUCERS, rather than as an error - some tables
    genuinely come from outside the process list.
    """
    running = set(running)
    producers = producers_by_table(pipelines)
    lines: list[str] = []
    for table in inputs_by_process(pipelines).get(procname, ()):
        declared = producers.get(table, set())
        if declared & running:
            continue
        # An external source means the table may well be flowing from
        # outside this stack, so this is context rather than a fault, and
        # it is said that way.
        external = EXTERNAL_PRODUCERS.get(table)
        if declared:
            names = " ".join(sorted(declared))
            line = (
                f"{procname} subscribes to `{table}`, which no running process "
                f"publishes - start `uqs start {names}`"
            )
            lines.append(
                f"{line}, unless it is coming from {external}." if external else f"{line}."
            )
        elif external:
            lines.append(
                f"{procname} subscribes to `{table}`, which no process in this "
                f"stack publishes - it comes from {external}."
            )
        else:
            lines.append(
                f"{procname} subscribes to `{table}`, which nothing declares a "
                f"producer for at all - check the registry."
            )
    return lines


def starved_processes(
    running: Iterable[str],
    pipelines: Iterable[Any] = PIPELINES,
) -> dict[str, list[str]]:
    """{procname: why it is starved} for every RUNNING process going hungry.

    Only processes that are themselves up: a stopped consumer with a stopped
    producer is not a fault, it is a stopped chain. A process here is one
    that answers `ps`, heartbeats, and reports `up` while receiving nothing
    - the exact state that has no other symptom.
    """
    running = set(running)
    starved = {}
    for procname in sorted(running):
        reasons = unfed_inputs(procname, running, pipelines)
        if reasons:
            starved[procname] = reasons
    return starved
