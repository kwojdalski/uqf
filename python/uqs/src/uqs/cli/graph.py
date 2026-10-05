"""`uqs graph`: every process and who feeds whom, as a tree.

The trees come from model/process_graph.py; this draws them. Each process is
coloured by whether it is up - the same status `uqs summary` reads - unless
`--offline` skips asking, and each edge is labelled with the tables it
carries, so a reader sees not just that markout1 needs the plant but which
of its tables.

Without `--interactive` the trees print once, as Rich trees, so the view
works in a pipe or a doc. With it they open in a Textual app: type to
filter (the `--interactive` table rule, on process and table names, keeping
the path to every match), ctrl+t to turn the trees the other way up, and a
panel describing the highlighted process. Enter exits printing its name.
"""

from __future__ import annotations

import sys
from typing import Annotated

import typer
from rich.console import Group
from rich.text import Text
from rich.tree import Tree

from uqs.cli import completion
from uqs.cli.shared import InteractiveOpt, PortOpt, _die, _paths, app, console, log
from uqs.cli.table_filter import match_row, terms
from uqs.model import process_graph
from uqs.model.process_graph import Direction, Node
from uqs.model.registry import DEFAULT_BASE_PORT
from uqs.paths import UqsError
from uqs.stack import listing, runtime

STATUS_STYLE = {"up": "bold green", "down": "red"}


def prune(trees: list[Node], query: str) -> list[Node]:
    """The trees cut down to the nodes `query` matches, and the paths to them.

    A node matches on its name or the tables its edge carries, by the same
    fuzzy rule as an `--interactive` table. Everything under a match is kept:
    finding a process should show what hangs off it too.
    """
    row_terms = terms(query)
    if not row_terms:
        return trees
    kept = []
    for node in trees:
        if match_row(row_terms, [node.name, ", ".join(node.tables)]) is not None:
            kept.append(node)
            continue
        if children := prune(node.children, query):
            kept.append(Node(node.name, node.tables, node.repeat, children))
    return kept


def label(node: Node, status: dict[str, str], direction: Direction) -> Text:
    """One node's line: its name in its status colour, then the edge's tables."""
    if node.name.startswith("("):
        text = Text(node.name, style="italic dim")
    else:
        text = Text(node.name, style=STATUS_STYLE.get(status.get(node.name, ""), ""))
    if node.tables:
        verb = "reads" if direction == "downstream" else "provides"
        text.append(f"  {verb} {', '.join(node.tables)}", style="dim")
    if node.repeat:
        text.append("  ↺ shown above", style="dim italic")
    return text


def rich_trees(trees: list[Node], status: dict[str, str], direction: Direction) -> Group:
    """The trees as Rich renderables, for printing."""

    def grow(branch: Tree, node: Node) -> None:
        for child in node.children:
            grow(branch.add(label(child, status, direction)), child)

    connected, alone = process_graph.unconnected(trees)
    drawn: list[Tree | Text] = []
    for node in connected:
        tree = Tree(label(node, status, direction), guide_style="dim")
        grow(tree, node)
        drawn.append(tree)
    if alone:
        line = Text("no declared edges: ", style="dim")
        for index, name in enumerate(alone):
            line.append(", " if index else "", style="dim")
            line.append(name, style=STATUS_STYLE.get(status.get(name, ""), ""))
        drawn.append(line)
    return Group(*drawn)


def live_status(port: int) -> dict[str, str]:
    """{procname: up/down}, as `uqs summary` reads it; empty when the fleet cannot say."""
    paths = _paths()
    try:
        result = runtime.summary(paths, base_port=port, timeout=30)
        ports = listing.configured_ports(paths, base_port=port)
    except UqsError as exc:
        log.debug("graph drawn without status: {}", exc)
        return {}
    return {row["Process"]: row["Status"] for row in listing.summary_rows(result.stdout, ports)}


def describe(name: str, info: dict[str, dict[str, str]], status: dict[str, str]) -> Text:
    """The side panel for one process: what `uqs list processes` knows, and its status."""
    if name.startswith("("):
        return Text(f"{name}\n\nProduced outside the stack - nothing here to start.")
    row = info.get(name, {})
    text = Text(name + "\n\n", style="bold")
    fields = [("status", status.get(name, "not asked"))]
    fields += [
        (k, row.get(k, "")) for k in ("proctype", "port", "startwithall", "inputs", "outputs")
    ]
    for key, value in fields:
        text.append(f"{key:<13}", style="dim")
        text.append(f"{value or '-'}\n")
    return text


@app.command()
def graph(
    proc: Annotated[
        str | None,
        typer.Argument(
            help="Draw only the tree from this process; omit for every process",
            autocompletion=completion.procname,
        ),
    ] = None,
    upstream: Annotated[
        bool,
        typer.Option(
            "--upstream",
            help="Descend to what each process needs, rather than to what reads from it.",
        ),
    ] = False,
    offline: Annotated[
        bool,
        typer.Option("--offline", help="Do not ask the fleet what is up; draw the graph alone."),
    ] = False,
    port: PortOpt = DEFAULT_BASE_PORT,
    interactive: InteractiveOpt = False,
) -> None:
    """Every process and who feeds whom, as a tree, with the tables on each edge.

    Downstream by default - from what nothing feeds, to what reads from it:
    "if this stops, what starves". `--upstream` turns it over: "what must be
    up for this to work". A process reached twice is drawn once and marked
    the second time, so the tree is the size of the graph. Colours are up
    (green) and down (red), from the same check as `uqs summary`.
    """
    paths = _paths()
    try:
        info = {r["procname"]: r for r in listing.list_items(paths, "processes", base_port=port)}
    except UqsError as exc:
        _die(exc)
        return
    edges = process_graph.edges()
    known = set(info) | {e.producer for e in edges} | {e.consumer for e in edges}
    if proc is not None and proc not in known:
        _die(UqsError(f"no process {proc!r} - `uqs list processes` names them"))
        return
    status = {} if offline else live_status(port)
    direction: Direction = "upstream" if upstream else "downstream"
    if not interactive:
        trees = process_graph.forest(info, edges, direction, root=proc)
        console.print(rich_trees(trees, status, direction))
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        _die(UqsError("--interactive needs a terminal; drop it to print the graph"))
        return
    from uqs.cli.graph_browser import browser

    chosen = browser(list(info), edges, status, info, direction, proc).run()
    if chosen is not None:
        console.print(chosen, markup=False, highlight=False)
