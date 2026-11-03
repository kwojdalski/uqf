"""Who writes each table: the job graph's producer map, read from the tree (#1862).

q already has the whole map. `.qetl.dag.producers`, after
`.qetl.dag.adopt_all[]`, names every job that writes a table: streaming jobs,
normalizers, bounded workers and reactions, each by job name. Python used to
keep three partial copies of it instead. `jobs.bounded_producers` re-parsed the
worker files and saw only bounded workers. `dependencies.producers_by_table`
and `plant_schema._publishers` saw only streaming publishers. No copy had a
reaction's output: `deal_positions`, which `demo_deals~rebuild_positions`
writes, had no producer anywhere in Python.

This module is that map, read from the tree's text, because uqs needs it
without q, and for trees q has not loaded: a job `uqs job new` has just
written, or a bundle being planned. The narrower maps are projections of it:

    jobs.bounded_producers   the datasets a bounded worker fills, which are
                             the only ones a reaction can watch
    plant_producers          the streaming jobs' processes, which publish
                             onto the plant; dependencies.producers_by_table
                             is held equal to it

It is held to q in both directions, by test_producer_map.py. The test compares
it with `uqs.generated.q_facts.DAG_PRODUCERS`, which `derive.py q-facts`
exports from q and a hook `--check`s. Where q is available, it also compares
it with a live `.qetl.dag.producers`. So a job shape this reader misses (an
`.qetl.reaction.on_worker` file, a new declaration verb) fails a test rather
than dropping out of the map.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from uqs.model.declarations import reaction_calls, read_declarations
from uqs.model.pipeline import PipelineKind
from uqs.paths import REACTION_DIR

#: q's names for the kinds of job that write tables (`.qetl.dag.kinds`).
BOUNDED = "bounded"
STREAM = "stream"
NORMALIZER = "normalizer"
REACTION = "reaction"

#: The kinds whose output goes onto the plant. A bounded worker and a reaction
#: write the HDB through their IO manager and never publish to the tickerplant.
PLANT_KINDS = frozenset({STREAM, NORMALIZER})


@dataclass(frozen=True)
class Producer:
    """One job that writes a table, and the processes it runs in.

    A reaction has no process of its own. It runs inside whichever bounded
    worker publishes the dataset it watches, so its `processes` are those.
    """

    job: str
    kind: str
    processes: tuple[str, ...]


def reaction_job(dataset: str, name: str) -> str:
    """The job name q registers a reaction under, as `.qetl.dag.reaction_job`
    builds it: a reaction's name is unique within its dataset, not globally."""
    return f"{dataset}~{name}"


def producers(root: Path) -> dict[str, tuple[Producer, ...]]:
    """{table: every job that writes it}, ordered by job name as q orders them.

    The jobs are the ones `.qetl.dag.adopt_all[]` registers from what this
    tree declares. Streaming jobs and normalizers write what they publish. A
    bounded worker writes its dataset. A reaction writes what `on_writing`
    claims for it; a plain `on` claims nothing.
    """
    found: dict[str, list[Producer]] = {}
    for d in read_declarations(root):
        if d.kind is PipelineKind.BACKFILL:
            kind, tables = BOUNDED, (d.dataset,) if d.dataset else ()
        else:
            kind = NORMALIZER if d.kind is PipelineKind.NORMALIZER else STREAM
            tables = d.publishes
        for table in dict.fromkeys(tables):
            found.setdefault(table, []).append(Producer(d.name, kind, (d.procname,)))
    fillers = {
        table: tuple(p for job in jobs if job.kind == BOUNDED for p in job.processes)
        for table, jobs in found.items()
    }
    for path in sorted((root / REACTION_DIR).glob("*.q")):
        for r in reaction_calls(path.read_text()):
            job = Producer(reaction_job(r.dataset, r.name), REACTION, fillers.get(r.dataset, ()))
            for table in dict.fromkeys(r.writes):
                found.setdefault(table, []).append(job)
    return {t: tuple(sorted(found[t], key=lambda p: p.job)) for t in sorted(found)}


def job_producers(root: Path) -> dict[str, tuple[str, ...]]:
    """{table: the jobs that write it}: the shape of `.qetl.dag.producers`."""
    return {t: tuple(p.job for p in ps) for t, ps in producers(root).items()}


def processes_by_table(root: Path, kinds: frozenset[str]) -> dict[str, tuple[str, ...]]:
    """{table: the processes that run its producers of `kinds`}, in job order.

    A table none of whose producers is of `kinds` is absent.
    """
    out: dict[str, tuple[str, ...]] = {}
    for table, jobs in producers(root).items():
        procs = [p for job in jobs if job.kind in kinds for p in job.processes]
        if procs:
            out[table] = tuple(dict.fromkeys(procs))
    return out


def plant_producers(root: Path) -> dict[str, set[str]]:
    """{table: the processes that publish it onto the plant}."""
    return {t: set(ps) for t, ps in processes_by_table(root, PLANT_KINDS).items()}
