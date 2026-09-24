"""The graph columns of `uqs summary`: what each process depends on, reads and
writes, rendered as cells.

Split out of cli/summary.py, which had reached the package's 400-line module
limit; this is the one part of it with no dependence on the rest.
"""

from __future__ import annotations

from uqs.model import dependencies

#: How many table names go on one line of a graph cell before it wraps.
#:
#: Rich would wrap these on its own, but on whitespace and at whatever width
#: is left over - so `fx_position` and `fx_limit_breach` could break as
#: `fx_position, fx_` / `limit_breach`, splitting a name across lines. These
#: cells are lists, and a reader scans them by counting entries, so the break
#: belongs between entries and nowhere else.
GRAPH_CELL_ITEMS_PER_LINE = 2


def graph_cell(items: tuple[str, ...] | list[str]) -> str:
    """A list of table or process names, broken across lines at the commas.

    Empty renders as a dim dash rather than blank: "this process declares no
    inputs" and "this column has nothing to say about it" look identical
    otherwise, and the first is a real fact about a feed.
    """
    if not items:
        return "[dim]-[/]"
    lines = [
        ", ".join(items[i : i + GRAPH_CELL_ITEMS_PER_LINE])
        for i in range(0, len(items), GRAPH_CELL_ITEMS_PER_LINE)
    ]
    # Every line but the last keeps its trailing comma, so a wrapped cell
    # still reads as one list rather than as separate values per line.
    return "\n".join(line + "," if i < len(lines) - 1 else line for i, line in enumerate(lines))


def attach_graph_columns(rows: list[dict[str, str]]) -> None:
    """Fill the graph columns on each row, in place.

    Derived from the same `Pipeline` declarations `verify_pipeline_edges`
    checks and `database.q` is generated from, so a process's row here cannot
    claim an edge the build would reject.

    A vendored TorQ process has no `Pipeline` entry and so no declared edges;
    it gets the same dash as a uqf process that genuinely has none, because
    this table is not the place to explain the difference.
    """
    inputs = dependencies.inputs_by_process()
    outputs = dependencies.outputs_by_process()
    depends = dependencies.depends_on_by_process()
    for row in rows:
        name = row["Process"]
        row["Depends on"] = graph_cell(depends.get(name, ()))
        row["Inputs"] = graph_cell(inputs.get(name, ()))
        row["Outputs"] = graph_cell(outputs.get(name, ()))
