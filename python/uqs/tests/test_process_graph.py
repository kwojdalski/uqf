"""`uqs graph`: the process graph as trees, the filter over them, and the browser."""

from __future__ import annotations

import asyncio

from textual.widgets import Tree
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import graph as cli_graph
from uqs.cli.graph_browser import browser
from uqs.model import process_graph
from uqs.model.process_graph import Edge, Node, forest, roots, unconnected

runner = CliRunner()

#: feed -> a -> c, feed -> b -> c, and a lone process.
EDGES = [
    Edge("feed", "a", ("quotes",)),
    Edge("feed", "b", ("trades",)),
    Edge("a", "c", ("marks",)),
    Edge("b", "c", ("fills",)),
]
PROCS = ["feed", "a", "b", "c", "lonely"]


def shape(node: Node) -> tuple:
    """A tree as nested tuples of (name, repeat, children), for comparing."""
    return (node.name, node.repeat, tuple(shape(c) for c in node.children))


def test_downstream_roots_are_what_nothing_feeds():
    assert roots(PROCS, EDGES, "downstream") == ["feed", "lonely"]


def test_upstream_roots_are_what_nothing_reads():
    assert roots(PROCS, EDGES, "upstream") == ["c", "lonely"]


def test_a_process_reached_twice_is_drawn_once():
    feed, lonely = forest(PROCS, EDGES, "downstream")
    # c has nothing beneath it, so its second appearance hides nothing and
    # is drawn plainly rather than marked.
    assert shape(feed) == (
        "feed",
        False,
        (("a", False, (("c", False, ()),)), ("b", False, (("c", False, ()),))),
    )
    assert shape(lonely) == ("lonely", False, ())


def test_a_repeat_is_marked_only_where_it_hides_a_subtree():
    (c, _) = forest(PROCS, EDGES, "upstream")
    a, b = c.children
    assert (a.name, a.repeat, b.name, b.repeat) == ("a", False, "b", False)
    # feed is reached under a, then again under b: drawn once, marked after.
    assert (a.children[0].repeat, b.children[0].repeat) == (False, False)
    edges = [*EDGES, Edge("src", "feed", ("raw",))]
    (c, _) = forest([*PROCS, "src"], edges, "upstream")
    a, b = c.children
    assert (a.children[0].repeat, b.children[0].repeat) == (False, True)


def test_edges_carry_their_tables():
    (feed, _) = forest(PROCS, EDGES, "downstream")
    assert [(n.name, n.tables) for n in feed.children] == [("a", ("quotes",)), ("b", ("trades",))]


def test_a_cycle_is_cut():
    loop = [Edge("x", "y", ("t",)), Edge("y", "x", ("u",))]
    (x,) = forest(["x", "y"], loop, "downstream", root="x")
    assert shape(x) == ("x", False, (("y", False, (("x", True, ()),)),))


def test_without_collapse_every_path_is_drawn():
    edges = [*EDGES, Edge("c", "d", ("out",))]
    (feed, _) = forest([*PROCS, "d"], edges, "downstream", collapse=False)
    a, b = feed.children
    assert [n.name for n in a.children[0].children] == ["d"]
    assert [n.name for n in b.children[0].children] == ["d"]


def test_unconnected_processes_are_listed_apart():
    connected, alone = unconnected(forest(PROCS, EDGES, "downstream"))
    assert ([t.name for t in connected], alone) == (["feed"], ["lonely"])


def test_a_filter_keeps_matches_and_the_path_to_them():
    trees = forest(PROCS, EDGES, "downstream", collapse=False)
    (feed,) = cli_graph.prune(trees, "b")
    assert shape(feed) == ("feed", False, (("b", False, (("c", False, ()),)),))


def test_a_filter_matches_the_tables_on_an_edge():
    trees = forest(PROCS, EDGES, "downstream", collapse=False)
    (feed,) = cli_graph.prune(trees, "marks")
    assert [n.name for n in feed.children] == ["a"]


def test_the_real_registry_has_the_fx_chain():
    # fxfeed1 publishes quote, which marketdata1 subscribes to.
    found = {(e.producer, e.consumer): e.tables for e in process_graph.edges()}
    assert "quote" in found[("fxfeed1", "marketdata1")]


def test_the_command_prints_the_trees_offline():
    result = runner.invoke(cli.app, ["graph", "--offline"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "marketdata1  reads quote" in result.output
    assert "no declared edges:" in result.output


def test_the_command_refuses_an_unknown_process():
    result = runner.invoke(cli.app, ["graph", "nosuch1", "--offline"])
    assert result.exit_code == 1


def drive(keys: list[str]) -> tuple[str | None, list[str]]:
    """Press `keys` in a browser over EDGES; its result and the labels showing."""
    app = browser(PROCS, EDGES, {"a": "up", "b": "down"}, {}, "downstream", None)

    async def run() -> list[str]:
        async with app.run_test() as pilot:
            await pilot.press(*keys)
            await pilot.pause()
            tree = app.query_one(Tree)
            lines = (tree.get_node_at_line(i) for i in range(tree.last_line + 1))
            shown = [str(node.label) for node in lines if node is not None]
            if app.is_running:
                await pilot.press("escape")
            return shown

    shown = asyncio.run(run())
    return app.return_value, shown


def test_the_browser_draws_every_process():
    _, shown = drive([])
    names = " ".join(shown)
    assert all(name in names for name in ("feed", "a", "b", "c", "lonely"))


def test_typing_filters_the_tree():
    _, shown = drive(list("trades"))
    assert [line.split()[0] for line in shown] == ["feed", "b", "c"]


def test_ctrl_t_turns_the_tree_upstream():
    _, shown = drive(["ctrl+t"])
    assert shown[0].startswith("c")


def test_enter_returns_the_highlighted_process():
    chosen, _ = drive(["down", "enter"])
    assert chosen == "a"
