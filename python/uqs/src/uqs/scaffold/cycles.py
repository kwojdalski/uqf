"""Refuse a scaffold that would close a dependency cycle (#1594).

`src/etl/init.q` ends its load with `.qetl.dag.check_acyclic[]`: one job that
subscribes to a table it, or something downstream of it, publishes stops the
whole tree loading. The planner checked that subscriptions exist and which
outputs are new, never the edges themselves - so a cycle was found only after
the files were written. This reads the tree's streaming declarations and the
plan's own, joins them on the tables between them, and refuses before writing.

Only jobs that read tables can close a cycle here: a bounded worker reads its
source's remote table, which the q graph keys apart from the local one of the
same name, so it only ever writes into the graph. A reaction reads the dataset
it is triggered by and writes what `on_writing` claims - q registers it as the
job `<dataset>~<name>` - so two reactions writing each other's trigger close
one (#2826).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from uqs.model.declarations import reaction_calls, read_declarations, read_file_text
from uqs.model.producers import reaction_job
from uqs.paths import REACTION_DIR, UqsError
from uqs.scaffold.plan import ScaffoldPlan


@dataclass(frozen=True)
class Node:
    """One job in the graph: what it reads and what it writes."""

    name: str
    subscribe_to: tuple[str, ...]
    publishes: tuple[str, ...]


def _reaction_nodes(source: str) -> list[Node]:
    return [Node(reaction_job(r.dataset, r.name), (r.dataset,), r.outputs)
            for r in reaction_calls(source)]  # fmt: skip


def planned_declarations(plan: ScaffoldPlan) -> list[Node]:
    """The jobs and reactions the plan's q files declare, read as the tree
    would read them."""
    found: list[Node] = []
    for action in plan.actions:
        if action.path.suffix == ".q":
            found += [Node(d.name, d.subscribe_to, d.publishes)
                      for d in read_file_text(action.body, action.path)]  # fmt: skip
            found += _reaction_nodes(action.body)
    return found


def tree_nodes(root: Path) -> list[Node]:
    """Every job and reaction the tree at `root` declares."""
    found = [Node(d.name, d.subscribe_to, d.publishes) for d in read_declarations(root)]
    for path in sorted((root / REACTION_DIR).glob("*.q")):
        found += _reaction_nodes(path.read_text())
    return found


def dependency_cycle(declarations: list[Node]) -> list[str] | None:
    """One cycle as [job, table, job, table, ..., job], or None when acyclic.

    A job depends on another when it reads (subscribes to, or for a reaction
    is triggered by) a table the other writes - the edge
    `.qetl.dag.topological` orders by.
    """
    writers: dict[str, list[str]] = {}
    for d in declarations:
        for table in d.publishes:
            writers.setdefault(table, []).append(d.name)
    reads = {d.name: d.subscribe_to for d in declarations}
    state: dict[str, int] = {}  # 1 on the current path, 2 done
    path: list[str] = []

    def visit(job: str) -> list[str] | None:
        state[job] = 1
        path.append(job)
        for table in reads.get(job, ()):
            for writer in writers.get(table, []):
                if state.get(writer) == 1:
                    return _with_tables(path[path.index(writer) :], reads, writers)
                if writer not in state and (found := visit(writer)):
                    return found
        path.pop()
        state[job] = 2
        return None

    for job in sorted(reads):
        if job not in state and (found := visit(job)):
            return found
    return None


def _with_tables(
    jobs: list[str], reads: dict[str, tuple[str, ...]], writers: dict[str, list[str]]
) -> list[str]:
    """The cycle through `jobs` as [job, table, ..., job], closed on its first.

    The DFS walks readers to writers, so the cycle is reversed into the
    direction rows flow: writer, the table it publishes, the job reading it.
    """
    flow = list(reversed(jobs))
    out: list[str] = []
    for i, job in enumerate(flow):
        nxt = flow[(i + 1) % len(flow)]
        table = next(t for t in reads[nxt] if job in writers.get(t, []))
        out += [job, table]
    return [*out, flow[0]]


def refuse_cycles(root: Path, plan: ScaffoldPlan) -> None:
    """Raise when the tree plus `plan` would no longer load as an acyclic graph."""
    new = planned_declarations(plan)
    names = {d.name for d in new}
    for d in new:
        overlap = sorted(set(d.subscribe_to) & set(d.publishes))
        if overlap:
            raise UqsError(
                f"{d.name} would subscribe to {', '.join(overlap)} and publish it too - a job "
                "that reads its own output is a cycle, and the tree would not load"
            )
    tree = [d for d in tree_nodes(root) if d.name not in names]
    cycle = dependency_cycle(tree + new)
    if cycle:
        raise UqsError(
            "this job would close a dependency cycle, and src/etl/init.q's "
            f"check_acyclic would stop the tree loading: {' -> '.join(cycle)}"
        )
