"""The process graph as trees, for `uqs graph`: who feeds whom, and through what.

An edge runs from a producer to a consumer and carries the tables the one
publishes and the other subscribes to - the same declarations behind
`uqs summary`'s graph columns (dependencies.py), so the two cannot disagree.
A table from outside the stack (EXTERNAL_PRODUCERS) and a bounded worker's
source get a producer named the way `Depends on` names them,
`(<table>: external)`.

The graph is not a tree: the plant's feeds reach most of the fleet. It is
drawn as one anyway, because a tree is what reads at thirty processes, and
the price is paid explicitly: a process reached a second time is shown as a
REPEAT - named, marked, not expanded again - so the drawing stays the size of
the graph rather than the number of paths through it. A cycle is cut the
same way.

Two directions. DOWNSTREAM starts at what nothing feeds and descends to what
reads from it: "if this stops, what starves". UPSTREAM starts at what nothing
reads and descends to what it needs: "what must be up for this to work". A
process with no declared edges at all - a vendored TorQ process - is a root
in both, so every process appears.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from uqs.model import dependencies
from uqs.model.registry import PIPELINES

Direction = Literal["downstream", "upstream"]


@dataclass(frozen=True)
class Edge:
    producer: str
    consumer: str
    tables: tuple[str, ...]


@dataclass
class Node:
    """One place a process appears in a tree.

    `tables` are what the edge from its parent carries, empty at a root.
    `repeat` marks a process already drawn elsewhere, and so not expanded.
    """

    name: str
    tables: tuple[str, ...] = ()
    repeat: bool = False
    children: list[Node] = field(default_factory=list)


def edges(pipelines: Iterable[Any] = PIPELINES) -> list[Edge]:
    """Every producer -> consumer edge, with the tables it carries."""
    pipelines = list(pipelines)
    producers = dependencies.producers_by_table(pipelines)
    carried: dict[tuple[str, str], list[str]] = defaultdict(list)
    for consumer, tables in dependencies.inputs_by_process(pipelines).items():
        for table in tables:
            sources = [p for p in sorted(producers.get(table, set())) if p != consumer]
            if table in dependencies.EXTERNAL_PRODUCERS:
                sources.append(f"({table}: external)")
            for producer in sources:
                carried[(producer, consumer)].append(table)
    for consumer, (reads, _writes, needs) in dependencies.worker_edges(pipelines).items():
        for producer in needs:
            carried[(producer, consumer)].extend(reads)
    return [Edge(p, c, tuple(t)) for (p, c), t in sorted(carried.items())]


def _adjacency(
    graph: Sequence[Edge], direction: Direction
) -> dict[str, list[tuple[str, tuple[str, ...]]]]:
    """{node: [(next node, tables)]}, descending in `direction`."""
    out: dict[str, list[tuple[str, tuple[str, ...]]]] = defaultdict(list)
    for edge in graph:
        if direction == "downstream":
            out[edge.producer].append((edge.consumer, edge.tables))
        else:
            out[edge.consumer].append((edge.producer, edge.tables))
    return out


def roots(processes: Iterable[str], graph: Sequence[Edge], direction: Direction) -> list[str]:
    """Where a tree starts: every node with nothing above it in `direction`."""
    nodes = set(processes) | {e.producer for e in graph} | {e.consumer for e in graph}
    above = {e.consumer if direction == "downstream" else e.producer for e in graph}
    return sorted(nodes - above, key=_order)


def _order(name: str) -> tuple[bool, str]:
    """Processes first, then the external producers, each alphabetically."""
    return (name.startswith("("), name)


def forest(
    processes: Iterable[str],
    graph: Sequence[Edge],
    direction: Direction,
    root: str | None = None,
    collapse: bool = True,
) -> list[Node]:
    """The graph as trees, from `root` alone or from every root.

    With `collapse`, a process is expanded the first time it is reached and
    shown as a repeat after that. Without it every path is drawn in full,
    which is what a filter needs - a match must not hide behind a repeat -
    and a cycle is still cut, since it would never end.
    """
    adjacency = _adjacency(graph, direction)
    expanded: set[str] = set()

    def grow(name: str, tables: tuple[str, ...], path: frozenset[str]) -> Node:
        # Only a process with something beneath it is marked: drawing a leaf
        # twice hides nothing, so a repeat there would be noise.
        hides = bool(adjacency.get(name))
        if hides and (name in path or (collapse and name in expanded)):
            return Node(name, tables, repeat=True)
        expanded.add(name)
        below = sorted(adjacency.get(name, []), key=lambda pair: _order(pair[0]))
        return Node(name, tables, children=[grow(n, t, path | {name}) for n, t in below])

    starts = [root] if root is not None else roots(processes, graph, direction)
    return [grow(name, (), frozenset()) for name in starts]


def unconnected(trees: Sequence[Node]) -> tuple[list[Node], list[str]]:
    """The trees that have edges, and the names of the roots that have none.

    A process with no declared edge - most of the vendored TorQ fleet - is a
    tree of one, and thirty of those read as noise between the trees that
    say something, so a drawing lists them together instead.
    """
    connected = [t for t in trees if t.children]
    return connected, [t.name for t in trees if not t.children]
